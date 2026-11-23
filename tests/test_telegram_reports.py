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
            path=Path(directory)/'one.png'
            __import__('PIL.Image',fromlist=['Image']).new('RGB',(16,16),'white').save(path)
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


class ReportGetTransportRetry(unittest.TestCase):
    def setup_source(self):
        from unittest.mock import Mock
        from telegram_reports.http import report_get
        invoice_source=lambda context,url:report_get(context,url).text()
        context=Mock();response=Mock(status=200);response.text.return_value='validated later'
        error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        return invoice_source,context,response,error,'https://su.lifemart.ru/turnover/waybill/report?day=21'

    def test_etimedout_retries_same_read_with_remaining_budget(self):
        from unittest.mock import patch
        source,ctx,response,error,url=self.setup_source()
        ctx.request.get.side_effect=[error('APIRequestContext.get: read ETIMEDOUT'),response]
        with patch('telegram_reports.http.time.monotonic',side_effect=[0,0,17]):
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

    def test_three_transport_failures_stop_and_expired_budget_stops_before_retry(self):
        from unittest.mock import patch
        source,ctx,response,error,url=self.setup_source()
        ctx.request.get.side_effect=error('read ETIMEDOUT')
        with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,3)
        ctx.request.get.reset_mock()
        with patch('telegram_reports.http.time.monotonic',side_effect=[0,0,181]):
            with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,1)

    def test_temporary_server_responses_retry_but_stop_after_three(self):
        source,ctx,response,error,url=self.setup_source()
        response.status=503;ctx.request.get.return_value=response
        with self.assertRaises(ConnectionError):source(ctx,url)
        self.assertEqual(ctx.request.get.call_count,3)


class ReportBrowserLoginFallback(unittest.TestCase):
    def test_only_transport_errors_enable_browser_login(self):
        import ssl,urllib.error
        from unittest.mock import Mock,patch
        from daily_metrics.core import StructureError
        from telegram_reports.session import report_bi
        for exc in (TimeoutError(),urllib.error.URLError('read timed out')):
            client=Mock();client.ensure_authenticated.side_effect=exc;browser=Mock()
            with patch('daily_metrics.extended_io.BIReports') as factory:
                report_bi(browser,client)
                factory.assert_called_once_with(browser,client,browser_login=True)
        for exc in (StructureError('SU login failed'),urllib.error.HTTPError('https://su.lifemart.ru',403,'forbidden',{},None),
                    urllib.error.URLError(ssl.SSLCertVerificationError('certificate'))):
            client=Mock();client.ensure_authenticated.side_effect=exc
            with patch('daily_metrics.extended_io.BIReports') as factory:
                with self.assertRaises(type(exc)):report_bi(Mock(),client)
                factory.assert_not_called()
        client=Mock();browser=Mock()
        with patch('daily_metrics.extended_io.BIReports') as factory:
            report_bi(browser,client)
            factory.assert_called_once_with(browser,client,browser_login=False)

    def test_browser_mode_uses_active_login_page_without_repeating_http_failure(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports,BI
        browser=Mock();client=Mock();client._authenticated=False
        client.ensure_authenticated.side_effect=TimeoutError('cached HTTP failure')
        page=browser.new_context.return_value.new_page.return_value
        page.goto.return_value.status=200;page.url=BI+'/superset/dashboard/37/'
        reader=BIReports(browser,client,browser_login=True);reader._browser_login=Mock()
        reader.authenticate()
        reader._browser_login.assert_called_once_with(page)
        client.ensure_authenticated.assert_not_called()
        self.assertEqual(page.bring_to_front.call_count,2)


class SharedReportTransport(unittest.TestCase):
    def test_only_existing_report_get_endpoints_are_allowed(self):
        from unittest.mock import Mock
        from telegram_reports.http import report_get
        from daily_metrics.core import StructureError
        from daily_metrics.config import BRANCHES
        ctx=Mock();ctx.request.get.return_value.status=200
        allowed=['https://bi.lifemart.ru/superset/explore/?form_data=1',
            'https://bi.lifemart.ru/superset/explore_json/?form_data=1',
            'https://su.lifemart.ru/turnover/waybill/report?day=21',
            'https://docs.google.com/spreadsheets/d/'+BRANCHES[0].book+'/export?format=pdf']
        for url in allowed:report_get(ctx,url)
        self.assertEqual(ctx.request.get.call_count,4)
        for url in ('http://bi.lifemart.ru/superset/explore/',
                    'https://bi.lifemart.ru/api/v1/chart/1',
                    'https://su.lifemart.ru/turnover/waybill/update/1',
                    'https://example.com/superset/explore/',
                    'https://docs.google.com/spreadsheets/d/unapproved/export'):
            with self.assertRaises(StructureError):report_get(ctx,url)
        self.assertEqual(ctx.request.get.call_count,4)

    def test_nested_renderer_budget_is_not_reset(self):
        from unittest.mock import Mock,patch
        from telegram_reports.http import report_get
        error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        ctx=Mock();ctx.request.get.side_effect=error('read ETIMEDOUT')
        with patch('telegram_reports.http.time.monotonic',side_effect=[0,0,30]):
            with self.assertRaises(ConnectionError):
                report_get(ctx,'https://bi.lifemart.ru/superset/explore/',timeout=20000)
        ctx.request.get.assert_called_once_with('https://bi.lifemart.ru/superset/explore/',timeout=20000)


class InvoiceHTTPRead(unittest.TestCase):
    def source(self):
        from unittest.mock import Mock
        from telegram_reports.invoices import invoice_source
        ctx=Mock();ctx.cookies.return_value=[{'name':'session','value':'opaque-test-session','domain':'su.lifemart.ru','path':'/','secure':True,'expires':-1}]
        return invoice_source,ctx,'https://su.lifemart.ru/turnover/waybill/report?day=21'

    def test_browser_session_is_kept_in_memory_and_read_uses_get(self):
        from unittest.mock import Mock,patch
        source,ctx,url=self.source();opener=Mock();response=Mock()
        response.read1.side_effect=[b'<html>report</html>',b'']
        opener.open.return_value.__enter__=Mock(return_value=response);opener.open.return_value.__exit__=Mock(return_value=False)
        with patch('telegram_reports.invoices.urllib.request.build_opener',return_value=opener) as factory:
            self.assertEqual(source(ctx,url),'<html>report</html>')
            cookies=list(factory.call_args.args[0].cookiejar)
        self.assertEqual(cookies[0].value,'opaque-test-session');self.assertTrue(cookies[0].secure)
        self.assertEqual(opener.open.call_args.args,(url,))
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'],60)

    def test_timeout_fallback_uses_only_remaining_budget(self):
        from unittest.mock import Mock,patch
        source,ctx,url=self.source();opener=Mock();opener.open.side_effect=TimeoutError()
        with patch('telegram_reports.invoices.urllib.request.build_opener',return_value=opener),patch('telegram_reports.invoices.time.monotonic',side_effect=[0,0,60]),patch('telegram_reports.invoices.report_get') as fallback:
            fallback.return_value.text.return_value='report'
            self.assertEqual(source(ctx,url),'report')
            fallback.assert_called_once_with(ctx,url,timeout=120000)

    def test_access_certificate_and_expired_deadline_do_not_fallback(self):
        import ssl,urllib.error
        from unittest.mock import Mock,patch
        from daily_metrics.core import StructureError
        source,ctx,url=self.source()
        for exc,expected in ((urllib.error.HTTPError(url,403,'forbidden',{},None),StructureError),
                            (urllib.error.URLError(ssl.SSLCertVerificationError('certificate')),urllib.error.URLError)):
            opener=Mock();opener.open.side_effect=exc
            with patch('telegram_reports.invoices.urllib.request.build_opener',return_value=opener),patch('telegram_reports.invoices.report_get') as fallback:
                with self.assertRaises(expected):source(ctx,url)
                fallback.assert_not_called()
        opener=Mock();opener.open.side_effect=TimeoutError()
        with patch('telegram_reports.invoices.urllib.request.build_opener',return_value=opener),patch('telegram_reports.invoices.time.monotonic',side_effect=[0,0,181]),patch('telegram_reports.invoices.report_get') as fallback:
            with self.assertRaises(ConnectionError):source(ctx,url)
            fallback.assert_not_called()


class InvoicePreflightCache(unittest.TestCase):
    def test_preflight_uses_exact_day_receiver_suppliers_without_new_page(self):
        import datetime as dt,urllib.parse
        from unittest.mock import Mock,patch
        from telegram_reports.invoices import fetch_invoices
        from daily_metrics.config import BRANCHES,INVOICE_SUPPLIERS
        ctx=Mock();branch=BRANCHES[0];day=dt.date(2026,11,21)
        with patch('telegram_reports.invoices.invoice_source',return_value='html') as source,patch('telegram_reports.invoices.invoice_total') as validate:
            self.assertEqual(fetch_invoices(ctx,branch,day),'html')
            validate.assert_called_once_with('html',branch.title,day,INVOICE_SUPPLIERS)
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(source.call_args.args[1]).query)
        self.assertEqual(params['Waybill[insert_date_from]'],['21.11.2026'])
        self.assertEqual(params['Waybill[insert_date_to]'],['21.11.2026'])
        self.assertEqual(params['Waybill[recipient_id][]'],[str(branch.department)])
        self.assertEqual(set(params['Waybill[contractor_id][]']),set(map(str,INVOICE_SUPPLIERS)))
        ctx.new_page.assert_not_called()

    def test_capture_reuses_and_revalidates_prefetched_html_without_network(self):
        import datetime as dt
        from unittest.mock import Mock,patch
        from telegram_reports.invoices import capture_invoices
        from daily_metrics.config import BRANCHES
        branch=BRANCHES[0];ctx=Mock();day=dt.date(2026,11,21)
        headings=['Подразделение','Подразделение получения товара','Контрагент','Сумма без НДС','Сумма НДС','Сумма с НДС']
        source='<table><tr>'+''.join('<th>'+h+'</th>' for h in headings)+'</tr><tr><td>accounting</td><td>'+branch.title+'</td><td>Supplier</td><td>10</td><td>2</td><td>12</td></tr></table>'
        with patch('telegram_reports.invoices.fetch_invoices') as fetch,patch('telegram_reports.invoices.invoice_total') as validate:
            capture_invoices(ctx,branch,day,'image.png',source=source)
            fetch.assert_not_called();validate.assert_called_once()
        ctx.request.get.assert_not_called()
        ctx.new_page.return_value.locator.return_value.screenshot.assert_called_once_with(path='image.png')

class TelegramDeliveryOutcomes(unittest.TestCase):
    def test_definitive_rejection_is_distinct_from_unknown_transport_failure(self):
        import io,json,tempfile,urllib.error
        from pathlib import Path
        from telegram_reports.transport import send_album
        with tempfile.TemporaryDirectory() as directory:
            photo=Path(directory)/'one.png'
            __import__('PIL.Image',fromlist=['Image']).new('RGB',(16,16),'white').save(photo)
            payload={'ok':False,'error_code':400,'description':'private diagnostic unit-key'}
            error=urllib.error.HTTPError('https://api.telegram.org/redacted',400,'bad request',{},io.BytesIO(json.dumps(payload).encode()))
            with patch('urllib.request.urlopen',side_effect=error) as request:
                with self.assertRaises(BotError) as caught:send_album(BotClient('unit-key'),123,[photo,photo],'caption')
                self.assertEqual(request.call_count,1)
                self.assertEqual(caught.exception.delivery_state,'rejected')
                self.assertEqual(caught.exception.details,payload)
                self.assertNotIn('unit-key',str(caught.exception))
                self.assertNotIn('private diagnostic',str(caught.exception))

    def test_one_unconfirmed_branch_does_not_block_other_approved_albums(self):
        import datetime as dt,json,os,tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from daily_metrics.config import BRANCHES
        from telegram_reports.run import deliver_albums
        for state in ('unknown','rejected'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as directory:
                previous=os.getcwd();os.chdir(directory)
                try:
                    images=[]
                    for branch in BRANCHES[1:]:
                        folder=Path(str(branch.department));folder.mkdir();photo=folder/'one.png';photo.write_bytes(b'\x89PNGdummy')
                        images.append((branch,[photo]*8,'caption'))
                    day=dt.date(2026,11,21)
                    with patch('telegram_reports.run.reporting_day',return_value=day),patch('telegram_reports.run.send_album',side_effect=[BotError('safe failure',delivery_state=state),8]) as send:
                        with self.assertRaises(BotError):deliver_albums(images,Mock(),123,day)
                        self.assertEqual(send.call_count,2)
                    self.assertEqual(json.loads(Path('telegram-receipts/153.json').read_text())['status'],state)
                    self.assertEqual(json.loads(Path('telegram-receipts/357.json').read_text())['status'],'accepted')
                finally:os.chdir(previous)

    def test_existing_attempt_stays_protected_without_explicit_missing_branch(self):
        from daily_metrics.config import BRANCHES
        from telegram_reports.run import branches_for_delivery
        attempted={BRANCHES[0].department}
        self.assertEqual(branches_for_delivery(list(BRANCHES),attempted),list(BRANCHES[1:]))
        self.assertEqual(branches_for_delivery(list(BRANCHES),attempted,BRANCHES[0].name),list(BRANCHES))
        all_attempted={b.department for b in BRANCHES}
        self.assertEqual(branches_for_delivery(list(BRANCHES),all_attempted),[])
        self.assertEqual(branches_for_delivery(list(BRANCHES),all_attempted,'all'),list(BRANCHES))


class TelegramRecipientSafety(unittest.TestCase):
    def test_recipient_mismatch_stops_remaining_albums(self):
        import datetime as dt,os,tempfile,json
        from pathlib import Path
        from unittest.mock import Mock
        from daily_metrics.config import BRANCHES
        from telegram_reports.run import deliver_albums
        with tempfile.TemporaryDirectory() as directory:
            previous=os.getcwd();os.chdir(directory)
            try:
                photo=Path('one.png');photo.write_bytes(b'\x89PNGdummy')
                day=dt.date(2026,11,21)
                collected=[(b,[photo]*8,'caption') for b in BRANCHES]
                with patch('telegram_reports.run.reporting_day',return_value=day),patch('telegram_reports.run.send_album',side_effect=BotError('recipient mismatch',delivery_state='unsafe')) as send:
                    with self.assertRaises(BotError):deliver_albums(collected,Mock(),123,day)
                    self.assertEqual(send.call_count,1)
                self.assertEqual(json.loads(Path('telegram-receipts/115.json').read_text())['status'],'unsafe')
            finally:os.chdir(previous)


class TelegramPhotoEncoding(unittest.TestCase):
    def test_native_png_remains_unchanged_and_jpeg_keeps_dimensions(self):
        import io
        from PIL import Image,PngImagePlugin
        from telegram_reports.transport import telegram_photo
        image=Image.new('RGB',(2357,929),'white')
        info=PngImagePlugin.PngInfo();info.add_text('Comment','native source metadata')
        stream=io.BytesIO();image.save(stream,format='PNG',pnginfo=info);native=stream.getvalue()
        result=telegram_photo(native)
        self.assertTrue(native.startswith(b'\x89PNG'))
        with Image.open(io.BytesIO(result)) as prepared:
            self.assertEqual(prepared.format,'JPEG');self.assertEqual(prepared.mode,'RGB')
            self.assertEqual(prepared.size,(2357,929))
            self.assertNotIn('Comment',prepared.info)
        with Image.open(io.BytesIO(native)) as source:self.assertEqual(source.info['Comment'],'native source metadata')

    def test_transparency_is_flattened_to_white_without_resizing(self):
        import io
        from PIL import Image
        from telegram_reports.transport import telegram_photo
        stream=io.BytesIO();Image.new('RGBA',(32,24),(255,0,0,0)).save(stream,format='PNG')
        with Image.open(io.BytesIO(telegram_photo(stream.getvalue()))) as image:
            self.assertEqual(image.size,(32,24));self.assertEqual(image.mode,'RGB')
            self.assertTrue(all(channel>=250 for channel in image.getpixel((0,0))))

class TelegramMultipartJPEG(unittest.TestCase):
    def test_wire_mime_and_attachment_names_match_jpeg_bytes(self):
        import io,json,tempfile
        from pathlib import Path
        from PIL import Image
        from telegram_reports.transport import send_album
        with tempfile.TemporaryDirectory() as directory:
            photo=Path(directory)/'source.png';Image.new('RGB',(32,24),'white').save(photo)
            native=photo.read_bytes()
            payload={'ok':True,'result':[{'chat':{'id':123,'type':'private'}}]*2}
            with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as request:
                self.assertEqual(send_album(BotClient('unit-key'),123,[photo,photo],'caption'),2)
                body=request.call_args.args[0].data
            self.assertEqual(body.count(b'Content-Type: image/jpeg'),2)
            self.assertEqual(body.count(b'filename="report.jpg"'),2)
            self.assertEqual(body.count(b'\xff\xd8'),2)
            self.assertNotIn(b'Content-Type: image/png',body)
            self.assertEqual(photo.read_bytes(),native)
