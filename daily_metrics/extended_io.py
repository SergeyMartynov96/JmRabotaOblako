"""Read-only adapters for the owner's three additional BI sources."""
import json
import urllib.parse
from html.parser import HTMLParser
from .core import StructureError
from .extended_core import daily_query

BI='https://bi.lifemart.ru'
BI_INDEX='/handbook/biSupersetSettings/index'
INVOICES='/turnover/waybill/report'
CHARTS={
    'uv':(37,'Упущенная выручка. Период. Магазины. % (мета) 2.0'),
    'osa':(56,'OSA % по in_stock Готовая еда + Япония'),
    'reasons':(241,'Списания. Причины. Период, %'),
}


class BILinks(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.csrf=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if a.get('name') in ('csrf_token','csrf-token'):
            self.csrf=a.get('value') or a.get('content')
        if a.get('data-bootstrap'):
            try:self.csrf=json.loads(a['data-bootstrap']).get('common',{}).get('csrf_token') or self.csrf
            except (ValueError,TypeError):pass
        if tag=='a':
            href=dict(attrs).get('href')
            if href:self.links.append(href)


class BIReports:
    def __init__(self,browser,client):
        self.browser=browser;self.client=client;self.context=None
        self.templates={};self.cache={};self._auth_error=None;self.csrf=None

    def authenticate(self):
        if self._auth_error is not None:raise self._auth_error
        try:self._authenticate()
        except Exception as exc:
            self._auth_error=exc
            raise

    def _authenticate(self):
        if self.context is not None:return
        if self._auth_error is not None:raise self._auth_error
        print('BI: authenticating SU session',flush=True)
        self.client.ensure_authenticated()
        # This accounting route and dashboard were verified from the SU menu.
        # Avoid reloading the slow menu for every cloud session.
        links=[BI+'/login/accounting?'+urllib.parse.urlencode({'next':'/superset/dashboard/37/'})]
        print('BI: opening accounting session',flush=True)
        context=self.browser.new_context()
        try:
            for handler in self.client.opener.handlers:
                if hasattr(handler,'cookiejar'):
                    context.add_cookies([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'secure':c.secure}
                                         for c in handler.cookiejar])
            # Follow the same accounting redirects in Chrome as the user's dashboard.
            def abort_initial_charts(route):route.abort()
            context.route('**/api/v1/chart/data*',abort_initial_charts)
            page=context.new_page()
            try:
                response=page.goto(links[0],wait_until='domcontentloaded',timeout=180000)
                if response is None or response.status!=200 or '/superset/' not in page.url:
                    raise StructureError('BI authentication failed')
                html=BILinks();html.feed(page.content());self.csrf=html.csrf
            finally:
                page.close();context.unroute('**/api/v1/chart/data*',abort_initial_charts)
            self.context=context
            print('BI: session authenticated',flush=True)
        except Exception as exc:
            self._auth_error=exc
            context.close();raise

    def api(self,path,data=None):
        self.authenticate()
        if not (path.startswith('/api/v1/dashboard/') or path.startswith('/api/v1/chart/')
                or path in ('/api/v1/security/csrf_token/','/api/v1/dataset/3437/column/reason/values/')):
            raise StructureError('Unapproved BI read endpoint')
        if data is None:
            response=self.context.request.get(BI+path,timeout=180000)
        else:
            if path!='/api/v1/chart/data':raise StructureError('Only BI query POST allowed')
            token=self.csrf or self.api('/api/v1/security/csrf_token/').get('result')
            if not token:raise StructureError('BI read token unavailable')
            response=self.context.request.post(BI+path,data=data,
                headers={'X-CSRFToken':token,'Referer':BI+'/'},timeout=180000)
        payload=response.json()
        if response.status!=200:
            types=','.join(str(e.get('error_type','unknown')) for e in payload.get('errors',[]) if isinstance(e,dict))
            raise StructureError('BI read unsuccessful HTTP '+str(response.status)+' at '+path+' '+types)
        if payload.get('errors'):raise StructureError('BI query rejected: '+','.join(str(e.get('error_type','unknown')) for e in payload['errors']))
        return payload

    def template(self,kind):
        if isinstance(self.templates.get(kind),Exception):raise self.templates[kind]
        try:return self._template(kind)
        except Exception as exc:
            self.templates[kind]=exc
            raise

    def _template(self,kind):
        if kind not in self.templates:
            dashboard,title=CHARTS[kind]
            charts=self.api(f'/api/v1/dashboard/{dashboard}/charts').get('result',[])
            matches=[chart for chart in charts if chart.get('slice_name')==title]
            if len(matches)!=1:raise StructureError('BI chart missing or duplicated')
            form=matches[0].get('form_data') or {}
            if isinstance(form,str):form=json.loads(form)
            detail=self.api(f"/api/v1/chart/{form['slice_id']}").get('result',{})
            context=detail.get('query_context')
            if isinstance(context,str):context=json.loads(context)
            if not context:raise StructureError('BI saved query unavailable')
            self.templates[kind]=(form,context)
        return self.templates[kind]

    def read(self,kind,branch,day):
        cache_key=(kind,branch.department,day)
        if cache_key not in self.cache:
            try:
                form,context=self.template(kind)
                query=daily_query(context,day,branch.title)
                query['form_data']={**form,'dashboardId':CHARTS[kind][0],
                    'time_range':query['queries'][0]['time_range'],'time_grain_sqla':'P1D',
                    'extra_form_data':{'time_range':query['queries'][0]['time_range'],
                                       'filters':[{'col':'title','op':'IN','val':[branch.title]}]}}
                self.cache[cache_key]=self.api('/api/v1/chart/data',query)
            except Exception as exc:self.cache[cache_key]=exc
        result=self.cache[cache_key]
        if isinstance(result,Exception):raise result
        return result

    def close(self):
        if self.context is not None:self.context.close()


class ExtraSources:
    def __init__(self,browser,client):
        self.bi=BIReports(browser,client);self.client=client
    def read(self,kind,branch,day):
        from .extended_core import bi_percent,bi_reasons,invoice_total
        from .config import INVOICE_SUPPLIERS
        if kind=='invoices':
            self.client.ensure_authenticated()
            params={'Waybill[insert_date_from]':day.strftime('%d.%m.%Y'),
                    'Waybill[insert_date_to]':day.strftime('%d.%m.%Y'),
                    'Waybill[department_id][]':[str(branch.department)],
                    'Waybill[contractor_id][]':list(INVOICE_SUPPLIERS),
                    'Waybill[itemsOnPage]':'1000','yt19':'Сформировать'}
            html=self.client.request(INVOICES+'?'+urllib.parse.urlencode(params,doseq=True))
            return invoice_total(html,branch.title,day,INVOICE_SUPPLIERS)
        payload=self.bi.read(kind,branch,day)
        if kind=='reasons':return bi_reasons(payload,'Списания %')
        metric={'uv':'УВ, % от общей выручки','osa':'OSA "На 20:00"'}[kind]
        return bi_percent(payload,metric,branch.title,day,kind)
    def close(self):self.bi.close()
