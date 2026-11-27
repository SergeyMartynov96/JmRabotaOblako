import argparse,datetime as dt,json,os,shutil,tempfile,urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image
from playwright.sync_api import sync_playwright
from daily_metrics.run import reporting_day
from daily_metrics.core import StructureError
from daily_metrics.source_probe import safe_error
from xk_metrics.config import STORES
from xk_metrics.sources import Client,Sources
from telegram_reports.catalog import ReportCharts,CHARTS
from telegram_reports.core import weekly_period
from telegram_reports.render import capture_chart,compose,active_page
from telegram_reports.security import seal_images,seal_tree,arm_failed_cleanup,auth_snapshot
from telegram_reports.transport import BotClient,BotError,decrypt_recipient
from telegram_reports.connect import BINDING
from telegram_reports.run import deliver_albums
from .sheets import preflight,capture

KINDS=('ui2_days','ui2','top_qty','top_rub','uv_dishes')


def caption(branch):
    return branch.name.lower()+'\nобщая таблица\nсписания по десертам\nсписания по позициям\nув по позициям'


def prior_attempts(day):
    found=set();repo=os.environ['GITHUB_REPOSITORY']
    for page in range(1,6):
        request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}',headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
        with urllib.request.urlopen(request,timeout=30) as response:items=json.load(response)['artifacts']
        found.update(a['name'] for a in items if not a.get('expired'))
        if len(items)<100:break
    return {s.branch.department for s in STORES.values() if f'xk-telegram-attempt-{day}-{s.branch.department}' in found}


def collect(bi,charts,store,day,folder):
    folder.mkdir();start,end=weekly_period(day+dt.timedelta(days=1));panels={}
    for kind in KINDS:
        active_page(bi)
        left,right=(day,day+dt.timedelta(days=1)) if kind in ('top_qty','top_rub') else (start,end)
        print(json.dumps({'branch':store.branch.name,'source':kind,'stage':'query'}),flush=True)
        form,payload=charts.read(kind,store.branch,left,right)
        (folder/(kind+'.json')).write_text(json.dumps({'form':form,'payload':payload},ensure_ascii=False))
        raw=folder/(kind+'.png')
        capture_chart(bi,form,payload,raw,*( (1800,950) if kind=='uv_dishes' else (880,420)))
        panels[kind]=(CHARTS[kind][1],raw)
    return panels


def assemble(browser,bi,store,day,folder,panels):
    images=[folder/f'{i:02}.png' for i in range(4)]
    print(json.dumps({'branch':store.branch.name,'assembly_stage':'weekly_sheet'}),flush=True)
    capture(bi.context,store,day,images[0])
    print(json.dumps({'branch':store.branch.name,'assembly_stage':'compose'}),flush=True)
    compose(browser,[panels['ui2_days'],panels['ui2']],images[1],columns=2)
    compose(browser,[panels['top_qty'],panels['top_rub']],images[2],columns=2)
    compose(browser,[panels['uv_dishes']],images[3])
    for path in images:
        with Image.open(path) as image:
            w,h=image.size
            if image.format!='PNG' or min(w,h)<=0 or w+h>10000 or max(w,h)/min(w,h)>20:raise StructureError('Invalid four-image report')
            image.verify()
    return store.branch,images,caption(store.branch)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--operation',choices=['preview','deliver'],default='preview')
    parser.add_argument('--branch',choices=['all','Х76','К44'],default='all');args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise StructureError('Use GitHub Actions')
    repo=os.environ['GITHUB_REPOSITORY'];ident=os.environ['GITHUB_RUN_ID']
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{ident}',headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as response:run=json.load(response)
    if dt.datetime.fromisoformat(run['created_at'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Yekaterinburg')).date()!=dt.datetime.now(ZoneInfo('Asia/Yekaterinburg')).date():raise StructureError('Stale queued report')
    day=reporting_day();stores=[s for name,s in STORES.items() if args.branch in ('all',name)]
    public=os.getenv('REVIEW_PUBLIC_KEY','')
    if args.operation=='preview' and not public:raise StructureError('Encrypted preview key required')
    bot=recipient=None
    if args.operation=='deliver':
        bot=BotClient(os.environ.get('TELEGRAM_BOT_TOKEN',''));me=bot.identity()
        recipient=decrypt_recipient(bot._token,me['id'],json.loads(BINDING.read_text()))
        attempted=prior_attempts(day);stores=[s for s in stores if s.branch.department not in attempted]
        if not stores:print('{"delivery_skipped":"prior_attempt_receipt"}');return
    # Read the actual native store cells before spending time in BI.
    for store in stores:preflight(store,day)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=not bool(os.getenv('DISPLAY')),executable_path=shutil.which('google-chrome'))
                sources=Sources(browser,Client());bi=sources.bi
                bi.seal_before_exit=lambda:seal_tree(root,public,'review.enc.json')
                bi.auth_failure_snapshot=auth_snapshot(root,public)
                try:
                    bi.authenticate();charts=ReportCharts(bi)
                    captured=[(s,root/str(s.branch.department),collect(bi,charts,s,day,root/str(s.branch.department))) for s in stores]
                    # Keep the first native BI page active and finish all BI images
                    # before opening any other browser page for composition.
                    albums=[assemble(browser,bi,s,day,folder,panels) for s,folder,panels in captured]
                except Exception:
                    arm_failed_cleanup(bi);raise
                finally:bi.close();browser.close()
            if reporting_day()!=day:raise StructureError('Report day changed before sending')
            if args.operation=='deliver':deliver_albums(albums,bot,recipient,day)
            print(json.dumps({'branches_collected':len(albums),'images_per_branch':4,'operation':args.operation}),flush=True)
        finally:
            if public:seal_tree(root,public,'review.enc.json')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        from xk_metrics.sources import transient_browser_error
        if os.getenv('REPORT_STATUS_FILE'):Path(os.environ['REPORT_STATUS_FILE']).write_text(json.dumps({'retryable':transient_browser_error(exc)}))
        print(json.dumps({'report_failed':True,'error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
