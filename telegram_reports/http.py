"""Bounded retries for the report's existing, explicitly allowed GET sources."""
import time,urllib.parse
from daily_metrics.config import BRANCHES
from daily_metrics.core import StructureError


def report_get(context,url,*,timeout=180000):
    target=urllib.parse.urlsplit(url)
    allowed=(target.scheme=='https' and not target.username and not target.password and (
        (target.netloc=='su.lifemart.ru' and target.path=='/turnover/waybill/report') or
        (target.netloc=='bi.lifemart.ru' and target.path in ('/superset/explore/','/superset/explore_json/')) or
        (target.netloc=='docs.google.com' and target.path in {
            '/spreadsheets/d/'+branch.book+'/export' for branch in BRANCHES})))
    if not allowed:raise StructureError('Unapproved report source read')
    deadline=time.monotonic()+min(timeout,180000)/1000
    for attempt in range(3):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ConnectionError('Report source transport deadline exhausted')
        try:
            response=context.request.get(url,timeout=max(1,int(remaining*1000)))
            if response.status in (429,500,502,503,504):
                if attempt==2:raise ConnectionError('Report source temporarily unavailable')
                print('Report source: retrying a temporary server response within the three-minute limit',flush=True)
                continue
            if response.status!=200:raise StructureError('Report source HTTP '+str(response.status))
            response.body()  # Buffer before returning; later validation uses these same bytes.
            return response
        except Exception as exc:
            message=str(exc).casefold()
            transport=(type(exc).__module__.startswith('playwright.') and
                (type(exc).__name__=='TimeoutError' or
                 (type(exc).__name__=='Error' and any(v in message for v in (
                    'etimedout','econnreset','econnrefused','enotfound','socket hang up',
                    'err_connection','err_timed_out','err_http2_protocol_error','connection closed','connection reset')))))
            if not transport:raise
            if attempt==2:raise ConnectionError('Report source transport unavailable') from None
            print('Report source: retrying a transient transport failure within the three-minute limit',flush=True)
    raise ConnectionError('Report source temporarily unavailable')
