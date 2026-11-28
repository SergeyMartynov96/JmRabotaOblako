import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from telegram_reports import render
from telegram_reports.session import report_bi
from playwright.sync_api import TimeoutError as BrowserTimeout

class ActiveFirstPage(unittest.TestCase):
    def test_page_is_retained_only_after_complete_and_real_data(self):
        bi=SimpleNamespace();page=Mock()
        with patch.object(render.time,'monotonic',return_value=30):render.prepare_native_page(bi,page,180)
        self.assertIs(bi.report_page,page);page.bring_to_front.assert_called_once()
        predicate=page.wait_for_function.call_args.args[0]
        self.assertIn("readyState==='complete'",predicate);self.assertIn("visibilityState==='visible'",predicate);self.assertIn('table td',predicate)
        self.assertEqual(page.wait_for_function.call_args.kwargs['timeout'],150000)
        page.close.assert_not_called()
    def test_stalled_first_page_does_not_become_a_ready_report_page(self):
        bi=SimpleNamespace();page=Mock();page.wait_for_function.side_effect=BrowserTimeout('not ready')
        with patch.object(render.time,'monotonic',return_value=0):
            with self.assertRaises(BrowserTimeout):render.prepare_native_page(bi,page,180)
        self.assertFalse(hasattr(bi,'report_page'))
    def test_old_reports_enable_native_warmup_and_reuse_retained_page(self):
        client=Mock();bi=report_bi(Mock(),client);page=Mock()
        with patch.object(render.time,'monotonic',return_value=0):bi.prepare_authenticated_page(page,180)
        with patch.object(render,'active_page') as active:
            self.assertIs(bi.report_page,page)
        # The actual active_page helper uses the retained page instead of opening another.
        page.is_closed.return_value=False
        with patch.object(bi,'context') as context:self.assertIs(render.active_page.__wrapped__(bi),page);context.new_page.assert_not_called()
