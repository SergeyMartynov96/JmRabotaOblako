import datetime as dt
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_schedule as schedule
import cloud_production as production


class DailyScheduleChecks(unittest.TestCase):
    def test_all_three_windows_are_daily_apply(self):
        for cron, local, slots, deadline in [
            ('30 3 * * *', '08:30', '09:00,10:00', '09:00'),
            ('30 5 * * *', '10:30', '11:00,12:00,13:00', '11:00'),
            ('0 9 * * *', '14:00', '15:00,16:00', '15:00'),
        ]:
            config = schedule.resolve('schedule', cron, requested_at='2026-11-09T00:00:00Z')
            self.assertEqual({k:config[k] for k in ('operation','slots','deadline','allow_empty')}, dict(operation='apply', slots=slots, deadline=deadline, allow_empty='true'))
            minute, hour, *_ = cron.split()
            self.assertEqual(f'{(int(hour) + 5) % 24:02d}:{int(minute):02d}', local)

    def test_manual_preview_stays_preview(self):
        self.assertEqual(schedule.resolve('workflow_dispatch')['operation'], 'preview')
        self.assertEqual(schedule.resolve('workflow_dispatch', operation='apply', slots='11:00')['slots'], '11:00')
        self.assertEqual(schedule.resolve('workflow_dispatch')['allow_empty'], 'false')

    def test_recovery_has_date_deadline_and_no_automatic_success_receipt(self):
        config = schedule.resolve('workflow_dispatch', operation='recover', slots='09:00,10:00',
                                  requested_at='2026-11-09T04:01:00Z')
        self.assertEqual(config['operation'], 'apply')
        self.assertEqual(config['deadline'], '10:00')
        self.assertEqual(config['expected_date'], '2026-11-09')
        self.assertEqual(config['include_sent'], 'true')
        self.assertEqual(config['window_key'], '')
        with self.assertRaises(ValueError):
            schedule.resolve('workflow_dispatch', operation='recover', slots='09:00,10:00',
                             requested_at='2026-11-09T05:00:00Z')

    def test_named_recovery_reconciles_entire_window_before_receipt(self):
        config = schedule.resolve('workflow_dispatch', operation='recover', slots='16:00',
                                  external_window='morning', requested_at='2026-11-09T04:01:00Z')
        self.assertEqual(config['slots'], '09:00,10:00')
        self.assertEqual(config['deadline'], '10:00')
        self.assertEqual(config['window_key'], '2026-11-09-morning')
        self.assertEqual(config['window'], 'morning')
        self.assertEqual(config['include_sent'], 'true')

    def test_unknown_schedule_fails_closed(self):
        with self.assertRaises(ValueError):
            schedule.resolve('schedule', '0 0 * * *')

    def test_late_scheduled_pass_cannot_silently_become_empty_success(self):
        with patch.dict(os.environ, {'LIFEMART_DEADLINE': '09:00'}):
            production.check_deadline(dt.datetime(2026, 11, 9, 8, 59))
            with self.assertRaises(production.Stop):
                production.check_deadline(dt.datetime(2026, 11, 9, 9, 0))
        with patch.dict(os.environ, {'LIFEMART_DEADLINE': ''}):
            production.check_deadline(dt.datetime(2026, 11, 9, 15, 0))


if __name__ == '__main__':
    unittest.main()
