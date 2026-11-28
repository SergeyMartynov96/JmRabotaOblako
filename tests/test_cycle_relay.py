import datetime as dt,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from daily_cycle import relay

class Relay(unittest.TestCase):
    def source(self,file,title,conclusion='skipped',hour=8):
        return {'id':4,'path':'.github/workflows/'+file,'display_title':title,'head_branch':'main','head_repository':{'full_name':'owner/repo'},'status':'completed','event':'workflow_dispatch','conclusion':conclusion,'created_at':f'2026-11-28T{hour-5:02}:00:00Z'}
    def now(self,hour=9):return dt.datetime(2026,11,28,hour,tzinfo=relay.TZ)
    def test_existing_eight_timer_recovers_all_five_stores(self):
        run=self.source('daily-metrics.yml','LifeMart metrics apply (all) ')
        self.assertEqual(relay.route(run,self.now(),'owner/repo'),'morning')
    def test_parent_completion_releases_reports_but_verify_and_report_do_not_loop(self):
        run=self.source('daily-cycle.yml','LifeMart cycle morning','success',7)
        self.assertEqual(relay.route(run,self.now(),'owner/repo'),'reports')
        for title in ['LifeMart cycle morning (verify)','LifeMart cycle reports','LifeMart cycle suppliers']:
            self.assertIsNone(relay.route({**run,'display_title':title},self.now(),'owner/repo'))
    def test_no_forks_stale_or_wrong_branch(self):
        run=self.source('daily-metrics.yml','LifeMart metrics apply (all)')
        for changes in [{'head_branch':'other'},{'head_repository':{'full_name':'attacker/repo'}},{'created_at':'2026-11-27T03:00:00Z'},{'event':'pull_request'},{'conclusion':'failure'}]:
            self.assertIsNone(relay.route({**run,**changes},self.now(),'owner/repo'))
    def test_evening_external_timers_cannot_run_early_or_reprocess_child(self):
        for code in ['k44','x76']:
            title='LifeMart '+('K44' if code=='k44' else 'X76')+' apply (16:00)'
            run=self.source('cloud-'+code+'.yml',title,hour=16)
            self.assertIsNone(relay.route(run,self.now(15),'owner/repo'))
            self.assertEqual(relay.route(run,self.now(16),'owner/repo'),'suppliers')
            self.assertIsNone(relay.route({**run,'display_title':title+' [cycle:123]'},self.now(16),'owner/repo'))
    def test_old_av_early_slots_are_not_turned_into_supplier_writes(self):
        self.assertEqual(relay.route(self.source('cloud-production.yml','LifeMart AV12 apply (morning)'),self.now(),'owner/repo'),'morning')
        self.assertIsNone(relay.route(self.source('cloud-production.yml','LifeMart AV12 apply (afternoon)'),self.now(14),'owner/repo'))
    def execute(self,exists=False,failed_post=False):
        source=self.source('daily-metrics.yml','LifeMart metrics apply (all)')
        with tempfile.TemporaryDirectory() as folder:
            event=Path(folder)/'event.json';event.write_text(json.dumps({'workflow_run':{'id':4}}))
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_run','GITHUB_EVENT_PATH':str(event),'GITHUB_REPOSITORY':'owner/repo'}),patch('sys.argv',['relay']),patch.object(relay,'route',return_value='morning'),patch.object(relay,'completed_cycle',return_value=False),patch.object(relay,'existing',return_value=exists),patch.object(relay,'api',side_effect=[source,ConnectionError('uncertain')] if failed_post else [source,None]) as api:
                if failed_post:
                    with self.assertRaises(ConnectionError):relay.main()
                else:relay.main()
                return api.call_args_list
    def test_existing_run_suppresses_automatic_duplicate(self):
        self.assertEqual(len(self.execute(exists=True)),1)
    def test_one_dispatch_only_even_when_acknowledgement_is_uncertain(self):
        calls=self.execute(failed_post=True)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[-1].args[1]['inputs'],{'stage':'morning','operation':'run'})
    def test_notice_after_missing_start_does_not_block_business_if_bot_fails(self):
        import daily_cycle.run as cycle
        with patch.object(cycle,'notify',side_effect=ConnectionError('uncertain')) as send:
            relay.late_notice('morning',dt.datetime.now(relay.TZ).date()-dt.timedelta(days=1))
            self.assertEqual(send.call_count,1)
    def test_successful_external_dispatch_emits_recovery_notice(self):
        with patch.object(relay,'late_notice') as notice:
            calls=self.execute()
            self.assertEqual(len(calls),2);self.assertEqual(notice.call_count,1)
