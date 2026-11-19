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


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':raise ValueError('Actions only')
    public=os.environ['REVIEW_PUBLIC_KEY']
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=shutil.which('google-chrome'))
                bi=BIReports(browser,SUReports())
                try:
                    form,saved=bi.template('reasons');day=reporting_day()
                    query=constrained_query(form,saved,BRANCHES[1],day,day+__import__('datetime').timedelta(days=1),241)
                    payload=bi.api('/api/v1/chart/data',query);rows=validate_data(payload)
                    print(json.dumps({'stage':'validated_data','row_count':len(rows),'columns':list(rows[0])}),flush=True)
                    (folder/'source.json').write_text(json.dumps({'form':query['form_data'],'payload':payload},ensure_ascii=False))
                    capture_chart(bi,query['form_data'],payload,folder/'reasons.png',width=900,height=500)
                    print('{"native_chart_captured":true}',flush=True)
                finally:bi.close();browser.close()
        finally:seal_images(folder,public,'review.enc.json')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'preview_error':safe_error(exc)}),flush=True)
        raise SystemExit(1) from None
