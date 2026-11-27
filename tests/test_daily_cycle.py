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
        with patch.object(run,'completed_cycle',return_value=False),patch.object(run,'api',return_value={'workflow_runs':[row]}),patch.object(run,'checked_wait') as wait:
            with self.assertRaises(RuntimeError):run.morning_dependency(dt.date(2026,11,27))
            wait.assert_not_called()
    def test_latest_recovery_success_allows_reports(self):
        rows=[{'display_title':'LifeMart cycle morning','created_at':'2026-11-27T02:00:00Z','id':n,'status':'completed','conclusion':c} for n,c in [(1,'failure'),(2,'success')]]
        with patch.object(run,'completed_cycle',return_value=True):run.morning_dependency(dt.date(2026,11,27))
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
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'99','GITHUB_REPOSITORY':'a/b'}),patch('sys.argv',['run','--stage','suppliers']),patch.object(run,'api',return_value=own),patch.object(run,'completed_cycle',return_value=False),patch.object(run,'valid_day',return_value=dt.date(2026,11,27)),patch.object(run,'child',return_value=failed) as child,patch.object(run,'notify') as notify,patch('subprocess.run') as logs:
            logs.return_value.returncode=1
            with self.assertRaises(SystemExit):run.main()
            self.assertEqual(child.call_count,1);self.assertEqual(notify.call_count,1)
            self.assertIn('Остановились: АВ12',notify.call_args.args[0])
    def test_uncertain_success_status_never_sends_second_message(self):
        import os
        from telegram_reports.transport import BotError
        own={'id':99,'created_at':'2026-11-27T11:00:00Z'}
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'99','GITHUB_REPOSITORY':'a/b'}),patch('sys.argv',['run','--stage','suppliers']),patch.object(run,'api',return_value=own),patch.object(run,'completed_cycle',return_value=False),patch.object(run,'valid_day',return_value=dt.date(2026,11,27)),patch.object(run,'child',return_value={'conclusion':'success'}),patch.object(run,'notify',side_effect=BotError('unknown')) as notify:
            with self.assertRaises(BotError):run.main()
            self.assertEqual(notify.call_count,1)
    def test_failure_location_never_forwards_raw_details(self):
        import json
        log='prefix '+json.dumps({'errors':['АВ12: reasons source failed (TimeoutError) secret=123','М42: uv source failed (ConnectionError) 345.67','unknown user money=8877']},ensure_ascii=False)
        self.assertEqual(run.failure_location(log),'АВ12 — причины списаний; М42 — упущенная выручка')
    def test_dry_run_cannot_satisfy_business_completion(self):
        artifact={'name':'daily-cycle-complete-2026-11-27-morning','expired':False,'workflow_run':{'id':7}}
        row={'status':'completed','conclusion':'success','path':'.github/workflows/daily-cycle.yml','head_branch':'main','display_title':'LifeMart cycle morning (verify)','created_at':'2026-11-27T02:00:00Z'}
        with patch.object(run,'api',side_effect=[{'artifacts':[artifact]},row]):self.assertFalse(run.completed_cycle(dt.date(2026,11,27),'morning'))
        row['display_title']='LifeMart cycle morning'
        with patch.object(run,'api',side_effect=[{'artifacts':[artifact]},row]):self.assertTrue(run.completed_cycle(dt.date(2026,11,27),'morning'))
        with patch.object(run,'api',return_value={'artifacts':[]}):self.assertFalse(run.completed_cycle(dt.date(2026,11,27),'morning'))
    def test_all_five_tables_are_checked_before_delivery(self):
        with patch('telegram_reports.sheets.preflight_week') as original,patch('xk_reports.sheets.preflight') as added:
            run.report_preflight(dt.date(2026,11,27))
            self.assertEqual(original.call_count,3);self.assertEqual(added.call_count,2)
            for c in original.call_args_list+added.call_args_list:self.assertEqual(c.args[1],dt.date(2026,11,26))
    def test_readiness_failure_identifies_shop_without_raw_error(self):
        with patch('telegram_reports.sheets.preflight_week',side_effect=ValueError('private body 12345')):
            with self.assertRaises(run.PreparationError) as error:run.report_preflight(dt.date(2026,11,27))
            self.assertEqual(error.exception.branch,'АВ12')
            self.assertNotIn('private',str(error.exception))
    def test_native_report_failure_names_last_approved_shop_and_step(self):
        import json
        lines=[{'branch':'Х76','source':'ui2_days','stage':'query'},{'branch':'К44','assembly_stage':'weekly_sheet'},{'report_failed':True,'error':'secret raw body'}]
        self.assertEqual(run.failure_location('\n'.join(map(json.dumps,lines))),'К44 — общая таблица')
