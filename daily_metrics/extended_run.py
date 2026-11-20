"""Blank-only transfer of yesterday's BI and invoice metrics."""
from .core import DASH,a1,money,same_cell_value
from .extended_core import extra_targets,percent_literal


def fill_extra(sheet,sources,branch,day,header,row,rows,apply,summary,check_date):
    layout=extra_targets(rows,header,row,day)
    targets={name:[layout[name]] for name in ('uv','osa','invoices')}
    targets['reasons']=[cell for cells in layout['reasons'].values() for cell in cells]
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
                # Missing categories never create zeros, dashes or replacement writes.
                values={(r,c):percent_literal(result[name],branch.name,1)
                        for name,locations in layout['reasons'].items() if name in result for r,c in locations}
            else:
                literal=DASH if result is None else (money(result) if metric=='invoices'
                    else percent_literal(result,branch.name,2 if metric=='uv' else 1))
                values={cells[0]:literal}
        except Exception as exc:
            errors.append(metric+' source failed ('+type(exc).__name__+')')
            continue
        if metric=='reasons' and getattr(result,'has_defect',False) and result.damage_base is not None:
            # Owner explicitly requested damage + defects. Correct only a verified
            # standalone damage scalar, preserving formulas and unrelated manual edits.
            old=percent_literal(result.damage_base,branch.name,1)
            merged=percent_literal(result['damage'],branch.name,1)
            for r,c in layout['reasons'].get('damage',[]):
                if (r,c) in pending or same_cell_value(old,merged):continue
                cell=a1(r,c);current=sheet.stable_formula(cell)
                if not same_cell_value(current,old):continue
                check_date(day)
                if apply:
                    if sheet.replace_if_unchanged(cell,old,merged):
                        summary['preserved']-=1;summary['written']+=1
                        summary['reconciled_damage']=summary.get('reconciled_damage',0)+1
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
