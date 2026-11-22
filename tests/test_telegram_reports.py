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

class OSAPivotTests(unittest.TestCase):
    def test_hidden_snapshots_removed_from_pivot_without_dropping_date_sort(self):
        import datetime as dt
        from types import SimpleNamespace
        from telegram_reports.core import constrained_query,OSA_METRICS
        all_metrics=[*OSA_METRICS,'OSA "На 17:00"','OSA "На вечер"','OSA "На утро"']
        saved={'queries':[{'metrics':all_metrics,'post_processing':[{'operation':'pivot','options':{
            'index':['__timestamp'],'columns':[],'aggregates':{m:{'operator':'mean'} for m in all_metrics}}},{'operation':'flatten'}]}]}
        result=constrained_query({},saved,SimpleNamespace(title='intended'),dt.date(2026,11,16),dt.date(2026,11,19),56,osa=True)
        post=result['queries'][0]['post_processing']
        self.assertEqual(list(post[0]['options']['aggregates']),list(OSA_METRICS))
        self.assertEqual(post[0]['options']['index'],['__timestamp'])
        self.assertEqual(post[1],{'operation':'flatten'})
        self.assertEqual(len(saved['queries'][0]['metrics']),5)

class CompleteReportTests(unittest.TestCase):
    def test_missing_eighth_image_blocks_delivery_preflight(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from types import SimpleNamespace
        from telegram_reports.run import preflight
        from daily_metrics.core import StructureError
        branch=SimpleNamespace(department=153)
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/(str(i)+'.png') for i in range(8)]
            for path in paths:Image.new('RGB',(100,100),'white').save(path)
            preflight([(branch,paths,'М42')],[branch])
            paths[-1].unlink()
            with self.assertRaises(StructureError):preflight([(branch,paths,'М42')],[branch])
            with self.assertRaises(StructureError):preflight([], [branch])

    def test_bi_page_is_reused_and_brought_to_front(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from telegram_reports.render import active_page
        page=Mock();page.is_closed.return_value=False
        context=Mock();context.new_page.return_value=page
        bi=SimpleNamespace(context=context)
        self.assertIs(active_page(bi),page)
        self.assertIs(active_page(bi),page)
        self.assertEqual(context.new_page.call_count,1)
        self.assertEqual(page.bring_to_front.call_count,2)
        page.close.assert_not_called()

class CleanupDeadlineTests(unittest.TestCase):
    def test_failed_browser_cleanup_has_one_twenty_second_watchdog(self):
        import contextlib,io
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from telegram_reports.security import arm_failed_cleanup
        seal=Mock();bi=SimpleNamespace(seal_before_exit=seal)
        with patch('threading.Timer') as timer:
            arm_failed_cleanup(bi);arm_failed_cleanup(bi)
            timer.assert_called_once()
            seconds,callback=timer.call_args.args
            self.assertEqual(seconds,20)
            with patch('telegram_reports.security.os._exit') as stop,patch('subprocess.check_output',return_value=b''),contextlib.redirect_stdout(io.StringIO()):callback()
            seal.assert_called_once();stop.assert_called_once_with(1)

class SUAuthenticationTests(unittest.TestCase):
    def test_network_retries_share_one_login_deadline(self):
        import urllib.error
        from unittest.mock import Mock,patch
        from telegram_reports.session import ReportSUReports
        def response():
            stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
            stream.read1.side_effect=[b'<html>ok</html>',b'']
            return stream
        client=ReportSUReports();client.opener=Mock()
        client.opener.open.side_effect=[urllib.error.URLError('temporary'),response(),response()]
        with patch('telegram_reports.session.time.monotonic',return_value=100):
            self.assertEqual(client.request('/site/login'),'<html>ok</html>')
            deadline=client.login_deadline
            self.assertEqual(client.request('/site/login',{'test':'value'}),'<html>ok</html>')
            self.assertEqual(client.login_deadline,deadline)
        self.assertEqual(client.opener.open.call_count,3)

    def test_access_denial_is_not_retried(self):
        import urllib.error
        from unittest.mock import Mock
        from telegram_reports.session import ReportSUReports
        client=ReportSUReports();client.opener=Mock()
        client.opener.open.side_effect=urllib.error.HTTPError('https://example.invalid',403,'Denied',{},None)
        with self.assertRaises(urllib.error.HTTPError):client.request('/site/login')
        self.assertEqual(client.opener.open.call_count,1)

class WorkerTerminationTests(unittest.TestCase):
    def test_only_owned_descendants_are_selected(self):
        from telegram_reports.security import worker_descendants
        self.assertEqual(worker_descendants(10,[(1,0),(10,1),(11,10),(12,11),(50,1),(51,50)]),[12,11])

class CaptionFormatTests(unittest.TestCase):
    def test_caption_has_no_date_or_yesterday_prefix(self):
        import datetime as dt
        from decimal import Decimal
        from types import SimpleNamespace
        from telegram_reports.run import caption
        result=caption(SimpleNamespace(name='TEST'),dt.date(2030,1,2),[Decimal('82.1'),Decimal('46.2')])
        self.assertTrue(result.startswith('TEST\n1. Приходные накладные'))
        self.assertNotIn('02.01',result)
        self.assertNotIn('за вчера',result)
        self.assertIn('7. OSA 11:00 82,1%',result)
        self.assertIn('8. OSA 20:00 46,2%',result)

class CanonicalTimerParent(unittest.TestCase):
    def test_sparse_webhook_uses_validated_rest_parent(self):
        import io,json,datetime as dt
        from unittest.mock import patch
        from telegram_reports.timer import canonical_event,due_time,TZ
        run=TimerTests().event()['workflow_run'];run['id']=123
        with patch.dict('os.environ',{'GITHUB_REPOSITORY':'owner/repo','GH_TOKEN':'test-only'}),patch('telegram_reports.timer.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(run).encode())) as request:
            event=canonical_event(123)
        self.assertEqual(request.call_args.args[0].full_url,'https://api.github.com/repos/owner/repo/actions/runs/123')
        self.assertEqual(due_time(event,dt.datetime(2026,11,20,8,50,tzinfo=TZ),'owner/repo'),dt.datetime(2026,11,20,9,tzinfo=TZ))
    def test_invalid_parent_cannot_select_another_endpoint(self):
        from unittest.mock import patch
        from telegram_reports.timer import canonical_event
        with patch('telegram_reports.timer.urllib.request.urlopen') as request:
            with self.assertRaises(ValueError):canonical_event('../foreign')
        request.assert_not_called()


class InvoiceTransportRetry(unittest.TestCase):
    def setup_source(self):
        from unittest.mock import Mock
        from telegram_reports.invoices import invoice_source
        context=Mock();response=Mock(status=200);response.text.return_value='validated later'
        error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        return invoice_source,context,response,error,'https://su.lifemart.ru/turnover/waybill/report?day=21'

    def test_etimedout_retries_same_read_with_remaining_budget(self):
        from unittest.mock import patch
        source,ctx,response,error,url=self.setup_source()
        ctx.request.get.side_effect=[error('APIRequestContext.get: read ETIMEDOUT'),response]
        with patch('telegram_reports.invoices.time.monotonic',side_effect=[0,0,17]):
            self.assertEqual(source(ctx,url),'validated later')
        self.assertEqual([c.args[0] for c in ctx.request.get.call_args_list],[url,url])
        self.assertEqual([c.kwargs['timeout'] for c in ctx.request.get.call_args_list],[180000,163000])

    def test_forbidden_schema_errors_and_unapproved_paths_never_retry(self):
        from daily_metrics.core import StructureError
        source,ctx,response,error,url=self.setup_source()
        ctx.request.get.return_value=response;response.status=403
        with self.assertRaises(StructureError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,1)
        ctx.request.get.reset_mock();ctx.request.get.side_effect=error('Invalid argument')
        with self.assertRaises(error):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,1)
        ctx.request.get.reset_mock()
        with self.assertRaises(StructureError):source(ctx,'https://su.lifemart.ru/turnover/waybill/update/1')
        ctx.request.get.assert_not_called()

    def test_two_transport_failures_stop_and_expired_budget_stops_before_retry(self):
        from unittest.mock import patch
        source,ctx,response,error,url=self.setup_source()
        ctx.request.get.side_effect=error('read ETIMEDOUT')
        with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,2)
        ctx.request.get.reset_mock()
        with patch('telegram_reports.invoices.time.monotonic',side_effect=[0,0,181]):
            with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,1)

    def test_temporary_server_responses_retry_but_stop_after_two(self):
        source,ctx,response,error,url=self.setup_source()
        response.status=503;ctx.request.get.return_value=response
        with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,2)
