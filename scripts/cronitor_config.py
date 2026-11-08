"""Three completion heartbeat monitors; identifiers and schedules are not credentials."""

def monitors():
    return [dict(key='lifemart-'+window, name='LifeMart '+label+' — confirmed completion',
                 type='heartbeat', schedules=[cron], timezone='Asia/Yekaterinburg',
                 grace_seconds=900, schedule_tolerance=0, failure_tolerance=0,
                 notify=['default'], paused=True,
                 note='Success pulse is sent only after saved requests and spreadsheet facts are verified. '
                      'Missing pulse is not proof of a purchase failure; inspect the GitHub run.')
            for window,label,cron in [('morning','08:30','30 8 * * *'),
                                       ('midday','10:30','30 10 * * *'),
                                       ('afternoon','14:00','0 14 * * *')]]
