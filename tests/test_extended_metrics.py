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
        self.assertEqual(bi_reasons(payload,'Списания %'),{'damage':Decimal('.300')})

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
        self.assertEqual(summary['reconciled_damage'],1)
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
        with self.assertRaises(StructureError):bi_reasons(payload,'Списания %')


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
        sheet.formula=Mock(return_value='=IF(TRUE,"",1)')
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
