import datetime as dt
import unittest
from daily_metrics.extended_core import reason_key,percent_literal,extra_targets,REASON_ALIASES
from daily_metrics.core import StructureError

class ReasonMapping(unittest.TestCase):
    def test_report_causes_match_their_own_columns(self):
        self.assertEqual(reason_key('Срок годности. Закуп'),'sg_purchase')
        self.assertEqual(reason_key('Срок годности. Мин.остатки'),'sg_min_stock')
        self.assertEqual(reason_key('Цеховое питание'),'workshop')
        self.assertEqual(reason_key('Расходные материалы'),'consumables')
        self.assertEqual(reason_key('СГ мин. сум.'),'sg_min_sum')
        self.assertIsNone(reason_key('Неизвестная причина'))
    def test_store_scales_are_preserved(self):
        self.assertEqual(percent_literal('19.15','М42'),'19,15%')
        self.assertEqual(percent_literal('19.15','АВ12'),'19,15')
        self.assertEqual(percent_literal('19.15','Г34'),'19,15')
        self.assertEqual(percent_literal('49.66','АВ12',1),'49,7')
    def test_both_reason_blocks_and_exact_day_are_located(self):
        rows=[['']*19 for _ in range(45)]
        rows[0][5]='УВ';rows[0][9]='OSA 20:00';rows[0][15]='по накладным'
        first=['СГ закуп','цех','расходка','порча','маркетинг','дегустация']
        second=['СГ ротация','СГ товароб.','СГ мин. сум.','СГ ручное','СГ мин. ост.','СГ пост.']
        rows[10][11:17]=first;rows[21][11:17]=second
        for r,start in [(11,11),(22,22)]:
            for offset in range(7):rows[r+offset][9]=f'{16+offset}.11'
        result=extra_targets(rows,0,3,dt.date(2026,11,18))
        self.assertEqual(result['uv'],(3,5))
        self.assertEqual(result['reasons']['sg_purchase'],[(13,11)])
        self.assertEqual(result['reasons']['sg_min_stock'],[(24,15)])
        self.assertEqual(set(result['reasons']),set(REASON_ALIASES))
        rows[24][9]='19.11'
        with self.assertRaises(StructureError):extra_targets(rows,0,3,dt.date(2026,11,18))

if __name__=='__main__':unittest.main()

class DailyQuery(unittest.TestCase):
    def test_exact_day_store_and_no_saved_context_mutation(self):
        from daily_metrics.extended_core import daily_query
        saved={'queries':[{'time_range':'old','filters':[{'col':'title','val':['other']},{'col':'in_stock','op':'==','val':1}],
                           'columns':[{'timeGrain':'P1W'}],'extras':{'where':'business rule'}}]}
        result=daily_query(saved,dt.date(2026,11,18),'store')
        q=result['queries'][0]
        self.assertEqual(q['time_range'],'2026-11-18 : 2026-11-19')
        self.assertEqual(q['filters'],[{'col':'in_stock','op':'==','val':1},{'col':'title','op':'IN','val':['store']}])
        self.assertEqual(q['extras']['where'],'business rule')
        self.assertEqual(q['columns'][0]['timeGrain'],'P1D')
        self.assertEqual(saved['queries'][0]['time_range'],'old')

class MissingReasons(unittest.TestCase):
    def test_only_reported_reasons_are_returned_including_explicit_zero(self):
        from daily_metrics.extended_core import bi_reasons
        from decimal import Decimal
        payload={'result':[{'status':'success','data':[
            {'reason':'Срок годности. Мин.остатки','Списания %':.001},
            {'reason':'Расходные материалы','Списания %':0}]}]}
        result=bi_reasons(payload,'Списания %')
        self.assertEqual(result,{'sg_min_stock':Decimal('.100'),'consumables':Decimal('0')})
        self.assertNotIn('sg_rotation',result)
        self.assertNotIn('sg_purchase',result)
    def test_schema_error_does_not_become_empty_report(self):
        from daily_metrics.extended_core import bi_reasons
        with self.assertRaises(StructureError):bi_reasons({'result':[{'data':[{'unrelated':1}]}]},'Списания %')

class ReasonTransfer(unittest.TestCase):
    def test_unreported_cells_stay_untouched_and_existing_values_preserved(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_run import fill_extra
        day=dt.date(2026,11,18)
        layout={'uv':(3,5),'osa':(3,9),'invoices':(3,15),
                'reasons':{'sg_purchase':[(13,11)],'sg_min_stock':[(24,15)],'sg_rotation':[(24,11)]},'date_anchors':[]}
        sheet=Mock();sheet.allowed=set();sheet.anchors={}
        sheet.formula.side_effect=lambda cell: 'filled' if cell in ('F4','J4','P4','L14') else ''
        sheet.stable_formula=sheet.formula
        sheet.put_blank.return_value=True
        sources=Mock();sources.read.return_value={'sg_purchase':2.2,'sg_min_stock':.1}
        summary={'written':0,'preserved':0,'missing':0,'planned':0}
        branch=Mock(name='branch');branch.name='М42'
        with patch('daily_metrics.extended_run.extra_targets',return_value=layout):
            errors=fill_extra(sheet,sources,branch,day,0,3,[],True,summary,lambda d:None)
        self.assertEqual(errors,[])
        sheet.put_blank.assert_called_once_with('P25','0,1%')
        self.assertEqual(summary['preserved'],4)
        self.assertEqual(summary['written'],1)

class ReadOnlyBoundaries(unittest.TestCase):
    def test_invoice_mutations_rejected_before_network(self):
        from daily_metrics.io import SUReports
        client=SUReports()
        for path in ('/turnover/waybill/report','/turnover/waybill/update/1','/handbook/biSupersetSettings/index'):
            with self.assertRaises(ValueError):client.request(path,{'x':'1'})
    def test_bi_mutations_rejected_before_network(self):
        from daily_metrics.extended_io import BIReports
        from unittest.mock import Mock
        reader=BIReports(Mock(),Mock());reader.authenticate=Mock()
        with self.assertRaises(StructureError):reader.api('/api/v1/chart/1',{'x':'1'})

class InvoiceReport(unittest.TestCase):
    def sample(self,date='18.11.2026 07:00',store='shop',supplier='Supplier',accounting='shop'):
        return '<table><tr><th>Дата и время приема товара</th><th>Подразделение</th><th>Подразделение получения товара</th><th>Контрагент</th><th>Сумма без НДС</th><th>Сумма с НДС</th></tr>'+f'<tr><td>{date}</td><td>{accounting}</td><td>{store}</td><td>{supplier}</td><td>100</td><td>105</td></tr><tr><td></td><td></td><td></td><td></td><td>100</td><td>105</td></tr></table>'
    def test_vat_total_and_no_double_counting_footer(self):
        from daily_metrics.extended_core import invoice_total
        self.assertEqual(invoice_total(self.sample(),'shop',dt.date(2026,11,18),{1:'Supplier'}),105)
    def test_receiver_can_differ_from_accounting_department(self):
        from daily_metrics.extended_core import invoice_total
        self.assertEqual(invoice_total(self.sample(accounting='warehouse'),'shop',dt.date(2026,11,18),{1:'Supplier'}),105)
    def test_missing_explicit_receiver_uses_document_department(self):
        from daily_metrics.extended_core import invoice_total
        self.assertEqual(invoice_total(self.sample(store=''),'shop',dt.date(2026,11,18),{1:'Supplier'}),105)
    def test_request_uses_receiving_department_filter(self):
        from unittest.mock import Mock
        from urllib.parse import urlsplit,parse_qs
        from daily_metrics.extended_io import ExtraSources
        from daily_metrics.config import INVOICE_SUPPLIERS
        client=Mock();client.request.return_value=self.sample(supplier=next(iter(INVOICE_SUPPLIERS.values())))
        source=ExtraSources(Mock(),client);branch=Mock(department=357,title='shop')
        self.assertEqual(source.read('invoices',branch,dt.date(2026,11,18)),105)
        params=parse_qs(urlsplit(client.request.call_args.args[0]).query)
        self.assertEqual(params['Waybill[recipient_id][]'],['357'])
        self.assertNotIn('Waybill[department_id][]',params)
    def test_other_day_store_supplier_and_partial_pages_rejected(self):
        from daily_metrics.extended_core import invoice_total
        for html in (self.sample(date='17.11.2026'),self.sample(store='other'),self.sample(supplier='other'),self.sample()+'<a href="?Waybill_page=2">2</a>'):
            with self.assertRaises(StructureError):invoice_total(html,'shop',dt.date(2026,11,18),{1:'Supplier'})

class BIWait(unittest.TestCase):
    def test_each_data_request_has_three_minute_limit_and_detects_explicit_error(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports
        reader=BIReports(Mock(),Mock());reader.authenticate=Mock();reader.context=Mock()
        token=Mock(status=200);token.json.return_value={'result':'test-csrf'}
        response=Mock(status=200);response.json.return_value={'errors':[{'error_type':'GENERIC_DB_ENGINE_ERROR'}]}
        reader.context.request.get.return_value=token
        reader.context.request.post.return_value=response
        with self.assertRaisesRegex(StructureError,'GENERIC_DB_ENGINE_ERROR'):
            reader.api('/api/v1/chart/data',{'queries':[]})
        self.assertEqual(reader.context.request.post.call_args.kwargs['timeout'],180000)

class BIDay(unittest.TestCase):
    def test_number_is_accepted_only_for_selected_store_and_day(self):
        from daily_metrics.extended_core import bi_percent
        from decimal import Decimal
        payload={'result':[{'status':'success','data':[{'title':'shop','__timestamp':'2026-11-18T00:00:00','UV':.1952}]}]}
        self.assertEqual(bi_percent(payload,'UV','shop',dt.date(2026,11,18),'uv'),Decimal('19.5200'))
        with self.assertRaises(StructureError):bi_percent(payload,'UV','other',dt.date(2026,11,18),'uv')
        with self.assertRaises(StructureError):bi_percent(payload,'UV','shop',dt.date(2026,11,17),'uv')

class OSASelection(unittest.TestCase):
    def test_only_20_hour_series_is_requested_and_business_filters_retained(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports
        reader=BIReports(Mock(),Mock())
        reader.template=Mock(return_value=({'slice_id':3696},{'queries':[{
            'metrics':['OSA "На 11:00"','OSA "На 20:00"','OSA "На утро"'],
            'filters':[{'col':'in_stock','op':'==','val':1}],
            'post_processing':[{'operation':'pivot'}]}]}))
        reader.api=Mock(return_value={'result':[]})
        branch=Mock(department=153,title='shop')
        reader.read('osa',branch,dt.date(2026,11,18))
        query=reader.api.call_args.args[1]['queries'][0]
        self.assertEqual(query['metrics'],['OSA "На 20:00"'])
        self.assertIn({'col':'in_stock','op':'==','val':1},query['filters'])
        self.assertEqual(query['post_processing'],[])

class ChartBinding(unittest.TestCase):
    def test_exact_saved_chart_is_loaded_without_whole_dashboard(self):
        import json
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports,CHARTS
        reader=BIReports(Mock(),Mock())
        reader.api=Mock(return_value={'result':{'slice_name':CHARTS['osa'][1],
            'params':json.dumps({'datasource':'4626__table'}),
            'query_context':json.dumps({'queries':[{'metrics':['OSA "На 20:00"']}]})}})
        form,context=reader.template('osa')
        self.assertEqual(form['slice_id'],3696)
        self.assertEqual(context['queries'][0]['metrics'],['OSA "На 20:00"'])
        reader.api.assert_called_once_with('/api/v1/chart/3696')
    def test_defects_are_added_to_damage_as_requested(self):
        from daily_metrics.extended_core import bi_reasons
        from decimal import Decimal
        payload={'result':[{'data':[{'reason':'Порча','Списания %':.001},{'reason':'Брак','Списания %':.002}]}]}
        self.assertEqual(bi_reasons(payload,'Списания %'),{'damage':Decimal('.300'),'raw_defect':Decimal('.200')})

class CombinedDamage(unittest.TestCase):
    def test_defect_metadata_does_not_leak_to_other_store(self):
        from daily_metrics.extended_core import bi_reasons
        combined=bi_reasons({'result':[{'data':[{'reason':'Порча','p':.001},{'reason':'Брак','p':.002}]}]},'p')
        other=bi_reasons({'result':[{'data':[{'reason':'Порча','p':.001}]}]},'p')
        self.assertTrue(combined.has_defect)
        self.assertFalse(other.has_defect)
        self.assertEqual(combined.damage_base,other['damage'])
    def test_replaces_only_confirmed_standalone_damage(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_core import ReasonValues
        from daily_metrics.extended_run import fill_extra
        result=ReasonValues(damage=.3);result.damage_base=.1;result.has_defect=True
        layout={'uv':(3,5),'osa':(3,9),'invoices':(3,15),'reasons':{'damage':[(17,14)]},'date_anchors':[]}
        sheet=Mock();sheet.allowed=set();sheet.anchors={}
        sheet.formula.side_effect=lambda cell:'0,1' if cell=='O18' else 'filled'
        sheet.stable_formula=sheet.formula
        sheet.replace_if_unchanged.return_value=True
        sources=Mock();sources.read.return_value=result
        summary={'written':0,'preserved':0,'missing':0,'planned':0}
        branch=Mock();branch.name='АВ12'
        with patch('daily_metrics.extended_run.extra_targets',return_value=layout):
            errors=fill_extra(sheet,sources,branch,dt.date(2026,11,18),0,3,[],True,summary,lambda day:None)
        self.assertEqual(errors,[])
        sheet.replace_if_unchanged.assert_called_once_with('O18','0,1','0,3')
        sheet.put_blank.assert_not_called()
        self.assertEqual(summary['reconciled_reasons'],1)
        self.assertEqual(summary['written'],1)
    def test_changed_manual_value_and_formula_are_never_replaced(self):
        from unittest.mock import Mock
        from daily_metrics.io import RevenueSheet
        for current in ('0,7','=0.1'):
            sheet=RevenueSheet.__new__(RevenueSheet);sheet.allowed={'O18'};sheet.anchors={'A6':'18.11.2026'}
            sheet.page=Mock();sheet.anchor_value=Mock(return_value='18.11.2026');sheet.formula=Mock(return_value=current)
            sheet._confirm_selection=lambda cell,result:result
            self.assertFalse(sheet.replace_if_unchanged('O18','0,1','0,3'))
            sheet.page.evaluate.assert_not_called()
    def test_browser_login_requires_secrets_before_network(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_io import BIReports
        reader=BIReports(Mock(),Mock());page=Mock()
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(StructureError):reader._browser_login(page)
        page.goto.assert_not_called()

class StableAnchors(unittest.TestCase):
    def test_stale_formula_bar_is_not_captured_as_week_heading(self):
        from unittest.mock import Mock
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.page=Mock()
        sheet.formula=Mock(side_effect=['18.11.2026','16.11-22.11','16.11-22.11'])
        sheet._confirm_selection=lambda cell,result:result
        self.assertEqual(sheet.stable_formula('G3132'),'16.11-22.11')
    def test_recheck_allows_transient_stale_read_but_rejects_real_week_change(self):
        from unittest.mock import Mock
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.page=Mock();sheet.anchors={'G3132':'16.11-22.11'}
        sheet.anchor_value=Mock(side_effect=['stale','16.11-22.11'])
        sheet.check_anchors()
        sheet.anchor_value=Mock(return_value='23.11-29.11')
        with self.assertRaisesRegex(StructureError,'G3132'):sheet.check_anchors()

class LoginErrors(unittest.TestCase):
    def test_http_error_is_detected_without_waiting_for_absent_form(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_io import BIReports
        reader=BIReports(Mock(),Mock());page=Mock();page.goto.return_value=Mock(status=503)
        with patch.dict('os.environ',{'LIFEMART_LOGIN':'test-user','LIFEMART_PASSWORD':'test-pass'}):
            with self.assertRaisesRegex(StructureError,'HTTP 503'):reader._browser_login(page)
        page.locator.assert_not_called()

class NativeDateAnchors(unittest.TestCase):
    def test_unformatted_date_keeps_year_even_with_short_display(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import native_cell,RevenueSheet
        import json
        payload={'status':'ok','table':{'cols':[{'type':'date'}],'rows':[{'c':[{'v':'Date(2026,10,18)','f':'18.11'}]}]}}
        body='google.visualization.Query.setResponse('+json.dumps(payload)+');'
        with patch('daily_metrics.io.get',return_value=body):
            self.assertEqual(native_cell('book',0,'A6'),('date','Date(2026,10,18)'))
            sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock(book='book',revenue_gid=0)
            self.assertTrue(sheet.is_reporting_date('A6',dt.date(2026,11,18)))
            self.assertFalse(sheet.is_reporting_date('A6',dt.date(2025,11,18)))
    def test_unexpected_native_response_is_not_a_blank_date(self):
        from unittest.mock import patch
        from daily_metrics.io import native_cell
        with patch('daily_metrics.io.get',return_value='<html>unavailable</html>'):
            with self.assertRaises(StructureError):native_cell('book',0,'A6')


class BIHTTPAuthentication(unittest.TestCase):
    def test_http_login_precedes_browser_context_and_failure_stops_browser(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports
        events=[]
        client=Mock()
        client.ensure_authenticated.side_effect=lambda: events.append('login')
        browser=Mock()
        def context():
            events.append('context')
            raise RuntimeError('stop before navigation')
        browser.new_context.side_effect=context
        reader=BIReports(browser,client)
        with self.assertRaises(RuntimeError):reader.authenticate()
        self.assertEqual(events,['login','context'])
        client.ensure_authenticated.side_effect=StructureError('login unavailable')
        browser.reset_mock()
        reader=BIReports(browser,client)
        with self.assertRaises(StructureError):reader.authenticate()
        browser.new_context.assert_not_called()


class TrainingReasonMapping(unittest.TestCase):
    def test_training_is_added_to_tasting_without_affecting_other_reasons(self):
        from decimal import Decimal
        from daily_metrics.extended_core import bi_reasons
        payload={'result':[{'status':'success','data':[
            {'reason':'Обучение','Списания %':.001},
            {'reason':'Срок годности. Закуп','Списания %':.02}]}]}
        self.assertEqual(bi_reasons(payload,'Списания %'),{'tasting':Decimal('.100'),'sg_purchase':Decimal('2.00')})
        payload['result'][0]['data'].append({'reason':'Дегустация','Списания %':.002})
        self.assertEqual(bi_reasons(payload,'Списания %')['tasting'],Decimal('.300'))
        payload['result'][0]['data'][0]['reason']='Unknown schema reason'
        partial=bi_reasons(payload,'Списания %')
        self.assertEqual(partial.unmapped,['Unknown schema reason'])
        self.assertEqual(partial['tasting'],Decimal('.200'))


class NativeSelectionConfirmation(unittest.TestCase):
    def test_stale_scalar_cannot_hide_empty_destination(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.page=Mock();sheet.branch=Mock()
        sheet.select=Mock();sheet.formula=Mock(return_value='')
        with patch('daily_metrics.io.native_cell',return_value=('number',None)):
            self.assertEqual(sheet._confirm_selection('L10','12345'),'')
        sheet.page.keyboard.press.assert_called_once_with('Escape')
    def test_empty_result_formula_is_preserved(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock();sheet.page=Mock();sheet.select=Mock()
        sheet.formula=Mock(return_value='=IF(TRUE,"",1)');sheet._reload_selection=Mock(return_value='=IF(TRUE,"",1)')
        with patch('daily_metrics.io.native_cell',return_value=('string',None)):
            self.assertEqual(sheet._confirm_selection('L10','=IF(TRUE,"",1)'),'=IF(TRUE,"",1)')


class PersistedWriteConfirmation(unittest.TestCase):
    def test_local_echo_waits_for_server_and_accepts_percent_roundoff(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock();sheet.page=Mock()
        with patch('daily_metrics.io.native_cell',side_effect=[('number',None),('number',.04800000000000001)]) as read:
            sheet._wait_persisted('L10','4,8%')
        self.assertEqual(read.call_count,2)
    def test_unsaved_echo_cannot_be_reported_as_written(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock();sheet.page=Mock()
        with patch('daily_metrics.io.native_cell',return_value=('number',None)):
            with self.assertRaisesRegex(StructureError,'not persisted'):sheet._wait_persisted('L10','4,8')


class NativeConcurrentEditSafety(unittest.TestCase):
    def test_stale_old_formula_cannot_replace_a_new_manual_scalar(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock();sheet.page=Mock()
        sheet.allowed={'L10'};sheet.check_anchors=Mock();sheet.stable_formula=Mock(return_value='0,1')
        with patch('daily_metrics.io.native_cell',return_value=('number',.7)):
            self.assertFalse(sheet.replace_if_unchanged('L10','0,1','0,3'))
        sheet.page.evaluate.assert_not_called()


class StaleFormulaRecovery(unittest.TestCase):
    def test_old_formula_does_not_hide_a_blank_native_cell(self):
        from unittest.mock import Mock,patch
        from daily_metrics.io import RevenueSheet
        sheet=RevenueSheet.__new__(RevenueSheet);sheet.branch=Mock();sheet.page=Mock();sheet.select=Mock()
        sheet.formula=Mock(return_value='=SUM(A1:A2)');sheet._reload_selection=Mock(return_value='')
        with patch('daily_metrics.io.native_cell',return_value=('number',None)):
            self.assertEqual(sheet._confirm_selection('J2233','=SUM(A1:A2)'), '')
        sheet._reload_selection.assert_called_once_with('J2233')

class InitialBIPageReadiness(unittest.TestCase):
    def test_accounting_commit_waits_for_active_superset_document(self):
        from unittest.mock import Mock,ANY
        from daily_metrics.extended_io import BIReports,BI
        browser=Mock();client=Mock();client._authenticated=True;client.opener.handlers=[]
        page=browser.new_context.return_value.new_page.return_value
        page.url=BI+'/login/accounting';page.goto.return_value.status=200
        events=[]
        page.bring_to_front.side_effect=lambda:events.append('active')
        def commit(*args,**kwargs):
            events.append('commit');return Mock(status=200)
        page.goto.side_effect=commit
        def ready(*args,**kwargs):
            events.append('ready');page.url=BI+'/superset/dashboard/37/'
        page.wait_for_url.side_effect=ready
        def token(*args,**kwargs):
            events.append('csrf');return Mock(json_value=lambda:'test-csrf')
        page.wait_for_function.side_effect=token
        reader=BIReports(browser,client);reader.authenticate()
        self.assertEqual(events,['active','commit','ready','csrf'])
        page.wait_for_url.assert_called_once_with(BI+'/superset/**',wait_until='commit',timeout=ANY)
        self.assertIs(reader.context,browser.new_context.return_value)

class BITransportRetry(unittest.TestCase):
    def reader(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports
        reader=BIReports(Mock(),Mock());reader.authenticate=Mock();reader.context=Mock();reader.csrf='test-csrf'
        return reader
    def test_transport_failure_retries_identical_read_inside_same_budget(self):
        from unittest.mock import Mock,patch
        Error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        reader=self.reader();response=Mock(status=200);response.json.return_value={'result':[]}
        reader.context.request.post.side_effect=[Error('private transport detail'),response]
        with patch('daily_metrics.extended_io.time.monotonic',side_effect=[0,1,2]):
            self.assertEqual(reader.api('/api/v1/chart/data',{'queries':[]}),{'result':[]})
        calls=reader.context.request.post.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].kwargs['data'],calls[1].kwargs['data'])
        self.assertEqual(calls[1].kwargs['timeout'],178000)
    def test_exhausted_budget_does_not_start_another_request(self):
        from unittest.mock import patch
        Error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        reader=self.reader();reader.context.request.post.side_effect=Error('private')
        with patch('daily_metrics.extended_io.time.monotonic',side_effect=[0,181]):
            with self.assertRaisesRegex(ConnectionError,'BI transport unavailable'):reader.api('/api/v1/chart/data',{})
        self.assertEqual(reader.context.request.post.call_count,1)
    def test_transient_html_server_error_can_retry_but_access_denial_cannot(self):
        from unittest.mock import Mock
        reader=self.reader();temporary=Mock(status=503);valid=Mock(status=200);valid.json.return_value={'result':[]}
        reader.context.request.post.side_effect=[temporary,valid]
        self.assertEqual(reader.api('/api/v1/chart/data',{}),{'result':[]})
        temporary.json.assert_not_called()
        reader=self.reader();denied=Mock(status=403);denied.json.return_value={'errors':[{'error_type':'FORBIDDEN'}]}
        reader.context.request.post.return_value=denied
        with self.assertRaises(StructureError):reader.api('/api/v1/chart/data',{})
        self.assertEqual(reader.context.request.post.call_count,1)


class BIReadDeadlineBoundary(unittest.TestCase):
    def test_deadline_expiring_between_retries_stops_before_network(self):
        from unittest.mock import patch
        Error=type('Error',(Exception,),{'__module__':'playwright._impl._errors'})
        reader=BITransportRetry().reader();reader.context.request.post.side_effect=Error('private')
        with patch('daily_metrics.extended_io.time.monotonic',side_effect=[0,179,181]):
            with self.assertRaisesRegex(ConnectionError,'deadline exhausted'):reader.api('/api/v1/chart/data',{})
        self.assertEqual(reader.context.request.post.call_count,1)


class FullManualWriteoffReason(unittest.TestCase):
    def test_full_bi_label_maps_to_existing_manual_column_alongside_other_reasons(self):
        from decimal import Decimal
        from daily_metrics.extended_core import bi_reasons
        payload={'result':[{'status':'success','data':[
            {'reason':'Срок годности. Ручное редактирование','Списания %':.001},
            {'reason':'Срок годности. Закуп','Списания %':.02}]}]}
        self.assertEqual(reason_key('СГ ручное'),'sg_manual')
        self.assertEqual(bi_reasons(payload,'Списания %'),{'sg_manual':Decimal('.100'),'sg_purchase':Decimal('2.00')})

class ZeroReasonTransfer(unittest.TestCase):
    def test_skip_rounded_zero_only_for_reasons_all_stores(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_run import fill_extra
        for store in ('АВ12','М42','Г34'):
            with self.subTest(store=store):
                layout={'uv':(0,0),'osa':(0,1),'invoices':(0,2),
                    'reasons':{'sg_manual':[(1,0)],'sg_post':[(1,1)],'workshop':[(1,2)]},'date_anchors':[]}
                sheet=Mock();sheet.allowed=set();sheet.anchors={};sheet.stable_formula.return_value=''
                sheet.put_blank.return_value=True
                sources=Mock();sources.read.side_effect=lambda metric,*_: {'sg_manual':0,'sg_post':.04,'workshop':.1} if metric=='reasons' else 0
                summary={'written':0,'preserved':0,'missing':0,'planned':0};branch=Mock();branch.name=store
                with patch('daily_metrics.extended_run.extra_targets',return_value=layout):
                    self.assertEqual(fill_extra(sheet,sources,branch,dt.date(2026,11,20),0,0,[],True,summary,lambda _:None),[])
                self.assertEqual({call.args[0] for call in sheet.put_blank.call_args_list},{'A1','B1','C1','C2'})
                self.assertEqual(summary['written'],4)

class InitialBIResources(unittest.TestCase):
    def test_first_load_allows_scripts_bootstrap_and_styles(self):
        from unittest.mock import Mock
        from daily_metrics.extended_io import BIReports,BI
        browser=Mock();client=Mock();client._authenticated=True;client.opener.handlers=[]
        context=browser.new_context.return_value;page=context.new_page.return_value
        page.url=BI+'/superset/dashboard/37/';page.goto.return_value.status=200
        BIReports(browser,client).authenticate()
        handler=context.route.call_args.args[1]
        for kind in ('document','script','stylesheet','xhr','fetch'):
            route=Mock();route.request.resource_type=kind;handler(route)
            route.continue_.assert_called_once();route.abort.assert_not_called()
        route=Mock();route.request.resource_type='image';handler(route);route.abort.assert_called_once()

class UnknownReasonIsolation(unittest.TestCase):
    def test_known_reasons_survive_unknown_label_without_guessing(self):
        from daily_metrics.extended_core import bi_reasons
        from decimal import Decimal
        result=bi_reasons({'result':[{'data':[
            {'reason':'Цеховое питание','p':.002},
            {'reason':'Новая причина','p':.03},
            {'reason':'Срок годности. Закуп','p':.04}]}]},'p')
        self.assertEqual(result,{'workshop':Decimal('.200'),'sg_purchase':Decimal('4.00')})
        self.assertEqual(result.unmapped,['Новая причина'])

    def test_transfer_known_nonzero_and_keep_failure_visible(self):
        from unittest.mock import Mock,patch
        from daily_metrics.extended_run import fill_extra
        from daily_metrics.extended_core import bi_reasons
        result=bi_reasons({'result':[{'data':[
            {'reason':'Цеховое питание','p':.002},{'reason':'Новая причина','p':.03}]}]},'p')
        layout={'uv':(0,0),'osa':(0,1),'invoices':(0,2),
            'reasons':{'workshop':[(1,0)],'damage':[(1,1)]},'date_anchors':[]}
        sheet=Mock();sheet.allowed=set();sheet.anchors={}
        sheet.stable_formula.side_effect=lambda cell:'filled' if cell in ('A1','B1','C1') else ''
        sheet.put_blank.return_value=True
        source=Mock();source.read.return_value=result;branch=Mock();branch.name='АВ12'
        summary={'written':0,'preserved':0,'missing':0,'planned':0}
        with patch('daily_metrics.extended_run.extra_targets',return_value=layout):
            errors=fill_extra(sheet,source,branch,dt.date(2026,11,21),0,0,[],True,summary,lambda _:None)
        sheet.put_blank.assert_called_once_with('A2','0,2')
        self.assertEqual(errors,['reasons source failed (StructureError): Unmapped BI reason'])

class MinimumOrderAmountReason(unittest.TestCase):
    def test_full_bi_label_maps_to_existing_minimum_sum_column(self):
        from decimal import Decimal
        from daily_metrics.extended_core import bi_reasons
        self.assertEqual(reason_key('Срок годности. Мин.сумма заявки'),'sg_min_sum')
        result=bi_reasons({'result':[{'data':[
            {'reason':'Срок годности. Мин.сумма заявки','p':.12},
            {'reason':'Срок годности. Закуп','p':.03},
            {'reason':'Цеховое питание','p':.002}]}]},'p')
        self.assertEqual(result,{'sg_min_sum':Decimal('12.00'),'sg_purchase':Decimal('3.00'),'workshop':Decimal('.200')})
        self.assertEqual(result.unmapped,[])
