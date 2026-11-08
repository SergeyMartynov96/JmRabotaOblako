"""Optional success heartbeat, sent only after verified window completion."""
import os
import re
import urllib.parse
import urllib.request


def validate(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'hc-ping.com'
            or not re.fullmatch(r'/[0-9a-f-]{36}', parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError('Invalid completion monitor URL')
    return url


def ping(window):
    if window not in ('morning', 'midday', 'afternoon'):
        raise ValueError('Invalid monitor window')
    url = os.getenv('HEALTHCHECK_' + window.upper(), '')
    if not url:
        print('::warning::Independent completion monitoring is not configured for this window.')
        return
    request = urllib.request.Request(validate(url), data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError('Completion monitor rejected heartbeat')
    print('Independent completion monitor acknowledged verified window.')


if __name__ == '__main__':
    import sys
    try:
        ping(os.environ['WINDOW'])
    except Exception as exc:
        print('FAIL: completion monitor; ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
