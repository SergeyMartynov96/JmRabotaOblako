"""At most one fresh-session retry for network-only failures; writes stay blank-only."""
import argparse,json,os,re,subprocess,sys,tempfile
from pathlib import Path
from .config import BRANCHES
from .run import reporting_day


def network_only(summary,day):
    errors=summary.get('errors')
    return (summary.get('date')==day.isoformat() and isinstance(errors,list) and bool(errors)
            and all(isinstance(error,str) and re.search(
                r'source failed \((TimeoutError|URLError|ConnectionError)\)$',error) for error in errors))


def run_with_retry(operation,branch):
    day=reporting_day()
    with tempfile.TemporaryDirectory(prefix='lifemart-metrics-') as directory:
        status=Path(directory)/'status.json'
        env={**os.environ,'METRICS_STATUS_FILE':str(status)}
        command=[sys.executable,'-m','daily_metrics.run','--operation',operation,'--branch',branch]
        for attempt in range(2):
            if reporting_day()!=day:
                print('Reporting day changed; retry stopped.',flush=True)
                return 1
            status.unlink(missing_ok=True)
            print(f'Metrics pass {attempt+1}/2; filled destinations are preserved.',flush=True)
            code=subprocess.run(command,env=env).returncode
            if code==0:return 0
            try:summary=json.loads(status.read_text())
            except (OSError,ValueError):return code
            if attempt==1 or not network_only(summary,day):return code
            print('Transient source failure; retrying pending destinations in a fresh session.',flush=True)
    return 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=('preview','apply'),default='preview')
    parser.add_argument('--branch',choices=['all']+[b.name for b in BRANCHES],default='all')
    args=parser.parse_args()
    raise SystemExit(run_with_retry(args.operation,args.branch))

if __name__=='__main__':main()
