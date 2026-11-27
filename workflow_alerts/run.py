import datetime as dt,hashlib,json,os,re,subprocess,urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from telegram_reports.transport import BotClient,BotError,decrypt_recipient
from telegram_reports.connect import BINDING

NAMES={
 'cloud-k44.yml':'К44 — перенос заявок в таблицу',
 'cloud-x76.yml':'Х76 — перенос заявок в таблицу',
 'cloud-production.yml':'АВ12 — перенос заявок в таблицу',
 'daily-metrics.yml':'АВ12, М42, Г34 — утреннее заполнение',
 'k44-morning-metrics.yml':'К44 — утреннее заполнение',
 'x76-morning-metrics.yml':'Х76 — утреннее заполнение',
 'telegram-reports.yml':'Отправка утренних отчётов в Telegram',
 'telegram-report-timer.yml':'Запуск утренних отчётов',
 'secret-guard.yml':'Проверка защиты секретов',
 'metrics-source-check.yml':'Проверка источников показателей',
 'cloud-readonly-test.yml':'Проверка чтения заявок',
 'procurement-window-check.yml':'Проверка дат закупа',
 'repair-writeoff.yml':'Восстановление сумм списаний',
 'secrets-audit.yml':'Проверка настроек доступа',
 'telegram-check.yml':'Проверка Telegram',
 'telegram-connect.yml':'Подключение Telegram',
 'telegram-report-inspect.yml':'Проверка структуры отчётов',
 'telegram-report-preview.yml':'Пробная сборка отчётов',
}

def diagnose(log,conclusion):
    if 'Sheet arrival date missing' in log:
        return 'arrival','В таблице не найдена дата привоза. Нужно проверить, что подготовлены строки нужной недели.'
    if any(x in log for x in ('Sheet date changed','Sheet header changed','Concurrent sheet change','Sheet selection not stable')):
        return 'sheet','Не удалось надёжно подтвердить дату или ячейку таблицы. Запись остановлена, чтобы не попасть в чужую строку.'
    if 'Summary metrics not ready:' in log:return 'incomplete','Не заполнены обязательные показатели таблицы. Неполный отчёт не отправлен.'
    if any(x in log for x in ('ModuleNotFoundError:','ImportError:','FAILED (failures=','FAILED (errors=')):
        return 'code','Не прошла проверка кода или зависимостей. Основная обработка могла не начаться.'
    if any(x in log for x in ('TimeoutError:','FAIL: TimeoutError','report_failed": true, "error": "ConnectionError','ETIMEDOUT','transport unavailable','transport deadline exhausted')) or conclusion=='timed_out':
        return 'timeout','Источник данных или страница не ответили вовремя. Нужна повторная проверка доступа и незавершённого переноса.'
    if any(x in log for x in ('SU login failed','SU browser login failed','HTTP 403','HTTP Error 403')):
        return 'access','Не удалось войти в систему или доступ отклонён. Нужна проверка подключения.'
    if 'Stored daily metric differs from source' in log:
        return 'mismatch','Сохранённое значение таблицы отличается от актуального источника. Нужна сверка цифр.'
    return 'other','Обработка остановилась с ошибкой. Причина требует проверки журнала запуска.'


def canonical(run,repo):
    path=run.get('path','').split('/')[-1]
    return (path in NAMES and run.get('head_branch')=='main' and
            run.get('head_repository',{}).get('full_name')==repo and
            run.get('event') in ('workflow_dispatch','schedule','push') and
            run.get('conclusion') in ('failure','timed_out'))


def api(path):
    repo=os.environ['GITHUB_REPOSITORY']
    request=urllib.request.Request('https://api.github.com/repos/'+repo+path,
        headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':raise RuntimeError('Actions only')
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    ident=event.get('workflow_run',{}).get('id') if os.getenv('GITHUB_EVENT_NAME')=='workflow_run' else os.getenv('PARENT_RUN')
    if not str(ident).isdigit():raise RuntimeError('Existing parent run required')
    run=api('/actions/runs/'+str(ident));repo=os.environ['GITHUB_REPOSITORY']
    if run.get('id')!=int(ident) or not canonical(run,repo):
        print('{"alert_skipped":"not_a_watched_failure"}');return
    result=subprocess.run(['gh','run','view',str(ident),'--repo',repo,'--log-failed'],capture_output=True,text=True,timeout=60)
    log=result.stdout[-500000:] if result.returncode==0 else ''
    category,reason=diagnose(log,run['conclusion'])
    completed=dt.datetime.fromisoformat(run['updated_at'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Yekaterinburg'))
    file=run['path'].split('/')[-1]
    bucket=completed.strftime('%Y-%m-%d-%H')+str(completed.minute//30)
    receipt='lifemart-alert-'+hashlib.sha256((file+bucket+category).encode()).hexdigest()[:32]
    artifacts=api('/actions/artifacts?per_page=100')['artifacts']
    if any(a['name']==receipt and not a.get('expired') for a in artifacts):
        print('{"alert_skipped":"incident_already_reported"}');return
    bot=BotClient(os.environ.get('TELEGRAM_BOT_TOKEN',''));me=bot.identity()
    recipient=decrypt_recipient(bot._token,me['id'],json.loads(BINDING.read_text()))
    text='⚠️ Сбой автоматизации LifeMart\n'+NAMES[file]+'\n'+completed.strftime('%d.%m.%Y %H:%M')+' (Екатеринбург)\n\n'+reason+'\n\nВажно: не считайте перенос завершённым до успешной проверки.\nЗапуск: https://github.com/'+repo+'/actions/runs/'+str(ident)
    # One send only: do not retry an ambiguous Telegram acknowledgement.
    Path('/tmp/lifemart-alert.json').write_text(json.dumps({'run':int(ident),'category':category,'status':'attempted'}))
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('receipt='+receipt+'\n')
    message=bot.api('sendMessage',{'chat_id':recipient,'text':text,'disable_web_page_preview':True})
    if message.get('chat',{}).get('id')!=recipient or message.get('chat',{}).get('type')!='private' or not message.get('message_id'):
        raise BotError('Private alert delivery not confirmed')
    Path('/tmp/lifemart-alert.json').write_text(json.dumps({'run':int(ident),'category':category,'status':'accepted'}))
    print(json.dumps({'private_alert_accepted':True,'source_run':int(ident),'category':category}))

if __name__=='__main__':main()
