"""Validated invoice excerpt: last supplier, receiving branch, three exact totals."""
import html,json,re,urllib.parse,urllib.request,urllib.error,http.cookiejar,ssl,time
from decimal import Decimal
from daily_metrics.config import INVOICE_SUPPLIERS
from daily_metrics.extended_core import invoice_total
from daily_metrics.core import Report,text,number,StructureError
from daily_metrics.extended_io import INVOICES
from daily_metrics.io import SU
from .http import report_get



def invoice_source(context,url):
    """Read with the browser's SU session through the proven HTTP report transport."""
    target=urllib.parse.urlsplit(url)
    if target.scheme!='https' or target.netloc!='su.lifemart.ru' or target.path!=INVOICES:
        raise StructureError('Unapproved invoice report read')
    deadline=time.monotonic()+180
    jar=http.cookiejar.CookieJar()
    for c in context.cookies(url):
        expiry=c.get('expires',-1)
        expiry=int(expiry) if expiry is not None and expiry>0 else None
        jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,
            c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),
            c.get('path','/'),True,c.get('secure',False),expiry,expiry is None,
            None,None,{},False))
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Invoice read deadline exhausted')
        with opener.open(url,timeout=min(60,remaining)) as response:
            chunks=[]
            while time.monotonic()<deadline:
                chunk=response.read1(65536)
                if not chunk:return b''.join(chunks).decode()
                chunks.append(chunk)
            raise TimeoutError('Invoice read deadline exhausted')
    except urllib.error.HTTPError as exc:
        if exc.code not in (429,500,502,503,504):raise StructureError('Invoice report HTTP '+str(exc.code)) from None
    except (TimeoutError,urllib.error.URLError) as exc:
        if isinstance(getattr(exc,'reason',None),ssl.SSLCertVerificationError):raise
    remaining=deadline-time.monotonic()
    if remaining<=0:raise ConnectionError('Invoice report transport deadline exhausted')
    print('Invoice report: retrying the same read through the browser-session transport',flush=True)
    return report_get(context,url,timeout=max(1,int(remaining*1000))).text()


def fetch_invoices(context,branch,day):
    params={'Waybill[insert_date_from]':day.strftime('%d.%m.%Y'),'Waybill[insert_date_to]':day.strftime('%d.%m.%Y'),
        'Waybill[recipient_id][]':[str(branch.department)],'Waybill[contractor_id][]':list(INVOICE_SUPPLIERS),
        'Waybill[itemsOnPage]':'1000','yt19':'Сформировать'}
    source=invoice_source(context,SU+INVOICES+'?'+urllib.parse.urlencode(params,doseq=True))
    invoice_excerpt(source,branch,day)
    return source


ROUNDING_TOLERANCE=Decimal('1.00')


def footer_amount(raw):
    # SU groups footer thousands with commas, while body amounts are ungrouped.
    compact=re.sub(r'[\s₽]', '', str(raw))
    if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+\.\d{1,2}',compact):
        compact=compact.replace(',','')
    elif re.fullmatch(r'[+-]?\d{1,3}(?:\.\d{3})+,\d{1,2}',compact):
        compact=compact.replace('.','')
    return number(compact)


def source_totals(table,start,columns,data):
    """Preserve SU's footer; validate its billed total with ruble rounding tolerance."""
    sums=[sum((number(r[c]) for r in data),Decimal(0)) for c in columns[-3:]]
    footers=[]
    for row in table[start:]:
        if len(row)<=max(columns):continue
        if row[columns[0]].strip() or row[columns[1]].strip() or row[columns[2]].strip():continue
        if not all(row[c].strip() for c in columns[-3:]):continue
        footers.append([footer_amount(row[c]) for c in columns[-3:]])
    if len(footers)>1:raise StructureError('Invoice source footer ambiguous')
    totals=footers[0] if footers else sums
    if footers and abs(sums[2]-totals[2])>ROUNDING_TOLERANCE:
        raise StructureError('Invoice source billed total differs from complete rows beyond rounding tolerance')
    # The source's VAT/base columns may contain their own corrections. Never
    # manufacture a billed total from them or block an otherwise verified report.
    print(json.dumps({'invoice_source_totals_preserved':True,
        'vat_arithmetic_within_rounding':abs(totals[0]+totals[1]-totals[2])<=ROUNDING_TOLERANCE}),flush=True)
    return totals


def invoice_excerpt(source,branch,day):
    # Validate every invoice field before slow BI collection as well as before export.
    invoice_total(source,branch.title,day,INVOICE_SUPPLIERS)
    doc=Report();doc.feed(source)
    matches=[]
    for _,table in doc.tables:
        for i,header in enumerate(table):
            if not any(text(v)=='сумма с ндс' for v in header):continue
            def col(label):
                found=[c for c,v in enumerate(header) if text(v)==label]
                if len(found)!=1:raise StructureError('Invoice excerpt header ambiguous')
                return found[0]
            columns=[col(v) for v in ('подразделение','подразделение получения товара','контрагент','сумма без ндс','сумма ндс','сумма с ндс')]
            data=[r for r in table[i+1:] if len(r)>max(columns) and (r[columns[1]].strip() or r[columns[0]].strip())]
            totals=source_totals(table,i+1,columns,data)
            matches.append((data[-1] if data else None,columns,totals))
    if len(matches)!=1:raise StructureError('Invoice excerpt table missing')
    return matches[0]


def capture_invoices(context,branch,day,destination,source=None):
    if source is None:source=fetch_invoices(context,branch,day)
    last,cols,totals=invoice_excerpt(source,branch,day)
    def money(v):return format(v,',.2f').replace(',',' ').replace('.',',')
    headings=['Подразделение получения товара','Контрагент','Сумма без НДС','Сумма НДС','Сумма с НДС']
    values=[branch.title,last[cols[2]],*[last[c] for c in cols[-3:]]] if last else [branch.title,'Нет накладных','','','']
    cells=lambda values:''.join('<td>'+html.escape(str(v))+'</td>' for v in values)
    markup='<h2>Приходные накладные — '+html.escape(branch.name)+'</h2><p>'+day.strftime('%d.%m.%Y')+' · '+html.escape(branch.title)+'</p><table><thead><tr>'+''.join('<th>'+v+'</th>' for v in headings)+'</tr></thead><tbody><tr>'+cells(values)+'</tr><tr class="total">'+cells(['Итого','',*[money(v) for v in totals]])+'</tr></tbody></table>'
    page=context.new_page()
    try:
        page.set_viewport_size({'width':1500,'height':400})
        page.set_content('<html><meta charset="utf-8"><style>body{font:18px Arial;margin:20px;color:#18272c}table{border-collapse:collapse;width:100%}th,td{border:1px solid #fff;padding:14px;text-align:left}th{background:#58a3c7;color:white}td{background:#e3f0f3}.total td{background:#ffd0b0;font-weight:bold}p{color:#52606a}</style><body><main>'+markup+'</main></body></html>')
        page.locator('main').screenshot(path=str(destination))
    finally:page.close()
