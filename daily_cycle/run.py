import argparse,datetime as dt,json,os,re,time,urllib.request,urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo
from telegram_reports.transport import BotClient,BotError,decrypt_recipient
from telegram_reports.connect import BINDING
from workflow_alerts.run import diagnose

TZ=ZoneInfo('Asia/Yekaterinburg')
HOURS={'morning':7,'reports':8,'suppliers':16}
LABELS={'morning':'07:00 — заполнение пяти филиалов','reports':'08:00 — отправка отчётов пяти филиалов','suppliers':'16:00 — поставщики АВ12, К44 и Х76'}
STEPS={
 'morning': [('daily-metrics.yml','АВ12, М42, Г34',{'operation':'apply','branch':'all'}),('x76-morning-metrics.yml','Х76',{'operation':'apply'}),('k44-morning-metrics.yml','К44',{'operation':'apply'})],
 'reports': [('telegram-reports.yml','АВ12, М42, Г34',{'operation':'deliver','branch':'all'}),('xk-telegram-reports.yml','Х76, К44',{'operation':'deliver','branch':'all'})],
 'suppliers': [('cloud-production.yml','АВ12',{'operation':'apply','slots':'09:00,10:00,11:00,12:00,13:00,14:00,15:00,16:00','external_window':'none'}),('cloud-k44.yml','К44',{'operation':'apply'}),('cloud-x76.yml','Х76',{'operation':'apply'})]}


def api(path,data=None):
    request=urllib.request.Request('https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']+path,
       data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                return json.load(response) if response.status!=204 else None
        except urllib.error.HTTPError as exc:
            # Dispatch is never retried: an uncertain response might already have created a run.
            if data is not None or exc.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(3)


def valid_day(created,now,stage):
    local=now.astimezone(TZ);original=dt.datetime.fromisoformat(created.replace('Z','+00:00')).astimezone(TZ)
    if original.date()!=local.date():raise RuntimeError('Stale daily cycle')
    if local.hour<HOURS[stage]:raise RuntimeError('Daily cycle is not due')
    return local.date()


def checked_wait(day,seconds=20):
    if dt.datetime.now(TZ).date()!=day:raise RuntimeError('Daily cycle date changed')
    time.sleep(seconds)


def child(file,inputs,tag,day):
    api('/actions/workflows/'+file+'/dispatches',{'ref':'main','inputs':{**inputs,'cycle_id':tag}})
    deadline=time.monotonic()+180;ident=None
    while time.monotonic()<deadline:
        runs=api('/actions/workflows/'+file+'/runs?event=workflow_dispatch&branch=main&per_page=50')['workflow_runs']
        matches=[r for r in runs if r.get('display_title','').endswith('[cycle:'+tag+']')]
        if len(matches)>1:raise RuntimeError('Ambiguous child run')
        if matches:ident=matches[0]['id'];break
        checked_wait(day,5)
    if ident is None:raise RuntimeError('Child dispatch acknowledgement missing')
    print(json.dumps({'child_run':ident,'workflow':file,'stage':'waiting'}),flush=True)
    deadline=time.monotonic()+55*60
    while time.monotonic()<deadline:
        run=api('/actions/runs/'+str(ident))
        if run['status']=='completed':return run
        checked_wait(day)
    raise RuntimeError('Child workflow deadline exceeded')


def completed_cycle(day,stage):
    name=f'daily-cycle-complete-{day}-{stage}'
    artifacts=api('/actions/artifacts?per_page=100&name='+name)['artifacts']
    for artifact in artifacts:
        if artifact['name']!=name or artifact.get('expired'):continue
        run=api('/actions/runs/'+str(artifact['workflow_run']['id']))
        if (run.get('status')=='completed' and run.get('conclusion')=='success'
            and run.get('path')=='.github/workflows/daily-cycle.yml' and run.get('head_branch')=='main'
            and run.get('display_title')=='LifeMart cycle '+stage
            and dt.datetime.fromisoformat(run['created_at'].replace('Z','+00:00')).astimezone(TZ).date()==day):return True
    return False


def morning_dependency(day):
    deadline=time.monotonic()+60*60
    while time.monotonic()<deadline:
        if completed_cycle(day,'morning'):return
        runs=api('/actions/workflows/daily-cycle.yml/runs?branch=main&per_page=100')['workflow_runs']
        matches=[r for r in runs if r.get('display_title')=='LifeMart cycle morning' and dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).astimezone(TZ).date()==day]
        if matches:
            run=max(matches,key=lambda r:r['id'])
            if run['status']=='completed' and run['conclusion']!='success':
                raise RuntimeError('Morning tables were not completed successfully')
        checked_wait(day)
    raise RuntimeError('Morning tables were not confirmed before report deadline')


class PreparationError(RuntimeError):
    def __init__(self,branch):self.branch=branch


def report_preflight(day):
    from daily_metrics.config import BRANCHES
    from telegram_reports.sheets import preflight_week
    from xk_metrics.config import STORES
    from xk_reports.sheets import preflight
    yesterday=day-dt.timedelta(days=1)
    for branch in BRANCHES:
        try:preflight_week(branch,yesterday)
        except Exception:raise PreparationError(branch.name) from None
    for store in STORES.values():
        try:preflight(store,yesterday)
        except Exception:raise PreparationError(store.branch.name) from None



class DeliveryError(RuntimeError):
    def __init__(self,branch):self.branch=branch


def read_delivery_receipt(artifact,department):
    import subprocess,tempfile
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(['gh','run','download',str(artifact['workflow_run']['id']),'--repo',os.environ['GITHUB_REPOSITORY'],
                        '--name',artifact['name'],'--dir',folder],capture_output=True,check=True,timeout=60)
        return json.loads((Path(folder)/(str(department)+'.json')).read_text())


def report_delivery_confirmed(day):
    from daily_metrics.config import BRANCHES
    from xk_metrics.config import STORES
    yesterday=day-dt.timedelta(days=1)
    groups=[(b,8,'telegram-attempt','telegram-reports.yml') for b in BRANCHES]+[(s.branch,4,'xk-telegram-attempt','xk-telegram-reports.yml') for s in STORES.values()]
    for branch,images,prefix,file in groups:
        name=f'{prefix}-{yesterday}-{branch.department}'
        artifacts=api('/actions/artifacts?per_page=100&name='+name)['artifacts']
        artifacts=[a for a in artifacts if a['name']==name and not a.get('expired')]
        if not artifacts:raise DeliveryError(branch.name)
        latest=max(artifacts,key=lambda a:a['id'])
        run=api('/actions/runs/'+str(latest['workflow_run']['id']))
        if run.get('path')!='.github/workflows/'+file or run.get('head_branch')!='main':raise DeliveryError(branch.name)
        receipt=read_delivery_receipt(latest,branch.department)
        if (receipt.get('status')!='accepted' or receipt.get('day')!=str(yesterday)
            or receipt.get('branch')!=branch.name or receipt.get('images')!=images):raise DeliveryError(branch.name)


def failure_location(log):
    """Extract only approved shop/metric labels, never source values or raw errors."""
    labels={'revenue':'выручка','writeoff':'списания','uv':'упущенная выручка','osa':'OSA','invoices':'приходные накладные','reasons':'причины списаний','procurement':'закуп факт'}
    result=[];last=''
    sources={'ui2_days':'недельные списания','ui2':'списания УИ2','top_qty':'ТОП списаний, шт','top_rub':'ТОП списаний, руб','uv_dishes':'УВ по позициям'}
    assemblies={'weekly_sheet':'общая таблица','invoices':'приходные накладные','compose':'сборка изображений'}
    for line in log.splitlines():
        try:item=json.loads(line[line.index('{'):])
        except (ValueError,TypeError):continue
        if not isinstance(item,dict):continue
        if item.get('branch') in ('АВ12','М42','Г34','Х76','К44'):
            step=sources.get(item.get('source')) or assemblies.get(item.get('assembly_stage'))
            if step:last=item['branch']+' — '+step
        for error in item.get('errors',[]) if isinstance(item.get('errors'),list) else []:
            if not isinstance(error,str):continue
            for branch in ('АВ12','М42','Г34','Х76','К44'):
                if error.startswith(branch+': '):
                    for key,label in labels.items():
                        if re.search(r'\b'+key+r' (?:source|destination)',error):
                            value=branch+' — '+label
                            if value not in result:result.append(value)
    return '; '.join(result) or last


def notify(text,stage,day):
    receipt=Path('cycle-receipt.json')
    bot=BotClient(os.environ.get('TELEGRAM_BOT_TOKEN',''));me=bot.identity()
    recipient=decrypt_recipient(bot._token,me['id'],json.loads(BINDING.read_text()))
    receipt.write_text(json.dumps({'day':str(day),'stage':stage,'status':'attempted'}))
    message=bot.api('sendMessage',{'chat_id':recipient,'text':text,'disable_web_page_preview':True})
    if message.get('chat',{}).get('id')!=recipient or message.get('chat',{}).get('type')!='private' or not message.get('message_id'):raise BotError('Cycle status delivery not confirmed')
    receipt.write_text(json.dumps({'day':str(day),'stage':stage,'status':'accepted'}))
    print(json.dumps({'cycle_status_accepted':True,'stage':stage}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=HOURS,required=True);parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise RuntimeError('Actions only')
    stage=args.stage;own=api('/actions/runs/'+os.environ['GITHUB_RUN_ID']);day=valid_day(own['created_at'],dt.datetime.now(TZ),stage)
    if args.verify:
        print(json.dumps({'stage':stage,'children':[x[0] for x in STEPS[stage]],'messages_sent':0,'writes':0}));return
    # One completed cycle per business stage/day. A failed cycle can be manually resumed.
    if completed_cycle(day,stage):
        print('{"cycle_skipped":"already_complete"}');return
    done=[];current='проверка готовности';failure=None
    try:
        if stage=='reports':
            morning_dependency(day)
            report_preflight(day)
        for file,label,inputs in STEPS[stage]:
            current=label;run=child(file,inputs,str(own['id']),day)
            if run['conclusion']!='success':
                import subprocess
                result=subprocess.run(['gh','run','view',str(run['id']),'--repo',os.environ['GITHUB_REPOSITORY'],'--log-failed'],capture_output=True,text=True,timeout=60)
                log=result.stdout[-500000:] if result.returncode==0 else ''
                category,reason=diagnose(log,run['conclusion'])
                location=failure_location(log) if category!='code' else ''
                if location:reason='Показатели: '+location+'\n'+reason
                failure=reason+'\nЗапуск: '+run['html_url'];break
            done.append(label)
        if failure is None and stage=='reports':
            current='подтверждение доставки всех отчётов'
            report_delivery_confirmed(day)
    except Exception as exc:
        # Static text only, never forward exception text or private source logs.
        failure={'Morning tables were not completed successfully':'Утреннее заполнение завершилось ошибкой. Отправка отчётов остановлена.',
                 'Morning tables were not confirmed before report deadline':'Нет подтверждения заполнения всех таблиц. Отправка отчётов остановлена.'}.get(str(exc),'Не удалось подтвердить завершение этапа ('+type(exc).__name__+'). Требуется проверка запуска.')
        if isinstance(exc,PreparationError):
            current=exc.branch+' — готовность общей таблицы'
            failure='Не удалось подтвердить дату или обязательные показатели таблицы. Отправка всех отчётов остановлена до проверки.'
        if isinstance(exc,DeliveryError):
            current=exc.branch+' — подтверждение доставки'
            failure='Нет подтверждённой квитанции Telegram для последней отправки этого магазина. Автоматический повтор отключён, чтобы не прислать дубли.'
    if failure is None:
        notify('✅ LifeMart: всё выполнено\n'+LABELS[stage]+'\n'+('Таблицы заполнены и проверены.' if stage=='morning' else 'Отчёты отправлены.' if stage=='reports' else 'Цифры поставщиков перенесены и проверены.'),stage,day)
        Path('cycle-complete.json').write_text(json.dumps({'day':str(day),'stage':stage,'status':'complete','run':own['id']}))
        return
    notify('⚠️ LifeMart: запуск не завершён\n'+LABELS[stage]+'\nОстановились: '+current+'\nУспешно: '+(', '.join(done) or 'нет завершённых этапов')+'\n'+failure+'\nОбщий запуск: https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+str(own['id']),stage,day)
    raise SystemExit(1)

if __name__=='__main__':main()
