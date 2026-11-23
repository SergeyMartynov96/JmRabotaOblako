"""Retry collection once for network failures, never after a delivery attempt."""
import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
from daily_metrics.config import BRANCHES
from daily_metrics.run import reporting_day


def run_with_retry(operation,branch,receipts_dir=None,retry_branch=None):
    receipts=Path(receipts_dir or 'telegram-receipts')
    day=reporting_day()
    with tempfile.TemporaryDirectory(prefix='lifemart-reports-') as directory:
        status=Path(directory)/'status.json'
        env={**os.environ,'REPORT_STATUS_FILE':str(status)}
        command=[sys.executable,'-m','telegram_reports.run','--operation',operation,'--branch',branch]
        if retry_branch:command+=['--retry-branch',retry_branch]
        for attempt in range(2):
            if reporting_day()!=day:
                print('Report day changed; collection retry stopped.',flush=True)
                return 1
            status.unlink(missing_ok=True)
            print(f'Report collection pass {attempt+1}/2.',flush=True)
            code=subprocess.run(command,env=env).returncode
            if code==0:return 0
            # An attempted marker is written BEFORE contacting Telegram. Even an
            # uncertain send outcome must never trigger an automatic re-send.
            if any(receipts.glob('*.json')):return code
            try:result=json.loads(status.read_text())
            except (OSError,ValueError):return code
            if attempt==1 or result.get('retryable') is not True:return code
            print('Transient collection failure before delivery; retrying in a fresh session.',flush=True)
    return 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=('preview','deliver','verify'),default='preview')
    parser.add_argument('--branch',choices=['all']+[b.name for b in BRANCHES],default='all')
    parser.add_argument('--retry-branch',choices=['none','all']+[b.name for b in BRANCHES],default='none')
    args=parser.parse_args()
    raise SystemExit(run_with_retry(args.operation,args.branch,retry_branch=None if args.retry_branch=='none' else args.retry_branch))

if __name__=='__main__':main()
