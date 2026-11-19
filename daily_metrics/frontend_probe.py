"""Read one chart through the request built by the authorized dashboard UI."""
import json,urllib.parse
from .core import StructureError
from .extended_core import daily_query,bi_percent
from .extended_io import BI,CHARTS


def read_uv_from_frontend(reader,branch,day):
    reader.authenticate()
    context=reader.context;captured=[]
    def capture(route):
        request=route.request
        try:
            body=request.post_data_json or {}
            form=body.get('form_data') or {}
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)
            if query.get('form_data'):
                form={**form,**json.loads(query['form_data'][0])}
            if form.get('slice_id')==3770:
                captured.append((request.url,request.headers,body))
        finally:route.abort()
    context.route('**/api/v1/chart/data*',capture)
    page=context.new_page()
    try:
        page.goto(BI+'/superset/dashboard/37/',wait_until='domcontentloaded',timeout=180000)
        page.get_by_text(CHARTS['uv'][1],exact=True).first.scroll_into_view_if_needed(timeout=180000)
        for _ in range(40):
            if captured:break
            page.wait_for_timeout(250)
        if not captured:raise StructureError('UI did not issue the selected chart request')
        url,headers,body=captured[0]
        query=daily_query(body,day,branch.title)
        header_names=('authorization','x-csrftoken','content-type','referer','x-guesttoken')
        headers={k:v for k,v in headers.items() if k.lower() in header_names}
        response=context.request.post(url,data=query,headers=headers,timeout=180000)
        if response.status!=200:raise StructureError('UI chart read HTTP '+str(response.status))
        return bi_percent(response.json(),'УВ, % от общей выручки',branch.title,day,'uv')
    finally:
        page.close();context.unroute('**/api/v1/chart/data*',capture)
