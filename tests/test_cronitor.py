import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cloud_heartbeat as heartbeat
import cronitor_config


class CronitorChecks(unittest.TestCase):
    def test_three_daily_completion_checks_allow_fifteen_minutes(self):
        monitors=cronitor_config.monitors()
        self.assertEqual(len(monitors),3)
        for monitor,cron in zip(monitors,['30 8 * * *','30 10 * * *','0 14 * * *']):
            self.assertEqual(monitor['type'],'heartbeat')
            self.assertEqual(monitor['schedules'],[cron])
            self.assertEqual(monitor['timezone'],'Asia/Yekaterinburg')
            self.assertEqual(monitor['grace_seconds'],900)
            self.assertEqual(monitor['schedule_tolerance'],0)
            self.assertTrue(monitor['paused'])
            self.assertEqual(monitor['notify'],['default'])

    def test_only_approved_destination_and_completion_activity(self):
        url=heartbeat.cronitor_url('morning','a'*32)
        parsed=urlsplit(url)
        self.assertEqual(parsed.netloc,'cronitor.link')
        self.assertEqual(parsed.path,'/p/'+('a'*32)+'/lifemart-morning')
        self.assertEqual(parse_qs(parsed.query),dict(state=['tick'],env=['production']))
        for window,token,environment in [('other','a'*32,'production'),('morning','a/b','production'),
                                          ('morning','a'*32,'arbitrary')]:
            with self.assertRaises(ValueError):
                heartbeat.cronitor_url(window,token,environment)

    def test_runtime_only_uses_telemetry_credential_and_sends_no_business_payload(self):
        with patch.dict(os.environ,{'CRONITOR_TELEMETRY_KEY':'a'*32}):
            with patch('cloud_heartbeat.urllib.request.urlopen') as api:
                api.return_value.__enter__.return_value.status=200
                with patch('builtins.print'):
                    heartbeat.ping('midday')
                req=api.call_args.args[0]
                self.assertEqual(req.data,b'')
                self.assertEqual(req.get_method(),'POST')
                self.assertIn('/lifemart-midday?',req.full_url)
                self.assertNotIn('amount',req.full_url)

    def test_delivery_probe_never_emits_production_success(self):
        with patch.dict(os.environ,{'CRONITOR_TELEMETRY_KEY':'a'*32}):
            with patch('cloud_heartbeat.urllib.request.urlopen') as api:
                api.return_value.__enter__.return_value.status=200
                with patch('builtins.print'):
                    heartbeat.verify_delivery()
                self.assertEqual(api.call_count,3)
                for call in api.call_args_list:
                    self.assertEqual(parse_qs(urlsplit(call.args[0].full_url).query)['env'],['setup-test'])
        with patch.dict(os.environ,{'CRONITOR_TELEMETRY_KEY':''}):
            with self.assertRaisesRegex(RuntimeError,'secret is missing'):
                heartbeat.verify_delivery()

    def test_monitor_delivery_operation_has_no_application_window(self):
        import cloud_schedule
        config=cloud_schedule.resolve('workflow_dispatch',operation='monitor-verify')
        self.assertEqual(config['operation'],'monitor-verify')
        self.assertEqual(config['window_key'],'')
        with self.assertRaises(ValueError):
            cloud_schedule.resolve('workflow_dispatch',operation='monitor-verify',external_window='morning')

    def test_transient_network_failure_is_retried_but_never_logged_with_key(self):
        with patch('cloud_heartbeat.urllib.request.urlopen',side_effect=OSError('private endpoint')) as api:
            with patch('cloud_heartbeat.time.sleep'):
                with self.assertRaisesRegex(RuntimeError,'could not be delivered'):
                    heartbeat.cronitor_ping('afternoon','a'*32)
                self.assertEqual(api.call_count,3)


if __name__=='__main__':
    unittest.main()
