"""Read-only check of the exact three procurement targets for today or tomorrow."""
import argparse,datetime as dt,json,os,shutil
from playwright.sync_api import sync_playwright
from .config import BRANCHES
from .io import RevenueSheet,csv_sheet
from .run import TZ
from .procurement_run import fill_procurement
from .core import StructureError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--when',choices=('today','tomorrow'),default='tomorrow')
    args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise StructureError('Use the read-only Actions procurement check')
    actual=dt.datetime.now(TZ).date()
    execution=actual+dt.timedelta(days=args.when=='tomorrow');report_day=execution-dt.timedelta(days=1)
    summary={'simulated_run_date':str(execution),'written':0,'planned':0,'preserved':0,'missing':0,'source_reads':0,'errors':[]}
    def load(book,gid):summary['source_reads']+=1;return csv_sheet(book,gid)
    def clock(expected):
        if expected!=report_day or dt.datetime.now(TZ).date()!=actual:raise StructureError('Preview day changed')
    def blocked(*args,**kwargs):raise StructureError('Read-only procurement probe attempted a write')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        try:
            for branch in BRANCHES:
                sheet=None
                try:
                    sheet=RevenueSheet(browser,branch,report_day)
                    sheet.put_blank=blocked;sheet.replace_if_unchanged=blocked
                    errors=fill_procurement(sheet,branch,report_day,sheet.refresh(),False,summary,clock,load)
                    summary['errors'].extend(branch.name+': '+e for e in errors)
                except Exception as exc:summary['errors'].append(branch.name+': '+type(exc).__name__)
                finally:
                    if sheet is not None:sheet.page.close()
        finally:browser.close()
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if summary['errors']:raise SystemExit(1)

if __name__=='__main__':main()
