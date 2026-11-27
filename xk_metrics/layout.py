"""Resolve each weekly block inside its own fixed horizontal store boundary."""
from daily_metrics.core import StructureError,value,date_matches
from daily_metrics.extended_core import key
REASONS={'закуп':'срокгодностизакуп','срокзакуп':'срокгодностизакуп',
         'цех':'цеховоепитание','цеховое':'цеховоепитание',
         'расходка':'расходныематериалы','миностатки':'срокгодностиминостатки',
         'минсумма':'срокгодностиминсумма','маркетинг':'маркетинг',
         'порча':'порча','обучение':'обучение','дегустация':'дегустация'}
FIELDS={'revenue':(3,'выручка'),'writeoff':(4,'списания'),'uv':(6,'ув'),
        'osa11':(7,'osa1100'),'osa20':(8,'osa2000'),
        'food':(9,'топ30готовая'),'dessert':(10,'топ10десерты'),
        'japan':(11,'топ15япония'),'procurement':(14,'закупфакт'),
        'invoices':(17,'понакладным')}

def candidates(rows,store,day):
    o=store.offset
    result=[]
    for h in range(1,len(rows)-1):
        if key(value(rows,h-1,o+1))!=store.marker:continue
        if any(key(value(rows,h,o+c))!=label for c,label in FIELDS.values()):
            raise StructureError('Store metric headers changed')
        for r in range(h+1,min(h+8,len(rows))):
            if date_matches(value(rows,r,o+1),day,allow_short=True):result.append((r,h))
    return result

def targets(rows,store,row,header,day):
    o=store.offset
    result={name:(row,o+c) for name,(c,_) in FIELDS.items()}
    rh=header+11
    if key(value(rows,rh,o+2))!='план':raise StructureError('Lower weekly header changed')
    lower=rh+1+day.weekday()
    if key(value(rows,lower,o+1))!=('пн','вт','ср','чт','пт','сб','вс')[day.weekday()]:
        raise StructureError('Lower weekday changed')
    result['lower_revenue']=(lower,o+7)
    reasons={}
    for c in range(o+12,o+24):
        label=key(value(rows,rh,c))
        if label in REASONS:reasons[REASONS[label]]=(lower,c)
    if not reasons:raise StructureError('Store reason headers missing')
    return result,reasons,rh
