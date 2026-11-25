import unittest
from decimal import Decimal
from telegram_reports.invoices import source_totals
from daily_metrics.core import StructureError

class InvoiceRounding(unittest.TestCase):
    columns=list(range(6))
    def rows(self,base='71622.70',vat='5963.87',total='77660.07',footer_total=None):
        row=['shop','shop','Supplier',base,vat,total]
        footer=['','','',base,vat,total if footer_total is None else footer_total]
        return [row,footer],[row]
    def test_su_vat_arithmetic_does_not_replace_or_block_billed_total(self):
        table,data=self.rows()
        self.assertEqual(source_totals(table,0,self.columns,data),[Decimal('71622.70'),Decimal('5963.87'),Decimal('77660.07')])
    def test_seven_kopecks_rounding_preserves_exact_source_footer(self):
        table,data=self.rows(total='77660.00',footer_total='77660.07')
        self.assertEqual(source_totals(table,0,self.columns,data)[2],Decimal('77660.07'))
    def test_one_ruble_boundary_and_larger_billed_discrepancy(self):
        for difference,accepted in [('1.00',True),('1.01',False)]:
            table,data=self.rows(total='100.00',footer_total=str(Decimal('100')+Decimal(difference)))
            if accepted:self.assertEqual(source_totals(table,0,self.columns,data)[2],Decimal('101'))
            else:
                with self.assertRaises(StructureError):source_totals(table,0,self.columns,data)
    def test_no_footer_keeps_all_source_column_sums(self):
        table,data=self.rows()
        self.assertEqual(source_totals(table[:1],0,self.columns,data)[2],Decimal('77660.07'))
    def test_ambiguous_footer_stops_instead_of_guessing(self):
        table,data=self.rows();table.append(table[-1])
        with self.assertRaises(StructureError):source_totals(table,0,self.columns,data)

    def test_native_excerpt_keeps_su_amounts_and_existing_scope_checks(self):
        import datetime as dt
        from unittest.mock import Mock
        from daily_metrics.config import BRANCHES,INVOICE_SUPPLIERS
        from telegram_reports.invoices import capture_invoices
        branch=BRANCHES[0];supplier=next(iter(INVOICE_SUPPLIERS.values()))
        headers=['Дата и время приема товара','Подразделение','Подразделение получения товара','Контрагент','Сумма без НДС','Сумма НДС','Сумма с НДС']
        def source(receiver):
            row=['24.11.2026 07:00',branch.title,receiver,supplier,'71622.70','5963.87','77660.07']
            footer=['','','','','71622.70','5963.87','77660.07']
            return '<table><tr>'+''.join('<th>'+v+'</th>' for v in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+v+'</td>' for v in r)+'</tr>' for r in (row,footer))+'</table>'
        context=Mock()
        capture_invoices(context,branch,dt.date(2026,11,24),'image.png',source=source(branch.title))
        markup=context.new_page.return_value.set_content.call_args.args[0]
        self.assertIn('77 660,07',markup)
        self.assertNotIn('77 586,57',markup)
        wrong=Mock()
        with self.assertRaises(StructureError):capture_invoices(wrong,branch,dt.date(2026,11,24),'image.png',source=source('other store'))
        wrong.new_page.assert_not_called()
