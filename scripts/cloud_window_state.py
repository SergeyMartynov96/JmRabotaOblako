"""Public status-only window receipts; no request IDs, prices or quantities."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import urllib.request


def valid_key(key):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-(morning|midday|afternoon)', key):
        raise ValueError('Invalid window key')
    dt.date.fromisoformat(key[:10])
    return key


def is_complete(key, artifacts, get_run, workflow_id):
    valid_key(key)
    for artifact in artifacts:
        if artifact['name'] != 'lifemart-av12-complete-' + key or artifact.get('expired', False):
            continue
        run = get_run(artifact['workflow_run']['id'])
        if (run.get('workflow_id') == workflow_id and run.get('status') == 'completed'
                and run.get('conclusion') == 'success' and run.get('head_branch') == 'main'
                and run.get('event') in ('schedule', 'workflow_dispatch')):
            return True
    return False


class GitHub:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', self.repo):
            raise ValueError('Invalid repository')
        self.token = os.environ['GH_TOKEN']

    def get(self, suffix):
        request = urllib.request.Request('https://api.github.com/repos/' + self.repo + suffix,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'LifeMart-window-receipts', 'X-GitHub-Api-Version': '2022-11-28'})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)

    def artifacts(self):
        page = 1
        while True:
            batch = self.get(f'/actions/artifacts?per_page=100&page={page}')['artifacts']
            yield from batch
            if len(batch) < 100:
                break
            page += 1


if __name__ == '__main__':
    import sys
    try:
        mode = sys.argv[1]
        key = os.getenv('WINDOW_KEY', '')
        if mode == 'check':
            completed = False
            if key:
                valid_key(key)
                metadata = json.loads(Path('/tmp/lifemart-run.json').read_text())
                github = GitHub()
                completed = is_complete(key, github.artifacts(),
                    lambda ident: github.get('/actions/runs/' + str(ident)), metadata['workflow_id'])
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('complete=' + str(completed).lower() + '\n')
            print('Window already complete; no application work needed.' if completed
                  else 'No successful receipt for this window; normal checks required.')
        elif mode == 'write':
            valid_key(key)
            Path('/tmp/lifemart-complete.json').write_text(json.dumps(dict(
                schema=1, scope='av12-facts', window_key=key, run_id=os.environ['GITHUB_RUN_ID'],
                completed_at=dt.datetime.now(dt.timezone.utc).isoformat())))
        else:
            raise ValueError('Invalid receipt operation')
    except Exception as exc:
        print('FAIL: window receipt check; ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
