"""Collect eight validated private report images for each approved branch."""
import argparse,datetime as dt,json,os,shutil,tempfile,urllib.request
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from daily_metrics.config import BRANCHES
from daily_metrics.io import SUReports
from daily_metrics.extended_io import BIReports
from daily_metrics.extended_core import bi_rows,percentage_points
from daily_metrics.core import StructureError
from daily_metrics.run import reporting_day
from daily_metrics.source_probe import safe_error
from .catalog import ReportCharts,CHARTS
from .session import ReportSUReports,report_bi
from .core import weekly_period,OSA_METRICS
from .render import capture_chart,compose,active_page
from .sheets import capture_week
from .invoices import capture_invoices,fetch_invoices
from .security import seal_images,seal_tree,arm_failed_cleanup,auth_snapshot
from .transport import BotClient,BotError,decrypt_recipient,send_album
from .connect import BINDING

TZ=ZoneInfo('Asia/Yekaterinburg')


def osa_yesterday(payload,day):
    rows=[]
    for row in bi_rows(payload):
        raw=row.get('__timestamp')
        if raw is None:raise StructureError('OSA report date missing')
        date=(dt.datetime.fromtimestamp(raw/1000,dt.timezone.utc).date() if isinstance(raw,(int,float))
              else dt.datetime.fromisoformat(str(raw).replace('Z','+00:00')).date())
        if date==day:rows.append(row)
    if len(rows)!=1:raise StructureError('Yesterday OSA row missing or ambiguous')
    values=[percentage_points(rows[0].get(metric)) for metric in OSA_METRICS]
    if any(v is None or not 0<=v<=100 for v in values):raise StructureError('Yesterday OSA values invalid')
    return values


def caption(branch,day,osa):
    return (
        f'{branch.name}\n1. Приходные накладные\n2. Списания по причинам\n3. Топ списаний шт/руб\n'
        '4. %УИ2 по неделе\n5. УВ по позициям\n6. УВ по стоп-статусу\n'
        '7. OSA 11:00 '+format(osa[0],'.1f').replace('.',',')+'%\n'
        '8. OSA 20:00 '+format(osa[1],'.1f').replace('.',',')+'%')


def collect_bi(browser,bi,charts,branch,day,folder):
    folder.mkdir();start,end=weekly_period(day+dt.timedelta(days=1))
    panels={};audit={}
    for kind in CHARTS:
        active_page(bi)
        print(json.dumps({'branch':branch.name,'stage':'query','source':kind}),flush=True)
        left,right=(day,day+dt.timedelta(days=1)) if kind in ('reasons','top_qty','top_rub') else (start,end)
        form,payload=(charts.read_stop(branch,left,right) if kind=='uv_stop' else charts.read(kind,branch,left,right))
        audit[kind]={'form':form,'payload':payload}
        (folder/'bi-source.json').write_text(json.dumps(audit,ensure_ascii=False))
        size=(1800,950) if kind=='uv_dishes' else (1800,460) if kind=='uv_stop' else (880,420)
        raw=folder/(kind+'.png')
        print(json.dumps({'branch':branch.name,'stage':'render','source':kind}),flush=True)
        capture_chart(bi,form,payload,raw,*size)
        panels[kind]=(CHARTS[kind][1],raw)
    osa=osa_yesterday(audit['osa_food_japan']['payload'],day)
    return panels,audit,osa



@contextmanager
def assembly_stage(branch,folder,stage):
    print(json.dumps({'branch':branch.name,'assembly_stage':stage}),flush=True)
    try:yield
    except Exception as exc:
        # Only the encrypted review action can export this private diagnostic.
        (folder/'assembly-error.json').write_text(json.dumps({'stage':stage,'type':type(exc).__name__,'message':str(exc)}))
        print(json.dumps({'branch':branch.name,'assembly_failed_stage':stage,'error':safe_error(exc)}),flush=True)
        raise


def assemble(browser,bi,branch,day,folder,panels,audit,osa,*,invoice_html=None,sync_reason_cells=False):
    images=[folder/f'{index:02}.png' for index in range(8)]
    with assembly_stage(branch,folder,'weekly_sheet'):
        if sync_reason_cells:
            from daily_metrics.extended_run import sync_report_reasons
            summary=sync_report_reasons(browser,branch,day,audit['reasons']['payload'])
            print(json.dumps({'branch':branch.name,'reason_cells_refreshed':summary['written']}),flush=True)
        capture_week(bi.context,branch,day,images[0])
    with assembly_stage(branch,folder,'invoices'):
        capture_invoices(bi.context,branch,day,images[1],source=invoice_html)
    with assembly_stage(branch,folder,'compose'):
        compose(browser,[panels['reasons']],images[2],width=700)
        compose(browser,[panels['top_qty'],panels['top_rub']],images[3],columns=2)
        compose(browser,[panels['ui2_days'],panels['ui2']],images[4],columns=2)
        compose(browser,[panels['uv_dishes']],images[5])
        compose(browser,[panels['uv_stop']],images[6])
        compose(browser,[panels[k] for k in ('osa_all','osa_food_japan','osa_food','osa_japan')],images[7],columns=2,heading='Динамика OSA по матрице магазина')
    text=caption(branch,day,osa)
    (folder/'review.json').write_text(json.dumps({'branch':branch.name,'day':str(day),'caption':text,'audit':audit},ensure_ascii=False))
    return images,text


def preflight(collected,branches):
    from PIL import Image
    expected={b.department for b in branches}
    actual=[b.department for b,images,text in collected]
    if len(actual)!=len(expected) or set(actual)!=expected:raise StructureError('Incomplete branch report collection')
    for branch,images,text in collected:
        if len(images)!=8 or len(text)>1024:raise StructureError('Incomplete eight-image report')
        for path in images:
            if not path.is_file() or path.stat().st_size>10_000_000:raise StructureError('Required report image missing or oversized')
            with Image.open(path) as image:
                width,height=image.size
                if image.format!='PNG' or min(width,height)<=0 or width+height>10000 or max(width,height)/min(width,height)>20:
                    raise StructureError('Report image cannot be sent intact')
                image.verify()



def branches_for_delivery(branches,attempted,retry_branch='none'):
    return [branch for branch in branches if branch.department not in attempted or branch.name==retry_branch or retry_branch=='all']


def deliver_albums(collected,bot,recipient,day):
    failures=[]
    for branch,images,text in collected:
        if reporting_day()!=day:raise StructureError('Report day changed before delivery')
        receipts=Path('telegram-receipts');receipts.mkdir(exist_ok=True)
        receipt=receipts/(str(branch.department)+'.json')
        receipt.write_text(json.dumps({'day':str(day),'branch':branch.name,'status':'attempted'}))
        try:
            accepted=send_album(bot,recipient,images,text)
        except BotError as exc:
            state=exc.delivery_state
            receipt.write_text(json.dumps({'day':str(day),'branch':branch.name,'status':state,'error':str(exc)}))
            if exc.details is not None:
                (Path(images[0]).parent/'delivery-error.json').write_text(json.dumps(exc.details))
            print(json.dumps({'branch':branch.name,'album_failed':True,'delivery_state':state,'error':str(exc)}),flush=True)
            failures.append(branch.name)
            if state=='unsafe':raise
            continue
        receipt.write_text(json.dumps({'day':str(day),'branch':branch.name,'status':'accepted','images':accepted}))
        print(json.dumps({'branch':branch.name,'album_accepted':True,'images':accepted}),flush=True)
    if failures:raise BotError('Telegram albums not confirmed: '+', '.join(failures))


def prior_attempts(day):
    token=os.environ['GH_TOKEN'];repo=os.environ['GITHUB_REPOSITORY'];names=set()
    for page in range(1,6):
        request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        with urllib.request.urlopen(request,timeout=30) as response:items=json.load(response)['artifacts']
        names.update(a['name'] for a in items if not a.get('expired'))
        if len(items)<100:break
    return {b.department for b in BRANCHES if f'telegram-attempt-{day}-{b.department}' in names}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--operation',choices=('preview','deliver','verify'),default='preview')
    parser.add_argument('--branch',choices=['all']+[b.name for b in BRANCHES],default='all')
    parser.add_argument('--retry-branch',choices=['none','all']+[b.name for b in BRANCHES],default='none')
    args=parser.parse_args()
    if args.retry_branch!='none' and (args.operation!='deliver' or os.getenv('GITHUB_EVENT_NAME')!='workflow_dispatch'):
        raise StructureError('Delivery override requires an explicit manual dispatch')
    if os.getenv('GITHUB_ACTIONS')!='true':raise StructureError('Use GitHub Actions for report collection')
    if args.operation=='verify':
        bot=BotClient(os.environ.get('TELEGRAM_BOT_TOKEN',''));me=bot.identity()
        decrypt_recipient(bot._token,me['id'],json.loads(BINDING.read_text()))
        print('{"timer_dispatch_verified":true,"private_binding_valid":true,"messages_sent":0}',flush=True)
        return
    request=urllib.request.Request('https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID'],
        headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as response:metadata=json.load(response)
    created=dt.datetime.fromisoformat(metadata['created_at'].replace('Z','+00:00')).astimezone(TZ)
    if created.date()!=dt.datetime.now(TZ).date():raise StructureError('Queued report belongs to another local day')
    day=reporting_day();branches=[b for b in BRANCHES if args.branch in ('all',b.name)]
    public=os.getenv('REVIEW_PUBLIC_KEY','')
    if args.operation=='preview' and not public:raise StructureError('Encrypted review public key required')
    bot=None;recipient=None
    if args.operation=='deliver':
        bot=BotClient(os.environ.get('TELEGRAM_BOT_TOKEN',''));me=bot.identity()
        recipient=decrypt_recipient(bot._token,me['id'],json.loads(BINDING.read_text()))
        attempted=prior_attempts(day)
        branches=branches_for_delivery(branches,attempted,args.retry_branch)
        if not branches:
            print('{"delivery_skipped":"prior_attempt_receipt"}',flush=True);return
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);collected=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=not bool(os.getenv('DISPLAY')),executable_path=shutil.which('google-chrome'))
                client=ReportSUReports()
                bi=report_bi(browser,client);charts=ReportCharts(bi)
                bi.seal_before_exit=lambda:seal_tree(root,public,'review.enc.json')
                bi.auth_failure_snapshot=auth_snapshot(root,public)
                try:
                    bi.authenticate()
                    invoice_sources={}
                    for branch in branches:
                        print(json.dumps({'branch':branch.name,'stage':'invoice_preflight'}),flush=True)
                        # HTTP reads only: do not open or switch browser pages here.
                        invoice_sources[branch.department]=fetch_invoices(bi.context,branch,day)
                        print(json.dumps({'branch':branch.name,'invoice_source_validated':True}),flush=True)
                    captured=[]
                    for branch in branches:
                        folder=root/str(branch.department)
                        captured.append((branch,folder,collect_bi(browser,bi,charts,branch,day,folder)))
                    # No other browser page is opened until every BI image has finished.
                    for branch,folder,source in captured:
                        images,text=assemble(browser,bi,branch,day,folder,*source,invoice_html=invoice_sources[branch.department],sync_reason_cells=args.operation=='deliver')
                        collected.append((branch,images,text))
                except Exception:
                    arm_failed_cleanup(bi)
                    raise
                finally:bi.close();browser.close()
            preflight(collected,branches)
            if args.operation=='deliver':deliver_albums(collected,bot,recipient,day)
            print(json.dumps({'branches_collected':len(collected),'images_per_branch':8,'operation':args.operation}),flush=True)
        finally:
            if public:
                # Flatten private review files with branch prefixes, avoiding public raw artifacts.
                flat=root/'review';flat.mkdir()
                for folder in list(root.iterdir()):
                    if folder==flat or not folder.is_dir():continue
                    for path in folder.iterdir():
                        if path.suffix in ('.png','.json'):shutil.copyfile(path,flat/(folder.name+'-'+path.name))
                seal_images(flat,public,'review.enc.json')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        if os.getenv('REPORT_STATUS_FILE'):
            Path(os.environ['REPORT_STATUS_FILE']).write_text(json.dumps({'retryable':type(exc).__name__ in ('TimeoutError','ConnectionError','URLError')}))
        print(json.dumps({'report_failed':True,'error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
