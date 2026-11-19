import datetime as dt
import unittest
from daily_metrics.core import DASH,StructureError,money,tops,procurement,revenue_report,writeoff_report,lower_targets,target_candidates
from daily_metrics.run import reporting_day,percentage_literal
from zoneinfo import ZoneInfo

DAY=dt.date(2026,11,17)

class MetricsRules(unittest.TestCase):
    def test_reporting_date_is_yekaterinburg_yesterday(self):
        now=dt.datetime(2026,11,18,0,1,tzinfo=ZoneInfo('Asia/Yekaterinburg'))
        self.assertEqual(reporting_day(now),DAY)
    def test_round_money(self):
        self.assertEqual(money('9 026,79'),'9027')
        self.assertEqual(money('2,50'),'3')
    def blocks(self,entries=('1','0'),total='50'):
        rows=[]
        for name in ('Готовая еда','Десерты','Япония'):
            rows += [['','17.11.2026',''],[name],['Очень длинное название продукта 1','1',entries[0]],['Очень длинное название продукта 2','1',entries[1]],['Итого:','100',total],[]]
        return rows
    def test_evening_total_only_and_order(self):
        self.assertEqual(tops(self.blocks(total='70'),DAY),['30']*3)
    def test_explicit_zero_versus_unfilled(self):
        self.assertEqual(tops(self.blocks(('0','0'),'0'),DAY),['100']*3)
        self.assertEqual(tops(self.blocks(('',''),'0'),DAY),[DASH]*3)
    def test_missing_tops_period_is_business_missing_data(self):
        self.assertEqual(tops(None,DAY),[DASH]*3)
    def test_full_availability_is_zero_absence(self):
        self.assertEqual(tops(self.blocks(('1','1'),'100'),DAY),['0']*3)
    def test_invalid_top_percentage_not_silently_used(self):
        with self.assertRaises(StructureError):tops(self.blocks(total='101'),DAY)
    def test_food_and_japan_without_desserts(self):
        rows=[['','Дата','Итого готовая еда','Итого Япония','Итого десерты'],['','17.11.2026','200','300','999']]
        self.assertEqual(procurement(rows,DAY),'500')
        self.assertEqual(procurement(rows,dt.date(2026,11,18)),DASH)
    def test_duplicate_procurement_date_rejected(self):
        rows=[['','Дата','Итого готовая еда'],['','17.11.2026','200'],['','17.11.2026','300']]
        with self.assertRaises(StructureError):procurement(rows,DAY)
    def test_revenue_exact_department_and_total(self):
        def table(title,n):return f'<h5>{title}</h5><table><tr><th>Дата</th><th>17.11.2026</th><th>Итоги:</th></tr><tr><td>Итоговая выручка</td><td>1</td><td>{n}</td></tr></table>'
        html=table('Другой филиал','999')+table('М42','192 517')
        self.assertEqual(revenue_report(html,'М42',DAY),'192517')
        self.assertEqual(revenue_report(html,'Г34',DAY),DASH)
        with self.assertRaises(StructureError):revenue_report(html,'М42',dt.date(2026,11,18))
    def test_writeoff_sums_documents_not_footer_twice(self):
        html='<table><tr><th>Дата</th><th>Подразделение</th><th>Себестоимость</th></tr><tfoot><tr><td></td><td></td><td>30</td></tr></tfoot><tbody><tr><td>17.11.2026</td><td>М42</td><td>10,40</td></tr><tr><td>17.11.2026</td><td>М42</td><td>19,60</td></tr></tbody></table>'
        self.assertEqual(writeoff_report(html,'М42',DAY),'30')
        with self.assertRaises(StructureError):writeoff_report(html,'Г34',DAY)
    def test_visible_percentage_precision(self):
        self.assertEqual(percentage_literal('6,1','6,10%'),'6,1%')
        self.assertEqual(percentage_literal('6,1','6,10'),'6,1')

if __name__=='__main__':unittest.main()

class BlankOnlyWrite(unittest.TestCase):
    def test_existing_number_dash_zero_and_formula_are_preserved(self):
        from daily_metrics.io import RevenueSheet
        from unittest.mock import Mock
        for existing in ('123','0','—','=SUM(A1:A2)'):
            sheet=object.__new__(RevenueSheet)
            sheet.allowed={'C5'};sheet.anchors={};sheet.formula=Mock(return_value=existing)
            sheet.page=Mock()
            self.assertFalse(sheet.put_blank('C5','999'))
            sheet.page.keyboard.type.assert_not_called()
    def test_date_movement_blocks_write(self):
        from daily_metrics.io import RevenueSheet
        from unittest.mock import Mock
        sheet=object.__new__(RevenueSheet)
        sheet.allowed={'C5'};sheet.anchors={'A5':'17.11.2026'}
        sheet.formula=Mock(return_value='18.11.2026');sheet.page=Mock()
        with self.assertRaises(StructureError):sheet.put_blank('C5','999')
        sheet.page.keyboard.type.assert_not_called()
    def test_procurement_cells_cannot_be_written(self):
        from daily_metrics.io import RevenueSheet
        sheet=object.__new__(RevenueSheet);sheet.allowed={'C5'}
        with self.assertRaises(StructureError):sheet.put_blank('V17','999')

class SharedRevenueReport(unittest.TestCase):
    def test_three_stores_use_one_report(self):
        from daily_metrics.io import SUReports,REVENUE
        from daily_metrics.config import BRANCHES
        from unittest.mock import Mock
        client=SUReports();client.login=Mock();client.request=Mock(return_value='report')
        for branch in BRANCHES:self.assertEqual(client.revenue(branch,DAY),'report')
        client.request.assert_called_once()
        self.assertEqual(client.request.call_args.args[1]['department[]'],['115','153','357'])
    def test_failed_report_is_not_repeated_for_other_stores(self):
        from daily_metrics.io import SUReports
        from daily_metrics.config import BRANCHES
        from unittest.mock import Mock
        client=SUReports();client.login=Mock();client.request=Mock(side_effect=TimeoutError())
        for branch in BRANCHES:
            with self.assertRaises(TimeoutError):client.revenue(branch,DAY)
        client.request.assert_called_once()

class PercentEntryRegression(unittest.TestCase):
    def test_percent_precision_is_numeric_not_string_equality(self):
        from daily_metrics.core import same_cell_value
        self.assertTrue(same_cell_value('4,80%','4,8%'))
        self.assertTrue(same_cell_value('0,048','4,8%'))
        self.assertFalse(same_cell_value('4,8','4,8%'))
        self.assertFalse(same_cell_value('4,8%%','4,8%'))
        self.assertFalse(same_cell_value('4,80%%','4,8%'))
        self.assertFalse(same_cell_value('4,8%','4,8'))
        self.assertTrue(same_cell_value('182 951','182951'))
        self.assertTrue(same_cell_value('—','—'))
    def test_blank_percent_uses_single_paste_and_verifies_formatted_number(self):
        from daily_metrics.io import RevenueSheet
        from unittest.mock import Mock
        sheet=object.__new__(RevenueSheet)
        sheet.allowed={'L18'};sheet.anchors={}
        sheet.formula=Mock(side_effect=['','4,80%'])
        sheet.select=Mock();sheet.page=Mock()
        self.assertTrue(sheet.put_blank('L18','4,8%'))
        sheet.page.keyboard.type.assert_not_called()
        sheet.page.evaluate.assert_called_once_with('(text) => navigator.clipboard.writeText(text)','4,8%')
        sheet.page.keyboard.press.assert_any_call('Control+V')

class LazySUAuthentication(unittest.TestCase):
    def test_already_filled_run_never_attempts_su_login(self):
        from daily_metrics import run
        from unittest.mock import Mock,patch
        import types,sys
        now=dt.datetime.now(run.TZ).date().isoformat()
        client=Mock();pw=Mock();manager=Mock()
        manager.__enter__=Mock(return_value=pw);manager.__exit__=Mock(return_value=False)
        module=types.ModuleType('playwright.sync_api');module.sync_playwright=Mock(return_value=manager)
        with patch.dict('os.environ',{'GITHUB_ACTIONS':'true','EXPECTED_RUN_DATE':now,'GITHUB_STEP_SUMMARY':''}),patch.object(sys,'argv',['metrics','--branch','М42']),patch.dict(sys.modules,{'playwright.sync_api':module}),patch.object(run,'SUReports',return_value=client),patch.object(run,'ExtraSources',return_value=Mock()),patch.object(run,'fill_branch',return_value=[]):
            run.main()
        client.login.assert_not_called()
    def test_login_is_shared_by_required_sources(self):
        from daily_metrics.io import SUReports
        from daily_metrics.config import BRANCHES
        from unittest.mock import Mock
        client=SUReports();client.login=Mock();client.request=Mock(return_value='report')
        client.revenue(BRANCHES[0],DAY)
        client.writeoff(BRANCHES[0],DAY)
        client.revenue(BRANCHES[1],DAY)
        client.login.assert_called_once()
        self.assertEqual(client.request.call_count,2)
    def test_failed_auth_not_repeated_by_other_branch_or_metric(self):
        from daily_metrics.io import SUReports
        from daily_metrics.config import BRANCHES
        from unittest.mock import Mock
        client=SUReports();client.login=Mock(side_effect=TimeoutError());client.request=Mock()
        for branch in BRANCHES:
            with self.assertRaises(TimeoutError):client.revenue(branch,DAY)
            with self.assertRaises(TimeoutError):client.writeoff(branch,DAY)
        client.login.assert_called_once();client.request.assert_not_called()
