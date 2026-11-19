"""Capture actual BI renderers using one already-validated, date-filtered API result."""
import base64,html,json,urllib.parse,time
from pathlib import Path
from daily_metrics.extended_io import BI
from daily_metrics.core import StructureError


def capture_chart(bi,form,payload,destination,width=900,height=430):
    deadline=time.monotonic()+180
    def remaining():return max(1,int((deadline-time.monotonic())*1000))
    page=bi.context.new_page();served=[]
    page.set_viewport_size({'width':width,'height':height})
    def serve(route):
        served.append(True)
        if '/superset/explore_json/' in route.request.url:
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload.get('legacy_payload') or {**payload['result'][0],'form_data':form}))
        else:route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
    page.route('**/api/v1/chart/data**',serve)
    page.route('**/superset/explore_json/**',serve)
    try:
        params={'form_data':json.dumps(form,ensure_ascii=False),'standalone':'1','height':height}
        response=page.goto(BI+'/superset/explore/?'+urllib.parse.urlencode(params),wait_until='commit',timeout=remaining())
        if response is None or response.status!=200:raise StructureError('BI renderer document failed')
        page.wait_for_function("""() => !!document.querySelector('svg,canvas,table') && !document.querySelector('[data-test="loading-indicator"]')""",timeout=remaining())
        # Wait for the validated result to reach the actual renderer, not just an empty scaffold.
        while not served and time.monotonic()<deadline:page.wait_for_timeout(100)
        if not served:raise StructureError('BI renderer did not request the validated dataset')
        page.wait_for_function("""() => {
          const root=document.querySelector('.chart-container') || document.querySelector('[data-test="chart-container"]') || document.body;
          return !!root.querySelector('svg path,svg rect,canvas,table td') && !root.querySelector('[data-test="loading-indicator"]');
        }""",timeout=remaining())
        page.evaluate("() => document.fonts.ready")
        # Disable transitions; a chart can otherwise be captured mid-animation.
        page.add_style_tag(content='*,*::before,*::after{animation:none!important;transition:none!important}')
        page.screenshot(path=str(destination))
        return {'validated_result_rendered':True}
    except Exception:
        try:page.screenshot(path=str(Path(destination).with_suffix(".failed.png")),timeout=5000)
        except Exception:pass
        raise
    finally:page.close()


def compose(browser,panels,destination,columns=1,heading=None):
    """Lay out unmodified source screenshots; no financial values are reconstructed."""
    context=browser.new_context();page=context.new_page()
    width=1800
    page.set_viewport_size({'width':width,'height':1400})
    cards=[]
    for title,path in panels:
        encoded=base64.b64encode(Path(path).read_bytes()).decode()
        cards.append('<section><h3>'+html.escape(title)+'</h3><img src="data:image/png;base64,'+encoded+'"></section>')
    try:
        page.set_content('<html><meta charset="utf-8"><style>body{margin:0;background:white;font:18px Arial;color:#172d39}main{padding:16px}h2{margin:0 0 14px}h3{font-size:18px;margin:0 0 8px}.grid{display:grid;grid-template-columns:repeat('+str(columns)+',minmax(0,1fr));gap:18px}img{width:100%;display:block}section{min-width:0}</style><main>'+('<h2>'+html.escape(heading)+'</h2>' if heading else '')+'<div class="grid">'+''.join(cards)+'</div></main></html>')
        page.locator('img').first.wait_for()
        page.wait_for_function('() => [...document.images].every(i=>i.complete && i.naturalWidth>0)')
        page.locator('main').screenshot(path=str(destination))
    finally:context.close()


def query_from_renderer(bi,form):
    """Ask Superset's own chart plugin to build a missing saved query; block execution."""
    page=bi.context.new_page();captured=[]
    deadline=time.monotonic()+180
    def intercept(route):
        if route.request.method=='POST':
            try:captured.append(route.request.post_data_json)
            except Exception:pass
        route.fulfill(status=200,content_type='application/json',body='{"result":[]}')
    page.route('**/api/v1/chart/data**',intercept)
    page.route('**/superset/explore_json/**',lambda route:route.abort())
    try:
        page.goto(BI+'/superset/explore/?'+urllib.parse.urlencode({'form_data':json.dumps(form,ensure_ascii=False),'standalone':1}),wait_until='commit',timeout=180000)
        while not captured and time.monotonic()<deadline:page.wait_for_timeout(100)
        if len(captured)!=1 or not captured[0].get('queries'):raise StructureError('Chart plugin did not build one report query')
        return captured[0]
    finally:page.close()
