import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from daily_metrics.extended_run import fill_extra

class ReasonRefresh(unittest.TestCase):
    def execute(self,apply=True,changed=False):
        layout={'uv':(0,5),'osa':(0,9),'invoices':(0,15),'date_anchors':[],
            'reasons':{'sg_purchase':[(2,0)],'sg_manual':[(2,1)],'sg_min_stock':[(2,2)],'marketing':[(2,3)],'damage':[(2,4)],'tasting':[(2,5)]}}
        cells={'A3':'4,3%','B3':'','C3':'','D3':'0,2%','E3':'=1/100','F3':'0,5%'}
        sheet=Mock();sheet.allowed=set();sheet.anchors={};sheet.stable_formula.side_effect=lambda cell:cells[cell]
        sheet.replace_if_unchanged.return_value=not changed;sheet.put_blank.return_value=True
        values={'sg_purchase':3,'sg_manual':.3,'sg_min_stock':.1,'marketing':0,'damage':.1}
        source=Mock();source.read.return_value=values
        summary={'written':0,'preserved':0,'missing':0,'planned':0}
        with patch('daily_metrics.extended_run.extra_targets',return_value=layout):
            errors=fill_extra(sheet,source,SimpleNamespace(name='М42'),dt.date(2026,11,23),0,0,[],apply,summary,Mock(),kinds=('reasons',))
        return sheet,source,summary,errors
    def test_refresh_changed_reason_and_fill_new_causes_only(self):
        sheet,source,summary,errors=self.execute()
        self.assertEqual(errors,[])
        source.read.assert_called_once()
        self.assertEqual(source.read.call_args.args[0],'reasons')
        sheet.replace_if_unchanged.assert_called_once_with('A3','4,3%','3,0%')
        self.assertEqual([c.args for c in sheet.put_blank.call_args_list],[('B3','0,3%'),('C3','0,1%')])
        self.assertEqual(summary['written'],3)
        self.assertEqual(summary['reconciled_reasons'],1)
        # Existing zero/absent causes, formulas and unrelated metric cells stay untouched.
        self.assertEqual(sheet.allowed,{'A3','B3','C3','D3','E3','F3'})
    def test_preview_never_changes_prefilled_or_blank_cells(self):
        sheet,_,summary,errors=self.execute(apply=False)
        self.assertEqual(errors,[]);self.assertEqual(summary['planned'],3)
        sheet.put_blank.assert_not_called();sheet.replace_if_unchanged.assert_not_called()
    def test_concurrent_change_blocks_report_refresh(self):
        sheet,_,summary,errors=self.execute(changed=True)
        self.assertEqual(errors,['reasons destination changed during reconciliation'])
        self.assertEqual(summary['written'],2)

class ReportSnapshotSync(unittest.TestCase):
    def test_saved_summary_must_match_snapshot_even_for_preserved_formulas(self):
        from daily_metrics.extended_run import sync_report_reasons
        from daily_metrics.core import StructureError
        for raw,valid in [(0.003,True),(0.099,False)]:
            sheet=Mock();sheet.refresh.return_value=[[],[],['','9,9%']]
            sheet.is_reporting_date.return_value=True;sheet.anchor_value.return_value='23.11.2026'
            branch=SimpleNamespace(name='М42',book='book',revenue_gid=0,newest_first=True)
            with patch('daily_metrics.io.RevenueSheet',return_value=sheet),patch('daily_metrics.io.native_cell',return_value=('number',raw)),patch('daily_metrics.core.target_candidates',return_value=[(0,0)]),patch('daily_metrics.extended_core.bi_reasons',return_value={'workshop':.3}),patch('daily_metrics.extended_run.extra_targets',return_value={'reasons':{'workshop':[(2,1)]}}),patch('daily_metrics.extended_run.fill_extra',return_value=[]),patch('daily_metrics.run.check_date'):
                if valid:sync_report_reasons(Mock(),branch,dt.date(2026,11,23),{})
                else:
                    with self.assertRaisesRegex(StructureError,'snapshot differs'):sync_report_reasons(Mock(),branch,dt.date(2026,11,23),{})
            sheet.page.close.assert_called_once()

    def test_report_preview_is_read_only_and_delivery_syncs_before_export(self):
        import tempfile
        from pathlib import Path
        from decimal import Decimal
        from telegram_reports.run import assemble
        kinds=('reasons','top_qty','top_rub','ui2_days','ui2','uv_dishes','uv_stop','osa_all','osa_food_japan','osa_food','osa_japan')
        branch=SimpleNamespace(name='М42');payload={'validated':'same object'}
        for deliver in (False,True):
            events=[]
            with tempfile.TemporaryDirectory() as directory,patch('daily_metrics.extended_run.sync_report_reasons',side_effect=lambda *a:events.append('sync') or {'written':3}) as sync,patch('telegram_reports.run.capture_week',side_effect=lambda *a,**k:events.append('export')),patch('telegram_reports.run.capture_invoices'),patch('telegram_reports.run.compose'):
                root=Path(directory);panels={k:(k,root/'source.png') for k in kinds}
                assemble(Mock(),Mock(),branch,dt.date(2026,11,23),root,panels,{'reasons':{'payload':payload}},[Decimal(1),Decimal(2)],sync_reason_cells=deliver)
                self.assertEqual(events,['sync','export'] if deliver else ['export'])
                if deliver:self.assertIs(sync.call_args.args[-1],payload)
                else:sync.assert_not_called()
