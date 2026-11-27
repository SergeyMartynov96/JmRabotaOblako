import unittest
from workflow_alerts.run import canonical,diagnose

class PrivateFailureAlerts(unittest.TestCase):
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
