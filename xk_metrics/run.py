"""Independent store jobs; validated sources are read before any permitted writes."""
import argparse,datetime as dt,json,os,shutil,dataclasses
from decimal import Decimal
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from daily_metrics.core import StructureError,DASH,a1,value,money,number,revenue_report,writeoff_report,date_matches,same_cell_value
from daily_metrics.io import RevenueSheet,csv_sheet,native_cell
from daily_metrics.run import check_date
from daily_metrics.extended_core import percent_literal,key
from .config import STORES
from .layout import candidates,targets
from .sources import Client,Sources,resolve_branch,read_tops
TZ=ZoneInfo('Asia/Yekaterinburg')


def procurement_value(rows,store,day):
    total=18 if store.branch.name=='Х76' else 20
    if key(value(rows,2,total))!=key(store.branch.name+' итог'):raise StructureError('Store procurement total changed')
    found=[r for r in range(3,len(rows)) if date_matches(value(rows,r,1),day)]
    if len(found)!=1:return None
    r=found[0]
    entries=[value(rows,r,c) for c in range(2,total)]
    if not any(v and v not in ('—','-','–') for v in entries):return None
    return money(value(rows,r,total))


def locate(sheet,rows,store,day):
    found=[(r,h) for r,h in candidates(rows,store,day) if sheet.is_reporting_date(a1(r,store.offset+1),day)]
    if len(found)!=1:raise StructureError('Store full-year reporting date missing or duplicated')
    return found[0]


def put_value(sheet,cell,literal,*,reason=False):
    if reason:
        current=sheet.stable_formula(cell)
        if current:
            if current.startswith('=') or same_cell_value(current,literal):return False
            try:number(current)
            except StructureError:return False
            if not sheet.replace_if_unchanged(cell,current,literal):
                raise StructureError('Reason changed concurrently; not overwritten')
            return True
    return sheet.put_blank(cell,literal)


def plan(sheet,store,branch,client,sources,day):
    rows=sheet.refresh();row,header=locate(sheet,rows,store,day)
    fields,reasons,rh=targets(rows,store,row,header,day)
    anchor=a1(row,store.offset+1)
    marker=a1(header-1,store.offset+1)
    sheet.anchors={anchor:sheet.anchor_value(anchor),marker:sheet.anchor_value(marker)}
    metrics={
        'revenue':revenue_report(client.revenue(branch,day),branch.title,day),
        'writeoff':writeoff_report(client.writeoff(branch,day),branch.title,day),
        'uv':sources.read('uv',branch,day),
        'invoices':sources.read('invoices',branch,day),
    }
    osa=sources.osa_pair(branch,day);metrics.update(zip(('osa11','osa20'),osa))
    metrics.update(zip(('food','dessert','japan'),read_tops(store,day)))
    procurement_rows=csv_sheet(branch.book,branch.procurement_gid)
    metrics['procurement']=procurement_value(procurement_rows,store,day)
    if any(v is None or v==DASH for v in metrics.values()):raise StructureError('Required daily source unavailable')
    result=[]
    for name,amount in metrics.items():
        literal=percent_literal(amount,branch.name,2 if name=='uv' else 1) if name in ('uv','osa11','osa20') else money(amount)
        result.append((fields[name],literal))
    result.append((fields['lower_revenue'],money(metrics['revenue'])))
    # Unknown or absent source reasons are deliberately not written.
    reported=sources.reasons(branch,day)
    for name,cell in reasons.items():
        amount=reported.get(name)
        if amount is None:continue
        literal=percent_literal(amount,'М42',1)  # Native reason cells use percent format.
        if number(literal)==0:continue
        result.append((cell,literal))
    for offset in (1,2):
        future=day+dt.timedelta(days=offset)
        found=candidates(rows,store,future)
        found=[(r,h) for r,h in found if sheet.is_reporting_date(a1(r,store.offset+1),future)]
        if len(found)>1:raise StructureError('Future procurement date duplicated')
        if not found:continue
        amount=procurement_value(procurement_rows,store,future)
        if amount is None:continue
        r,h=found[0];cell=(r,store.offset+14)
        if key(value(rows,h,store.offset+14))!='закупфакт':raise StructureError('Future procurement header changed')
        date=a1(r,store.offset+1);sheet.anchors[date]=sheet.anchor_value(date)
        result.append((cell,amount))
    if any(not store.offset<=c<store.offset+25 for (r,c),literal in result):raise StructureError('Cross-store write forbidden')
    sheet.allowed={a1(r,c) for (r,c),literal in result}
    sheet.reason_cells={a1(r,c) for r,c in reasons.values()}
    return result,len(reported.keys()-reasons.keys())


def execute(store,operation):
    now=dt.datetime.now(TZ);day=now.date()-dt.timedelta(days=1)
    if os.getenv('GITHUB_ACTIONS')!='true':raise StructureError('Use GitHub Actions')
    if os.getenv('EXPECTED_RUN_DATE')!=now.date().isoformat():raise StructureError('Stale queued store job')
    client=Client()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        sources=Sources(browser,client);sheet=None
        try:
            sources.bi.authenticate()
            branch=resolve_branch(client,store,day)
            sheet=RevenueSheet(browser,branch,day)
            changes,skipped=plan(sheet,store,branch,client,sources,day)
            print(json.dumps({'branch':branch.name,'date':str(day),'sources_validated':True,'planned_cells':len(changes),'unmatched_reasons_skipped':skipped}),flush=True)
            written=preserved=0
            for (r,c),literal in changes:
                check_date(day)
                cell=a1(r,c)
                if operation=='apply':
                    if put_value(sheet,cell,literal,reason=cell in sheet.reason_cells):written+=1
                    else:preserved+=1
            if operation=='verify':
                rows=sheet.refresh();daily_row,header=locate(sheet,rows,store,day)
                for (r,c),literal in changes:
                    if r not in (daily_row,header+12+day.weekday()):continue
                    _,saved=native_cell(branch.book,branch.revenue_gid,a1(r,c))
                    if saved is None or not same_cell_value(str(saved),literal):
                        raise StructureError('Stored daily metric differs from source ('+a1(r,c)+')')
                print(json.dumps({'branch':branch.name,'yesterday_saved_values_match_sources':True}),flush=True)
            print(json.dumps({'branch':branch.name,'date':str(day),'operation':operation,'written':written,'preserved':preserved,'errors':[]}),flush=True)
        finally:
            if sheet is not None:sheet.page.close()
            sources.close();browser.close()


def main(store_name):
    parser=argparse.ArgumentParser();parser.add_argument('--operation',choices=['preview','apply','verify'],default='preview');args=parser.parse_args()
    # One bounded fresh session retry for transport errors only; existing cells stay intact.
    import urllib.error
    for attempt in range(2):
        try:execute(STORES[store_name],args.operation);return
        except (TimeoutError,ConnectionError,urllib.error.URLError) as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code not in (429,500,502,503,504):raise
            if attempt:raise
            print('Transient source failure; retrying this store in a fresh session',flush=True)
