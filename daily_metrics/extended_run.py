"""Daily BI transfer; current reported reasons may refresh verified old scalars."""
from .core import DASH,StructureError,a1,money,same_cell_value,number
from .extended_core import extra_targets,percent_literal,reason_literal


def fill_extra(sheet,sources,branch,day,header,row,rows,apply,summary,check_date,*,kinds=None):
    layout=extra_targets(rows,header,row,day)
    targets={name:[layout[name]] for name in ('uv','osa','invoices')}
    targets['reasons']=[cell for cells in layout['reasons'].values() for cell in cells]
    if kinds is not None:targets={name:cells for name,cells in targets.items() if name in kinds}
    sheet.allowed.update(a1(r,c) for cells in targets.values() for r,c in cells)
    for r,c in layout['date_anchors']:
        cell=a1(r,c);sheet.anchors[cell]=sheet.anchor_value(cell)
    errors=[]
    for metric,cells in targets.items():
        pending=[(r,c) for r,c in cells if not sheet.stable_formula(a1(r,c))]
        print(branch.name+': '+metric+' destinations pending='+str(len(pending))+' total='+str(len(cells)),flush=True)
        summary['preserved']+=len(cells)-len(pending)
        if apply and not pending and metric!='reasons':continue
        print(branch.name+': reading '+metric,flush=True)
        try:
            result=sources.read(metric,branch,day)
            if metric=='reasons':
                # Keep a visible failure for unmapped labels while transferring
                # independently validated known causes to their own columns.
                if getattr(result,'unmapped',[]):
                    errors.append('reasons source failed (StructureError): Unmapped BI reason')
                    print(branch.name+': unmapped reason labels='+repr(result.unmapped),flush=True)
                # Missing categories never create zeros, dashes or replacement writes.
                values={(r,c):literal
                        for name,locations in layout['reasons'].items() if name in result
                        if (literal:=reason_literal(result[name],branch.name)) is not None
                        for r,c in locations}
            else:
                literal=DASH if result is None else (money(result) if metric=='invoices'
                    else percent_literal(result,branch.name,2 if metric=='uv' else 1))
                values={cells[0]:literal}
        except Exception as exc:
            errors.append(metric+' source failed ('+type(exc).__name__+')'+(': '+str(exc) if isinstance(exc,StructureError) else ''))
            continue
        if metric=='reasons':
            # Source-authoritative only for reported, nonzero reason values.
            # Formulas, other metrics and concurrent edits remain protected.
            for (r,c),literal in values.items():
                if (r,c) in pending:continue
                cell=a1(r,c);current=sheet.stable_formula(cell)
                if current.startswith('=') or same_cell_value(current,literal):continue
                try:number(current)
                except StructureError:continue
                check_date(day)
                if apply:
                    if sheet.replace_if_unchanged(cell,current,literal):
                        summary['preserved']-=1;summary['written']+=1
                        summary['reconciled_reasons']=summary.get('reconciled_reasons',0)+1
                    else:errors.append('reasons destination changed during reconciliation')
                else:summary['preserved']-=1;summary['planned']+=1
        for r,c in pending:
            if (r,c) not in values:continue
            check_date(day)
            literal=values[(r,c)]
            if apply:
                if sheet.put_blank(a1(r,c),literal):
                    summary['written']+=1;summary['missing']+=literal==DASH
                else:summary['preserved']+=1
            else:
                summary['planned']+=1;summary['missing']+=literal==DASH
    return errors


def sync_report_reasons(browser,branch,day,payload):
    """Use the report's already validated exact-day dataset before native export."""
    from types import SimpleNamespace
    from .io import RevenueSheet
    from .core import target_candidates
    from .extended_core import bi_reasons
    from .run import check_date
    check_date(day)
    result=bi_reasons(payload,'Списания %')
    if getattr(result,'unmapped',[]):raise StructureError('Unmapped report reason; summary not refreshed')
    sheet=RevenueSheet(browser,branch,day)
    try:
        rows=sheet.refresh()
        found=[(r,h) for r,h in target_candidates(rows,day,branch.newest_first)
               if sheet.is_reporting_date(a1(r,0),day)]
        if len(found)!=1:raise StructureError('Report reason date missing or ambiguous')
        row,header=found[0]
        sheet.allowed=set()
        date=a1(row,0);sheet.anchors={date:sheet.anchor_value(date)}
        summary={'written':0,'preserved':0,'missing':0,'planned':0}
        frozen=SimpleNamespace(read=lambda kind,b,d:result)
        errors=fill_extra(sheet,frozen,branch,day,header,row,rows,True,summary,check_date,kinds=('reasons',))
        if errors:raise StructureError('Report reason reconciliation incomplete')
        return summary
    finally:sheet.page.close()
