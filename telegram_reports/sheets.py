"""Export only the matching native Sheets weekly block, without editing it."""
import urllib.parse
from pathlib import Path
from PIL import ImageChops
import pypdfium2 as pdfium
from daily_metrics.io import csv_sheet,native_cell,get
from daily_metrics.core import target_candidates,a1,StructureError,lower_targets,value,number,DASH
from daily_metrics.extended_core import extra_targets
from .http import report_get


def assert_summary_complete(rows,row,header,day):
    layout=extra_targets(rows,header,row,day)
    lower_revenue,lower_writeoff=lower_targets(rows,header,day)
    required={'revenue':(row,2),'writeoff':(row,3),'top_food':(row,6),
              'top_dessert':(row,7),'top_japan':(row,8),'procurement':(row,12),
              'uv':layout['uv'],'osa':layout['osa'],'invoices':layout['invoices'],
              'weekly_revenue':lower_revenue,'writeoff_percent':lower_writeoff}
    missing=[]
    for name,(r,c) in required.items():
        raw=value(rows,r,c)
        if raw==DASH:continue  # Explicit source absence follows the owner's existing rule.
        try:number(raw)
        except StructureError:missing.append(name)
    if missing:raise StructureError('Summary metrics not ready: '+','.join(missing))


def preflight_week(branch,day):
    rows=csv_sheet(branch.book,branch.revenue_gid)
    expected=f'Date({day.year},{day.month-1},{day.day})'
    found=[(r,h) for r,h in target_candidates(rows,day,branch.newest_first)
           if native_cell(branch.book,branch.revenue_gid,a1(r,0))==('date',expected)]
    if len(found)!=1:raise StructureError('Report week must have one full-year date match')
    assert_summary_complete(rows,*found[0],day)


def capture_week(context,branch,day,destination,*,require_complete=False):
    rows=csv_sheet(branch.book,branch.revenue_gid)
    candidates=[]
    expected=f'Date({day.year},{day.month-1},{day.day})'
    for row,header in target_candidates(rows,day,branch.newest_first):
        kind,raw=native_cell(branch.book,branch.revenue_gid,a1(row,0))
        if kind=='date' and raw==expected:candidates.append((row,header))
    if len(candidates)!=1:raise StructureError('Report week must have one full-year date match')
    row,header=candidates[0]
    if require_complete:assert_summary_complete(rows,row,header,day)
    layout=extra_targets(rows,header,row,day)
    last=max(r for r,c in layout['date_anchors'])+7-day.weekday()
    width=max([len([c for c in rows[header] if c])]+[c+1 for targets in layout['reasons'].values() for r,c in targets])
    width=max(width,17)
    params={'format':'pdf','gid':branch.revenue_gid,'r1':max(0,header-1),'r2':last+1,'c1':0,'c2':width,
        'size':'A3','portrait':'false','scale':'4','gridlines':'true','sheetnames':'false','printtitle':'false',
        'pagenum':'UNDEFINED','top_margin':0.15,'bottom_margin':0.15,'left_margin':0.15,'right_margin':0.15}
    response=report_get(context,f'https://docs.google.com/spreadsheets/d/{branch.book}/export?'+urllib.parse.urlencode(params))
    data=response.body()
    if response.status!=200 or not data.startswith(b'%PDF'):raise StructureError('Native weekly Sheets export failed')
    pdf=pdfium.PdfDocument(data)
    try:
        if len(pdf)!=1:raise StructureError('Weekly table export spans multiple pages')
        page=pdf[0]
        try:image=page.render(scale=2).to_pil().convert('RGB')
        finally:page.close()
        bbox=ImageChops.invert(image).convert('L').point(lambda v:255 if v>30 else 0).getbbox()
        if not bbox:raise StructureError('Native weekly table image empty')
        image.crop((max(0,bbox[0]-8),max(0,bbox[1]-8),min(image.width,bbox[2]+8),min(image.height,bbox[3]+8))).save(destination)
    finally:pdf.close()
