import datetime as dt,unittest
from unittest.mock import patch
from xk_metrics.config import STORES
from xk_reports.run import caption,collect
from xk_reports.sheets import export_bounds

class Reports(unittest.TestCase):
    def test_private_caption(self):
        for name,store in STORES.items():
            self.assertEqual(caption(store.branch),name.lower()+'\nобщая таблица\nсписания по десертам\nсписания по позициям\nув по позициям')
    def test_disjoint_store_crops(self):
        a=export_bounds(STORES['Х76'],2027,2038);b=export_bounds(STORES['К44'],2027,2038)
        self.assertEqual((a['c1'],a['c2']),(1,25));self.assertEqual((b['c1'],b['c2']),(26,50))
        self.assertLessEqual(a['c2'],b['c1']);self.assertEqual((a['r1'],a['r2']),(2026,2047))
    def test_daily_tops_weekly_ui2_and_uv(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        charts=Mock();charts.read.return_value=({}, {})
        day=dt.date(2026,11,26)
        with tempfile.TemporaryDirectory() as folder,patch('xk_reports.run.active_page'),patch('xk_reports.run.capture_chart'):
            panels=collect(Mock(),charts,STORES['Х76'],day,Path(folder)/'images')
        self.assertEqual(list(panels),['ui2_days','ui2','top_qty','top_rub','uv_dishes'])
        for call in charts.read.call_args_list:
            kind,branch,start,end=call.args
            self.assertEqual(start,day if kind.startswith('top_') else dt.date(2026,11,23))
            self.assertEqual(end,dt.date(2026,11,27));self.assertEqual(branch,STORES['Х76'].branch)
