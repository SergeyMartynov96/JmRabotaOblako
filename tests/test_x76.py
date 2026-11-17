import datetime as dt
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cloud_production as production
import cloud_k44_schedule as schedule
import cloud_window_state as state
import cloud_heartbeat as heartbeat


class X76Checks(unittest.TestCase):
    def test_x76_plan_is_exact_and_no_request_edits(self):
        with patch.dict(os.environ,{'LIFEMART_SCOPE':'Х76'}):
            self.assertEqual(production.active_branches(),(production.X76_BRANCH,))
            name,dep,ident,gid=production.X76_BRANCH
            items=[dict(id='1',quantity='2',price='100',packing='1',name='Food')]
            entry=dict(items=items,before=production.fingerprint(items),after=production.fingerprint(items))
            plan=dict(branches=[dict(name=name,department=dep,ident=ident,gid=gid,groups=[dict(cards=[entry])])])
            production.validate_transfer_plan(plan)
            entry['desired']=[dict(items[0],quantity='1')]
            with self.assertRaises(production.Stop):production.validate_transfer_plan(plan)
            del entry['desired'];plan['branches'][0]['gid']='293705136'
            with self.assertRaises(production.Stop):production.validate_transfer_plan(plan)

    def test_first_production_is_tomorrow_after_1600(self):
        with patch.dict(os.environ,{'LIFEMART_SCOPE':'Х76','LIFEMART_PREVIEW':'true'}):
            production.check_transfer_time(preview=True,now=dt.datetime(2026,11,17,18))
            for now in [dt.datetime(2026,11,17,18),dt.datetime(2026,11,18,15,59)]:
                with self.assertRaises(production.Stop):production.check_transfer_time(now=now)
            production.check_transfer_time(now=dt.datetime(2026,11,18,16))

    def test_scheduled_and_manual_apply_cannot_write_today(self):
        for event in ['schedule','workflow_dispatch']:
            setup=schedule.resolve(event,'apply','2026-11-17T13:00:00Z',scope='Х76')
            self.assertEqual(setup['operation'],'verify')
            tomorrow=schedule.resolve(event,'apply','2026-11-18T11:00:00Z',scope='Х76')
            self.assertEqual(tomorrow['operation'],'apply')
            self.assertEqual(tomorrow['start'],'16:00')
            self.assertEqual(tomorrow['window'],'x76')
            self.assertEqual(tomorrow['expected_date'],'2026-11-18')
        self.assertEqual(schedule.resolve('schedule','apply','2026-11-17T11:00:00Z')['operation'],'apply')

    def test_receipts_do_not_mix_k44_and_x76(self):
        key='2026-11-18-endofday';run=dict(workflow_id=42,status='completed',conclusion='success',head_branch='main',event='workflow_dispatch')
        artifact=dict(name='lifemart-x76-complete-'+key,expired=False,workflow_run={'id':1})
        with patch.dict(os.environ,{'LIFEMART_SCOPE':'Х76'}):
            self.assertTrue(state.is_complete(key,[artifact],lambda _:run,42))
            self.assertFalse(state.is_complete(key,[dict(artifact,name='lifemart-k44-complete-'+key)],lambda _:run,42))
        self.assertIn('/lifemart-x76?',heartbeat.cronitor_url('x76','a'*24))

    def test_other_editing_branches_stay_disabled(self):
        for scope in ['М42','Г34','all']:
            with patch.dict(os.environ,{'LIFEMART_SCOPE':scope}),self.assertRaises(production.Stop):
                production.active_branches()


if __name__=='__main__':unittest.main()
