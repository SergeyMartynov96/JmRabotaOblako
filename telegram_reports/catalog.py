"""Resolve only exact approved chart titles, then verify saved query metadata."""
from daily_metrics.core import StructureError
from .core import saved_chart,constrained_query,validate_data,validate_scope

CHARTS={
 'reasons':(241,'Списания. Причины. Период, %'),
 'top_qty':(241,'Списания. Категории. ТОП-блюда, шт'),
 'top_rub':(241,'Списания. Категории. ТОП-блюда, руб'),
 'ui2_days':(241,'Списания % от выбранного блюда, категории'),
 'ui2':(241,'Списания % от выбранного блюда, УИ2'),
 'uv_dishes':(37,'Упущенная выручка. Блюда. Таблица_Топы (мета). 2.0'),
 'uv_stop':(37,'Упущенная выручка. Cтоп-статус, % 3.1.'),
 'osa_all':(56,'OSA % по in_stock'),
 'osa_food_japan':(56,'OSA % по in_stock Готовая еда + Япония'),
 'osa_food':(56,'OSA % по in_stock Готовая еда'),
 'osa_japan':(56,'OSA % по in_stock Япония'),
}


def normalized(title):return ' '.join(title.split())

class ReportCharts:
    def __init__(self,bi):self.bi=bi;self.catalogs={};self.templates={}
    def template(self,kind):
        if kind not in self.templates:
            dashboard,title=CHARTS[kind]
            if dashboard not in self.catalogs:
                self.catalogs[dashboard]=self.bi.api(f'/api/v1/dashboard/{dashboard}/charts')['result']
            matches=[c for c in self.catalogs[dashboard] if normalized(c.get('slice_name',''))==normalized(title)]
            if len(matches)!=1:raise StructureError('Approved chart missing or ambiguous: '+kind)
            brief=matches[0]
            form=brief.get('form_data') or {}
            import json
            if isinstance(form,str):form=json.loads(form)
            ident=brief.get('id') or brief.get('slice_id') or form.get('slice_id')
            if type(ident) is not int:raise StructureError('Approved chart identifier missing: '+kind)
            detail=self.bi.api(f'/api/v1/chart/{ident}')['result']
            if normalized(detail.get('slice_name',''))!=normalized(title):raise StructureError('Chart identity changed: '+kind)
            if not detail.get('query_context'):
                from .render import query_from_renderer
                detail['query_context']=query_from_renderer(self.bi,form)
            detail['id']=ident
            self.templates[kind]=saved_chart(detail)
        return self.templates[kind]
    def read(self,kind,branch,start,end):
        form,saved=self.template(kind)
        query=constrained_query(form,saved,branch,start,end,CHARTS[kind][0],osa=kind.startswith('osa_'))
        form=query['form_data']
        if kind in ('top_qty','top_rub','ui2','uv_dishes'):
            # Match the selected UI sorting before capturing its first visible page.
            needles={'top_qty':'Списания, шт','top_rub':'Списания, руб','ui2':'Списания, руб.','uv_dishes':'УВ. руб'}
            needle=needles[kind]
            metrics=query['queries'][0].get('metrics',[])
            candidates=[m for m in metrics if normalized(m.get('label','') if isinstance(m,dict) else m).rstrip('.')==normalized(needle).rstrip('.')]
            if len(candidates)!=1:raise StructureError('Report sort metric missing: '+kind)
            metric=candidates[0]
            query['queries'][0]['orderby']=[[metric,False]]
            # Table renderer also sorts client-side using the displayed column index.
            groupby=form.get('groupby',[])
            labels=[m.get('label') if isinstance(m,dict) else m for m in metrics]
            form['order_desc']=True
            form['order_by_cols']=[__import__('json').dumps([needle,False])]
            form['order_by']= [[len(groupby)+labels.index(metric.get('label') if isinstance(metric,dict) else metric),'desc']]
        if kind=='uv_dishes':form['page_length']=200
        if kind=='uv_stop':form['bar_stacked']=False
        payload=self.bi.api('/api/v1/chart/data',query)
        validate_data(payload)
        validate_scope(payload['result'][0],start,end)
        return form,payload

    def read_stop(self,branch,start,end):
        import copy,json,urllib.parse
        from daily_metrics.extended_io import BI
        form,_=self.template('uv_stop');form=copy.deepcopy(form)
        form.update({'time_range':f'{start.isoformat()} : {end.isoformat()}','time_grain_sqla':'P1D','bar_stacked':False})
        form['adhoc_filters']=form.get('adhoc_filters',[])+[{'expressionType':'SIMPLE','subject':'title','operator':'IN',
            'comparator':[branch.title],'clause':'WHERE','isExtra':True,'isNew':False}]
        form['extra_form_data']={}
        response=self.bi.context.request.get(BI+'/superset/explore_json/?'+urllib.parse.urlencode({'form_data':json.dumps(form,ensure_ascii=False),'force':'true'}),timeout=180000)
        if response.status!=200:raise StructureError('BI stop-status HTTP '+str(response.status))
        payload=response.json()
        validate_scope(payload,start,end)
        if payload.get('error') or not payload.get('data'):raise StructureError('BI stop-status data unavailable')
        return form,{'legacy_payload':payload}
