"""Hybrid orchestration is explicitly requested and preserves existing sources."""
from contextlib import closing, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg
import decisions_topics as dt
from topic_overlays import read_overlays
from session_timeline import TimelineReader
from live_dashboard import LiveDashboard

SID = '12345678-1234-4234-8234-123456789abc'


class HybridAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root/'test.db'
        self.con = cg.connect(self.db)
        self.addCleanup(self.con.close)
        with self.con:
            self.con.execute("INSERT INTO sessions(id,cwd,project,first_seen,last_seen) VALUES (?,'example','example','x','x')", (SID,))
            self.con.execute("INSERT INTO messages(id,session_id,source_path,offset_bytes,role,source_kind,content) VALUES ('msg',?,'fixture',0,'user','response_item','Synthetic source request')", (SID,))
        self.source = {'schemaVersion':'1','rootId':'root','title':'Synthetic map', 'source':{'messageCount':1},
            'nodes':[
                {'id':'root','kind':'root','parentId':None,'childIds':['a','b']},
                {'id':'a','kind':'topic','title':'Topic A','summary':'Scope A','parentId':'root','childIds':['q','answer']},
                {'id':'b','kind':'topic','title':'Topic B','summary':'Scope B','parentId':'root','childIds':[]},
                {'id':'q','kind':'item','classification':'answered_question','title':'Question','summary':'Saved summary',
                 'parentId':'a','childIds':[],'evidence':[{'messageId':'m1','quote':'Synthetic source','verified':True}]},
                {'id':'answer','kind':'item','classification':'assistant_statement','title':'Answer','parentId':'a','childIds':[],
                 'evidence':[]}],
            'relations':[{'id':'r','from':'answer','to':'q','type':'answers'}],
            'messages':[{'id':'m1','text':'Synthetic source request','speaker':'user','index':0}]}
        self.skill = self.root/'cm/plugins/conversation-map/skills/conversation-map'
        (self.skill/'scripts').mkdir(parents=True)
        (self.skill/'references').mkdir()
        (self.skill/'scripts/conversation-map.mjs').write_text('stub',encoding='utf-8')
        (self.skill/'references/analysis.schema.json').write_text('{}',encoding='utf-8')

    def save_source(self):
        path=self.root/'map.json';path.write_text(json.dumps(self.source),encoding='utf-8')
        cg.import_map(self.con,SID,path)
        with self.con:
            self.con.execute("INSERT INTO analyses VALUES (?, 'original-hash', 'ok', 'original-time', 'original-detail')",(SID,))

    def tables(self):
        return {table:[tuple(row) for row in self.con.execute('SELECT * FROM '+table)]
                for table in ('sessions','messages','session_maps','analyses')}

    def overlay(self, source):
        return {'schemaVersion':'topic-assignments-1','sourceMapSha256':dt.map_fingerprint(source),
                'classifier':'openai-decisions','model':dt.MODEL,'policyVersion':dt.POLICY_VERSION,'minConfidence':0.85,
                'assignments':[{'nodeId':node['id'],'topicId':'b','status':'assigned','confidence':0.99}
                               for node in source['nodes'] if node['kind']=='item']}

    def fake_process(self, cmd, **kwargs):
        if cmd[0]=='codex':
            self.assertIn('separate Decisions classifier', kwargs['input'])
            Path(cmd[cmd.index('-o')+1]).write_text(json.dumps({'nodes':[], 'relations':[]}),encoding='utf-8')
        elif cmd[0]=='node' and cmd[2]=='build':
            out=Path(cmd[cmd.index('--out-dir')+1]);out.mkdir()
            (out/'map.json').write_text(json.dumps(self.source),encoding='utf-8')
        return type('Result',(),{'returncode':0,'stdout':'','stderr':''})()

    def test_permission_guards_fail_before_database_or_model_access(self):
        missing=self.root/'must-not-exist.db'
        cases=[['assign-topics','--session',SID],
               ['analyze','--session',SID,'--cm-root','unused','--topic-classifier','decisions','--allow-model'],
               ['analyze','--session',SID,'--cm-root','unused','--topic-classifier','decisions','--allow-api'],
               ['analyze','--cm-root','unused','--topic-classifier','decisions','--allow-model','--allow-api'],
               ['analyze','--session',SID,'--cm-root','unused','--topic-classifier','decisions','--allow-model','--allow-api','--force']]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(cg,'connect') as connect, \
             patch.object(ap,'auto_analyze') as model,patch.object(dt,'request_decisions') as request, \
             redirect_stderr(io.StringIO()):
            for args in cases:self.assertEqual(cg.main(['--db',str(missing),*args]),2)
            connect.assert_not_called();model.assert_not_called();request.assert_not_called()
        self.assertFalse(missing.exists())

    def test_invalid_threshold_and_missing_key_fail_before_codex(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch.object(cg.subprocess,'run') as model:
            with self.assertRaises(dt.DecisionsError):
                ap.auto_analyze(self.con,SID,self.root/'cm',self.root,topic_classifier='decisions',allow_api=True)
            model.assert_not_called()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(cg.subprocess,'run') as model:
            with self.assertRaises(ValueError):
                ap.auto_analyze(self.con,SID,self.root/'cm',self.root,topic_classifier='decisions',allow_api=True,topic_confidence=float('nan'))
            model.assert_not_called()

    def test_existing_analysis_reused_and_only_sidecar_changes(self):
        self.save_source();before=self.tables();map_copy=deepcopy(self.source)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}), \
             patch.object(cg.shutil,'which',side_effect=AssertionError('No Codex lookup')), \
             patch.object(cg.subprocess,'run',side_effect=AssertionError('No model')), \
             patch.object(dt,'classify_map',side_effect=lambda source,*a,**kw:self.overlay(source)) as classify:
            result=ap.auto_analyze(self.con,SID,self.root/'unused',self.root,topic_classifier='decisions',allow_api=True)
        self.assertIn('existing analysis preserved',result);self.assertEqual(classify.call_count,1)
        self.assertEqual(self.tables(),before);self.assertEqual(self.source,map_copy)
        self.assertEqual(read_overlays(self.con)[SID]['assignments'][0]['topicId'],'b')

    def test_new_hybrid_map_and_sidecar_are_saved_together(self):
        before=self.tables()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}), \
             patch.object(cg.shutil,'which',side_effect=lambda name:name), \
             patch.object(ap,'_run',side_effect=self.fake_process), \
             patch.object(dt,'classify_map',side_effect=lambda source,*a,**kw:self.overlay(source)):
            ap.auto_analyze(self.con,SID,self.root/'cm',self.root,topic_classifier='decisions',allow_api=True)
        saved=json.loads(self.con.execute('SELECT map_json FROM session_maps').fetchone()[0])
        self.assertEqual(saved,self.source)
        self.assertEqual(self.tables()['messages'],before['messages'])
        self.assertEqual(len(read_overlays(self.con)),1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM analyses').fetchone()[0],1)

    def test_api_failure_leaves_new_map_and_analysis_unpublished(self):
        before=self.tables()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}), \
             patch.object(cg.shutil,'which',side_effect=lambda name:name), \
             patch.object(ap,'_run',side_effect=self.fake_process), \
             patch.object(dt,'classify_map',side_effect=dt.DecisionsError('Synthetic failure')):
            with self.assertRaises(dt.DecisionsError):
                ap.auto_analyze(self.con,SID,self.root/'cm',self.root,topic_classifier='decisions',allow_api=True)
        self.assertEqual(self.tables(),before);self.assertEqual(read_overlays(self.con),{})

    def test_store_failure_rolls_back_map_and_analysis_transaction(self):
        before=self.tables()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}), \
             patch.object(cg.shutil,'which',side_effect=lambda name:name), \
             patch.object(ap,'_run',side_effect=self.fake_process), \
             patch.object(dt,'classify_map',side_effect=lambda source,*a,**kw:self.overlay(source)), \
             patch('topic_overlays.store_overlay',side_effect=RuntimeError('Synthetic save failure')):
            with self.assertRaises(RuntimeError):
                ap.auto_analyze(self.con,SID,self.root/'cm',self.root,topic_classifier='decisions',allow_api=True)
        self.assertEqual(self.tables(),before);self.assertEqual(read_overlays(self.con),{})

    def test_failed_reclassification_keeps_prior_overlay_and_original_map(self):
        self.save_source()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(dt,'classify_map',side_effect=lambda source,*a,**kw:self.overlay(source)):
            ap.assign_topics(self.con,SID,self.root,allow_api=True)
        before=self.tables();old=read_overlays(self.con)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(dt,'classify_map',side_effect=dt.DecisionsError('Synthetic failure')):
            with self.assertRaises(dt.DecisionsError):ap.assign_topics(self.con,SID,self.root,allow_api=True)
        self.assertEqual(self.tables(),before);self.assertEqual(read_overlays(self.con),old)

    def test_concurrent_map_change_prevents_applying_stale_assignments(self):
        self.save_source()
        def changed(source,*args,**kwargs):
            modified=deepcopy(source);modified['title']='Changed by another writer'
            with closing(sqlite3.connect(self.db)) as other:
                with other:
                    other.execute('UPDATE session_maps SET map_json=? WHERE session_id=?',(json.dumps(modified),SID))
            return self.overlay(source)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(dt,'classify_map',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'changed during classification'):
                ap.assign_topics(self.con,SID,self.root,allow_api=True)
        self.assertEqual(read_overlays(self.con),{})

    def test_assign_command_never_runs_codex(self):
        self.save_source();before=self.tables()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(cg.subprocess,'run',side_effect=AssertionError('No Codex')), \
             patch.object(dt,'classify_map',side_effect=lambda source,*a,**kw:self.overlay(source)),redirect_stdout(io.StringIO()):
            result=cg.main(['--db',str(self.db),'assign-topics','--session',SID,'--allow-api'])
        self.assertEqual(result,0);self.assertEqual(self.tables(),before)

    def test_existing_map_through_real_classifier_mock_transport_and_viewer(self):
        self.save_source();before=self.tables();graph=cg.build_graph(self.con)
        def reply(payload):
            return {'answers':[{'name':question['name'],'type':'choice','choice':'t1','confidence':0.99}
                               for question in payload['questions']]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}),patch.object(dt,'request_decisions',side_effect=reply) as request:
            ap.assign_topics(self.con,SID,self.root,allow_api=True)
            ap.assign_topics(self.con,SID,self.root,allow_api=True)
        self.assertEqual(request.call_count,1)
        view=TimelineReader(self.db).read(SID)
        self.assertEqual(next(node for node in view['analysis']['structure'] if node['id']=='q')['parentId'],'b')
        self.assertEqual(next(node for node in view['analysis']['nodes'] if node['id']=='q')['parentId'],'a')
        self.assertEqual(view['analysis']['relations'],self.source['relations'])
        self.assertEqual(cg.build_graph(self.con,previous=graph),graph)
        self.assertEqual(self.tables(),before)

    def test_collection_and_viewer_never_invoke_classification_even_with_key(self):
        self.save_source()
        viewer=LiveDashboard(self.root/'viewer',self.db,0)
        self.addCleanup(viewer.close)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test-key'}), \
             patch.object(dt,'request_decisions',side_effect=AssertionError('API forbidden')) as request, \
             patch.object(ap,'auto_analyze',side_effect=AssertionError('Codex forbidden')) as model:
            cg.watch_tick(self.con,self.root/'empty-codex',dashboard=viewer)
            cg.publish_dashboard(self.con,viewer,read_only_viewer=True)
            TimelineReader(self.db).read(SID)
        request.assert_not_called();model.assert_not_called()


if __name__=='__main__':unittest.main()
