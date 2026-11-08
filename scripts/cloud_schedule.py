"""Resolve GitHub cron events to the approved local sending windows."""
import os

WINDOWS = {
    '30 3 * * *': ('09:00,10:00', '09:00'),
    '30 5 * * *': ('11:00,12:00,13:00', '11:00'),
    '0 9 * * *': ('15:00,16:00', '15:00'),
}


def resolve(event, schedule='', operation='preview', slots='15:00,16:00'):
    if event == 'schedule':
        if schedule not in WINDOWS:
            raise ValueError('Unknown scheduled window')
        slots, deadline = WINDOWS[schedule]
        return dict(operation='apply', slots=slots, deadline=deadline, allow_empty='true')
    if event != 'workflow_dispatch' or operation not in ('preview', 'apply'):
        raise ValueError('Unsupported event or operation')
    return dict(operation=operation, slots=slots, deadline='', allow_empty='false')


if __name__ == '__main__':
    config = resolve(os.environ['EVENT_NAME'], os.getenv('EVENT_SCHEDULE', ''),
                     os.getenv('INPUT_OPERATION') or 'preview',
                     os.getenv('INPUT_SLOTS') or '15:00,16:00')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in config.items():
            if '\n' in value or '\r' in value:
                raise ValueError('Invalid workflow input')
            output.write(f'{key}={value}\n')
