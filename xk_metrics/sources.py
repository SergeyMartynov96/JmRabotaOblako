"""Read only exact stores/dates; no changes to the existing three-store adapters."""
import dataclasses
import time
import urllib.parse
import urllib.error
from decimal import Decimal
from playwright.sync_api import TimeoutError as BrowserTimeout
from daily_metrics.session import ReportSUReports
from daily_metrics.io import SU,REVENUE,WRITEOFF,csv_sheet
from daily_metrics.core import Report,StructureError,DASH,value,date_matches,number,text
from daily_metrics.extended_io import ExtraSources,BIReports,CHARTS
from daily_metrics.extended_core import daily_query,bi_percent,bi_rows,key,percentage_points

class Client(ReportSUReports):
    def adopt_browser_session(self,context,*,verified=False):
        super().adopt_browser_session(context,verified=verified)
        self.report_context=context

    def revenue(self,branch,day):
        if day not in self.revenue_cache:
            self.ensure_authenticated()
            params={'department[]':['140','401'],
                'brandId':'','format':'1','date_s':day.strftime('%d.%m.%Y'),
                'date_do':day.strftime('%d.%m.%Y'),'day_of_week[]':['all'],'night':'2'}
            self.revenue_cache[day]=self.browser_revenue(params)
        return self.revenue_cache[day]

    def writeoff(self,branch,day):
        self.ensure_authenticated()
        context=getattr(self,'report_context',None)
        if context is None:raise StructureError('Verified browser session required for writeoff')
        if branch.department not in (140,401):raise StructureError('Foreign writeoff store')
        params={'WriteOffAct[create_date_from]':day.strftime('%d.%m.%Y'),
                'WriteOffAct[create_date_to]':day.strftime('%d.%m.%Y'),
                'WriteOffAct[search_project_id]':'2',
                'WriteOffAct[department_id][]':[str(branch.department)],'yt6':'Сформировать'}
        url=SU+WRITEOFF+'?'+urllib.parse.urlencode(params,doseq=True)
        deadline=time.monotonic()+180
        print('SU: reading writeoff through the verified browser session',flush=True)
        for attempt in range(2):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ConnectionError('Writeoff report deadline exhausted')
            try:
                response=context.request.get(url,timeout=max(1,int(min(120,remaining)*1000)))
                if response.status in (429,500,502,503,504):raise ConnectionError('Temporary writeoff server response')
                if response.status!=200:raise StructureError('Writeoff report HTTP '+str(response.status))
                observed=urllib.parse.urlsplit(response.url)
                if (observed.scheme,observed.netloc,observed.path)!=('https','su.lifemart.ru',WRITEOFF):
                    raise StructureError('Writeoff response left the approved endpoint')
                html=response.text()
                if 'LoginForm[password]' in html:raise StructureError('Writeoff session expired')
                return html
            except Exception as exc:
                if not transient_browser_error(exc):raise
                if attempt:raise ConnectionError('Writeoff report transport unavailable') from None
                print('SU: retrying writeoff within the three-minute budget',flush=True)

    def browser_revenue(self,params):
        expected={'department[]','brandId','format','date_s','date_do','day_of_week[]','night'}
        if set(params)!=expected or params['department[]']!=['140','401'] or params['date_s']!=params['date_do']:
            raise StructureError('Revenue request left the approved store/day scope')
        context=getattr(self,'report_context',None)
        if context is None:raise StructureError('Verified browser session required for revenue')
        deadline=time.monotonic()+660
        print('SU: reading revenue through the verified browser session',flush=True)
        for attempt in range(2):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ConnectionError('Revenue report deadline exhausted')
            try:
                response=context.request.post(SU+REVENUE,
                    data=urllib.parse.urlencode(params,doseq=True),
                    headers={'Content-Type':'application/x-www-form-urlencoded'},
                    timeout=max(1,int(min(600,remaining)*1000)))
                if response.status in (429,500,502,503,504):raise ConnectionError('Temporary revenue server response')
                if response.status!=200:raise StructureError('Revenue report HTTP '+str(response.status))
                observed=urllib.parse.urlsplit(response.url)
                if (observed.scheme,observed.netloc,observed.path)!=('https','su.lifemart.ru',REVENUE):
                    raise StructureError('Revenue response left the approved report endpoint')
                html=response.text()
                if 'LoginForm[password]' in html:raise StructureError('Revenue session expired')
                return html
            except Exception as exc:
                if not transient_browser_error(exc):raise
                if attempt:raise ConnectionError('Revenue report transport unavailable') from None
                print('SU: retrying revenue within the original generation budget',flush=True)


def transient_browser_error(exc):
    message=str(exc).casefold()
    return isinstance(exc,(TimeoutError,ConnectionError)) or (
        type(exc).__module__.startswith('playwright.') and
        (type(exc).__name__=='TimeoutError' or any(code in message for code in (
            'etimedout','econnreset','enotfound','socket hang up','connection reset','connection closed'))))


def resolve_branch(client,store,day):
    doc=Report();doc.feed(client.revenue(store.branch,day))
    titles={heading.strip() for heading,rows in doc.tables if text(heading)==text(store.branch.title)}
    if len(titles)!=1:raise StructureError('Requested store revenue title missing or ambiguous')
    title=titles.pop()
    print(store.branch.name+': verified source store title='+title,flush=True)
    return dataclasses.replace(store.branch,title=title)


def read_tops(store,day):
    rows=csv_sheet(store.branch.tops_book,store.tops_gid)
    groups=((1,3,33),(38,40,50),(54,56,66)) if store.branch.name=='Х76' else ((0,2,32),(35,37,47),(50,52,67))
    output=[]
    for header,start,end in groups:
        cols=[c for c in range(1,len(rows[header])) if date_matches(value(rows,header,c),day,allow_short=True)]
        if len(cols)!=1:raise StructureError('Tops reporting date missing or ambiguous')
        c=cols[0]+1
        expected_time='19:00' if store.branch.name=='Х76' else '18:00'
        if value(rows,header+1,c)!=expected_time:raise StructureError('Tops evening time changed')
        if not all(value(rows,r,0) for r in range(start,end)):raise StructureError('Tops product block changed')
        entries=[value(rows,r,c) for r in range(start,end)]
        if not any(entries):raise StructureError('Tops evening marks not filled')
        if any(v not in ('','0','1') for v in entries):raise StructureError('Invalid tops marks')
        total=number(value(rows,end,c))
        if not 0<=total<=100:raise StructureError('Tops percentage outside range')
        output.append(str(total))
    return output


class StoreBIReports(BIReports):
    def _load_su_login_form(self,page,deadline):
        try:return super()._load_su_login_form(page,deadline)
        except BrowserTimeout:
            # A failed navigation has no usable execution context. Close this
            # disposable page before the shared diagnostic evaluate can stall.
            try:page.close()
            except Exception:pass
            raise


class Sources(ExtraSources):
    def __init__(self,browser,client):
        # This isolated runner reads all financial reports in the same verified
        # browser session; avoid first repeating the unreliable HTTP login POST.
        self.client=client
        self.bi=StoreBIReports(browser,client,browser_login=True)

    def read(self,kind,branch,day):
        if kind!='invoices':return super().read(kind,branch,day)
        from telegram_reports.invoices import fetch_invoices
        from daily_metrics.extended_core import invoice_total
        from daily_metrics.config import INVOICE_SUPPLIERS
        if self.bi.context is None:raise StructureError('Verified browser session required for invoices')
        source=fetch_invoices(self.bi.context,branch,day)
        return invoice_total(source,branch.title,day,INVOICE_SUPPLIERS)

    def osa_pair(self,branch,day):
        form,context=self.bi.template('osa')
        query=daily_query(context,day,branch.title)
        q=query['queries'][0]
        labels=['OSA "На 11:00"','OSA "На 20:00"']
        metrics=[m for m in q.get('metrics',[]) if (m.get('label') if isinstance(m,dict) else m) in labels]
        if len(metrics)!=2:raise StructureError('OSA 11/20 metric missing')
        q['metrics']=metrics;q['post_processing']=[]
        query['form_data']={**form,'dashboardId':CHARTS['osa'][0],
            'time_range':q['time_range'],'time_grain_sqla':'P1D',
            'extra_form_data':{'time_range':q['time_range'],'filters':[{'col':'title','op':'IN','val':[branch.title]}]}}
        payload=self.bi.api('/api/v1/chart/data',query)
        result=[bi_percent(payload,label,branch.title,day,'osa') for label in labels]
        if any(v is None for v in result):raise StructureError('OSA day unavailable')
        return result

    def reasons(self,branch,day):
        # Exact separate labels: training is not silently added to tasting here.
        result={}
        for row in bi_rows(self.bi.read('reasons',branch,day)):
            if 'reason' not in row or 'Списания %' not in row:raise StructureError('BI reason columns missing')
            name=key(row['reason'])
            if name in result:raise StructureError('Duplicate BI reason')
            amount=percentage_points(row['Списания %'])
            if amount is not None:result[name]=amount
        return result
