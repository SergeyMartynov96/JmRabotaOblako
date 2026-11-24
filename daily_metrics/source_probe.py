"""Manual, read-only source verification. Never logs source financial values."""
import json,shutil,argparse,urllib.parse,os,tempfile
from pathlib import Path
from .config import BRANCHES
from .io import csv_sheet
from .session import ReportSUReports as SUReports
from .run import reporting_day
from .extended_io import ExtraSources
from .extended_core import extra_targets,bi_rows
from .core import target_candidates,number,value,money


def safe_error(exc):
    if type(exc).__name__ in ('StructureError','BotError'):return str(exc)
    message=str(exc).casefold()
    for marker in ('etimedout','econnreset','enotfound','socket hang up','invalid url','too many redirects','err_connection','err_name_not_resolved','err_timed_out','certificate'):
        if marker in message:return type(exc).__name__+': '+marker
    return type(exc).__name__


class InvoiceBrowserClient:
    def __init__(self,context):self.context=context
    def ensure_authenticated(self):pass
    def request(self,path,data=None):
        from .io import SU
        from .core import StructureError
        parsed=urllib.parse.urlsplit(path)
        if data is not None or parsed.scheme or parsed.netloc or parsed.path!='/turnover/waybill/report':
            raise ValueError('Only invoice report GET allowed')
        from telegram_reports.invoices import invoice_source
        html=invoice_source(self.context,SU+path)
        if 'LoginForm[password]' in html:raise StructureError('SU invoice session expired')
        return html


def verify_invoices(browser,client,branches,day):
    from decimal import Decimal,ROUND_DOWN
    context=browser.new_context();page=context.new_page()
    sources=ExtraSources(browser,client);failures=[];verified=0
    try:
        try:
            client.ensure_authenticated()
        except __import__('urllib.error',fromlist=['HTTPError']).HTTPError:raise
        except (TimeoutError,__import__('urllib.error',fromlist=['URLError']).URLError):
            sources.bi._browser_login(page)
            sources.client=InvoiceBrowserClient(context)
        page.close()
        for branch in branches:
            print(json.dumps({'stage':'read','branch':branch.name,'source':'invoices'}),flush=True)
            try:
                result=sources.read('invoices',branch,day)
                if result is None:raise ValueError('No invoice data')
                rows=csv_sheet(branch.book,branch.revenue_gid)
                row,header=target_candidates(rows,day,branch.newest_first)[0]
                layout=extra_targets(rows,header,row,day);r,c=layout['invoices']
                stored=value(rows,r,c)
                info={'branch':branch.name,'source':'invoices','valid':True,'receiving_department_filter':True}
                if stored:
                    expected=number(stored)
                    info['matches_rounded_existing']=number(money(result))==expected
                    info['matches_existing_without_kopecks']=result.quantize(Decimal('1'),rounding=ROUND_DOWN)==expected
                    info['has_kopecks']=result!=result.quantize(Decimal('1'),rounding=ROUND_DOWN)
                    if not (info['matches_rounded_existing'] or info['matches_existing_without_kopecks']):
                        failures.append(branch.name+': invoices destination mismatch')
                else:failures.append(branch.name+': invoices destination missing')
                verified+=1
                print(json.dumps(info,ensure_ascii=False),flush=True)
            except Exception as exc:
                failures.append(branch.name+': invoices')
                print(json.dumps({'branch':branch.name,'error':safe_error(exc)}),flush=True)
    finally:page.close();context.close();sources.close()
    print(json.dumps({'verified_reads':verified,'failures':failures},ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)



def verify_base(client,branches,day):
    from .core import revenue_report,writeoff_report,tops,lower_targets,same_cell_value,DASH
    from .io import tops_sheet
    failures=[];verified=0;details=[]
    for branch in branches:
        rows=csv_sheet(branch.book,branch.revenue_gid)
        row,header=target_candidates(rows,day,branch.newest_first)[0]
        lower,_=lower_targets(rows,header,day)
        sources={
            'revenue':lambda:([revenue_report(client.revenue(branch,day),branch.title,day)]*2,[(row,2),lower]),
            'writeoff':lambda:([writeoff_report(client.writeoff(branch,day),branch.title,day)],[(row,3)]),
            'tops':lambda:(tops(tops_sheet(branch.tops_book,day),day),[(row,6),(row,7),(row,8)]),
        }
        for kind,read in sources.items():
            print(json.dumps({'stage':'read','branch':branch.name,'source':kind}),flush=True)
            try:
                expected,targets=read()
                details.append({'branch':branch.name,'source':kind,'date':str(day),'expected':expected,'stored':[value(rows,r,c) for r,c in targets],'cells':targets})
                matches=[same_cell_value(value(rows,r,c),literal) for (r,c),literal in zip(targets,expected)]
                valid=len(matches)==len(targets) and all(matches) and DASH not in expected
                if not valid:failures.append(branch.name+': '+kind+' mismatch or missing')
                else:verified+=len(matches)
                print(json.dumps({'branch':branch.name,'source':kind,'matches_existing':valid,'checked_cells':len(matches)},ensure_ascii=False),flush=True)
            except Exception as exc:
                failures.append(branch.name+': '+kind)
                print(json.dumps({'branch':branch.name,'source':kind,'error':safe_error(exc)},ensure_ascii=False),flush=True)
    if os.getenv('REVIEW_PUBLIC_KEY'):
        from telegram_reports.security import seal_images
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'audit.json').write_text(json.dumps(details,ensure_ascii=False))
            seal_images(directory,os.environ['REVIEW_PUBLIC_KEY'],'review.enc.json')
    print(json.dumps({'verified_cells':verified,'failures':failures},ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)


def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',choices=('bi','invoices','cells','reasons','base'),default='bi')
    parser.add_argument('--branch',choices=['all']+[b.name for b in BRANCHES],default='all')
    args=parser.parse_args();branches=[b for b in BRANCHES if args.branch in ('all',b.name)]
    day=reporting_day();client=SUReports();failures=[];verified=0
    if args.source=='base':
        verify_base(client,branches,day);return
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        if args.source=='cells':
            from .io import RevenueSheet,native_cell
            from .core import a1
            try:
                for branch in branches:
                    sheet=RevenueSheet(browser,branch,day)
                    try:
                        rows=sheet.refresh();row,header=target_candidates(rows,day,branch.newest_first)[0]
                        layout=extra_targets(rows,header,row,day)
                        cells=[(row,2),(row,3)]+[loc[0] for loc in layout['reasons'].values()]
                        for r,c in cells:
                            cell=a1(r,c);formula=sheet.stable_formula(cell)
                            _,raw=native_cell(branch.book,branch.revenue_gid,cell)
                            print(json.dumps({'branch':branch.name,'cell':cell,'formula_present':bool(formula),
                                'native_present':raw is not None and raw!='','formula_length':len(formula),
                                'formula_is_date':bool(__import__('re').fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}',formula))}),flush=True)
                    finally:sheet.page.close()
            finally:browser.close()
            return
        if args.source=='invoices':
            try:verify_invoices(browser,client,branches,day)
            finally:browser.close()
            return
        sources=ExtraSources(browser,client)
        try:
            sources.bi.authenticate()
            kinds=('reasons',) if args.source=='reasons' else ('uv','osa','reasons')
            for kind in kinds:
                print(json.dumps({'stage':'inspect','source':kind}),flush=True)
                try:
                    form,context=sources.bi.template(kind)
                    print(json.dumps({'source':kind,'slice_id':form.get('slice_id'),'value_format':form.get('valueFormat'),'csrf_html':bool(sources.bi.csrf),
                        'queries':[{k:q.get(k) for k in ('columns','granularity','is_timeseries','post_processing')} for q in context.get('queries',[])]},ensure_ascii=False),flush=True)
                except Exception as exc:
                    print(json.dumps({'source':kind,'template_error':safe_error(exc)}),flush=True)
            for branch in branches:
                rows=csv_sheet(branch.book,branch.revenue_gid)
                row,header=target_candidates(rows,day,branch.newest_first)[0]
                layout=extra_targets(rows,header,row,day)
                for kind in kinds:
                    print(json.dumps({'stage':'read','branch':branch.name,'source':kind}),flush=True)
                    try:
                        result=sources.read(kind,branch,day)
                        info={'branch':branch.name,'source':kind,'valid':True,'present':result is not None}
                        if kind=='reasons':
                            raw=bi_rows(sources.bi.read(kind,branch,day))
                            info['reason_labels']=[r.get('reason') for r in raw]
                            info['known_reasons']=list(result)
                            info['unmapped_reason_labels']=getattr(result,'unmapped',[])
                            if info['unmapped_reason_labels']:failures.append(branch.name+': unmapped reasons')
                            from .extended_core import percent_literal,reason_literal
                            from .core import same_cell_value
                            missing=[];mismatched=[];matched=[]
                            info['zero_reasons_skipped']=[name for name,amount in result.items() if reason_literal(amount,branch.name) is None]
                            for name,amount in result.items():
                                if reason_literal(amount,branch.name) is None:continue
                                for r,c in layout['reasons'].get(name,[]):
                                    stored=value(rows,r,c)
                                    if not stored:missing.append(name)
                                    elif same_cell_value(stored,percent_literal(amount,branch.name,1)):matched.append(name)
                                    else:mismatched.append(name)
                            info['matched_reason_columns']=matched
                            info['missing_reported_columns']=missing
                            info['mismatched_reason_columns']=mismatched
                            if missing or mismatched:failures.append(branch.name+': reasons destination mismatch')
                        else:
                            r,c=layout[kind];stored=value(rows,r,c)
                            if result is not None and stored:
                                expected=number(stored)
                                from decimal import Decimal,ROUND_HALF_UP
                                import re
                                decimals=re.search(r'[.,](\d+)\s*%?$',stored)
                                places=len(decimals[1]) if decimals else 0
                                copy_places=0 if kind=='invoices' else 2 if kind=='uv' else 1
                                actual=number(result).quantize(Decimal(10)**-copy_places,rounding=ROUND_HALF_UP)
                                actual=actual.quantize(Decimal(10)**-places,rounding=ROUND_HALF_UP)
                                info['matches_existing']=actual==expected
                                if not info['matches_existing']:failures.append(branch.name+': '+kind+' destination mismatch')
                            elif result is not None:failures.append(branch.name+': '+kind+' destination missing')
                        if result is not None:verified+=1
                        else:failures.append(branch.name+': '+kind+' empty')
                        print(json.dumps(info,ensure_ascii=False),flush=True)
                    except Exception as exc:
                        failures.append(branch.name+': '+kind)
                        print(json.dumps({'branch':branch.name,'source':kind,'error':safe_error(exc)}),flush=True)
                        if kind!='invoices':
                            try:
                                raw=bi_rows(sources.bi.read(kind,branch,day))
                                print(json.dumps({'source':kind,'row_count':len(raw),'columns':list(raw[0]) if raw else [],
                                                  'reason_labels':[r.get('reason') for r in raw] if kind=='reasons' else []},ensure_ascii=False),flush=True)
                            except Exception:pass
        finally:sources.close();browser.close()
    print(json.dumps({'verified_reads':verified,'failures':failures},ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'probe_error':type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
