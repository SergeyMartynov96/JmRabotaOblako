import hashlib
import unittest
from unittest.mock import patch
from telegram_reports.transport import BotClient, BotError, encrypt_recipient, decrypt_recipient, matching_private_chat, wait_for_private_start, send_connection_test

class TelegramTests(unittest.TestCase):
    def test_binding_authenticated(self):
        envelope=encrypt_recipient('unit-key',12,123)
        self.assertEqual(decrypt_recipient('unit-key',12,envelope),123)
        for key,bot in [('wrong-key',12),('unit-key',13)]:
            with self.assertRaises(BotError):decrypt_recipient(key,bot,envelope)
        envelope['ciphertext']='AAAA'
        with self.assertRaises(BotError):decrypt_recipient('unit-key',12,envelope)
        with self.assertRaises(BotError):encrypt_recipient('unit-key',12,-123)

    def update(self,ident=123,kind='private',sender=123,text='/start unit-code'):
        return {'message':{'chat':{'id':ident,'type':kind},'from':{'id':sender,'is_bot':False},'text':text}}

    def test_matching_only_private_challenge_holder(self):
        digest=hashlib.sha256(b'unit-code').hexdigest()
        self.assertEqual(matching_private_chat([self.update()],digest),123)
        for update in [self.update(kind='group'),self.update(sender=456),self.update(text='/start wrong')]:
            self.assertIsNone(matching_private_chat([update],digest))
        with self.assertRaises(BotError):matching_private_chat([self.update(),self.update(456,sender=456)],digest)

    def test_poll_does_not_acknowledge_queue(self):
        class Fake:
            def api(inner,method,params=None,**kw):
                if method=='getWebhookInfo':return {'url':''}
                self.assertNotIn('offset',params)
                return [self.update()]
        self.assertEqual(wait_for_private_start(Fake(),hashlib.sha256(b'unit-code').hexdigest()),123)

    def test_send_verifies_destination(self):
        class Fake:
            def api(inner,*args):return {'chat':{'id':456,'type':'private'},'message_id':1}
        with self.assertRaises(BotError):send_connection_test(Fake(),123)

    def test_transport_error_redacted(self):
        with patch('urllib.request.urlopen',side_effect=OSError('unit-key confidential response')):
            with self.assertRaises(BotError) as caught:BotClient('unit-key').api('getMe')
        self.assertNotIn('unit-key',str(caught.exception))
        self.assertNotIn('confidential',str(caught.exception))

class ReportPeriodTests(unittest.TestCase):
    def test_week_to_yesterday_and_monday_exception(self):
        import datetime as dt
        from telegram_reports.core import weekly_period
        self.assertEqual(weekly_period(dt.date(2026,11,19)),(dt.date(2026,11,16),dt.date(2026,11,19)))
        self.assertEqual(weekly_period(dt.date(2026,11,23)),(dt.date(2026,11,16),dt.date(2026,11,23)))
        self.assertEqual(weekly_period(dt.date(2027,1,1)),(dt.date(2026,12,28),dt.date(2027,1,1)))

    def test_query_does_not_mutate_saved_template(self):
        import datetime as dt,copy
        from types import SimpleNamespace
        from telegram_reports.core import constrained_query,OSA_METRICS
        saved={'queries':[{'filters':[{'col':'title','op':'IN','val':['foreign']},{'col':'active','op':'==','val':True}],
            'metrics':list(OSA_METRICS)+['OSA "На 17:00"'],'post_processing':[{'operation':'pivot'}]}]}
        original=copy.deepcopy(saved)
        query=constrained_query({},saved,SimpleNamespace(title='intended'),dt.date(2026,11,16),dt.date(2026,11,19),56,osa=True)
        self.assertEqual(saved,original)
        self.assertEqual(query['queries'][0]['time_range'],'2026-11-16 : 2026-11-19')
        self.assertEqual(query['queries'][0]['metrics'],list(OSA_METRICS)+['OSA \"На 17:00\"'])
        self.assertEqual(query['queries'][0]['filters'],[{'col':'active','op':'==','val':True},{'col':'title','op':'IN','val':['intended']}])

class DeliveryTests(unittest.TestCase):
    def test_yesterday_osa_uses_exact_date_not_last_row(self):
        import datetime as dt
        from telegram_reports.run import osa_yesterday
        from daily_metrics.core import StructureError
        def row(day,one,two):
            return {'__timestamp':day,'OSA "На 11:00"':one,'OSA "На 20:00"':two}
        payload={'result':[{'data':[row('2026-11-18',.8,.5),row('2026-11-17',.9,.6)]}]}
        self.assertEqual(osa_yesterday(payload,dt.date(2026,11,18)),[80,50])
        with self.assertRaises(StructureError):osa_yesterday(payload,dt.date(2026,11,19))

    def test_album_failure_never_retries_or_leaks_transport_details(self):
        import tempfile
        from pathlib import Path
        from telegram_reports.transport import send_album
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'one.png';path.write_bytes(b'\x89PNGdummy')
            with patch('urllib.request.urlopen',side_effect=OSError('unit-key and private chat')) as call:
                with self.assertRaises(BotError) as caught:send_album(BotClient('unit-key'),123,[path,path],'caption')
                self.assertEqual(call.call_count,1)
                self.assertNotIn('unit-key',str(caught.exception))
                self.assertIn('do not automatically retry',str(caught.exception))

class TimerTests(unittest.TestCase):
    def event(self,**changes):
        run={'path':'.github/workflows/daily-metrics.yml','head_branch':'main','head_repository':{'full_name':'owner/repo'},
             'event':'workflow_dispatch','display_title':'LifeMart metrics apply (all)','created_at':'2026-11-20T03:00:00Z'}
        run.update(changes)
        return {'action':'requested','workflow_run':run}
    def test_only_morning_external_signal_arms_nine(self):
        import datetime as dt
        from telegram_reports.timer import due_time,TZ
        now=dt.datetime(2026,11,20,8,1,tzinfo=TZ)
        self.assertEqual(due_time(self.event(),now,'owner/repo'),dt.datetime(2026,11,20,9,tzinfo=TZ))
        for change in ({'display_title':'LifeMart metrics preview (all)'},{'head_branch':'feature'},{'created_at':'2026-11-20T04:00:00Z'},
                       {'created_at':'2026-11-19T03:00:00Z'},{'head_repository':{'full_name':'foreign/repo'}}):
            self.assertIsNone(due_time(self.event(**change),now,'owner/repo'))
        with self.assertRaises(ValueError):due_time(self.event(),now.replace(hour=10),'owner/repo')

class ScopeValidationTests(unittest.TestCase):
    def test_server_must_confirm_filter_and_exact_range(self):
        import datetime as dt
        from telegram_reports.core import validate_scope
        from daily_metrics.core import StructureError
        start=dt.date(2026,11,18);end=dt.date(2026,11,19)
        valid={'status':'success','applied_filters':[{'column':'title'}],'rejected_filters':[],
            'from_dttm':1794960000000.0,'to_dttm':1795046400000.0}
        validate_scope(valid,start,end)
        for change in ({'applied_filters':[]},{'rejected_filters':[{'column':'title'}]},
                       {'from_dttm':1794787200000.0},{'status':'failed'}):
            with self.assertRaises(StructureError):validate_scope({**valid,**change},start,end)
