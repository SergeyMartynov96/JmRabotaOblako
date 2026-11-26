import unittest,datetime as dt
from unittest.mock import patch
from telegram_reports.sheets import assert_summary_complete
from daily_metrics.core import StructureError,DASH

class SummaryReadiness(unittest.TestCase):
    def check(self,revenue='100',reason_blank=True):
        rows=[['1']*19 for _ in range(4)];rows[0][2]=revenue
        if reason_blank:rows[3]=['']*19
        layout={'uv':(0,5),'osa':(0,9),'invoices':(0,15),'reasons':{}}
        with patch('telegram_reports.sheets.extra_targets',return_value=layout),patch('telegram_reports.sheets.lower_targets',return_value=((1,2),(1,3))):
            assert_summary_complete(rows,0,0,dt.date(2026,11,25))
    def test_blank_completed_day_and_formula_error_block_the_report(self):
        for raw in ('','#DIV/0!','not a metric'):
            with self.assertRaisesRegex(StructureError,'revenue'):self.check(raw)
    def test_real_zero_and_explicit_source_absence_are_not_invented_data(self):
        for raw in ('0',DASH,'100,25'):self.check(raw)
    def test_unreported_reason_cells_can_remain_blank(self):
        self.check(reason_blank=True)
