import datetime as dt,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from telegram_reports import retry

DAY=dt.date(2030,1,2)

class CollectionRetry(unittest.TestCase):
    def execute(self,retryable=True,marker=None,second_code=0,write_status=True):
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            receipts=Path(directory)/'receipts';receipts.mkdir()
            def child(command,env):
                calls.append(command)
                if write_status:Path(env['REPORT_STATUS_FILE']).write_text(json.dumps({'retryable':retryable}))
                if marker:(receipts/'115.json').write_text(json.dumps({'status':marker}))
                return SimpleNamespace(returncode=1 if len(calls)==1 else second_code)
            with patch.object(retry,'reporting_day',return_value=DAY),patch.object(retry.subprocess,'run',side_effect=child):
                result=retry.run_with_retry('deliver','all',receipts)
        return result,calls
    def test_network_collection_failure_retries_once(self):
        result,calls=self.execute()
        self.assertEqual(result,0);self.assertEqual(len(calls),2);self.assertEqual(calls[0],calls[1])
    def test_attempted_or_accepted_delivery_never_retries(self):
        for marker in ('attempted','accepted'):
            result,calls=self.execute(marker=marker)
            self.assertEqual(result,1);self.assertEqual(len(calls),1)
    def test_schema_error_and_crash_without_status_never_retry(self):
        for kwargs in ({'retryable':False},{'write_status':False}):
            result,calls=self.execute(**kwargs)
            self.assertEqual(result,1);self.assertEqual(len(calls),1)
    def test_two_failed_collections_stay_failed(self):
        result,calls=self.execute(second_code=1)
        self.assertEqual(result,1);self.assertEqual(len(calls),2)
    def test_changed_day_cannot_start_another_collection(self):
        def child(command,env):
            Path(env['REPORT_STATUS_FILE']).write_text(json.dumps({'retryable':True}))
            return SimpleNamespace(returncode=1)
        with tempfile.TemporaryDirectory() as directory,patch.object(retry,'reporting_day',side_effect=[DAY,DAY,DAY+dt.timedelta(days=1)]),patch.object(retry.subprocess,'run',side_effect=child) as run:
            self.assertEqual(retry.run_with_retry('deliver','all',Path(directory)),1)
        self.assertEqual(run.call_count,1)
