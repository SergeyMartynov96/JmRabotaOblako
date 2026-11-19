import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from daily_metrics.core import StructureError,a1,value
from daily_metrics.procurement_run import fill_procurement,future_value,procurement_days

DAY=dt.date(2030,11,19)
BRANCH=SimpleNamespace(book='test-book',procurement_gid=1,newest_first=False)


def revenue_rows(days):
    header=['']*13
    for col,label in ((2,'выручка'),(3,'списания'),(6,'Топ готовая'),(7,'Топ десерты'),(8,'Топ Япония'),(12,'закуп факт')):header[col]=label
    return [header]+[[day.strftime('%d.%m.%Y')]+['']*12 for day in days]


def source_rows(entries):
    return [['','Дата','Поставщик','СУММА ГОТОВАЯ+ЯПОНИЯ','Итого десерты']]+[
        ['',day.strftime('%d.%m.%Y'),supplier,total,'900'] for day,supplier,total in entries]

class Sheet:
    def __init__(self,rows,existing=None):
        self.rows=rows;self.cells=dict(existing or {});self.allowed=set();self.anchors={};self.writes=[]
        self.native={a1(r,0):dt.datetime.strptime(row[0],'%d.%m.%Y').date() for r,row in enumerate(rows) if row and row[0]}
    def is_reporting_date(self,cell,day):return self.native.get(cell)==day
    def stable_formula(self,cell):return self.cells.get(cell,'')
    def cell_has_content(self,cell):return bool(self.cells.get(cell,''))
    def anchor_value(self,cell):return ('date',str(self.native[cell])) if cell in self.native else ('string','закуп факт')
    def put_blank(self,cell,literal):
        assert cell in self.allowed and cell.startswith('M')
        if self.cells.get(cell):return False
        self.cells[cell]=literal;self.writes.append((cell,literal));return True


def summary():return {'written':0,'planned':0,'preserved':0,'missing':0}

class ProcurementWindow(unittest.TestCase):
    def test_today_and_tomorrow_follow_yesterday_even_when_yesterday_filled(self):
        days=procurement_days(DAY);rows=revenue_rows(days);sheet=Sheet(rows,{'M2':'existing','O3':'invoice-preserved'})
        load=Mock(return_value=source_rows([(days[1],'100.50','100.50'),(days[2],'200','200')]))
        counts=summary();clock=Mock()
        self.assertEqual(fill_procurement(sheet,BRANCH,DAY,rows,True,counts,clock,load),[])
        self.assertEqual(sheet.writes,[('M3','101'),('M4','200')]);load.assert_called_once()
        self.assertEqual(sheet.cells['O3'],'invoice-preserved')
        self.assertEqual(sheet.allowed,{'M3','M4'})
        self.assertTrue(all(call.args==(DAY,) for call in clock.call_args_list))

    def test_prefilled_three_days_never_query_procurement_source(self):
        rows=revenue_rows(procurement_days(DAY));sheet=Sheet(rows,{'M2':'0','M3':'—','M4':'=SUM(C1:D1)'})
        load=Mock(side_effect=TimeoutError());counts=summary()
        self.assertEqual(fill_procurement(sheet,BRANCH,DAY,rows,True,counts,Mock(),load),[])
        load.assert_not_called();self.assertEqual(counts['preserved'],3);self.assertEqual(sheet.writes,[])

    def test_future_pending_has_no_dash_zero_or_error_write(self):
        days=procurement_days(DAY);rows=revenue_rows(days);sheet=Sheet(rows,{'M2':'existing'})
        load=Mock(return_value=source_rows([(days[1],'','0'),(days[2],'','–')]))
        counts=summary()
        self.assertEqual(fill_procurement(sheet,BRANCH,DAY,rows,True,counts,Mock(),load),[])
        self.assertEqual(sheet.writes,[]);self.assertEqual(counts['procurement_pending'],2)

    def test_clock_guard_is_not_disabled_for_future_writes(self):
        days=procurement_days(DAY);rows=revenue_rows(days);sheet=Sheet(rows,{'M2':'existing'})
        with self.assertRaises(StructureError):
            fill_procurement(sheet,BRANCH,DAY,rows,True,summary(),Mock(side_effect=StructureError('Date changed')),
                             Mock(return_value=source_rows([(days[1],'10','10')])))
        self.assertEqual(sheet.writes,[])

    def test_csv_failure_is_cached_and_never_written_as_dash(self):
        rows=revenue_rows(procurement_days(DAY));sheet=Sheet(rows);load=Mock(side_effect=TimeoutError())
        errors=fill_procurement(sheet,BRANCH,DAY,rows,True,summary(),Mock(),load)
        self.assertEqual(len(errors),3);load.assert_called_once();self.assertEqual(sheet.writes,[])

    def test_future_week_not_prepared_is_skipped_without_inventing_dates(self):
        rows=revenue_rows([DAY]);sheet=Sheet(rows,{'M2':'existing'});counts=summary();load=Mock()
        self.assertEqual(fill_procurement(sheet,BRANCH,DAY,rows,True,counts,Mock(),load),[])
        self.assertEqual(counts['procurement_pending'],2);load.assert_not_called()

    def test_preview_never_writes(self):
        days=procurement_days(DAY);rows=revenue_rows(days);sheet=Sheet(rows);sheet.put_blank=Mock(side_effect=AssertionError('Read only'))
        counts=summary()
        self.assertEqual(fill_procurement(sheet,BRANCH,DAY,rows,False,counts,Mock(),Mock(return_value=source_rows([(d,'10','10') for d in days]))),[])
        sheet.put_blank.assert_not_called();self.assertEqual(counts['planned'],3)

    def test_full_year_native_anchor_rejects_historical_same_short_date(self):
        days=procurement_days(DAY);rows=revenue_rows(days);rows[1][0]=days[1].strftime('%d.%m.%Y')
        sheet=Sheet(rows,{'M3':'current-filled','M4':'filled'});sheet.native['A2']=days[1].replace(year=2029)
        # Historical row stays untouched; yesterday is mandatory, so reject its missing date.
        with self.assertRaises(StructureError):fill_procurement(sheet,BRANCH,DAY,rows,True,summary(),Mock(),Mock())
        self.assertEqual(sheet.writes,[])

class FutureAvailability(unittest.TestCase):
    def test_nonzero_total_and_explicit_zero_supplier_are_available(self):
        self.assertEqual(future_value(source_rows([(DAY,'10','10')]),DAY),'10')
        self.assertEqual(future_value(source_rows([(DAY,'0','0')]),DAY),'0')
        self.assertEqual(future_value(source_rows([(DAY,'','0.49')]),DAY),'0')
    def test_zero_formula_without_supplier_data_stays_pending(self):
        self.assertIsNone(future_value(source_rows([(DAY,'','0')]),DAY))
        self.assertIsNone(future_value(source_rows([]),DAY))
    def test_food_japan_totals_exclude_dessert_and_its_zero_readiness(self):
        rows=[['','Дата','Еда','Итого готовая еда','Десерт','Итого десерты','Япония','Итого Япония'],
              ['',DAY.strftime('%d.%m.%Y'),'10','10','900','900','20','20']]
        self.assertEqual(future_value(rows,DAY),'30')
        rows[1][2]='';rows[1][3]='0';rows[1][6]='';rows[1][7]='0'
        self.assertIsNone(future_value(rows,DAY))
    def test_window_crosses_month_and_year_without_changing_other_metrics(self):
        self.assertEqual(procurement_days(dt.date(2030,12,31)),(dt.date(2030,12,31),dt.date(2031,1,1),dt.date(2031,1,2)))

class ManualValueSafety(unittest.TestCase):
    def test_native_zero_and_amount_preserved_when_formula_bar_lags(self):
        from daily_metrics.io import RevenueSheet
        from unittest.mock import patch
        sheet=object.__new__(RevenueSheet);sheet.branch=SimpleNamespace(book='test',revenue_gid=1)
        sheet.stable_formula=Mock(return_value='')
        for native in (0,123,'—'):
            with patch('daily_metrics.io.native_cell',return_value=('number',native)):
                self.assertTrue(sheet.cell_has_content('M3'))
        with patch('daily_metrics.io.native_cell',return_value=('string',None)):
            self.assertFalse(sheet.cell_has_content('M3'))
    def test_empty_result_formula_still_counts_as_occupied(self):
        from daily_metrics.io import RevenueSheet
        from unittest.mock import patch
        sheet=object.__new__(RevenueSheet);sheet.stable_formula=Mock(return_value='=""')
        with patch('daily_metrics.io.native_cell') as native:
            self.assertTrue(sheet.cell_has_content('M3'));native.assert_not_called()
