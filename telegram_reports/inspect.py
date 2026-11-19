"""Read only chart identities and renderer schemas; never output financial rows."""
import json,shutil
from playwright.sync_api import sync_playwright
from daily_metrics.io import SUReports
from daily_metrics.extended_io import BIReports
from daily_metrics.source_probe import safe_error


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        bi=BIReports(browser,SUReports())
        try:
            for dashboard in (241,37,56):
                charts=bi.api(f'/api/v1/dashboard/{dashboard}/charts').get('result',[])
                for chart in charts:
                    params=chart.get('form_data') or chart.get('params') or {}
                    if isinstance(params,str):params=json.loads(params)
                    context=chart.get('query_context')
                    if isinstance(context,str):context=json.loads(context)
                    print(json.dumps({'dashboard':dashboard,'id':chart.get('id'),'title':chart.get('slice_name'),
                        'viz_type':params.get('viz_type'),'query_saved':bool(context)},ensure_ascii=False),flush=True)
        finally:bi.close();browser.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'inspection_error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
