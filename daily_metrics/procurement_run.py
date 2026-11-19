"""Blank-only procurement: yesterday, today and tomorrow; other metrics stay daily."""
import datetime as dt
from .core import (DASH,StructureError,a1,date_matches,number,procurement,
                   procurement_total_columns,target_candidates,text,value)
from .io import csv_sheet
from .extended_core import key


def procurement_days(report_day):
    return tuple(report_day+dt.timedelta(days=offset) for offset in range(3))


def future_value(rows,day):
    # A future placeholder is pending data, never a permanent dash or zero.
    normalized=[[DASH if v.strip() in ('–','-') else v for v in row] for row in rows]
    result=procurement(normalized,day)
    if result==DASH:return None
    if number(result)!=0:return result
    target=[r for r,row in enumerate(normalized) if date_matches(value(normalized,r,1),day)][0]
    totals=procurement_total_columns(normalized)
    if any(number(value(normalized,target,c))!=0 for c in totals):return result
    # Zero formulas on otherwise empty supplier rows are not completed procurement.
    all_totals={c for row in normalized[:6] for c,v in enumerate(row) if 'итого' in text(v) or 'сумма' in text(v)}
    for col in totals:
        start=max([1]+[c for c in all_totals if c<col])+1
        for c in range(start,col):
            raw=value(normalized,target,c)
            if not raw or raw==DASH:continue
            try:number(raw)
            except StructureError:continue
            return result
    return None


def find_target(sheet,rows,day,newest_first):
    found=[(row,header) for row,header in target_candidates(rows,day,newest_first)
           if sheet.is_reporting_date(a1(row,0),day)]
    if len(found)>1:raise StructureError('Procurement target date duplicated')
    if not found:return None
    row,header=found[0]
    if key(value(rows,header,12))!='закупфакт':raise StructureError('Procurement target header changed')
    return row,header


def fill_procurement(sheet,branch,report_day,rows,apply,summary,check_date,load=None):
    """Only M cells may be added to the write allowlist, with full-year anchors."""
    load=load or csv_sheet;source=None;source_error=None;errors=[]
    for day in procurement_days(report_day):
        try:target=find_target(sheet,rows,day,branch.newest_first)
        except Exception as exc:
            errors.append('procurement target '+day.isoformat()+' failed ('+type(exc).__name__+')');continue
        if target is None:
            if day==report_day:raise StructureError('Yesterday procurement target date missing')
            summary['procurement_pending']=summary.get('procurement_pending',0)+1
            continue  # A future weekly block has not been prepared; never invent dates.
        row,header=target;cell=a1(row,12)
        if sheet.cell_has_content(cell):
            summary['preserved']+=1
            continue
        try:
            if source_error is not None:raise source_error
            if source is None:
                try:source=load(branch.book,branch.procurement_gid)
                except Exception as exc:source_error=exc;raise
            literal=procurement(source,day) if day==report_day else future_value(source,day)
        except Exception as exc:
            errors.append('procurement source '+day.isoformat()+' failed ('+type(exc).__name__+')');continue
        if literal is None:
            summary['procurement_pending']=summary.get('procurement_pending',0)+1
            continue
        # The execution-day guard remains yesterday; the destination date is checked
        # independently and explicitly allowed only for these three procurement cells.
        check_date(report_day)
        for anchor in (a1(row,0),a1(header,12)):
            sheet.anchors[anchor]=sheet.anchor_value(anchor)
        if not sheet.is_reporting_date(a1(row,0),day):raise StructureError('Procurement date moved before write')
        sheet.allowed.add(cell)
        if apply:
            if sheet.put_blank(cell,literal):
                summary['written']+=1;summary['missing']+=literal==DASH
            else:summary['preserved']+=1
        else:
            summary['planned']+=1;summary['missing']+=literal==DASH
    return errors
