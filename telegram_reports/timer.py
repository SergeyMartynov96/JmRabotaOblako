"""Use the existing external 08:00 signal to dispatch private reports at 09:00."""
import argparse,datetime as dt,json,os,time,urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Asia/Yekaterinburg')


def due_time(event,now,repo):
    run=event.get('workflow_run',{})
    if event.get('action')!='requested' or run.get('path')!='.github/workflows/daily-metrics.yml':return None
    if run.get('head_branch')!='main' or run.get('head_repository',{}).get('full_name')!=repo:return None
    if run.get('event')!='workflow_dispatch' or run.get('display_title')!='LifeMart metrics apply (all)':return None
    created=dt.datetime.fromisoformat(run['created_at'].replace('Z','+00:00')).astimezone(TZ)
    local=now.astimezone(TZ)
    if created.date()!=local.date() or created.hour!=8 or created.minute>30:return None
    target=dt.datetime.combine(created.date(),dt.time(9),TZ)
    if local>target+dt.timedelta(minutes=15):raise ValueError('Telegram timer window expired')
    return target


def dispatch(operation):
    repo=os.environ['GITHUB_REPOSITORY'];token=os.environ['GH_TOKEN']
    payload={'ref':'main','inputs':{'operation':operation,'branch':'all'}}
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/workflows/telegram-reports.yml/dispatches',
        data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            if response.status!=204:raise ValueError('Report dispatch not accepted')
    except Exception as exc:raise RuntimeError('Report dispatch failed ('+type(exc).__name__+')') from None
    print(json.dumps({'dispatch_accepted':True,'operation':operation}),flush=True)


def canonical_event(run_id):
    # Webhook workflow_run omits fields present in REST (e.g. display_title).
    # Resolve only this repository's numeric run ID, then apply all original guards.
    if not str(run_id).isdigit():raise ValueError('Parent run ID required')
    repo=os.environ['GITHUB_REPOSITORY'];token=os.environ['GH_TOKEN']
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{run_id}',
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as response:run=json.load(response)
    if run.get('id')!=int(run_id):raise ValueError('Parent run mismatch')
    return {'action':'requested','workflow_run':run}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');parser.add_argument('--arm-morning',action='store_true');args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise ValueError('Actions only')
    if args.verify:dispatch('verify');return
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if args.arm_morning:event=canonical_event(os.getenv('PARENT_RUN',''))
    elif event.get('action')=='requested':event=canonical_event(event.get('workflow_run',{}).get('id',''))
    target=due_time(event,dt.datetime.now(TZ),os.environ['GITHUB_REPOSITORY'])
    if target is None:print('{"timer_skipped":"not_the_external_morning_run"}',flush=True);return
    print('Report dispatch armed for 09:00 Asia/Yekaterinburg',flush=True)
    while True:
        remaining=(target-dt.datetime.now(TZ)).total_seconds()
        if remaining<=0:break
        time.sleep(min(20,remaining))
    due_time(event,dt.datetime.now(TZ),os.environ['GITHUB_REPOSITORY'])
    dispatch('deliver')

if __name__=='__main__':main()
