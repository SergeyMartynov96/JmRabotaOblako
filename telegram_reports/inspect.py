"""Read only chart identities and renderer schemas; never output financial rows."""
import json,shutil
from playwright.sync_api import sync_playwright
from daily_metrics.io import SUReports
from daily_metrics.extended_io import BIReports
from daily_metrics.source_probe import safe_error


class DiagnosedBI(BIReports):
    def _browser_login(self,page):
        documents=[];failures={}
        page.on('response',lambda response:documents.append({'status':response.status,'path':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(response.url).path}) if response.request.resource_type=='document' else None)
        def failed(request):
            kind=request.resource_type
            failures[kind]=failures.get(kind,0)+1
        page.on('requestfailed',failed)
        try:super()._browser_login(page)
        except Exception:
            try:
                state=page.evaluate("() => ({ready:document.readyState,inputs:[...document.querySelectorAll('input')].map(e=>({name:e.name,type:e.type,visible:!!e.getClientRects().length})),forms:document.forms.length})")
                print(json.dumps({'login_diagnostic':state,'documents':documents,'failed_resources':failures}),flush=True)
            except Exception:print('Login diagnostic DOM unavailable',flush=True)
            raise


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        bi=DiagnosedBI(browser,SUReports())
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
