"""Capture actual BI renderers using one already-validated, date-filtered API result."""
import base64,html,json,urllib.parse,time,re
from pathlib import Path
from daily_metrics.extended_io import BI
from daily_metrics.core import StructureError
from .security import arm_failed_cleanup,bounded_bi_operation
from .http import report_get


@bounded_bi_operation
def active_page(bi):
    page=getattr(bi,'report_page',None)
    if page is None or page.is_closed():
        page=bi.context.new_page();bi.report_page=page
    page.bring_to_front()
    return page


def load_explore(bi,page,form,height,deadline):
    """Buffer the authorized HTML response before Chromium starts parsing it."""
    def remaining():return max(1,int((deadline-time.monotonic())*1000))
    url=BI+'/superset/explore/?'+urllib.parse.urlencode({'form_data':json.dumps(form,ensure_ascii=False),'standalone':'1','height':height})
    response=report_get(bi.context,url,timeout=remaining())
    location=urllib.parse.urlsplit(response.url)
    if response.status!=200 or location.hostname!='bi.lifemart.ru' or location.path!='/superset/explore/':
        raise StructureError('BI renderer document authentication failed')
    document=response.text()
    if '<html' not in document.casefold():raise StructureError('BI renderer HTML missing')
    headers={k:v for k,v in response.headers.items() if k.casefold() not in ('content-length','content-encoding','transfer-encoding','connection')}
    def document_response(route):route.fulfill(status=200,headers=headers,body=document)
    pattern='**/superset/explore/**'
    page.route(pattern,document_response)
    try:
        return page.goto(url,wait_until='commit',timeout=remaining())
    except Exception:
        arm_failed_cleanup(bi)
        raise
    finally:page.unroute(pattern,document_response)


@bounded_bi_operation
def capture_chart(bi,form,payload,destination,width=900,height=430):
    deadline=time.monotonic()+180
    def remaining():return max(1,int((deadline-time.monotonic())*1000))
    page=active_page(bi);served=[]
    page.set_viewport_size({'width':width,'height':height})
    def serve(route):
        served.append(True)
        if '/superset/explore_json/' in route.request.url:
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload.get('legacy_payload') or {**payload['result'][0],'form_data':form}))
        else:route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
    page.route('**/api/v1/chart/data**',serve)
    page.route('**/superset/explore_json/**',serve)
    try:
        phase='document'
        print(json.dumps({'renderer_phase':phase,'slice_id':form.get('slice_id')}),flush=True)
        response=load_explore(bi,page,form,height,deadline)
        if response is None or response.status!=200:raise StructureError('BI renderer document failed')
        phase='visible_content'
        page.wait_for_function("() => document.visibilityState==='visible'",timeout=remaining())
        page.wait_for_function("""() => !!document.querySelector('svg,canvas,table') && !document.querySelector('[data-test="loading-indicator"]')""",timeout=remaining())
        # Wait for the validated result to reach the actual renderer, not just an empty scaffold.
        while not served and time.monotonic()<deadline:page.wait_for_timeout(100)
        if not served:raise StructureError('BI renderer did not request the validated dataset')
        page.wait_for_function("""() => {
          const root=document.querySelector('.chart-container') || document.querySelector('[data-test="chart-container"]') || document.body;
          return !!root.querySelector('svg path,svg rect,canvas,table td') && !root.querySelector('[data-test="loading-indicator"]');
        }""",timeout=remaining())
        page.wait_for_function("() => document.fonts.status==='loaded'",timeout=remaining())
        # D3/ECharts animate SVG attributes in JavaScript; CSS alone does not stop
        # those transitions. Require the actual rendered image to settle.
        phase='settled_image'
        previous=None;stable=0
        while time.monotonic()<deadline:
            current=page.screenshot(timeout=remaining())
            stable=stable+1 if current==previous else 0
            if stable>=2:break
            previous=current
            page.wait_for_timeout(500)
        else:raise StructureError('BI rendered image did not settle within three minutes')
        # Disable transitions; a chart can otherwise be captured mid-animation.
        page.add_style_tag(content='*,*::before,*::after{animation:none!important;transition:none!important}')
        if str(form.get('slice_id'))=='4412':page.locator('table').first.screenshot(path=str(destination))
        else:page.screenshot(path=str(destination))
        return {'validated_result_rendered':True}
    except Exception as exc:
        arm_failed_cleanup(bi)
        # Raw diagnostics are private and only included in encrypted review.
        Path(destination).with_suffix('.error.json').write_text(json.dumps({'type':type(exc).__name__,'message':str(exc)}))
        code=re.search(r'(?:net::)?ERR_[A-Z_]+',str(exc))
        print(json.dumps({'renderer_failed_phase':locals().get('phase','setup'),'slice_id':form.get('slice_id'),'transport_code':code.group(0) if code else None}),flush=True)
        try:page.screenshot(path=str(Path(destination).with_suffix(".failed.png")),timeout=5000)
        except Exception:pass
        raise
    finally:
        page.unroute('**/api/v1/chart/data**',serve)
        page.unroute('**/superset/explore_json/**',serve)


def compose(browser,panels,destination,columns=1,heading=None,width=1800):
    """Lay out unmodified source screenshots; no financial values are reconstructed."""
    context=browser.new_context();page=context.new_page()
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


@bounded_bi_operation
def query_from_renderer(bi,form):
    """Ask Superset's own chart plugin to build a missing saved query; block execution."""
    page=active_page(bi);captured=[]
    deadline=time.monotonic()+180
    def intercept(route):
        if route.request.method=='POST':
            try:captured.append(route.request.post_data_json)
            except Exception:pass
        route.fulfill(status=200,content_type='application/json',body='{"result":[]}')
    page.route('**/api/v1/chart/data**',intercept)
    def abort_legacy(route):route.abort()
    page.route('**/superset/explore_json/**',abort_legacy)
    try:
        load_explore(bi,page,form,500,deadline)
        while not captured and time.monotonic()<deadline:page.wait_for_timeout(100)
        if len(captured)!=1 or not captured[0].get('queries'):raise StructureError('Chart plugin did not build one report query')
        return captured[0]
    except Exception:
        arm_failed_cleanup(bi)
        raise
    finally:
        page.unroute('**/api/v1/chart/data**',intercept)
        page.unroute('**/superset/explore_json/**',abort_legacy)
