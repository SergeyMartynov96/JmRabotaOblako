"""Validated invoice excerpt: last supplier, receiving branch, three exact totals."""
import html,urllib.parse,time
from decimal import Decimal
from daily_metrics.config import INVOICE_SUPPLIERS
from daily_metrics.extended_core import invoice_total
from daily_metrics.core import Report,text,number,StructureError
from daily_metrics.extended_io import INVOICES
from daily_metrics.io import SU



def invoice_source(context,url):
    """Retry only a transient, read-only invoice transport within one 180s budget."""
    target=urllib.parse.urlsplit(url)
    if target.scheme!='https' or target.netloc!='su.lifemart.ru' or target.path!=INVOICES:
        raise StructureError('Unapproved invoice report read')
    deadline=time.monotonic()+180
    for attempt in range(2):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ConnectionError('Invoice report transport deadline exhausted')
        try:
            response=context.request.get(url,timeout=max(1,int(remaining*1000)))
            if response.status in (429,500,502,503,504):
                if attempt:raise ConnectionError('Invoice report temporarily unavailable')
                print('Invoice report: retrying a temporary server response within the three-minute limit',flush=True)
                continue
            if response.status!=200:raise StructureError('Invoice report HTTP '+str(response.status))
            return response.text()
        except Exception as exc:
            message=str(exc).casefold()
            transport=(type(exc).__module__.startswith('playwright.') and
                (type(exc).__name__=='TimeoutError' or
                 (type(exc).__name__=='Error' and any(v in message for v in (
                    'etimedout','econnreset','econnrefused','enotfound','socket hang up',
                    'err_connection','err_timed_out','err_http2_protocol_error','connection closed','connection reset')))))
            if not transport:raise
            if attempt:raise ConnectionError('Invoice report transport unavailable') from None
            print('Invoice report: retrying a transient transport failure within the three-minute limit',flush=True)
    raise ConnectionError('Invoice report temporarily unavailable')


def capture_invoices(context,branch,day,destination):
    params={'Waybill[insert_date_from]':day.strftime('%d.%m.%Y'),'Waybill[insert_date_to]':day.strftime('%d.%m.%Y'),
        'Waybill[recipient_id][]':[str(branch.department)],'Waybill[contractor_id][]':list(INVOICE_SUPPLIERS),
        'Waybill[itemsOnPage]':'1000','yt19':'Сформировать'}
    source=invoice_source(context,SU+INVOICES+'?'+urllib.parse.urlencode(params,doseq=True))
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
            totals=[sum((number(r[c]) for r in data),Decimal(0)) for c in columns[-3:]]
            if totals[0]+totals[1]!=totals[2]:raise StructureError('Invoice VAT totals do not reconcile')
            matches.append((data[-1] if data else None,columns,totals))
    if len(matches)!=1:raise StructureError('Invoice excerpt table missing')
    last,cols,totals=matches[0]
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
