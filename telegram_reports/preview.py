"""Private visual proof; only encrypted files leave the Actions runner."""
import json,os,shutil,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from daily_metrics.io import SUReports
from daily_metrics.config import BRANCHES
from daily_metrics.extended_io import BIReports
from daily_metrics.run import reporting_day
from daily_metrics.source_probe import safe_error
from .core import constrained_query,validate_data
from .render import capture_chart
from .security import seal_images
from .session import ReportSUReports
from .catalog import ReportCharts
from .core import weekly_period


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':raise ValueError('Actions only')
    public=os.environ['REVIEW_PUBLIC_KEY']
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
                client=ReportSUReports();client.ensure_authenticated()
                bi=BIReports(browser,client)
                try:
                    bi.authenticate();charts=ReportCharts(bi);day=reporting_day()
                    start,end=weekly_period(day+__import__('datetime').timedelta(days=1))
                    for kind in ('uv_stop','osa_food_japan'):
                        form,payload=(charts.read_stop(BRANCHES[1],start,end) if kind=='uv_stop' else charts.read(kind,BRANCHES[1],start,end))
                        (folder/(kind+'.json')).write_text(json.dumps({'form':form,'payload':payload},ensure_ascii=False))
                        capture_chart(bi,form,payload,folder/(kind+'.png'),width=1800 if kind=='uv_stop' else 900,height=500)
                        print(json.dumps({'source':kind,'native_chart_captured':True}),flush=True)
                finally:bi.close();browser.close()
        finally:seal_images(folder,public,'review.enc.json')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'preview_error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
