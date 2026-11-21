"""Additional daily metrics and exact reason-to-column mapping."""
import datetime as dt
from decimal import Decimal,ROUND_HALF_UP
import re
from .core import StructureError,text,value,date_matches

REASON_ALIASES={
    'sg_purchase':('сг закуп','срок годности закуп'),
    'workshop':('цех','цеховое питание'),
    'consumables':('расходка','расходные материалы'),
    'damage':('порча','брак'),
    'marketing':('маркетинг',),
    'tasting':('дегустация','обучение'),
    'sg_rotation':('сг ротация','срок годности ротация'),
    'sg_turnover':('сг товароб','сг товарооборот','срок годности товарооборот','срок годности товароб'),
    'sg_min_sum':('сг мин сум','срок годности мин сумма','срок годности минимальная сумма'),
    'sg_manual':('сг ручное','срок годности ручное','срок годности ручное редактирование','сг ручное редактирование'),
    'sg_min_stock':('сг мин ост','срок годности мин остатки','срок годности минимальные остатки'),
    'sg_post':('сг пост','срок годности поставщик','срок годности пост'),
}

def key(raw):
    return re.sub(r'[^а-яa-z0-9]','',text(raw))


def reason_key(raw):
    normalized=key(raw)
    found=[name for name,aliases in REASON_ALIASES.items() if normalized in {key(alias) for alias in aliases}]
    if len(found)>1:raise StructureError('Reason alias ambiguous')
    return found[0] if found else None


def percent_literal(points,branch_name,places=None):
    n=Decimal(str(points))
    if not n.is_finite():raise StructureError('Non-finite percentage')
    if places is not None:n=n.quantize(Decimal(10)**-places,rounding=ROUND_HALF_UP)
    raw=format(n,'f').replace('.',',')
    return raw+'%' if branch_name=='М42' else raw


def reason_literal(points,branch_name):
    """Skip causes that display as zero; retain the parser's original data."""
    literal=percent_literal(points,branch_name,1)
    return literal if Decimal(literal.rstrip("%").replace(",",".")) else None


def extra_targets(rows,header,day_row,day):
    fields={}
    for name,match in (
        ('uv',lambda t:t in ('ув','упущенная','упущеннаявыручка')),
        ('osa',lambda t:t in ('osa2000','оса2000')),
        ('invoices',lambda t:t in ('понакладным','накладныефакт')),
    ):
        cols=[c for c in range(2,min(19,len(rows[header]))) if match(key(value(rows,header,c)))]
        if len(cols)!=1:raise StructureError(name+' target missing or ambiguous')
        fields[name]=(day_row,cols[0])
    end=min(len(rows),header+55)
    for r in range(header+8,end):
        if text(value(rows,r,2))=='выручка':end=r-1;break
    reasons={}
    date_anchors=set()
    for r in range(header+8,end):
        mapped=[(c,reason_key(v)) for c,v in enumerate(rows[r][:19]) if reason_key(v)]
        if not mapped:continue
        # A header may contain two repeated SG-purchase locations, one in each block.
        left=min(c for c,_ in mapped)
        dates=[(rr,c) for rr in range(r,min(r+9,end)) for c in range(left)
               if date_matches(value(rows,rr,c),day,allow_short=True)]
        if len(dates)!=1:raise StructureError('Reason date missing or ambiguous')
        rr,dc=dates[0];date_anchors.add((rr,dc))
        for col,name in mapped:reasons.setdefault(name,[]).append((rr,col))
    if set(reasons)!=set(REASON_ALIASES):raise StructureError('Incomplete reason column layout')
    fields['reasons']=reasons
    fields['date_anchors']=sorted(date_anchors)
    return fields


def daily_query(saved,day,title):
    """Constrain a saved BI read to exactly one store and one completed local day."""
    import copy
    query=copy.deepcopy(saved)
    queries=query.get('queries',[])
    if len(queries)!=1:raise StructureError('Expected one BI query')
    q=queries[0]
    q['time_range']=f'{day.isoformat()} : {(day+dt.timedelta(days=1)).isoformat()}'
    q['filters']=[f for f in q.get('filters',[]) if f.get('col')!='title']+[
        {'col':'title','op':'IN','val':[title]}]
    q.setdefault('extras',{})['time_grain_sqla']='P1D'
    for col in q.get('columns',[]):
        if isinstance(col,dict) and col.get('timeGrain'):col['timeGrain']='P1D'
    query['force']=True
    query['result_format']='json';query['result_type']='full'
    return query


def percentage_points(raw):
    from .core import number
    if raw is None:return None
    result=number(raw)*100
    if result<0:raise StructureError('Negative BI percentage')
    return result


def bi_rows(payload):
    results=payload.get('result',[])
    if len(results)!=1:raise StructureError('Unexpected BI result count')
    result=results[0]
    if result.get('error') or result.get('status') not in (None,'success'):
        raise StructureError('BI result failed')
    rows=result.get('data')
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise StructureError('BI table data unavailable')
    return rows


def bi_percent(payload,metric,title,day,kind):
    rows=bi_rows(payload)
    if not rows:return None
    if len(rows)!=1:raise StructureError('BI day or store not unique')
    row=rows[0]
    if 'title' in row and row['title']!=title:raise StructureError('Foreign BI store')
    if '__timestamp' in row:
        raw=row['__timestamp']
        try:
            observed=(dt.datetime.fromtimestamp(float(raw)/1000,dt.timezone.utc).date()
                      if isinstance(raw,(int,float)) else dt.datetime.fromisoformat(str(raw).replace('Z','+00:00')).date())
        except (ValueError,TypeError,OverflowError):raise StructureError('BI date unavailable') from None
        if observed!=day:raise StructureError('Foreign BI report date')
    if metric not in row:raise StructureError('BI metric missing')
    result=percentage_points(row[metric])
    if kind=='osa' and result is not None and result>100:raise StructureError('OSA outside 0..100')
    return result


class ReasonValues(dict):
    damage_base=None
    has_defect=False


def bi_reasons(payload,metric):
    values=ReasonValues();seen=set()
    for row in bi_rows(payload):
        if 'reason' not in row or metric not in row:raise StructureError('BI reason columns missing')
        raw=key(row['reason']);name=reason_key(row['reason'])
        if name is None:raise StructureError('Unmapped BI reason')
        if raw in seen:raise StructureError('BI reason duplicated')
        seen.add(raw)
        result=percentage_points(row[metric])
        if result is None:continue
        if name=='damage':
            values[name]=values.get(name,Decimal(0))+result
            if raw=='порча':values.damage_base=result
            if raw=='брак':values.has_defect=True
        elif name=='tasting':
            # Owner maps training into tasting, including days with both causes.
            values[name]=values.get(name,Decimal(0))+result
        else:
            if name in values:raise StructureError('BI reason duplicated')
            values[name]=result
    return values


def invoice_total(html,title,day,suppliers):
    from .core import Report,number
    if re.search(r'Waybill_page(?:=|%3D)[2-9]',html):
        raise StructureError('Invoice report paginated; partial total forbidden')
    doc=Report();doc.feed(html)
    found=[]
    for _,table in doc.tables:
        headers=[(i,row) for i,row in enumerate(table) if any(text(v)=='сумма с ндс' for v in row)]
        for hi,header in headers:
            def column(label):
                cols=[i for i,v in enumerate(header) if text(v)==label]
                if len(cols)!=1:raise StructureError('Invoice column missing or duplicated')
                return cols[0]
            amount=column('сумма с ндс');dep=column('подразделение')
            recipient=column('подразделение получения товара')
            received=column('дата и время приема товара');supplier=column('контрагент')
            values=[]
            for row in table[hi+1:]:
                # Footer sums have no department and must not be counted twice.
                if len(row)<=max(amount,dep,recipient,received,supplier):continue
                destination=row[recipient].strip() or row[dep].strip()
                if not destination:continue
                if text(destination)!=text(title):raise StructureError('Foreign invoice receiving department')
                if not row[received].startswith(day.strftime('%d.%m.%Y')):
                    raise StructureError('Foreign invoice receipt date')
                if text(row[supplier]) not in {text(name) for name in suppliers.values()}:
                    raise StructureError('Foreign invoice supplier')
                values.append(number(row[amount]))
            found.append(sum(values) if values else None)
    if len(found)!=1:raise StructureError('Invoice table missing or duplicated')
    return found[0]
