"""Run with python -m daily_metrics.run, only from GitHub Actions."""
import argparse
import datetime as dt
from decimal import Decimal
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import BRANCHES
from .core import (DASH,StructureError,a1,date_matches,lower_targets,money,number,
                   procurement,revenue_report,target_candidates,tops,value,writeoff_report)
from .io import RevenueSheet,SUReports,csv_sheet,tops_sheet

TZ=ZoneInfo('Asia/Yekaterinburg')


def reporting_day(now=None):
    return (now or dt.datetime.now(TZ)).astimezone(TZ).date()-dt.timedelta(days=1)


def check_date(expected):
    if reporting_day()!=expected:
        raise StructureError('Reporting day changed; historical writes forbidden')


def percentage_literal(displayed,target_display):
    # Copy the visible source precision, not the unrounded division result.
    n=number(displayed)
    # Sheets interprets an explicit percent correctly in percent-formatted cells.
    return str(n).replace('.',',')+'%' if '%' in target_display else str(n).replace('.',',')


def fill_branch(browser,client,branch,day,apply,summary):
    print(branch.name+': checking revenue date and blank cells',flush=True)
    sheet=RevenueSheet(browser,branch,day)
    try:
        rows=sheet.refresh()
        found=[]
        for row,header in target_candidates(rows,day,branch.newest_first):
            # Native date's underlying full year prevents matching a historical same dd.mm.
            if date_matches(sheet.formula(a1(row,0)),day):
                found.append((row,header))
        if len(found)!=1:raise StructureError('Revenue date missing or duplicated')
        row,header=found[0]
        lower_revenue,lower_writeoff=lower_targets(rows,header,day)
        cells=[(row,c) for c in (2,3,6,7,8,12)]+[lower_revenue,lower_writeoff]
        sheet.allowed={a1(r,c) for r,c in cells}
        # Lock the full-year date, weekly heading and lower date before each write.
        anchors=[a1(row,0),a1(lower_revenue[0]-1-day.weekday(),lower_revenue[1]),a1(lower_writeoff[0],lower_writeoff[1]-1)]
        sheet.anchors={cell:sheet.formula(cell) for cell in anchors}
        metrics={}
        errors=[]
        sources={
            'revenue':lambda:revenue_report(client.revenue(branch,day),branch.title,day),
            'writeoff':lambda:writeoff_report(client.writeoff(branch,day),branch.title,day),
            'tops':lambda:tops(tops_sheet(branch.tops_book,day),day),
            'procurement':lambda:procurement(csv_sheet(branch.book,branch.procurement_gid),day),
        }
        targets={'revenue':[(row,2),lower_revenue],'writeoff':[(row,3)],
                 'tops':[(row,6),(row,7),(row,8)],'procurement':[(row,12)]}
        for metric,load in sources.items():
            # No need to query a source when all of its destinations are already filled.
            if apply and all(sheet.formula(a1(r,c)) for r,c in targets[metric]):
                summary['preserved']+=len(targets[metric]);continue
            print(branch.name+': reading '+metric,flush=True)
            try:metrics[metric]=load()
            except Exception:
                # A transport/auth/schema failure is a technical error, unlike an empty valid report.
                errors.append(metric+' source failed')
                metrics[metric]=[DASH]*3 if metric=='tops' else DASH
            result=metrics[metric]
            for i,(r,c) in enumerate(targets[metric]):
                literal=result[i] if metric=='tops' else result
                check_date(day)
                if apply:
                    if sheet.put_blank(a1(r,c),literal):
                        summary['written']+=1;summary['missing']+=literal==DASH
                    else:summary['preserved']+=1
                else:
                    if not sheet.formula(a1(r,c)):
                        summary['planned']+=1;summary['missing']+=literal==DASH
        # Formula E is never edited. Wait for recalculation, copy its DISPLAYED rounded value.
        r,c=lower_writeoff
        if not sheet.formula(a1(r,c)):
            current=sheet.refresh()
            displayed=value(current,row,4)
            try:
                if not displayed or displayed.startswith('#'):literal=DASH
                else:
                    number(displayed)
                    # Existing lower percentage style inferred from another day in the same block.
                    literal=percentage_literal(displayed,'%' if branch.name=='М42' else '')
            except StructureError:literal=DASH
            if apply:
                check_date(day)
                if sheet.put_blank(a1(r,c),literal):
                    summary['written']+=1;summary['missing']+=literal==DASH
            else:summary['planned']+=1
        else:summary['preserved']+=1
        return errors
    finally:sheet.page.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=('preview','apply'),default='preview')
    parser.add_argument('--branch',choices=['all']+[b.name for b in BRANCHES],default='all')
    args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':
        raise SystemExit('Local metrics runs are disabled; use GitHub Actions')
    day=reporting_day()
    # Workflow carries its creation date; a queued run may not act on another day's data.
    if os.getenv('EXPECTED_RUN_DATE')!=dt.datetime.now(TZ).date().isoformat():
        raise SystemExit('Stale workflow; no historical metrics writes allowed')
    summary={'date':day.isoformat(),'operation':args.operation,'written':0,'planned':0,'preserved':0,'missing':0,'errors':[]}
    client=SUReports()
    try:client.login()
    except Exception:
        summary['errors'].append('SU login failed; revenue/writeoff unavailable')
        client=None  # Continue independent Sheets sources even when SU is unavailable.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            for branch in BRANCHES:
                if args.branch not in ('all',branch.name):continue
                try:
                    errors=fill_branch(browser,client,branch,day,args.operation=='apply',summary)
                    summary['errors'].extend(branch.name+': '+e for e in errors)
                except Exception as exc:
                    message=str(exc) if isinstance(exc,StructureError) else 'technical failure'
                    summary['errors'].append(branch.name+': '+message)
        finally:browser.close()
    # Counts only; no source documents, money, credentials, or remote error bodies in logs.
    print(json.dumps(summary,ensure_ascii=False))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('Daily metrics\n\n'+json.dumps(summary,ensure_ascii=False,indent=2))
    if summary['errors']:raise SystemExit(1)

if __name__=='__main__':main()
