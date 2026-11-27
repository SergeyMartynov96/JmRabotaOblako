import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
from daily_metrics.run import reporting_day


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--operation',choices=['preview','deliver'],default='preview');parser.add_argument('--branch',choices=['all','Х76','К44'],default='all');args=parser.parse_args()
    day=reporting_day()
    with tempfile.TemporaryDirectory() as directory:
        status=Path(directory)/'status.json'
        for attempt in range(2):
            if reporting_day()!=day:raise SystemExit('Report date changed')
            status.unlink(missing_ok=True)
            code=subprocess.run([sys.executable,'-m','xk_reports.run','--operation',args.operation,'--branch',args.branch],env={**os.environ,'REPORT_STATUS_FILE':str(status)}).returncode
            if code==0:return
            if any(Path('telegram-receipts').glob('*.json')):raise SystemExit(code)
            try:retry=json.loads(status.read_text()).get('retryable') is True
            except (OSError,ValueError):retry=False
            if not retry or attempt:raise SystemExit(code)
            print('Retrying collection once in a fresh session before delivery',flush=True)

if __name__=='__main__':main()
