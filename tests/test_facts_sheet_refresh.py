import sys,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cloud_production as m

class FactsSheetRefresh(unittest.TestCase):
    def test_locate_refreshes_rows_before_matching_arrival(self):
        sheet=m.Sheet.__new__(m.Sheet)
        sheet.rows=[['','28.11.2026','old']];sheet.headers=['','Дата','supplier']
        def refresh():sheet.rows=[sheet.headers,['','29.11.2026','']]
        sheet.refresh=MagicMock(side_effect=refresh)
        sheet.formula=MagicMock(side_effect=['supplier','29.11.2026'])
        with patch.object(m.ro,'supplier_column',return_value=2):
            self.assertEqual(sheet.locate('supplier','29.11.2026'),(1,2))
        sheet.refresh.assert_called_once()
    def test_formula_requires_independent_selections(self):
        sheet=m.Sheet.__new__(m.Sheet);sheet.page=MagicMock();sheet.select=MagicMock()
        sheet.page.locator.return_value.inner_text.side_effect=['old','new','new']
        self.assertEqual(sheet.formula('B11'),'new');self.assertEqual(sheet.select.call_count,3)
