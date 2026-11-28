"""Recover missing native cron from the already working external timer signals."""
import argparse,datetime as dt,json,os,time
from pathlib import Path
from .run import api,TZ,HOURS,completed_cycle


def route(source,now,repo):
    if (source.get('head_repository',{}).get('full_name')!=repo or source.get('head_branch')!='main'
        or source.get('status')!='completed' or source.get('event') not in ('workflow_dispatch','schedule')):return None
    local=now.astimezone(TZ)
    try:created=dt.datetime.fromisoformat(source['created_at'].replace('Z','+00:00')).astimezone(TZ)
    except (KeyError,ValueError):return None
    if created.date()!=local.date():return None
    title=source.get('display_title','').strip();file=source.get('path')
    if file=='.github/workflows/daily-cycle.yml':
        return 'reports' if title=='LifeMart cycle morning' and source.get('conclusion')=='success' else None
    if '[cycle:' in title or source.get('conclusion')!='skipped':return None
    if file=='.github/workflows/daily-metrics.yml' and title=='LifeMart metrics apply (all)' and local.hour>=7:return 'morning'
    if file=='.github/workflows/cloud-production.yml' and title=='LifeMart AV12 apply (morning)' and local.hour>=7:return 'morning'
    if file in ('.github/workflows/cloud-k44.yml','.github/workflows/cloud-x76.yml') and local.hour>=16:
        expected='LifeMart '+('K44' if 'k44' in file else 'X76')+' apply (16:00)'
        if title==expected:return 'suppliers'
    return None


def existing(stage,day):
    rows=api('/actions/workflows/daily-cycle.yml/runs?branch=main&per_page=100')['workflow_runs']
    return any(r.get('display_title')=='LifeMart cycle '+stage and dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).astimezone(TZ).date()==day for r in rows)


def late_notice(stage,day):
    from .run import notify,LABELS
    now=dt.datetime.now(TZ)
    due=dt.datetime.combine(day,dt.time(HOURS[stage]),TZ)
    if now<due+dt.timedelta(minutes=15):return
    try:
        notify('⚠️ LifeMart: задержка запуска\n'+LABELS[stage]+'\nПлановый общий запуск не появился. Внешний таймер запустил восстановление. Итог придёт после проверки данных.',stage,day)
    except Exception as exc:
        print(json.dumps({'recovery_notice_failed':True,'error_type':type(exc).__name__}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise RuntimeError('Actions only')
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    ident=event.get('workflow_run',{}).get('id') if os.getenv('GITHUB_EVENT_NAME')=='workflow_run' else os.getenv('PARENT_RUN')
    if not str(ident).isdigit():raise RuntimeError('Existing source run required')
    now=dt.datetime.now(TZ);source=api('/actions/runs/'+str(ident))
    stage=route(source,now,os.environ['GITHUB_REPOSITORY'])
    if not stage:print('{"relay_skipped":"unrelated_source"}');return
    day=now.date()
    if stage=='morning' and completed_cycle(day,'morning'):stage='reports'
    if args.verify:print(json.dumps({'relay_verified':True,'stage':stage,'dispatches':0}));return
    if existing(stage,day):print(json.dumps({'relay_skipped':'cycle_already_created','stage':stage}));return
    # The morning completion event can arrive before the report release time.
    due=dt.datetime.combine(day,dt.time(HOURS[stage]),TZ)
    while dt.datetime.now(TZ)<due:
        time.sleep(min(20,(due-dt.datetime.now(TZ)).total_seconds()))
    if dt.datetime.now(TZ).date()!=day:raise RuntimeError('Relay date changed')
    if stage=='reports' and not completed_cycle(day,'morning'):raise RuntimeError('Morning completion receipt missing')
    if existing(stage,day):print(json.dumps({'relay_skipped':'cycle_already_created','stage':stage}));return
    # One POST only; an uncertain dispatch response is never blindly repeated.
    api('/actions/workflows/daily-cycle.yml/dispatches',{'ref':'main','inputs':{'stage':stage,'operation':'run'}})
    print(json.dumps({'relay_dispatch_accepted':True,'stage':stage,'source_run':int(ident)}),flush=True)
    late_notice(stage,day)

if __name__=='__main__':main()
