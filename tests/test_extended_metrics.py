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
    def sample(self,date='18.11.2026 07:00',store='shop',supplier='Supplier'):
        return '<table><tr><th>Дата и время приема товара</th><th>Подразделение</th><th>Контрагент</th><th>Сумма без НДС</th><th>Сумма с НДС</th></tr>'+f'<tr><td>{date}</td><td>{store}</td><td>{supplier}</td><td>100</td><td>105</td></tr><tr><td></td><td></td><td></td><td>100</td><td>105</td></tr></table>'
    def test_vat_total_and_no_double_counting_footer(self):
        from daily_metrics.extended_core import invoice_total
        self.assertEqual(invoice_total(self.sample(),'shop',dt.date(2026,11,18),{1:'Supplier'}),105)
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
