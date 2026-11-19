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
            print(json.dumps({'invoice_forms':p.forms,'invoice_fields':p.fields,'supplier_options':matches},ensure_ascii=False),flush=True)
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
        for title,url in targets.items():
            try:
                page=context.new_page();page.goto(url,wait_until='domcontentloaded',timeout=120000)
                page.wait_for_timeout(2500)
                # Only visible headings, chart labels, and form-control names; no values/tokens.
                info=page.evaluate('''() => ({headings:[...document.querySelectorAll('h1,h2,h3')].map(x=>x.textContent.trim()),charts:[...document.querySelectorAll('.dashboard-component-chart-holder')].map(x=>({id:x.id,title:x.querySelector('.header-title')?.textContent})),frames:[...document.querySelectorAll('iframe')].map(x=>({src:x.getAttribute('src')?.split('?')[0]})),app:!!document.querySelector('#app'),bootstrapAttributes:document.querySelector('#app')?.getAttributeNames().filter(x=>x.includes('bootstrap'))})''')
                print(json.dumps({'dashboard':title,'location':safe_path(page.url),'schema':info},ensure_ascii=False),flush=True)
                page.close()
            except Exception as exc:print(json.dumps({'dashboard':title,'error':type(exc).__name__}),flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'probe_error':type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
