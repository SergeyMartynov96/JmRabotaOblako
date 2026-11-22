"""Validated invoice excerpt: last supplier, receiving branch, three exact totals."""
import html,urllib.parse
from decimal import Decimal
from daily_metrics.config import INVOICE_SUPPLIERS
from daily_metrics.extended_core import invoice_total
from daily_metrics.core import Report,text,number,StructureError
from daily_metrics.extended_io import INVOICES
from daily_metrics.io import SU
from .http import report_get



def invoice_source(context,url):
    return report_get(context,url).text()


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
