"""Compatibility import for the shared bounded accounting session."""
import time
from daily_metrics.session import ReportSUReports


def report_bi(browser,client):
    """Use the normal Chrome login only when the HTTP login transport times out."""
    import ssl,urllib.error
    from daily_metrics.extended_io import BIReports
    browser_login=False
    try:client.ensure_authenticated()
    except urllib.error.HTTPError:raise
    except (TimeoutError,urllib.error.URLError) as exc:
        if isinstance(getattr(exc,'reason',None),ssl.SSLCertVerificationError):raise
        browser_login=True
        print('SU: HTTP login transport unavailable; using the normal active Chrome sign-in page',flush=True)
    from .render import prepare_native_page
    bi=BIReports(browser,client,browser_login=browser_login)
    bi.prepare_authenticated_page=lambda page,deadline:prepare_native_page(bi,page,deadline)
    return bi
