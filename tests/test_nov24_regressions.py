import datetime as dt
import io
import unittest
from unittest.mock import Mock
from daily_metrics.io import SUReports, REVENUE
from daily_metrics.extended_core import bi_reasons
from scripts.cloud_k44_schedule import resolve

class SourceRecovery(unittest.TestCase):
    def test_report_retries_timeout_with_bounded_socket(self):
        client=SUReports()
        response=Mock()
        response.read1.side_effect=[b'<table>ok</table>',b'']
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        client.opener=Mock()
        client.opener.open.side_effect=[TimeoutError(),context]
        self.assertEqual(client.request(REVENUE,{'format':'1'}),'<table>ok</table>')
        self.assertEqual(client.opener.open.call_count,2)
        self.assertTrue(all(c.kwargs['timeout']<=45 for c in client.opener.open.call_args_list))

    def test_late_native_evening_runs_do_not_apply_next_day(self):
        for scope in ('К44','Х76'):
            late=resolve('schedule',requested_at='2026-11-23T22:19:39Z',scope=scope)
            self.assertEqual(late['operation'],'verify')
            normal=resolve('schedule',requested_at='2026-11-23T11:00:00Z',scope=scope)
            self.assertEqual(normal['operation'],'apply')
            manual=resolve('workflow_dispatch','apply','2026-11-23T22:19:39Z',scope)
            self.assertEqual(manual['operation'],'apply')
