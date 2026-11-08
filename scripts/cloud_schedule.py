"""Resolve native and external triggers to the same protected purchase window."""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Yekaterinburg')
WINDOW_CONFIG = {
    'morning': dict(cron='30 3 * * *', start='08:30', slots='09:00,10:00', deadline='09:00'),
    'midday': dict(cron='30 5 * * *', start='10:30', slots='11:00,12:00,13:00', deadline='11:00'),
    'afternoon': dict(cron='0 9 * * *', start='14:00', slots='15:00,16:00', deadline='15:00'),
}
WINDOWS = {x['cron']: (x['slots'], x['deadline']) for x in WINDOW_CONFIG.values()}


def resolve(event, schedule='', operation='preview', slots='15:00,16:00',
            external_window='none', requested_at=None):
    key = None
    if event == 'schedule':
        key = next((k for k, v in WINDOW_CONFIG.items() if v['cron'] == schedule), None)
        if key is None:
            raise ValueError('Unknown scheduled window')
    elif event != 'workflow_dispatch' or operation not in ('preview', 'apply', 'verify', 'monitor-verify'):
        raise ValueError('Unsupported event or operation')
    elif external_window != 'none':
        if external_window not in WINDOW_CONFIG or operation != 'apply':
            raise ValueError('External trigger must select an approved apply window')
        key = external_window
    if key:
        config = WINDOW_CONFIG[key]
        requested_at = requested_at or dt.datetime.now(dt.timezone.utc)
        if isinstance(requested_at, str):
            requested_at = dt.datetime.fromisoformat(requested_at.replace('Z', '+00:00'))
        if requested_at.tzinfo is None:
            raise ValueError('Trigger timestamp requires timezone')
        date = requested_at.astimezone(TZ).date().isoformat()
        return dict(operation='apply', slots=config['slots'], deadline=config['deadline'],
                    allow_empty='true', start=config['start'], expected_date=date,
                    window_key=date + '-' + key, window=key)
    return dict(operation=operation, slots=slots, deadline='', allow_empty='false',
                start='', expected_date='', window_key='', window='')


if __name__ == '__main__':
    metadata = json.loads(open('/tmp/lifemart-run.json').read())
    config = resolve(os.environ['EVENT_NAME'], os.getenv('EVENT_SCHEDULE', ''),
                     os.getenv('INPUT_OPERATION') or 'preview',
                     os.getenv('INPUT_SLOTS') or '15:00,16:00',
                     os.getenv('INPUT_EXTERNAL_WINDOW') or 'none', metadata['created_at'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in config.items():
            if '\n' in value or '\r' in value:
                raise ValueError('Invalid workflow input')
            output.write(f'{key}={value}\n')
