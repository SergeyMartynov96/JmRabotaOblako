import datetime as dt
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_schedule as schedule
import cloud_window_state as state
import cloud_production as production
import cloud_heartbeat as heartbeat
import external_cron_config as external


class ExternalScheduleChecks(unittest.TestCase):
    def test_native_and_external_triggers_share_window_key(self):
        for key, config in schedule.WINDOW_CONFIG.items():
            native = schedule.resolve('schedule', config['cron'], requested_at='2026-11-09T03:30:00Z')
            direct = schedule.resolve('workflow_dispatch', operation='apply', external_window=key,
                                      requested_at='2026-11-09T03:35:00Z')
            self.assertEqual(native, direct)
            self.assertEqual(direct['window_key'], '2026-11-09-' + key)
            self.assertEqual(direct['slots'], config['slots'])

    def test_request_date_uses_yekaterinburg_not_utc(self):
        config = schedule.resolve('workflow_dispatch', operation='apply', external_window='morning',
                                  requested_at='2026-11-08T23:50:00Z')
        self.assertEqual(config['expected_date'], '2026-11-09')

    def test_external_window_cannot_be_preview_or_arbitrary(self):
        with self.assertRaises(ValueError):
            schedule.resolve('workflow_dispatch', external_window='morning')
        with self.assertRaises(ValueError):
            schedule.resolve('workflow_dispatch', operation='apply', external_window='other')
        self.assertEqual(schedule.resolve('workflow_dispatch', operation='verify')['window_key'], '')

    def test_delayed_event_cannot_roll_over_to_next_date_or_start_early(self):
        with patch.dict(os.environ, {'LIFEMART_EXPECTED_DATE':'2026-11-09',
                 'LIFEMART_WINDOW_START':'08:30', 'LIFEMART_DEADLINE':'09:00'}):
            production.check_deadline(dt.datetime(2026,11,9,8,30))
            for invalid in [dt.datetime(2026,11,9,8,29), dt.datetime(2026,11,9,9,0),
                            dt.datetime(2026,11,10,8,30)]:
                with self.assertRaises(production.Stop):
                    production.check_deadline(invalid)

    def test_only_successful_same_workflow_main_receipt_suppresses_retry(self):
        key = '2026-11-09-morning'
        artifact = dict(name='lifemart-av12-complete-'+key, expired=False, workflow_run={'id':1})
        run = dict(workflow_id=42, status='completed', conclusion='success',
                   head_branch='main', event='workflow_dispatch')
        self.assertTrue(state.is_complete(key, [artifact], lambda _:run, 42))
        self.assertFalse(state.is_complete(key, [dict(artifact, name='lifemart-complete-'+key)], lambda _:run, 42))
        for field, value in [('workflow_id',43), ('status','in_progress'), ('conclusion','failure'),
                             ('head_branch','other'), ('event','pull_request')]:
            self.assertFalse(state.is_complete(key, [artifact], lambda _:dict(run, **{field:value}), 42))
        self.assertFalse(state.is_complete(key, [dict(artifact, expired=True)], lambda _:run, 42))
        self.assertFalse(state.is_complete('2026-11-10-morning', [artifact], lambda _:run, 42))
        with self.assertRaises(ValueError):
            state.valid_key('2026-02-30-morning')

    def test_job_templates_are_disabled_and_only_dispatch_during_approved_windows(self):
        jobs = external.jobs()
        self.assertEqual(len(jobs), 3)
        for item, window in zip(jobs, schedule.WINDOW_CONFIG):
            job = item['job']
            self.assertFalse(job['enabled'])
            self.assertEqual(job['requestMethod'], 1)
            self.assertEqual(job['schedule']['timezone'], 'Asia/Yekaterinburg')
            body = json.loads(job['extendedData']['body'])
            self.assertEqual(body['inputs']['external_window'], window)
            self.assertEqual(body['inputs']['operation'], 'apply')
            self.assertNotIn('LIFEMART_PASSWORD', json.dumps(job))
            self.assertLess(max(job['schedule']['minutes']), 55)

    def test_monitor_does_not_accept_other_destinations_or_emit_body(self):
        valid = 'https://hc-ping.com/00000000-0000-0000-0000-000000000000'
        self.assertEqual(heartbeat.validate(valid), valid)
        for url in ['http://hc-ping.com/abc', 'https://example.com/abc', valid+'?secret=x']:
            with self.assertRaises(ValueError):
                heartbeat.validate(url)
        with patch.dict(os.environ, {'HEALTHCHECK_MORNING':valid}):
            with patch('cloud_heartbeat.urllib.request.urlopen') as mocked:
                mocked.return_value.__enter__.return_value.status=200
                heartbeat.ping('morning')
                req=mocked.call_args.args[0]
                self.assertEqual(req.data, b'')
                self.assertEqual(req.get_method(), 'POST')


if __name__ == '__main__':
    unittest.main()
