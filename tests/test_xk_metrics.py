import datetime as dt
import unittest
from unittest.mock import patch
from xk_metrics.config import STORES
from xk_metrics.layout import FIELDS,candidates,targets
from xk_metrics.run import procurement_value
from xk_metrics.sources import read_tops
from daily_metrics.core import StructureError

class SeparateStoreMetrics(unittest.TestCase):
    def test_source_store_requires_the_verified_full_title(self):
        from unittest.mock import MagicMock
        from xk_metrics.sources import resolve_branch
        doc=MagicMock();doc.tables=[('ЖМ Тюмень Харьковская 77',[])]
        with patch('xk_metrics.sources.Report',return_value=doc):
            with self.assertRaises(StructureError):resolve_branch(MagicMock(),STORES['Х76'],dt.date(2026,11,26))
            doc.tables=[(STORES['Х76'].branch.title,[])]
            self.assertEqual(resolve_branch(MagicMock(),STORES['Х76'],dt.date(2026,11,26)).department,401)

    def test_forbidden_http_response_is_never_retried(self):
        from urllib.error import HTTPError
        from xk_metrics.run import main
        with patch('sys.argv',['x76','--operation','preview']),patch('xk_metrics.run.execute',side_effect=HTTPError('https://su.lifemart.ru',403,'Forbidden',{},None)) as execute:
            with self.assertRaises(HTTPError):main('Х76')
        self.assertEqual(execute.call_count,1)

    def test_transient_failure_retries_only_the_selected_store(self):
        from xk_metrics.run import main
        with patch('sys.argv',['k44','--operation','preview']),patch('xk_metrics.run.execute',side_effect=[TimeoutError(),None]) as execute:
            main('К44')
        self.assertEqual(execute.call_count,2)
        self.assertTrue(all(call.args[0].branch.name=='К44' for call in execute.call_args_list))

    def rows(self):
        rows=[['']*48 for _ in range(25)]
        for store in STORES.values():
            o=store.offset;rows[0][o+1]=store.marker
            for c,label in FIELDS.values():rows[1][o+c]=label
            for i in range(7):
                rows[2+i][o+1]=f'{23+i}.11'
                rows[13+i][o+1]=('пн','вт','ср','чт','пт','сб','вс')[i]
            rows[12][o+2]='План';rows[12][o+12]='закуп' if not o else 'срок закуп'
            rows[12][o+13]='цех' if not o else 'перемещения'
        return rows
    def test_stores_on_one_sheet_are_strictly_separate(self):
        rows=self.rows();day=dt.date(2026,11,26)
        for store in STORES.values():
            self.assertEqual(candidates(rows,store,day),[(5,1)])
            metrics,reasons,header=targets(rows,store,5,1,day)
            self.assertTrue(all(store.offset<=c<store.offset+25 for r,c in list(metrics.values())+list(reasons.values())))
            self.assertEqual(metrics['lower_revenue'],(16,store.offset+7))
            self.assertIn('срокгодностизакуп',reasons)
            self.assertNotIn('перемещения',reasons)
    def test_foreign_store_marker_is_not_a_target(self):
        rows=self.rows();rows[0][1]='другой магазин'
        self.assertEqual(candidates(rows,STORES['Х76'],dt.date(2026,11,26)),[])
    def test_header_change_fails_instead_of_shifting_writes(self):
        rows=self.rows();rows[1][7]='OSA 17:00'
        with self.assertRaises(StructureError):targets(rows,STORES['Х76'],5,1,dt.date(2026,11,26))
    def test_procurement_uses_only_each_store_main_total(self):
        for name,col in [('Х76',18),('К44',20)]:
            rows=[['']*25 for _ in range(4)];rows[2][col]=name+' итог'
            rows[3][1]='26.11.2026';rows[3][2]='350';rows[3][col]='350';rows[3][24]='900'
            self.assertEqual(procurement_value(rows,STORES[name],dt.date(2026,11,26)),'350')
            rows[3][2]='';rows[3][col]='0'
            self.assertIsNone(procurement_value(rows,STORES[name],dt.date(2026,11,26)))
    def test_tops_evening_blanks_are_not_formula_zero(self):
        rows=[['']*4 for _ in range(70)]
        for h,start,end in ((0,2,32),(35,37,47),(50,52,67)):
            rows[h][1]='26.11';rows[h+1][2]='18:00'
            for r in range(start,end):rows[r][0]='product'
            rows[end][2]='0'
        with patch('xk_metrics.sources.csv_sheet',return_value=rows):
            with self.assertRaisesRegex(StructureError,'marks not filled'):read_tops(STORES['К44'],dt.date(2026,11,26))
            for r in (2,37,52):rows[r][2]='0'
            self.assertEqual(read_tops(STORES['К44'],dt.date(2026,11,26)),['0','0','0'])
    def test_jobs_are_independent_and_dependencies_precede_tests(self):
        from pathlib import Path
        for slug in ('x76','k44'):
            workflow=Path(f'.github/workflows/{slug}-morning-metrics.yml').read_text()
            self.assertIn("cron: '0 2 * * *'",workflow)
            self.assertIn(f'python3 -m xk_metrics.{slug}',workflow)
            self.assertLess(workflow.index('pip install'),workflow.index('unittest discover'))
