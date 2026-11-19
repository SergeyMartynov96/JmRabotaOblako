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
                    title=chart.get('slice_name','')
                    selected=(title in ('Списания. Категории. ТОП-блюда, руб','Списания. Категории. ТОП-блюда, шт','Списания. Причины. Период, %','Списания % от выбранного блюда, категории','Списания % от выбранного блюда, УИ2')
                        or ('Таблица_Топы' in title) or ('3.1.' in title and 'динамика' not in title)
                        or title in ('OSA % по in_stock','OSA % по in_stock Готовая еда + Япония','OSA % по in_stock Готовая еда','OSA % по in_stock Япония'))
                    if not selected:continue
                    ident=chart.get('id') or chart.get('slice_id') or params.get('slice_id')
                    print(json.dumps({'dashboard':dashboard,'id':ident,'title':title,'chart_keys':list(chart),
                        'form':params},ensure_ascii=False),flush=True)
                    if ident:
                        detail=bi.api(f'/api/v1/chart/{ident}').get('result',{})
                        context=detail.get('query_context')
                        if isinstance(context,str):context=json.loads(context)
                        print(json.dumps({'id':ident,'title':title,'query_context':context},ensure_ascii=False),flush=True)
        finally:bi.close();browser.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'inspection_error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
