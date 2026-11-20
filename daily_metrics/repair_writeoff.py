"""One-time repair of two placeholders written by failed run 44666617897."""
import datetime as dt,json,os,shutil
from .config import BRANCHES
from .core import DASH,StructureError,a1,money,number,target_candidates,writeoff_report
from .session import ReportSUReports as SUReports
from .io import RevenueSheet,csv_sheet,native_cell
from .run import reporting_day,check_date


def main():
    day=dt.date(2026,11,19)
    if os.getenv('GITHUB_ACTIONS')!='true' or reporting_day()!=day:
        raise StructureError('One-time repair window expired')
    from playwright.sync_api import sync_playwright
    client=SUReports();result=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        try:
            for branch in BRANCHES:
                if branch.name not in ('АВ12','Г34'):continue
                sheet=RevenueSheet(browser,branch,day)
                try:
                    rows=sheet.refresh();row,header=target_candidates(rows,day,branch.newest_first)[0]
                    date_cell=a1(row,0);cell=a1(row,3)
                    if not sheet.is_reporting_date(date_cell,day):raise StructureError('Repair date mismatch')
                    sheet.allowed={cell};sheet.anchors={date_cell:sheet.anchor_value(date_cell)}
                    _,raw=native_cell(branch.book,branch.revenue_gid,cell)
                    if raw!=DASH:
                        result.append({'branch':branch.name,'preserved':True});continue
                    amount=writeoff_report(client.writeoff(branch,day),branch.title,day)
                    if amount==DASH:raise StructureError('Repair source has no confirmed amount')
                    number(amount);check_date(day)
                    if not sheet.replace_if_unchanged(cell,DASH,money(amount)):
                        raise StructureError('Repair placeholder changed')
                    _,stored=native_cell(branch.book,branch.revenue_gid,cell)
                    if number(stored)!=number(money(amount)):raise StructureError('Repair persistence not confirmed')
                    result.append({'branch':branch.name,'repaired':True})
                finally:sheet.page.close()
        finally:browser.close()
    print(json.dumps({'repair_run':44666617897,'results':result},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
