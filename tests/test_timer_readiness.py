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
        self.assertIn('types: [completed]',workflow)
        self.assertNotIn('types: [requested]',workflow)
