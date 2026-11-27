"""Read only exact stores/dates; no changes to the existing three-store adapters."""
import dataclasses
import urllib.parse
from decimal import Decimal
from daily_metrics.session import ReportSUReports
from daily_metrics.io import REVENUE,csv_sheet
from daily_metrics.core import Report,StructureError,DASH,value,date_matches,number
from daily_metrics.extended_io import ExtraSources,CHARTS
from daily_metrics.extended_core import daily_query,bi_percent,bi_rows,key,percentage_points

class Client(ReportSUReports):
    def revenue(self,branch,day):
        if day not in self.revenue_cache:
            self.ensure_authenticated()
            self.revenue_cache[day]=self.request(REVENUE,{'department[]':['140','401'],
                'brandId':'','format':'1','date_s':day.strftime('%d.%m.%Y'),
                'date_do':day.strftime('%d.%m.%Y'),'day_of_week[]':['all'],'night':'2'})
        return self.revenue_cache[day]


def resolve_branch(client,store,day):
    doc=Report();doc.feed(client.revenue(store.branch,day))
    tokens=('харьков',) if store.branch.name=='Х76' else ('атриум','куйбыш')
    titles={heading.strip() for heading,rows in doc.tables if any(t in heading.casefold() for t in tokens)}
    if len(titles)!=1:raise StructureError('Requested store revenue title missing or ambiguous')
    title=titles.pop()
    print(store.branch.name+': verified source store title='+title,flush=True)
    return dataclasses.replace(store.branch,title=title)


def read_tops(store,day):
    rows=csv_sheet(store.branch.tops_book,store.tops_gid)
    groups=((1,3,33),(38,40,50),(54,56,66)) if store.branch.name=='Х76' else ((0,2,32),(35,37,47),(50,52,67))
    output=[]
    for header,start,end in groups:
        cols=[c for c in range(1,len(rows[header])) if date_matches(value(rows,header,c),day,allow_short=True)]
        if len(cols)!=1:raise StructureError('Tops reporting date missing or ambiguous')
        c=cols[0]+1
        expected_time='19:00' if store.branch.name=='Х76' else '18:00'
        if value(rows,header+1,c)!=expected_time:raise StructureError('Tops evening time changed')
        if not all(value(rows,r,0) for r in range(start,end)):raise StructureError('Tops product block changed')
        entries=[value(rows,r,c) for r in range(start,end)]
        if not any(entries):raise StructureError('Tops evening marks not filled')
        if any(v not in ('','0','1') for v in entries):raise StructureError('Invalid tops marks')
        total=number(value(rows,end,c))
        if not 0<=total<=100:raise StructureError('Tops percentage outside range')
        output.append(str(total))
    return output


class Sources(ExtraSources):
    def osa_pair(self,branch,day):
        form,context=self.bi.template('osa')
        query=daily_query(context,day,branch.title)
        q=query['queries'][0]
        labels=['OSA "На 11:00"','OSA "На 20:00"']
        metrics=[m for m in q.get('metrics',[]) if (m.get('label') if isinstance(m,dict) else m) in labels]
        if len(metrics)!=2:raise StructureError('OSA 11/20 metric missing')
        q['metrics']=metrics;q['post_processing']=[]
        query['form_data']={**form,'dashboardId':CHARTS['osa'][0],
            'time_range':q['time_range'],'time_grain_sqla':'P1D',
            'extra_form_data':{'time_range':q['time_range'],'filters':[{'col':'title','op':'IN','val':[branch.title]}]}}
        payload=self.bi.api('/api/v1/chart/data',query)
        result=[bi_percent(payload,label,branch.title,day,'osa') for label in labels]
        if any(v is None for v in result):raise StructureError('OSA day unavailable')
        return result

    def reasons(self,branch,day):
        # Exact separate labels: training is not silently added to tasting here.
        result={}
        for row in bi_rows(self.bi.read('reasons',branch,day)):
            if 'reason' not in row or 'Списания %' not in row:raise StructureError('BI reason columns missing')
            name=key(row['reason'])
            if name in result:raise StructureError('Duplicate BI reason')
            amount=percentage_points(row['Списания %'])
            if amount is not None:result[name]=amount
        return result
