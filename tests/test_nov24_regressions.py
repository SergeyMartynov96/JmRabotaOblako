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
        self.assertTrue(all(c.kwargs['timeout']<=600 for c in client.opener.open.call_args_list))

    def test_late_native_evening_runs_do_not_apply_next_day(self):
        for scope in ('К44','Х76'):
            late=resolve('schedule',requested_at='2026-11-23T22:19:39Z',scope=scope)
            self.assertEqual(late['operation'],'verify')
            normal=resolve('schedule',requested_at='2026-11-23T11:00:00Z',scope=scope)
            self.assertEqual(normal['operation'],'apply')
            manual=resolve('workflow_dispatch','apply','2026-11-23T22:19:39Z',scope)
            self.assertEqual(manual['operation'],'apply')

    def test_rc_defects_combine_with_damage(self):
        from decimal import Decimal
        result=bi_reasons({'result':[{'data':[{'reason':'Порча','p':.001},{'reason':'Брак РЦ','p':.002}]}]},'p')
        self.assertEqual(result['damage'],Decimal('.300'))
        self.assertTrue(result.has_defect)
        self.assertEqual(result.unmapped,[])

    def test_delayed_native_av_windows_do_not_open_before_start(self):
        from scripts.cloud_schedule import resolve as av_resolve, WINDOW_CONFIG
        for config in WINDOW_CONFIG.values():
            actual=av_resolve('schedule',config['cron'],requested_at='2026-11-23T21:05:53Z')
            self.assertEqual(actual['operation'],'verify')
        actual=av_resolve('workflow_dispatch',operation='apply',external_window='afternoon',requested_at='2026-11-23T21:05:53Z')
        self.assertEqual(actual['operation'],'apply')

    def test_metrics_bi_browser_fallback_only_for_transport_failure(self):
        from daily_metrics.extended_io import BIReports
        import urllib.error,ssl
        for failure,allowed in [(TimeoutError(),True),(urllib.error.HTTPError('https://su.lifemart.ru',403,'denied',{},None),False),(urllib.error.URLError(ssl.SSLCertVerificationError()),False)]:
            browser=Mock();browser.new_context.side_effect=RuntimeError('browser stage reached')
            client=Mock();client.ensure_authenticated.side_effect=failure
            bi=BIReports(browser,client)
            with self.assertRaises(RuntimeError if allowed else type(failure)):
                bi.authenticate()
            self.assertEqual(bi.browser_login,allowed)
            self.assertEqual(browser.new_context.call_count,int(allowed))

    def test_damage_alone_never_creates_raw_defect(self):
        result=bi_reasons({'result':[{'data':[{'reason':'Порча','p':.001}]}]},'p')
        self.assertIn('damage',result)
        self.assertNotIn('raw_defect',result)
        result=bi_reasons({'result':[{'data':[{'reason':'Порча','p':.001},{'reason':'Брак','p':.002}]}]},'p')
        self.assertEqual(result['damage'],result['raw_defect']+result.damage_base)

    def test_native_bootstrap_reloads_once_only_before_dataset_request(self):
        from telegram_reports import render
        from unittest.mock import patch
        from types import SimpleNamespace
        for served in ([],[True]):
            page=Mock();page.wait_for_function.side_effect=[None,render.BrowserTimeout('bootstrap stalled'),None,None]
            with patch.object(render,'load_explore',return_value=SimpleNamespace(status=200)) as load,patch.object(render.time,'monotonic',return_value=0):
                if served:
                    with self.assertRaises(render.BrowserTimeout):render.load_chart_document(Mock(),page,{},400,180,served)
                    self.assertEqual(load.call_count,1)
                else:
                    render.load_chart_document(Mock(),page,{},400,180,served)
                    self.assertEqual(load.call_count,2)
                    page.bring_to_front.assert_called_once()

    def test_native_bootstrap_never_exceeds_two_document_attempts(self):
        from telegram_reports import render
        from unittest.mock import patch
        from types import SimpleNamespace
        page=Mock();page.wait_for_function.side_effect=[None,render.BrowserTimeout('stalled'),None,render.BrowserTimeout('stalled')]
        with patch.object(render,'load_explore',return_value=SimpleNamespace(status=200)) as load,patch.object(render.time,'monotonic',return_value=0):
            with self.assertRaises(render.BrowserTimeout):render.load_chart_document(Mock(),page,{},400,180,[])
            self.assertEqual(load.call_count,2)

    def test_separate_defect_header_is_not_aggregate_damage_target(self):
        from daily_metrics.extended_core import extra_targets
        rows=[['']*19 for _ in range(45)]
        rows[0][5]='УВ';rows[0][9]='OSA 20:00';rows[0][15]='по накладным'
        rows[10][11:17]=['СГ закуп','цех','расходка','порча','маркетинг','дегустация']
        rows[21][10:17]=['Брак','СГ ротация','СГ товароб.','СГ мин. сум.','СГ ручное','СГ мин. ост.','СГ пост.']
        for start in (11,22):
            for offset in range(7):rows[start+offset][9]=f'{16+offset}.11'
        result=extra_targets(rows,0,3,dt.date(2026,11,18))
        self.assertEqual(result['reasons']['damage'],[(13,14)])
        self.assertEqual(result['reasons']['raw_defect'],[(24,10)])
