import urllib.parse,time
from PIL import ImageChops
import pypdfium2 as pdfium
from daily_metrics.io import csv_sheet,native_cell
from daily_metrics.core import a1,value,number,StructureError
from xk_metrics.layout import candidates,targets


def preflight(store,day):
    branch=store.branch;rows=csv_sheet(branch.book,branch.revenue_gid)
    expected=('date',f'Date({day.year},{day.month-1},{day.day})')
    found=[(r,h) for r,h in candidates(rows,store,day)
           if native_cell(branch.book,branch.revenue_gid,a1(r,store.offset+1))==expected]
    if len(found)!=1:raise StructureError('Store summary reporting date missing or ambiguous')
    row,header=found[0];fields,reasons,lower=targets(rows,store,row,header,day)
    for name,(r,c) in fields.items():
        try:number(value(rows,r,c))
        except StructureError:raise StructureError('Summary metrics not ready: '+name) from None
    return header,lower


def export_bounds(store,header,lower):
    return {'r1':header-1,'r2':lower+9,'c1':store.offset+1,'c2':store.offset+20}


def capture(context,store,day,destination):
    header,lower=preflight(store,day)
    params={'format':'pdf','gid':store.branch.revenue_gid,**export_bounds(store,header,lower),
        'size':'A3','portrait':'false','scale':'4','gridlines':'true','sheetnames':'false','printtitle':'false',
        'pagenum':'UNDEFINED','top_margin':0.15,'bottom_margin':0.15,'left_margin':0.15,'right_margin':0.15}
    url=f'https://docs.google.com/spreadsheets/d/{store.branch.book}/export?'+urllib.parse.urlencode(params)
    deadline=time.monotonic()+180
    for attempt in range(3):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ConnectionError('Summary export deadline exceeded')
        try:
            response=context.request.get(url,timeout=max(1,int(remaining*1000)))
            if response.status in (429,500,502,503,504):raise ConnectionError('Summary export temporarily unavailable')
            if response.status!=200:raise StructureError('Summary export HTTP '+str(response.status))
            data=response.body()
            if not data.startswith(b'%PDF'):raise StructureError('Summary export is not a native PDF')
            break
        except Exception as exc:
            from xk_metrics.sources import transient_browser_error
            if not transient_browser_error(exc) or attempt==2:raise
    pdf=pdfium.PdfDocument(data)
    try:
        if len(pdf)!=1:raise StructureError('Store summary spans multiple pages')
        page=pdf[0]
        try:image=page.render(scale=2).to_pil().convert('RGB')
        finally:page.close()
        bbox=ImageChops.invert(image).convert('L').point(lambda v:255 if v>30 else 0).getbbox()
        if not bbox:raise StructureError('Empty store summary image')
        image.crop((max(0,bbox[0]-8),max(0,bbox[1]-8),min(image.width,bbox[2]+8),min(image.height,bbox[3]+8))).save(destination)
    finally:pdf.close()
