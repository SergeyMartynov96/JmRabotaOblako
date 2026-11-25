import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from daily_metrics.extended_io import BIReports,SU
from daily_metrics.core import StructureError
from playwright.sync_api import TimeoutError as BrowserTimeout

class LoginBootstrap(unittest.TestCase):
    def page(self):
        page=Mock();page.url=SU+'/site/login'
        page.goto.return_value=page.reload.return_value=SimpleNamespace(status=200)
        return page
    def test_incomplete_login_document_gets_one_active_reload_before_submission(self):
        page=self.page();page.wait_for_function.side_effect=[BrowserTimeout('form missing'),None]
        reader=BIReports(Mock(),Mock())
        with patch('daily_metrics.extended_io.time.monotonic',return_value=0):reader._load_su_login_form(page,180)
        page.goto.assert_called_once();page.reload.assert_called_once()
        self.assertEqual(page.bring_to_front.call_count,2)
        page.locator.assert_not_called()
        self.assertEqual(page.wait_for_function.call_args_list[0].kwargs['timeout'],60000)
        self.assertEqual(page.wait_for_function.call_args_list[1].kwargs['timeout'],180000)
    def test_ready_form_is_not_reloaded(self):
        page=self.page();reader=BIReports(Mock(),Mock())
        with patch('daily_metrics.extended_io.time.monotonic',return_value=0):reader._load_su_login_form(page,180)
        page.reload.assert_not_called()
    def test_repeated_stall_stops_and_http_rejection_is_not_reloaded(self):
        page=self.page();page.wait_for_function.side_effect=BrowserTimeout('still missing')
        with patch('daily_metrics.extended_io.time.monotonic',return_value=0):
            with self.assertRaises(BrowserTimeout):BIReports(Mock(),Mock())._load_su_login_form(page,180)
        page.reload.assert_called_once()
        page=self.page();page.goto.return_value=SimpleNamespace(status=403)
        with self.assertRaises(StructureError):BIReports(Mock(),Mock())._load_su_login_form(page,180)
        page.reload.assert_not_called();page.wait_for_function.assert_not_called()

    def test_login_form_requires_original_secure_origin(self):
        for url in ('http://su.lifemart.ru/site/login','https://other.invalid/site/login'):
            page=self.page();page.url=url
            with self.assertRaises(StructureError):BIReports(Mock(),Mock())._load_su_login_form(page,180)
            page.wait_for_function.assert_not_called();page.reload.assert_not_called()
