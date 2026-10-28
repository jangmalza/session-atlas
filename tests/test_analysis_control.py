"""Synthetic job/credential tests. No model or provider requests are permitted."""
from contextlib import closing
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import analysis_pipeline as ap
import context_graph as cg
from analysis_control import AnalysisControl, ControlError, read_json, write_json
from analysis_credentials import WindowsCredentials
from live_dashboard import LiveDashboard

SID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
SECRET = 'synthetic-key-never-use-for-real-requests'


class MemoryCredentials:
    supported = True
    kind = 'windows'
    def __init__(self): self.key = None
    def read(self, *, presence_only=False): return bool(self.key) if presence_only else self.key
    def write(self, key): self.key = key
    def delete(self): self.key = None


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.database = self.root / 'collection.db'
        self.home = self.root / 'home'; (self.home / 'sessions').mkdir(parents=True)
        with closing(cg.connect(self.database)) as con:
            for sid in (SID, OTHER):
                path = self.home / 'sessions' / ('rollout-' + sid + '.jsonl')
                rows = [{'type':'session_meta','payload':{'id':sid}}, {'type':'response_item',
                    'payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'SYNTHETIC PRIVATE QUESTION'}]}}]
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
                cg.scan_file(con,path)
            cg._save_map(con, OTHER, self.map('old'), 'unchanged-old-fingerprint'); con.commit()
            self.graph = cg.build_graph(con)
        self.preferences = self.database.with_suffix('.topic-display.json')
        self.preferences.write_text('{"schemaVersion":"topic-display-1","sessions":{}}')
        self.credentials = MemoryCredentials()
        self.controller = AnalysisControl(self.database,self.home,credentials=self.credentials,
            launcher=lambda *_: SimpleNamespace(pid=os.getpid()))
        self.controller.root.mkdir()
        write_json(self.controller.root/'settings.json',{'cmRoot':str(self.root/'cm')})
        write_json(self.controller.root/'codex-status.json',{'status':'ready','at':1})
        self.addCleanup(patch.stopall)
        patch('analysis_control.shutil.which',return_value='synthetic-executable').start()
        patch.object(ap,'find_conversation_map',return_value=(self.root/'cm.mjs',self.root/'schema.json')).start()
        patch.dict(os.environ,{'OPENAI_API_KEY':''}).start()
        patch('decisions_topics.request_decisions',side_effect=AssertionError('External API forbidden')).start()

    def map(self,title):
        return {'nodes':[{'id':'root','kind':'root'}, {'id':'item','kind':'item','title':title,
                'classification':'open_question','status':'open','evidence':[]}], 'relations':[]}

    def rows(self):
        with closing(cg.connect(self.database)) as con:
            return {table:[tuple(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('messages','session_maps','analyses','topic_assignments')}

    def arguments(self,session=SID,mode='codex'):
        preview=self.controller.preview(session,mode)
        return {'session':session,'mode':mode,'revision':preview['revision'],
                'allowModel':mode!='decisions','allowApi':mode!='codex'}

    def analyzer(self,con,sid,root,out,*,progress):
        progress('validating')
        cg._save_map(con,sid,self.map('new'),'new-fingerprint')
        con.execute('INSERT INTO analyses VALUES (?,?,?,?,?)',(sid,'new-fingerprint','ok',cg.now(),'synthetic'))
        con.commit()

    def test_settings_and_preview_never_start_processes_or_reveal_keys(self):
        self.credentials.write(SECRET)
        with (patch('analysis_control.subprocess.run',side_effect=AssertionError('No launch')),
              patch('analysis_control.subprocess.Popen',side_effect=AssertionError('No launch'))):
            value=self.controller.settings(); preview=self.controller.preview(SID,'hybrid')
        self.assertTrue(value['keyRegistered']);self.assertIsNone(preview['blockedReason'])
        self.assertNotIn(SECRET,json.dumps([value,preview]))
        self.assertFalse(value['autoAnalysis'])

    def test_key_management_has_no_plaintext_files_or_database_changes(self):
        before=self.rows(); preferences=self.preferences.read_bytes()
        response=self.controller.edit_settings({'action':'save_key','key':SECRET})
        self.assertTrue(response['keyRegistered']);self.assertNotIn(SECRET,json.dumps(response))
        for path in self.controller.root.rglob('*'):
            if path.is_file():self.assertNotIn(SECRET.encode(),path.read_bytes())
        self.assertEqual(self.rows(),before);self.assertEqual(self.preferences.read_bytes(),preferences)
        self.assertFalse(self.controller.edit_settings({'action':'delete_key'})['keyRegistered'])

    def test_codex_status_discards_output_and_only_runs_explicit_login_status(self):
        with patch('analysis_control.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(self.controller.check_codex()['codexAuth'],'ready')
        self.assertEqual(run.call_args.args[0][1:],['login','status'])
        import subprocess
        self.assertEqual(run.call_args.kwargs['stdout'],subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['stderr'],subprocess.DEVNULL)

    def test_consent_stale_preview_existing_map_and_duplicate_jobs_are_rejected(self):
        args=self.arguments();args['allowModel']=False
        with self.assertRaises(ControlError):self.controller.submit(args,self.graph)
        args=self.arguments();args['revision']='stale'
        with self.assertRaises(ControlError):self.controller.submit(args,self.graph)
        self.assertEqual(self.controller.preview(OTHER,'codex')['blockedReason'],'existing_analysis_preserved')
        self.controller.submit(self.arguments(),self.graph)
        with self.assertRaises(ControlError) as error:self.controller.submit(self.arguments(),self.graph)
        self.assertEqual(error.exception.code,'job_busy')

    def test_model_setting_is_validated_persisted_and_passed_to_the_analyzer(self):
        self.assertEqual(self.controller.settings()['codexModel'],'')
        for bad in [' x','a'*65,'gpt 6','model/x',None,3]:
            with self.assertRaises(ControlError) as error:self.controller.edit_settings({'action':'save_model','model':bad})
            self.assertEqual(error.exception.code,'invalid_model')
        cm_root=self.controller.config()['cmRoot']
        value=self.controller.edit_settings({'action':'save_model','model':'gpt-6-astra'})
        self.assertEqual(value['codexModel'],'gpt-6-astra');self.assertEqual(value['cmRoot'],cm_root,'saving a model keeps the map folder')
        for bad in ['Medium','medium high','',None]:
            if bad=='':continue
            with self.assertRaises(ControlError) as error:self.controller.edit_settings({'action':'save_reasoning','reasoning':bad})
            self.assertEqual(error.exception.code,'invalid_reasoning')
        value=self.controller.edit_settings({'action':'save_reasoning','reasoning':'medium'})
        self.assertEqual(value['codexReasoning'],'medium');self.assertEqual(value['codexModel'],'gpt-6-astra','saving the effort keeps the model')
        seen={}
        def analyzer(con,sid,root,out,*,progress,model=None,reasoning=None):
            seen.update(model=model,reasoning=reasoning);self.analyzer(con,sid,root,out,progress=progress)
        job=self.controller.submit(self.arguments(),self.graph)
        self.controller.execute(job['id'],analyzer=analyzer)
        self.assertEqual(seen,{'model':'gpt-6-astra','reasoning':'medium'});self.assertEqual(self.controller.status()['state'],'completed')
        self.assertEqual(self.controller.edit_settings({'action':'save_model','model':''})['codexModel'],'','an empty model falls back to the Codex config')
        self.assertEqual(self.controller.edit_settings({'action':'save_reasoning','reasoning':''})['codexReasoning'],'')

    def test_codex_failure_reports_its_own_code_without_provider_output(self):
        def fail(con,sid,root,out,*,progress,**_):
            raise ap.AnalysisError('chunk 1/1: codex exec failed (exit 1); provider output omitted',code='codex_exec_failed')
        job=self.controller.submit(self.arguments(),self.graph)
        self.controller.execute(job['id'],analyzer=fail)
        status=self.controller.status()
        self.assertEqual(status['error'],'codex_exec_failed');self.assertEqual(status['failedStage'],'analyzing')
        self.assertNotIn('exit 1',json.dumps(status))

    def test_success_adds_only_selected_analysis_and_survives_new_controller(self):
        before=self.rows();preferences=self.preferences.read_bytes()
        job=self.controller.submit(self.arguments(),self.graph)
        self.controller.execute(job['id'],analyzer=self.analyzer)
        self.assertEqual(self.controller.status()['state'],'completed')
        after=self.rows();self.assertEqual(after['messages'],before['messages'])
        self.assertEqual(next(r for r in after['session_maps'] if r[0]==OTHER),before['session_maps'][0])
        self.assertEqual(len(after['session_maps']),2);self.assertEqual(self.preferences.read_bytes(),preferences)
        self.assertTrue((self.controller.root/job['id']/'before.zip').is_file())
        self.assertFalse((self.controller.root/job['id']/'work.db').exists())
        other=AnalysisControl(self.database,self.home,credentials=self.credentials)
        self.assertEqual(other.status()['state'],'completed')
        with patch.object(ap,'auto_analyze',side_effect=AssertionError('Must not retry')):
            other.execute(job['id'])
        self.assertEqual(self.rows(),after)

    def test_validation_failure_never_publishes_partial_map_or_private_diagnostics(self):
        before=self.rows();job=self.controller.submit(self.arguments(),self.graph)
        def fail(con,*args,progress):
            self.analyzer(con,*args,progress=progress)
            raise RuntimeError('SYNTHETIC PRIVATE QUESTION '+SECRET)
        self.controller.execute(job['id'],analyzer=fail)
        self.assertEqual(self.controller.status()['state'],'failed');self.assertEqual(self.rows(),before)
        status=json.dumps(self.controller.status());self.assertNotIn(SECRET,status);self.assertNotIn('PRIVATE QUESTION',status)

    def test_messages_arriving_during_analysis_are_kept_and_stale_result_not_adopted(self):
        job=self.controller.submit(self.arguments(),self.graph)
        def concurrent(con,*args,progress):
            self.analyzer(con,*args,progress=progress)
            with closing(cg.connect(self.database)) as live,live:
                live.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',('f'*64,SID,'fixture',999,'user','response_item','New fixture request',''))
        self.controller.execute(job['id'],analyzer=concurrent)
        self.assertEqual(self.controller.status()['error'],'source_changed')
        rows=self.rows();self.assertEqual(len(rows['messages']),3);self.assertEqual(len(rows['session_maps']),1)

    def test_api_only_keeps_saved_map_and_analysis_byte_for_byte(self):
        self.credentials.write(SECRET);before=self.rows()
        job=self.controller.submit(self.arguments(OTHER,'decisions'),self.graph)
        called=[]
        def classify(con,sid,out):called.append(sid)
        with patch.object(ap,'auto_analyze',side_effect=AssertionError('Codex forbidden')):
            self.controller.execute(job['id'],classifier=classify)
        self.assertEqual(called,[OTHER]);self.assertEqual(self.rows(),before)

    def test_http_actions_require_same_origin_header_and_never_echo_input(self):
        dashboard=LiveDashboard(self.root/'live',self.database,0,codex_home=self.home)
        dashboard.analysis=self.controller;self.addCleanup(dashboard.close)
        with closing(cg.connect(self.database)) as con:cg.publish_dashboard(con,dashboard)
        dashboard.start();base=dashboard.url.rstrip('/')
        def post(path,body,headers=None):
            return urlopen(Request(base+'/analysis/'+path,data=json.dumps(body).encode(),headers=headers or {'Content-Type':'application/json'}),timeout=5)
        with self.assertRaises(HTTPError) as error:post('settings.json',{'action':'save_key','key':SECRET})
        self.assertEqual(error.exception.code,403);error.exception.close()
        headers={'Content-Type':'application/json','Origin':base,'X-Context-Graph-Action':'settings'}
        with post('settings.json',{'action':'save_key','key':SECRET},headers) as response:
            result=response.read();self.assertNotIn(SECRET.encode(),result)
        with self.assertRaises(HTTPError) as error:post('settings.json',{'action':'save_key','key':SECRET},{**headers,'Origin':'https://untrusted.invalid'})
        self.assertEqual(error.exception.code,403);error.exception.close()
        with urlopen(base+'/analysis/job.json') as response:self.assertEqual(json.load(response)['state'],'idle')

    def test_duplicate_worker_does_not_remove_running_staging_files(self):
        job=self.controller.submit(self.arguments(),self.graph)
        entered=threading.Event();release=threading.Event()
        def blocked(con,*args,progress):
            entered.set();release.wait(10);self.analyzer(con,*args,progress=progress)
        worker=threading.Thread(target=lambda:self.controller.execute(job['id'],analyzer=blocked))
        worker.start()
        try:
            self.assertTrue(entered.wait(10))
            self.controller.execute(job['id'],analyzer=lambda *_:self.fail('Duplicate worker ran'))
            self.assertTrue((self.controller.root/job['id']/'work.db').is_file())
        finally:release.set();worker.join(10)
        self.assertEqual(self.controller.status()['state'],'completed')

    def test_committed_receipt_recovers_status_after_progress_write_failure(self):
        job=self.controller.submit(self.arguments(),self.graph)
        update=self.controller.update
        def lose_completion(job_id,state,**fields):
            if state=='completed':raise OSError('synthetic status failure')
            return update(job_id,state,**fields)
        with patch.object(self.controller,'update',side_effect=lose_completion):
            self.controller.execute(job['id'],analyzer=self.analyzer)
        self.assertEqual(self.controller.status()['state'],'completed')
        self.assertNotIn('error',self.controller.status())

    def test_real_worker_entry_rejects_stale_snapshot_before_any_provider_work(self):
        job=self.controller.submit(self.arguments(),self.graph)
        path=self.controller.root/job['id']/'request.json'
        request=read_json(path);request['revision']='stale';write_json(path,request)
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'analysis_control.py'),
                               '--database',str(self.database),'--job',job['id']],
                              env={**os.environ,'PATH':'','OPENAI_API_KEY':'','PYTHONUTF8':'1'},
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        self.assertEqual(result.returncode,0)
        self.assertEqual(self.controller.status()['error'],'source_changed')
        self.assertEqual(len(self.rows()['session_maps']),1)

    def test_tailscale_control_routes_require_matching_proxy_identity(self):
        origin='https://fixture.example.ts.net'
        dashboard=LiveDashboard(self.root/'tail-live',self.database,0,codex_home=self.home,
                                tailscale_origin=origin,tailscale_user='fixture@example.invalid')
        dashboard.analysis=self.controller;self.addCleanup(dashboard.close)
        with closing(cg.connect(self.database)) as con:cg.publish_dashboard(con,dashboard)
        dashboard.start()
        headers={'Origin':origin,'Content-Type':'application/json','X-Context-Graph-Action':'settings'}
        body=json.dumps({'action':'save_key','key':SECRET}).encode()
        for identity in (None,'wrong@example.invalid'):
            supplied={**headers,**({'Tailscale-User-Login':identity} if identity else {})}
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(dashboard.url+'analysis/settings.json',data=body,headers=supplied),timeout=5)
            self.assertEqual(error.exception.code,403);error.exception.close()
        with urlopen(Request(dashboard.url+'analysis/settings.json',data=body,
                     headers={**headers,'Tailscale-User-Login':'fixture@example.invalid'}),timeout=5) as response:
            self.assertTrue(json.load(response)['keyRegistered'])

    def test_worker_launch_uses_no_secret_arguments_or_stored_key_environment(self):
        self.credentials.write(SECRET);self.controller.launcher=None
        process=SimpleNamespace(pid=os.getpid(),poll=lambda:None,wait=lambda:0)
        with patch('analysis_control.subprocess.Popen',return_value=process) as launch:
            self.controller.submit(self.arguments(SID,'hybrid'),self.graph)
        self.assertNotIn(SECRET,json.dumps(launch.call_args.args))
        self.assertNotIn(SECRET,json.dumps(launch.call_args.kwargs['env']))
        self.assertEqual(launch.call_args.kwargs['stdout'],subprocess.DEVNULL)
        self.assertEqual(launch.call_args.kwargs['stderr'],subprocess.DEVNULL)


@unittest.skipUnless(os.name=='nt','Windows credential integration')
class WindowsCredentialTests(unittest.TestCase):
    def test_isolated_credential_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as name:
            store=WindowsCredentials(Path(name)/'synthetic.db')
            try:
                store.write(SECRET)
                self.assertTrue(store.read(presence_only=True));self.assertEqual(store.read(),SECRET)
                replacement=WindowsCredentials(Path(name)/'synthetic.db')
                self.assertEqual(replacement.read(),SECRET)
            finally:store.delete()
            self.assertFalse(store.read(presence_only=True))
