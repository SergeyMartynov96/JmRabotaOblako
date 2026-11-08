"""Optional completion pulse, sent only after a verified production window."""
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

WINDOWS = ('morning', 'midday', 'afternoon')


def validate(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'hc-ping.com'
            or not re.fullmatch(r'/[0-9a-f-]{36}', parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError('Invalid completion monitor URL')
    return url


def cronitor_url(window, token, environment='production'):
    if window not in WINDOWS or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', token):
        raise ValueError('Invalid Cronitor completion configuration')
    if environment not in ('production', 'setup-test'):
        raise ValueError('Invalid monitor environment')
    # A heartbeat is a completed-window confirmation, not a workflow-start event.
    query = urllib.parse.urlencode({'state':'tick', 'env':environment})
    return f'https://cronitor.link/p/{token}/lifemart-{window}?{query}'


def cronitor_ping(window, token, environment='production'):
    request = urllib.request.Request(cronitor_url(window, token, environment), data=b'', method='POST')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError('Completion monitor rejected pulse')
            print('Cronitor acknowledged setup-test probe.' if environment == 'setup-test'
                  else 'Cronitor acknowledged verified-window completion pulse.')
            return
        except (OSError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('Completion pulse could not be delivered') from None
            time.sleep(attempt + 1)


def ping(window):
    if window not in WINDOWS:
        raise ValueError('Invalid monitor window')
    token = os.getenv('CRONITOR_TELEMETRY_KEY', '')
    if token:
        cronitor_ping(window, token)
        return
    url = os.getenv('HEALTHCHECK_' + window.upper(), '')
    if not url:
        print('::warning::Independent completion monitoring is not configured for this window.')
        return
    request = urllib.request.Request(validate(url), data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError('Completion monitor rejected heartbeat')
    print('Independent completion monitor acknowledged verified window.')


def verify_delivery():
    token = os.getenv('CRONITOR_TELEMETRY_KEY', '')
    if not token:
        raise RuntimeError('Cronitor telemetry secret is missing')
    for window in WINDOWS:
        cronitor_ping(window, token, 'setup-test')


if __name__ == '__main__':
    import sys
    try:
        if sys.argv[1:] == ['--verify']:
            verify_delivery()
        elif not sys.argv[1:]:
            ping(os.environ['WINDOW'])
        else:
            raise ValueError('Unknown monitor operation')
    except Exception as exc:
        print('FAIL: completion monitor; ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
