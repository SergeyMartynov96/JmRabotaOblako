"""Manual read-only source discovery. Logs schema/labels only, never source data or URLs with tokens."""
import json,re,urllib.parse,shutil
from html.parser import HTMLParser
from .io import SU
from scripts.cloud_readonly_test import ReadOnlySU

BI_INDEX='/handbook/biSupersetSettings/index'
INVOICE='/turnover/waybill/report'
ALIASES=('Шабалда','Веселов','Нигаматуллин','Сиразетдинов','Амбруази','Яровиков','Мазрова','Курмачев','Веденева','Парамонова','Сергеева','Кабо','Кисель','Мисфуд','Мурачев','Разогрели и поели','Качество вкуса','Водопьянова','Маркетинг и еда','Мамадалиев','Маматулин','Завод ЕКБ','Сафарова')
TARGETS=('Списания ЖМ (Старая версия)','Упущенная выручка ЖМ','Остатки ЖМ')

class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.links=[];self.link=None;self.parts=[];self.fields=[];self.forms=[];self.select=None;self.option=None;self.options=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='tr':self.row={'text':[],'links':[]}
        if tag=='a':self.link=a.get('href');self.parts=[]
        if tag=='form':self.forms.append({k:a.get(k) for k in ('id','action','method')})
        if tag in ('input','select') and a.get('name'):self.fields.append({k:a.get(k) for k in ('name','type','id')})
        if tag=='select':self.select=a.get('name')
        if tag=='option':self.option=a.get('value');self.option_text=[]
    def handle_data(self,data):
        if self.row is not None:self.row['text'].append(data.strip())
        if self.link is not None:self.parts.append(data.strip())
        if self.option is not None:self.option_text.append(data.strip())
    def handle_endtag(self,tag):
        if tag=='a' and self.link is not None:
            link=(' '.join(self.parts),self.link);self.links.append(link)
            if self.row is not None:self.row['links'].append(link)
            self.link=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None
        if tag=='option' and self.option is not None:
            self.options.append((self.select,self.option,' '.join(self.option_text)));self.option=None
        if tag=='select':self.select=None

def safe_path(url):
    u=urllib.parse.urlsplit(url)
    return {'host':u.hostname,'path':re.sub(r'/[^/]{60,}', '/<opaque>',u.path),'query_keys':list(urllib.parse.parse_qs(u.query))}

def main():
    print(json.dumps({'stage':'login'}),flush=True)
    client=ReadOnlySU();client.login()
    print(json.dumps({'stage':'authenticated'}),flush=True)
    parsed={};targets={}
    for path in (INVOICE,BI_INDEX):
        print(json.dumps({'stage':'read_schema','path':path}),flush=True)
        with client.opener.open(SU+path,timeout=120) as response:html=response.read().decode()
        p=Page();p.feed(html);parsed[path]=p
        if path==INVOICE:
            matches=[{'field':field,'id':ident,'name':name} for field,ident,name in p.options if any(alias.casefold() in name.casefold() for alias in ALIASES)]
            hints=[]
            for marker in ('w_selectedyw1','new Select','department_id','department_to','contractor_id'):
                for match in list(re.finditer(re.escape(marker),html))[-6:]:
                    snippet=html[max(0,match.start()-90):match.end()+190]
                    hints.append(snippet)
            
            supplier_matches={}
            for alias in ALIASES:
                with client.opener.open(SU+'/turnover/waybill/contractorAutocomplete?'+urllib.parse.urlencode({'term':alias}),timeout=90) as response:
                    supplier_matches[alias]=json.loads(response.read().decode())
            print(json.dumps({'supplier_matches':supplier_matches},ensure_ascii=False),flush=True)
            print(json.dumps({'invoice_forms':p.forms,'invoice_fields':p.fields,'supplier_options':matches,'autocomplete_hints':hints},ensure_ascii=False),flush=True)
        else:
            for row in p.rows:
                for title in TARGETS:
                    if title in ' '.join(row['text']) and row['links']:
                        targets[title]=urllib.parse.urljoin(SU,row['links'][-1][1])
            print(json.dumps({'bi_links':{k:safe_path(v) for k,v in targets.items()}},ensure_ascii=False),flush=True)
    if not targets:return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
        context=browser.new_context()
        for handler in client.opener.handlers:
            if hasattr(handler,'cookiejar'):
                context.add_cookies([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'secure':c.secure} for c in handler.cookiejar])
        page=context.new_page()
        page.goto(next(iter(targets.values())),wait_until='domcontentloaded',timeout=120000)
        print(json.dumps({'stage':'bi_authenticated','location':safe_path(page.url)}),flush=True)
        for title,url in targets.items():
            try:
                nxt=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('next',[''])[0]
                ident=int(re.search(r'/dashboard/(\d+)',urllib.parse.unquote(nxt)).group(1))
                response=context.request.get(f'https://bi.lifemart.ru/api/v1/dashboard/{ident}',timeout=90000)
                payload=response.json().get('result',{})
                metadata=payload.get('json_metadata',{})
                if isinstance(metadata,str):metadata=json.loads(metadata)
                filters=[{k:f.get(k) for k in ('id','name','filterType','targets')} for f in metadata.get('native_filter_configuration',[])]
                response=context.request.get(f'https://bi.lifemart.ru/api/v1/dashboard/{ident}/charts',timeout=90000)
                charts=response.json().get('result',[])
                wanted=[]
                for chart in charts:
                    name=chart.get('slice_name','')
                    if not (('Период. Магазины. %' in name) or ('Причины. Период, %' in name) or ('in_stock Готовая еда + Япония' in name)):continue
                    fd=chart.get('form_data') or chart.get('params') or {}
                    if isinstance(fd,str):fd=json.loads(fd)
                    fields={k:fd.get(k) for k in ('datasource','viz_type','groupby','columns','rows','row_pivots','col_pivots','granularity_sqla','x_axis','time_grain_sqla','y_axis_format','value_format','number_format')}
                    metrics=fd.get('metrics',[])
                    fields['metrics']=[m if isinstance(m,str) else {'label':m.get('label'),'expressionType':m.get('expressionType')} for m in metrics]
                    fields['filter_columns']=[f.get('subject') for f in fd.get('adhoc_filters',[])]
                    detail=context.request.get(f"https://bi.lifemart.ru/api/v1/chart/{fd['slice_id']}",timeout=90000).json().get('result',{})
                    qc=detail.get('query_context')
                    if isinstance(qc,str):qc=json.loads(qc)
                    fields.update({k:fd.get(k) for k in ('groupbyRows','groupbyColumns','valueFormat','extra_form_data','adhoc_filters')})
                    fields['query_context']=qc
                    wanted.append({'id':fd.get('slice_id'),'title':name,'chart_keys':list(chart),'form_keys':list(fd),'fields':fields})
                print(json.dumps({'dashboard':title,'id':ident,'api_status':response.status,'filters':filters,'charts':wanted},ensure_ascii=False),flush=True)
            except Exception as exc:print(json.dumps({'dashboard':title,'error':type(exc).__name__}),flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'probe_error':type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
