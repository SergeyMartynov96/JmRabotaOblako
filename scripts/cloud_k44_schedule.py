"""K44 end-of-day facts; preview cannot activate writes before 16:00."""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo


def resolve(event, operation='preview', requested_at=None, scope='К44'):
    if scope not in ('К44', 'Х76'):
        raise ValueError('Invalid evening scope')
    if event == 'schedule':
        operation = 'apply'
    elif event != 'workflow_dispatch' or operation not in ('preview', 'apply', 'verify'):
        raise ValueError('Invalid K44 operation')
    requested_at = requested_at or dt.datetime.now(dt.timezone.utc)
    if isinstance(requested_at, str):
        requested_at = dt.datetime.fromisoformat(requested_at.replace('Z', '+00:00'))
    if requested_at.tzinfo is None:
        raise ValueError('Timezone required')
    local = requested_at.astimezone(ZoneInfo('Asia/Yekaterinburg'))
    date = local.date().isoformat()
    # A delayed native cron after midnight must not open tomorrow's business window.
    # No receipt or completion pulse is issued for this verification-only pass.
    if event == 'schedule' and local.time() < dt.time(16):
        operation = 'verify'
    if scope == 'Х76' and operation == 'apply' and date < '2026-11-18':
        operation = 'verify'  # Not a completed business pass: no receipt or pulse.
    return dict(operation=operation, slots='09:00,10:00,11:00,12:00,13:00,14:00,15:00,16:00',
                deadline='', allow_empty='false', start='16:00' if operation == 'apply' else '',
                expected_date=date, window_key=date+'-endofday', window='k44' if scope == 'К44' else 'x76', include_sent='true')


if __name__ == '__main__':
    metadata = json.loads(open('/tmp/lifemart-run.json').read())
    config = resolve(os.environ['EVENT_NAME'], os.getenv('INPUT_OPERATION') or 'preview', metadata['created_at'], os.getenv('LIFEMART_SCOPE', 'К44'))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        for k,v in config.items():
            out.write(f'{k}={v}\n')
