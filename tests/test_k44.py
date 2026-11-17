import datetime as dt
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_production as production
import cloud_k44_schedule as schedule
import cloud_window_state as state
import cloud_readonly_test as ro
import cloud_heartbeat as heartbeat


class K44Checks(unittest.TestCase):
    def test_k44_is_separate_from_default_av12(self):
        self.assertEqual(production.active_branches()[0][0], 'АВ12')
        with patch.dict(os.environ, {'LIFEMART_SCOPE':'К44'}):
            self.assertEqual(production.active_branches(), (production.K44_BRANCH,))
            name,dep,ident,gid=production.K44_BRANCH
            plan=dict(branches=[dict(name=name,department=dep,ident=ident,gid=gid,groups=[])])
            production.validate_transfer_plan(plan)
            plan['branches'][0]['department']=115
            with self.assertRaises(production.Stop):
                production.validate_transfer_plan(plan)

    def test_write_cannot_run_before_1600_even_with_preview_env(self):
        with patch.dict(os.environ, {'LIFEMART_SCOPE':'К44', 'LIFEMART_PREVIEW':'true'}):
            now=dt.datetime(2026,11,17,15,59)
            production.check_transfer_time(preview=True, now=now)
            with self.assertRaises(production.Stop):
                production.check_transfer_time(now=now)
            production.check_transfer_time(now=now.replace(hour=16,minute=0))

    def test_pending_supplier_blocks_completion_but_preview_can_inspect(self):
        entry=dict(supplier='Кабо',status='Ожидает отправки (16:00) +5 GMT')
        self.assertTrue(production.k44_candidate(entry,['Дата','Кабо'],preview=True))
        with self.assertRaises(production.Stop):
            production.k44_candidate(entry,['Дата','Кабо'])
        self.assertFalse(production.k44_candidate(entry,['Дата','Мисфуд']))
        for status in ['На рассмотрении','Отгружена','Отправлено']:
            self.assertTrue(production.k44_candidate(dict(entry,status=status),['Дата','Кабо']))
        with self.assertRaises(production.Stop):
            production.k44_candidate(dict(entry,status='Unknown'),['Дата','Кабо'])

    def test_schedule_keeps_local_date_and_1600_start(self):
        config=schedule.resolve('schedule',requested_at='2026-11-17T11:00:00Z')
        self.assertEqual(config['operation'],'apply')
        self.assertEqual(config['start'],'16:00')
        self.assertEqual(config['expected_date'],'2026-11-17')
        self.assertEqual(config['window_key'],'2026-11-17-endofday')
        self.assertEqual(schedule.resolve('workflow_dispatch',requested_at='2026-11-17T06:00:00Z')['operation'],'preview')
        with self.assertRaises(ValueError):
            schedule.resolve('workflow_dispatch','recover')

    def test_receipts_and_monitor_are_isolated(self):
        key='2026-11-17-endofday'
        run=dict(workflow_id=2,status='completed',conclusion='success',head_branch='main',event='schedule')
        av=dict(name='lifemart-av12-complete-'+key,expired=False,workflow_run={'id':1})
        k44=dict(av,name='lifemart-k44-complete-'+key)
        with patch.dict(os.environ,{'LIFEMART_SCOPE':'К44'}):
            self.assertTrue(state.is_complete(key,[k44],lambda _:run,2))
            self.assertFalse(state.is_complete(key,[av],lambda _:run,2))
        self.assertFalse(state.is_complete(key,[k44],lambda _:run,2))
        self.assertIn('/lifemart-k44?',heartbeat.cronitor_url('k44','a'*24))

    def test_header_transposition_preserves_supplier_identity(self):
        self.assertEqual(ro.supplier_column('Амбруази',['Дата','амбраузи']),1)
        self.assertIsNone(ro.supplier_column('Амбруази',['Амбруази','Амбруази']))
        self.assertIsNone(ro.supplier_column('ИП Мухачев Кирилл Сергеевич (РЦ)',['Мурачев']))
        self.assertIsNone(ro.supplier_column('Мамадалиев',['Маматулин']))


if __name__ == '__main__':
    unittest.main()
