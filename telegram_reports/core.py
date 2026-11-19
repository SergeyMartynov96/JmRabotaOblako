"""Read-only report periods and saved BI query constraints."""
import copy,datetime as dt,json
from daily_metrics.core import StructureError
from daily_metrics.extended_core import daily_query,bi_rows

OSA_METRICS=('OSA "На 11:00"','OSA "На 20:00"')


def weekly_period(today):
    end=today
    start=today-dt.timedelta(days=today.weekday() or 7)
    return start,end


def constrained_query(form,saved,branch,start,end,dashboard,osa=False):
    if end<=start:raise StructureError('Empty report period')
    query=daily_query(saved,start,branch.title)
    period=f'{start.isoformat()} : {end.isoformat()}'
    for q in query['queries']:
        q['time_range']=period
        if osa:
            metrics=[m for m in q.get('metrics',[]) if (m.get('label') if isinstance(m,dict) else m) in OSA_METRICS]
            if len(metrics)!=2:raise StructureError('OSA report requires exactly 11:00 and 20:00')
            q['metrics']=metrics
            q['orderby']=[[OSA_METRICS[0],False]]
            # Preserve native chronological pivot ordering, selecting only the two
            # required snapshots in both the query and its post-processing.
            for operation in q.get('post_processing',[]):
                if operation.get('operation')=='pivot':
                    aggregates=operation.get('options',{}).get('aggregates')
                    if aggregates is not None:
                        operation['options']['aggregates']={k:v for k,v in aggregates.items() if k in OSA_METRICS}
    display=copy.deepcopy(form)
    display.update({'dashboardId':dashboard,'time_range':period,'time_grain_sqla':'P1D',
        'extra_form_data':{'time_range':period,'filters':[{'col':'title','op':'IN','val':[branch.title]}]}})
    if osa:display['metrics']=query['queries'][0]['metrics']
    query['form_data']=display
    return query


def saved_chart(detail):
    form=detail.get('form_data') or detail.get('params') or {}
    context=detail.get('query_context')
    if isinstance(form,str):form=json.loads(form)
    if isinstance(context,str):context=json.loads(context)
    if not isinstance(form,dict) or not context:raise StructureError('Saved chart query missing')
    form={**form,'slice_id':detail['id']}
    return form,context


def validate_data(payload):
    # ECharts may receive a pivoted result; the API must still report a real successful result.
    rows=bi_rows(payload)
    if not rows:raise StructureError('Report returned no data; blank image not permitted')
    return rows


def validate_scope(result,start,end):
    """Require the BI server itself to confirm the receiving store filter and dates."""
    if result.get('error') or result.get('errors') or result.get('status')!='success':
        raise StructureError('BI report was not successful')
    if result.get('rejected_filters') or not any(f.get('column')=='title' for f in result.get('applied_filters',[])):
        raise StructureError('BI did not apply the selected store filter')
    for field,date in (('from_dttm',start),('to_dttm',end)):
        expected=dt.datetime.combine(date,dt.time(),dt.timezone.utc).timestamp()*1000
        if result.get(field)!=expected:raise StructureError('BI returned a different report period')
