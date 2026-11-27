import datetime as dt,unittest
from unittest.mock import patch
from pathlib import Path
from daily_cycle import run
from workflow_alerts.run import canonical

class Cycle(unittest.TestCase):
    def test_hours_and_stores(self):
        self.assertEqual(run.HOURS,{'morning':7,'reports':8,'suppliers':16})
        self.assertEqual([x[1] for x in run.STEPS['morning']],['АВ12, М42, Г34','Х76','К44'])
        self.assertEqual([x[1] for x in run.STEPS['suppliers']],['АВ12','К44','Х76'])
        self.assertEqual(run.STEPS['suppliers'][0][2]['slots'],'09:00,10:00,11:00,12:00,13:00,14:00,15:00,16:00')
    def test_date_and_time_guards(self):
        with self.assertRaises(RuntimeError):run.valid_day('2026-11-27T01:00:00Z',dt.datetime(2026,11,27,1,59,tzinfo=dt.timezone.utc),'morning')
        with self.assertRaises(RuntimeError):run.valid_day('2026-11-26T11:00:00Z',dt.datetime(2026,11,27,11,tzinfo=dt.timezone.utc),'suppliers')
        self.assertEqual(run.valid_day('2026-11-27T11:00:00Z',dt.datetime(2026,11,27,11,tzinfo=dt.timezone.utc),'suppliers'),dt.date(2026,11,27))
    def test_failed_morning_blocks_reports(self):
        row={'display_title':'LifeMart cycle morning','created_at':'2026-11-27T02:00:00Z','id':7,'status':'completed','conclusion':'failure'}
        with patch.object(run,'api',return_value={'workflow_runs':[row]}),patch.object(run,'checked_wait') as wait:
            with self.assertRaises(RuntimeError):run.morning_dependency(dt.date(2026,11,27))
            wait.assert_not_called()
    def test_latest_recovery_success_allows_reports(self):
        rows=[{'display_title':'LifeMart cycle morning','created_at':'2026-11-27T02:00:00Z','id':n,'status':'completed','conclusion':c} for n,c in [(1,'failure'),(2,'success')]]
        with patch.object(run,'api',return_value={'workflow_runs':rows}):run.morning_dependency(dt.date(2026,11,27))
    def test_uncertain_send_not_retried(self):
        from telegram_reports.transport import BotError
        import tempfile,os
        with tempfile.TemporaryDirectory() as folder,patch('daily_cycle.run.BotClient') as bot,patch('daily_cycle.run.decrypt_recipient',return_value=5),patch('daily_cycle.run.BINDING') as binding,patch('daily_cycle.run.Path',side_effect=lambda _:Path(folder)/'receipt.json'):
            binding.read_text.return_value='{}';bot.return_value.identity.return_value={'id':1};bot.return_value.api.side_effect=BotError('uncertain')
            with self.assertRaises(BotError):run.notify('status','morning',dt.date(2026,11,27))
            self.assertEqual(bot.return_value.api.call_count,1)
    def test_child_failure_not_duplicated_by_notifier(self):
        row={'path':'.github/workflows/daily-metrics.yml','head_branch':'main','head_repository':{'full_name':'a/b'},'event':'workflow_dispatch','conclusion':'failure','display_title':'metrics [cycle:123]'}
        self.assertFalse(canonical(row,'a/b'))
    def test_only_coordinator_has_business_crons(self):
        root=Path('.github/workflows')
        for file in [x[0] for steps in run.STEPS.values() for x in steps]:
            self.assertNotIn('  schedule:',(root/file).read_text())
        workflow=(root/'daily-cycle.yml').read_text()
        for cron in ['0 2 * * *','0 3 * * *','0 11 * * *']:self.assertIn("cron: '"+cron+"'",workflow)
        self.assertNotIn('  workflow_run:',(root/'telegram-report-timer.yml').read_text())
    def test_failed_child_stops_next_child_and_reports_once(self):
        import os
        own={'id':99,'created_at':'2026-11-27T11:00:00Z'}
        failed={'id':2,'conclusion':'failure','html_url':'https://github.com/a/b/actions/runs/2'}
        now=dt.datetime(2026,11,27,17,tzinfo=dt.timezone.utc)
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'99','GITHUB_REPOSITORY':'a/b'}),patch('sys.argv',['run','--stage','suppliers']),patch.object(run,'api',side_effect=[own,{'workflow_runs':[]}]),patch.object(run,'valid_day',return_value=dt.date(2026,11,27)),patch.object(run,'child',return_value=failed) as child,patch.object(run,'notify') as notify,patch('subprocess.run') as logs:
            logs.return_value.returncode=1
            with self.assertRaises(SystemExit):run.main()
            self.assertEqual(child.call_count,1);self.assertEqual(notify.call_count,1)
            self.assertIn('Остановились: АВ12',notify.call_args.args[0])
    def test_uncertain_success_status_never_sends_second_message(self):
        import os
        from telegram_reports.transport import BotError
        own={'id':99,'created_at':'2026-11-27T11:00:00Z'}
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'99','GITHUB_REPOSITORY':'a/b'}),patch('sys.argv',['run','--stage','suppliers']),patch.object(run,'api',side_effect=[own,{'workflow_runs':[]}]),patch.object(run,'valid_day',return_value=dt.date(2026,11,27)),patch.object(run,'child',return_value={'conclusion':'success'}),patch.object(run,'notify',side_effect=BotError('unknown')) as notify:
            with self.assertRaises(BotError):run.main()
            self.assertEqual(notify.call_count,1)
