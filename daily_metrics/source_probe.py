"""Manual, read-only source verification. Never logs source financial values."""
import json,shutil
from .config import BRANCHES
from .io import SUReports,csv_sheet
from .run import reporting_day
from .extended_io import ExtraSources
from .extended_core import extra_targets,bi_rows
from .core import target_candidates,number,value,money


def safe_error(exc):
    if type(exc).__name__=='StructureError':return str(exc)
    message=str(exc).casefold()
    for marker in ('econnreset','enotfound','socket hang up','invalid url','too many redirects','err_connection','err_name_not_resolved','err_timed_out','certificate'):
        if marker in message:return type(exc).__name__+': '+marker
    return type(exc).__name__


def main():
    from playwright.sync_api import sync_playwright
    day=reporting_day();client=SUReports();failures=[];verified=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        sources=ExtraSources(browser,client)
        try:
            sources.bi.authenticate()
            for kind in ('uv','osa','reasons'):
                print(json.dumps({'stage':'inspect','source':kind}),flush=True)
                try:
                    form,context=sources.bi.template(kind)
                    print(json.dumps({'source':kind,'slice_id':form.get('slice_id'),'value_format':form.get('valueFormat'),'csrf_html':bool(sources.bi.csrf),
                        'queries':[{k:q.get(k) for k in ('columns','granularity','is_timeseries','post_processing')} for q in context.get('queries',[])]},ensure_ascii=False),flush=True)
                except Exception as exc:
                    print(json.dumps({'source':kind,'template_error':safe_error(exc)}),flush=True)
            try:
                labels=sources.bi.api('/api/v1/dataset/3437/column/reason/values/').get('result',[])
                print(json.dumps({'available_reason_labels':labels},ensure_ascii=False),flush=True)
            except Exception as exc:print(json.dumps({'reason_labels_error':safe_error(exc)}),flush=True)
            for branch in BRANCHES:
                rows=csv_sheet(branch.book,branch.revenue_gid)
                row,header=target_candidates(rows,day,branch.newest_first)[0]
                layout=extra_targets(rows,header,row,day)
                for kind in ('uv','osa','reasons'):
                    print(json.dumps({'stage':'read','branch':branch.name,'source':kind}),flush=True)
                    try:
                        result=sources.read(kind,branch,day)
                        info={'branch':branch.name,'source':kind,'valid':True,'present':result is not None}
                        if kind=='reasons':
                            raw=bi_rows(sources.bi.read(kind,branch,day))
                            info['reason_labels']=[r.get('reason') for r in raw]
                            info['known_reasons']=list(result)
                        else:
                            r,c=layout[kind];stored=value(rows,r,c)
                            if result is not None and stored:
                                expected=number(stored)
                                from decimal import Decimal,ROUND_HALF_UP
                                actual=number(result).quantize(Decimal('1') if kind=='invoices' else Decimal('0.01') if kind=='uv' else Decimal('0.1'),rounding=ROUND_HALF_UP)
                                info['matches_existing']=actual==expected
                        if result is not None:verified+=1
                        else:failures.append(branch.name+': '+kind+' empty')
                        print(json.dumps(info,ensure_ascii=False),flush=True)
                    except Exception as exc:
                        failures.append(branch.name+': '+kind)
                        print(json.dumps({'branch':branch.name,'source':kind,'error':safe_error(exc)}),flush=True)
                        if kind=='uv' and '403' in str(exc):
                            try:
                                from .frontend_probe import read_uv_from_frontend
                                actual=read_uv_from_frontend(sources.bi,branch,day)
                                r,c=layout[kind]
                                print(json.dumps({'frontend_read':True,'present':actual is not None,
                                    'matches_existing':actual is not None and round(actual,2)==number(value(rows,r,c))}),flush=True)
                            except Exception as fallback:
                                print(json.dumps({'frontend_error':safe_error(fallback)}),flush=True)
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
