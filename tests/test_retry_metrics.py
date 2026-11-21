import datetime as dt,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from daily_metrics import retry

DAY=dt.date(2030,1,2)

class FreshSessionRetry(unittest.TestCase):
    def execute(self,errors,second_code=0):
        calls=[]
        def child(command,env):
            calls.append(command)
            if errors is not None:
                Path(env['METRICS_STATUS_FILE']).write_text(json.dumps({'date':DAY.isoformat(),'errors':errors}))
            return SimpleNamespace(returncode=1 if len(calls)==1 else second_code)
        with patch.object(retry,'reporting_day',return_value=DAY),patch.object(retry.subprocess,'run',side_effect=child):
            result=retry.run_with_retry('apply','all')
        return result,calls
    def test_partial_network_failure_restarts_same_blank_only_operation_once(self):
        result,calls=self.execute(['АВ12: revenue source failed (TimeoutError)'])
        self.assertEqual(result,0);self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(calls[0][-4:],['--operation','apply','--branch','all'])
    def test_failed_second_attempt_is_not_hidden(self):
        result,calls=self.execute(['АВ12: revenue source failed (URLError)'],1)
        self.assertEqual(result,1);self.assertEqual(len(calls),2)
    def test_schema_access_and_selection_failures_do_not_retry(self):
        for error in ('АВ12: reasons source failed (StructureError)','АВ12: invoices source failed (HTTPError)','АВ12: Cell selection mismatch'):
            result,calls=self.execute([error])
            self.assertEqual(result,1);self.assertEqual(len(calls),1)
    def test_crash_without_new_receipt_does_not_retry(self):
        result,calls=self.execute(None)
        self.assertEqual(result,1);self.assertEqual(len(calls),1)
    def test_day_rollover_stops_before_second_process(self):
        def child(command,env):
            Path(env['METRICS_STATUS_FILE']).write_text(json.dumps({'date':DAY.isoformat(),'errors':['АВ12: revenue source failed (TimeoutError)']}))
            return SimpleNamespace(returncode=1)
        with patch.object(retry,'reporting_day',side_effect=[DAY,DAY,DAY+dt.timedelta(days=1)]),patch.object(retry.subprocess,'run',side_effect=child) as run:
            self.assertEqual(retry.run_with_retry('apply','all'),1)
        self.assertEqual(run.call_count,1)

    def test_future_procurement_transport_failure_can_retry(self):
        result,calls=self.execute(['Г34: procurement source 2030-01-03 failed (URLError)'])
        self.assertEqual(result,0);self.assertEqual(len(calls),2)
    def test_mixed_schema_and_network_errors_do_not_retry(self):
        result,calls=self.execute(['АВ12: revenue source failed (TimeoutError)','АВ12: reasons source failed (StructureError)'])
        self.assertEqual(result,1);self.assertEqual(len(calls),1)

    def test_expired_login_window_is_a_timeout_not_a_schema_failure(self):
        from unittest.mock import Mock
        from daily_metrics.session import ReportSUReports
        client=ReportSUReports();client.login_deadline=0;client.opener=Mock()
        with patch('daily_metrics.session.time.monotonic',return_value=1):
            with self.assertRaises(TimeoutError):client.request('/site/login')
        client.opener.open.assert_not_called()
