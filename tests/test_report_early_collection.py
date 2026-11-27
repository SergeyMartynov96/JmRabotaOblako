import datetime as dt
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from telegram_reports import run, timer


class EarlyCollection(unittest.TestCase):
    def test_timer_requests_nine_oclock_delivery(self):
        response=MagicMock();response.__enter__.return_value.status=204
        with patch.dict('os.environ',{'GITHUB_REPOSITORY':'owner/repo','GH_TOKEN':'test'}),patch.object(timer.urllib.request,'urlopen',return_value=response) as opened:
            timer.dispatch('deliver')
        payload=json.loads(opened.call_args.args[0].data)
        self.assertEqual(payload['inputs'],{'operation':'deliver','branch':'all','delivery_time':'09:00'})

    def test_summary_waits_until_nine_in_local_timezone(self):
        day=dt.date(2026,11,26)
        moments=[dt.datetime(2026,11,27,8,59,50,tzinfo=run.TZ),dt.datetime(2026,11,27,9,0,tzinfo=run.TZ)]
        with patch.object(run.dt,'datetime',wraps=dt.datetime) as clock,patch.object(run.time,'sleep') as sleep:
            clock.now.side_effect=moments
            run.wait_for_summary_time(day,'09:00')
        sleep.assert_called_once_with(10)

    def test_late_collection_does_not_delay_delivery(self):
        with patch.object(run.dt,'datetime',wraps=dt.datetime) as clock,patch.object(run.time,'sleep') as sleep:
            clock.now.return_value=dt.datetime(2026,11,27,9,4,tzinfo=run.TZ)
            run.wait_for_summary_time(dt.date(2026,11,26),'09:00')
        sleep.assert_not_called()

    def test_midnight_stops_before_summary_or_delivery(self):
        with patch.object(run.dt,'datetime',wraps=dt.datetime) as clock:
            clock.now.return_value=dt.datetime(2026,11,28,0,0,tzinfo=run.TZ)
            with self.assertRaisesRegex(run.StructureError,'day changed'):
                run.wait_for_summary_time(dt.date(2026,11,26),'09:00')

    def test_manual_immediate_delivery_does_not_wait(self):
        with patch.object(run.time,'sleep') as sleep:
            run.wait_for_summary_time(dt.date(2026,11,26),'immediate')
        sleep.assert_not_called()

    def test_early_input_is_propagated_and_summary_is_captured_after_wait(self):
        workflow=Path('.github/workflows/telegram-reports.yml').read_text()
        self.assertIn("REPORT_DELIVERY_TIME: ${{ inputs.delivery_time || 'immediate' }}",workflow)
        source=Path('telegram_reports/run.py').read_text()
        collection=source.index('captured.append((branch,folder,collect_bi(')
        wait=source.index('wait_for_summary_time(day,os.getenv(')
        summary=source.index('images,text=assemble(',wait)
        self.assertLess(collection,wait);self.assertLess(wait,summary)

