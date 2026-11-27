import unittest
from telegram_reports.timer import metrics_ready

class CompletedMetricsGate(unittest.TestCase):
    def test_only_completed_success_can_release_the_report(self):
        self.assertTrue(metrics_ready({'status':'completed','conclusion':'success'}))
        for run in ({},{'status':'queued'},{'status':'in_progress','conclusion':None},{'status':'completed','conclusion':'failure'},{'status':'completed','conclusion':'cancelled'},{'status':'completed','conclusion':'skipped'},{'status':'in_progress','conclusion':'success'}):
            self.assertFalse(metrics_ready(run))

    def test_dependency_installation_precedes_daily_tests(self):
        from pathlib import Path
        workflow=Path('.github/workflows/daily-metrics.yml').read_text()
        self.assertLess(workflow.index('pip install -r requirements-test.txt'),workflow.index('python3 -m unittest'))
        self.assertLess(workflow.index('python3 -m unittest'),workflow.index('Read or fill yesterday metrics'))

    def test_automatic_timer_is_triggered_by_completion_not_request(self):
        from pathlib import Path
        workflow=Path('.github/workflows/telegram-report-timer.yml').read_text()
        self.assertNotIn('  workflow_run:',workflow)
        self.assertNotIn('types: [requested]',workflow)

    def execute_timer(self,first,last,minutes_to_delivery=-1):
        import tempfile,json,datetime as dt
        from pathlib import Path
        from unittest.mock import patch
        from telegram_reports import timer
        def event(conclusion):return {'action':'requested','workflow_run':{'id':17,'status':'completed','conclusion':conclusion}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'event.json';path.write_text(json.dumps({'action':'completed','workflow_run':{'id':17}}))
            target=dt.datetime.now(timer.TZ)+dt.timedelta(minutes=minutes_to_delivery)
            with patch.dict('os.environ',{'GITHUB_ACTIONS':'true','GITHUB_EVENT_PATH':str(path),'GITHUB_REPOSITORY':'owner/repo'}),patch('sys.argv',['timer']),patch.object(timer,'canonical_event',side_effect=[event(first),event(last)]) as canonical,patch.object(timer,'due_time',return_value=target),patch.object(timer,'dispatch') as dispatch,patch.object(timer.time,'sleep') as sleep:
                timer.main()
                return canonical.call_count,dispatch.call_args_list,sleep.call_count
    def test_failed_parent_never_waits_or_dispatches(self):
        calls,dispatches,sleeps=self.execute_timer('failure','success')
        self.assertEqual((calls,dispatches,sleeps),(1,[],0))
    def test_collection_is_released_before_nine_after_half_past_eight(self):
        calls,dispatches,sleeps=self.execute_timer('success','success',minutes_to_delivery=20)
        self.assertEqual(calls,2)
        self.assertEqual([c.args for c in dispatches],[('deliver',)])
        self.assertEqual(sleeps,0)
    def test_parent_is_checked_again_at_actual_dispatch_time(self):
        calls,dispatches,_=self.execute_timer('success','failure')
        self.assertEqual(calls,2);self.assertEqual(dispatches,[])
        calls,dispatches,_=self.execute_timer('success','success')
        self.assertEqual(calls,2);self.assertEqual([c.args for c in dispatches],[('deliver',)])
