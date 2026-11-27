import unittest
from workflow_alerts.run import canonical,diagnose

class PrivateFailureAlerts(unittest.TestCase):
    def test_su_request_timeouts_and_metrics_timeouts_are_understandable(self):
        self.assertIn('СУ',diagnose('FAIL: TimeoutError\nTRACE: cloud_readonly_test.py:178','failure')[1])
        self.assertEqual(diagnose('revenue source failed (TimeoutError)','failure')[0],'timeout')
    def test_missing_arrival_includes_only_validated_date(self):
        self.assertIn('29.11.2026',diagnose('FAIL: Sheet arrival date missing: 29.11.2026','failure')[1])
        self.assertNotIn('99.99.9999',diagnose('FAIL: Sheet arrival date missing: 99.99.9999','failure')[1])
    def test_test_failure_is_not_mistaken_for_operational_date_error(self):
        self.assertEqual(diagnose('Sheet arrival date missing\nFAILED (failures=1)','failure')[0],'code')
    def test_only_owned_main_failures_are_watched(self):
        run={'path':'.github/workflows/cloud-k44.yml','head_branch':'main','head_repository':{'full_name':'owner/repo'},'event':'workflow_dispatch','conclusion':'failure'}
        self.assertTrue(canonical(run,'owner/repo'))
        for field,value in [('conclusion','cancelled'),('head_branch','other'),('event','pull_request'),('head_repository',{'full_name':'attacker/repo'}),('path','.github/workflows/private-failure-alerts.yml')]:
            self.assertFalse(canonical({**run,field:value},'owner/repo'))
    def test_russian_reasons_do_not_forward_raw_log_data(self):
        for raw,category in [('FAIL: Sheet arrival date missing: 29.11.2026','arrival'),('FAIL: Sheet date changed','sheet'),('FAIL: TimeoutError','timeout')]:
            code,message=diagnose(raw+' PASSWORD=private-secret','failure')
            self.assertEqual(code,category)
            self.assertNotIn('private-secret',message)
            self.assertNotIn('PASSWORD',message)
    def test_unknown_error_remains_honest_and_private(self):
        code,message=diagnose('arbitrary sensitive payload','failure')
        self.assertEqual(code,'other');self.assertNotIn('payload',message)
