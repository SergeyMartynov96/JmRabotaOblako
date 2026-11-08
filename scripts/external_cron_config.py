"""Generate three disabled cron-job.org job templates, containing no credentials."""
import json

REPO = 'SergeyMartynov96/JmRabotaOblako'
URL = f'https://api.github.com/repos/{REPO}/actions/workflows/cloud-production.yml/dispatches'


def jobs():
    result = []
    for window, hour, minutes in [
        ('morning', 8, [30, 35, 40, 45, 50]),
        ('midday', 10, [30, 35, 40, 45, 50]),
        ('afternoon', 14, list(range(0, 55, 5))),
    ]:
        result.append({'job': {
            'title': 'LifeMart ' + window,
            'url': URL, 'enabled': False, 'saveResponses': False,
            'requestMethod': 1, 'requestTimeout': 25,
            'schedule': {'timezone': 'Asia/Yekaterinburg', 'expiresAt': 0,
                         'hours': [hour], 'minutes': minutes, 'mdays': [-1],
                         'months': [-1], 'wdays': [-1]},
            'extendedData': {'headers': {'Authorization': 'Bearer REPOSITORY_ACTIONS_TOKEN',
                                        'Content-Type': 'application/json',
                                        'Accept': 'application/vnd.github+json',
                                        'X-GitHub-Api-Version': '2022-11-28'},
                             'body': json.dumps({'ref': 'main', 'inputs': {
                                 'operation': 'apply', 'external_window': window}})},
        }})
    return result


if __name__ == '__main__':
    print(json.dumps(jobs(), ensure_ascii=False, indent=2))
