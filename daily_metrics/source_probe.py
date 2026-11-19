"""Manual, read-only source verification. Never logs source financial values."""
import json,shutil
from .config import BRANCHES
from .io import SUReports,csv_sheet
from .run import reporting_day
from .extended_io import ExtraSources
from .extended_core import extra_targets,bi_rows
from .core import target_candidates,number,value,money


def main():
    from playwright.sync_api import sync_playwright
    day=reporting_day();client=SUReports()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        sources=ExtraSources(browser,client)
        try:
            for kind in ('uv','osa','reasons'):
                print(json.dumps({'stage':'inspect','source':kind}),flush=True)
                try:
                    form,context=sources.bi.template(kind)
                    print(json.dumps({'source':kind,'slice_id':form.get('slice_id'),'value_format':form.get('valueFormat'),
                        'queries':[{k:q.get(k) for k in ('columns','granularity','is_timeseries','post_processing')} for q in context.get('queries',[])]},ensure_ascii=False),flush=True)
                except Exception as exc:
                    print(json.dumps({'source':kind,'template_error':type(exc).__name__}),flush=True)
            for branch in BRANCHES:
                rows=csv_sheet(branch.book,branch.revenue_gid)
                row,header=target_candidates(rows,day,branch.newest_first)[0]
                layout=extra_targets(rows,header,row,day)
                for kind in ('uv','osa','invoices','reasons'):
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
                        print(json.dumps(info,ensure_ascii=False),flush=True)
                    except Exception as exc:
                        print(json.dumps({'branch':branch.name,'source':kind,'error':type(exc).__name__}),flush=True)
                        if kind!='invoices':
                            try:
                                raw=bi_rows(sources.bi.read(kind,branch,day))
                                print(json.dumps({'source':kind,'row_count':len(raw),'columns':list(raw[0]) if raw else [],
                                                  'reason_labels':[r.get('reason') for r in raw] if kind=='reasons' else []},ensure_ascii=False),flush=True)
                            except Exception:pass
        finally:sources.close();browser.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'probe_error':type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
