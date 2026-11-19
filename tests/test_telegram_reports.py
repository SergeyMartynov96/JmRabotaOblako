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
        self.assertEqual(query['queries'][0]['metrics'],list(OSA_METRICS))
        self.assertEqual(query['queries'][0]['filters'],[{'col':'active','op':'==','val':True},{'col':'title','op':'IN','val':['intended']}])
