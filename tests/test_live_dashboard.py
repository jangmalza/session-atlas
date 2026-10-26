"""Model-free collection, stable analysis projection, and local live HTTP tests."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from contextlib import redirect_stderr

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from live_dashboard import LiveDashboard, atomic_write, embedded_json, load_graph

SID='01234567-89ab-4def-8234-0123456789ab'


class LiveDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.home=self.root/'codex'
        self.rollout=self.home/'sessions'/f'rollout-{SID}.jsonl'
        self.rollout.parent.mkdir(parents=True)
        events=[{'type':'session_meta','payload':{'id':SID,'cwd':'C:/demo/Project'}},
                self.event('user','PRIVATE ORIGINAL REQUEST',0),
                self.event('assistant','PRIVATE ORIGINAL ANSWER',1)]
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
        self.db_file=self.root/'knowledge.db'
        self.db=cg.connect(self.db_file)
        self.addCleanup(self.db.close)
        cg.scan(self.db,self.home)
        self.fingerprint=cg.digest(cg.serialize_transcript(cg.transcript(self.db,SID)))
        self.map={'nodes':[{'id':'root','kind':'root'},
              {'id':'choice','kind':'item','title':'Use SQLite','summary':'Storage choice','classification':'decision','status':'active',
               'evidence':[{'messageId':'m1','quote':'PRIVATE ORIGINAL REQUEST','verified':True}]},
              {'id':'done','kind':'item','title':'Setup finished','summary':'Reported completion','classification':'completed_action','status':'done',
               'evidence':[{'messageId':'m2','quote':'PRIVATE ORIGINAL ANSWER','verified':True}]}],
              'relations':[{'id':'r1','from':'done','to':'choice','type':'implements'}],
              'source':{'messageCount':2}}
        self.map_file=self.root/'map.json'
        self.save_map(self.map)
        self.graph=cg.build_graph(self.db)
        self.graph_file=self.root/'previous.html'
        cg.render_html(self.graph,self.graph_file)

    def event(self,role,text,second):
        return {'type':'response_item','timestamp':f'2026-10-20T00:00:{second:02d}Z',
                'payload':{'type':'message','role':role,'content':[{'type':'input_text' if role=='user' else 'output_text','text':text}]}}

    def save_map(self,value):
        self.map_file.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        cg.import_map(self.db,SID,self.map_file,self.fingerprint)

    def dashboard(self):
        live=LiveDashboard(self.root/'live',self.db_file,0,self.graph_file)
        self.addCleanup(live.close)
        return live

    def test_new_dialogues_collect_without_models_or_mutating_analysis(self):
        live=self.dashboard()
        original_map=self.db.execute('SELECT * FROM session_maps').fetchone()
        original_messages={r['id']:tuple(r) for r in self.db.execute('SELECT * FROM messages')}
        with patch.object(cg,'auto_analyze',side_effect=AssertionError('Model forbidden')) as model, \
             patch.object(cg.subprocess,'run',side_effect=AssertionError('External processes forbidden')):
            cg.watch_tick(self.db,self.home,dashboard=live)
            first=(live.directory/'graph.html').read_bytes()
            self.assertEqual(live.status()['pendingMessageCount'],0)
            with self.rollout.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(self.event('user','PRIVATE NEW REQUEST',2))+'\n')
            self.assertEqual(cg.watch_tick(self.db,self.home,dashboard=live)['added_messages'],1)
            self.assertEqual(live.status()['messageCount'],3)
            self.assertEqual(live.status()['pendingMessageCount'],1)
            self.assertEqual(live.status()['pendingSessionCount'],1)
            self.assertFalse(live.status()['autoAnalysis'])
            self.assertEqual((live.directory/'graph.html').read_bytes(),first)
            self.assertEqual(tuple(self.db.execute('SELECT * FROM session_maps').fetchone()),tuple(original_map))
            for row in self.db.execute('SELECT * FROM messages'):
                if row['id'] in original_messages:self.assertEqual(tuple(row),original_messages[row['id']])
            self.assertEqual(cg.watch_tick(self.db,self.home,dashboard=live)['added_messages'],0)
            self.assertEqual(live.status()['messageCount'],3)
            self.assertFalse(model.called)
        self.assertNotIn('PRIVATE', (live.directory/'status.json').read_text(encoding='utf-8'))

    def test_explicit_import_updates_graph_while_preserving_existing_ids_and_edges(self):
        live=self.dashboard()
        cg.watch_tick(self.db,self.home,dashboard=live)
        initial=load_graph(live.directory/'graph.html')
        revised=deepcopy(self.map)
        revised['nodes'][1]['title']='SQLite 사용 결정'
        revised['nodes'][1]['summary']='한국어로 표시한 기존 결정'
        revised['nodes'].append({'id':'new-fact','kind':'item','title':'새로 가져온 분석 항목','summary':'명시적으로 가져온 분석',
                                 'classification':'fact','status':'none','evidence':[]})
        revised['relations'].append({'id':'r2','from':'new-fact','to':'choice','type':'supports'})
        self.save_map(revised)
        with patch.object(cg,'auto_analyze',side_effect=AssertionError('Model forbidden')):
            cg.watch_tick(self.db,self.home,dashboard=live)
        updated=load_graph(live.directory/'graph.html')
        self.assertTrue({n['id'] for n in initial['nodes']} <= {n['id'] for n in updated['nodes']})
        self.assertEqual(updated['edges'][:len(initial['edges'])],initial['edges'])
        choice=next(n for n in updated['nodes'] if any(p['source_node']=='choice' for p in n['provenance']))
        old=next(n for n in initial['nodes'] if any(p['source_node']=='choice' for p in n['provenance']))
        self.assertEqual(choice['id'],old['id'])
        self.assertEqual(choice['provenance'],old['provenance'])

    def test_existing_distinct_ids_are_not_merged_after_display_translation(self):
        revised=deepcopy(self.map)
        revised['nodes'][2]['classification']='decision'
        revised['nodes'][2]['status']='active'
        self.save_map(revised)
        previous=cg.build_graph(self.db)
        revised['nodes'][1]['title']=revised['nodes'][2]['title']='동일한 번역 제목'
        self.save_map(revised)
        updated=cg.build_graph(self.db,previous=previous)
        self.assertEqual(len(updated['nodes']),2)
        self.assertEqual([n['id'] for n in updated['nodes']],[n['id'] for n in previous['nodes']])
        self.assertEqual(updated['edges'],previous['edges'])

    def test_published_questions_embed_rechecked_offline_order_without_changing_graph(self):
        revised=deepcopy(self.map)
        revised['nodes'][1]['classification']='answered_question'
        revised['nodes'][1]['status']='answered'
        revised['nodes'][2]['classification']='assistant_statement'
        revised['relations'][0]['type']='answers'
        self.save_map(revised)
        live=self.dashboard()
        with patch.object(cg,'auto_analyze',side_effect=AssertionError('Models forbidden')):
            cg.watch_tick(self.db,self.home,dashboard=live)
        document=(live.directory/'graph.html').read_text(encoding='utf-8')
        context=embedded_json(document,'qa-context')
        self.assertEqual(context[SID]['messages'][0]['role'],'user')
        self.assertEqual(context[SID]['analysis']['anchors']['choice'][0]['index'],0)
        self.assertEqual(context[SID]['analysis']['relations'][0]['type'],'answers')
        self.assertNotIn('PRIVATE ORIGINAL',json.dumps(context))
        self.assertEqual(embedded_json(document,'graph-data'),live.graph)
        self.assertEqual(json.loads(self.db.execute('SELECT map_json FROM session_maps').fetchone()[0]),revised)

    def test_new_session_can_merge_into_a_unique_translated_existing_identity(self):
        revised=deepcopy(self.map)
        revised['nodes'][1]['title']='SQLite 사용 결정'
        self.save_map(revised)
        other='11111111-1111-4111-8111-111111111111'
        with self.db:
            self.db.execute("INSERT INTO sessions(id,cwd,project,first_seen,last_seen) VALUES (?,'C:/demo/Project','Project','x','x')",(other,))
        path=self.root/'other.json'
        path.write_text(json.dumps({'nodes':[deepcopy(revised['nodes'][1])],'relations':[]}),encoding='utf-8')
        cg.import_map(self.db,other,path)
        updated=cg.build_graph(self.db,previous=self.graph)
        self.assertEqual(len(updated['nodes']),2)
        target=next(n for n in updated['nodes'] if n['classification']=='decision')
        self.assertEqual(target['id'],next(n for n in self.graph['nodes'] if n['classification']=='decision')['id'])
        self.assertEqual(len(target['provenance']),2)

    def test_legacy_watch_analysis_flags_are_rejected_before_creating_a_database(self):
        target=self.root/'must-not-exist.db'
        with patch.object(cg,'auto_analyze',side_effect=AssertionError('Model forbidden')), redirect_stderr(io.StringIO()):
            self.assertEqual(cg.main(['--db',str(target),'watch','--analyze','--allow-model']),2)
        self.assertFalse(target.exists())

    def test_live_directory_never_overwrites_unmanaged_files(self):
        directory=self.root/'existing'
        directory.mkdir()
        original=directory/'index.html'
        original.write_text('PRESERVE',encoding='utf-8')
        with self.assertRaises(ValueError):LiveDashboard(directory,self.db_file,0)
        self.assertEqual(original.read_text(encoding='utf-8'),'PRESERVE')

    def test_atomic_write_failure_preserves_the_last_file(self):
        target=self.root/'keep.html'
        target.write_bytes(b'original')
        with patch('live_dashboard.os.replace',side_effect=PermissionError('blocked')):
            with self.assertRaises(PermissionError):atomic_write(target,b'replacement')
        self.assertEqual(target.read_bytes(),b'original')
        self.assertEqual(list(self.root.glob('.keep.html-*')),[])

    def test_loopback_http_is_read_only_whitelisted_and_aggregate_only(self):
        live=self.dashboard()
        cg.watch_tick(self.db,self.home,dashboard=live)
        live.start()
        with urlopen(live.url+'status.json') as response:
            payload=response.read().decode()
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(response.headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertEqual(json.loads(payload)['messageCount'],2)
        self.assertNotIn('PRIVATE',payload)
        for path in ('../knowledge.db','.context-graph-live.json','map.json'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                urlopen(live.url+path)
            self.assertEqual(raised.exception.code,404)
            raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(live.url+'status.json',headers={'Host':'untrusted.example'}))
        self.assertEqual(raised.exception.code,400)
        raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(live.url+'analyze',data=b'run-model',method='POST'))
        self.assertEqual(raised.exception.code,405)
        raised.exception.close()

    def test_scan_errors_do_not_log_exception_content_or_overwrite_analysis(self):
        live=self.dashboard()
        before=self.db.execute('SELECT map_json FROM session_maps').fetchone()[0]
        log=io.StringIO()
        with patch.object(cg,'scan_file',side_effect=OSError('PRIVATE CONTENT')), redirect_stderr(log):
            result=cg.watch_tick(self.db,self.home,dashboard=live)
        self.assertEqual(result['scan_errors'],1)
        self.assertEqual(live.status()['state'],'warning')
        self.assertNotIn('PRIVATE',log.getvalue())
        self.assertEqual(self.db.execute('SELECT map_json FROM session_maps').fetchone()[0],before)

    def test_restart_reuses_projection_ids_from_the_managed_directory(self):
        live=self.dashboard()
        cg.watch_tick(self.db,self.home,dashboard=live)
        initial=live.graph
        restarted=LiveDashboard(live.directory,self.db_file,0)
        self.addCleanup(restarted.close)
        cg.watch_tick(self.db,self.home,dashboard=restarted)
        self.assertEqual(restarted.graph,initial)

    def test_conversation_map_snapshot_is_copied_without_rewriting_its_data(self):
        from test_ui_localization import artifact
        from localize_map import localize_html
        source=self.root/'map.ko.html'
        original=localize_html(artifact()[0]).encode('utf-8')
        source.write_bytes(original)
        live=LiveDashboard(self.root/'live',self.db_file,0,self.graph_file,source)
        self.addCleanup(live.close)
        cg.watch_tick(self.db,self.home,dashboard=live)
        self.assertEqual((live.directory/'map.html').read_bytes(),original)
        self.assertEqual(source.read_bytes(),original)
        self.assertTrue(live.status()['mapReady'])
        revision=live.status()['mapRevision']
        changed=original.replace(b'Filters and Export',b'Updated display')
        # That text exists only in inert JSON/static source, so viewer CSP stays valid.
        source.write_bytes(changed)
        cg.watch_tick(self.db,self.home,dashboard=live)
        self.assertNotEqual(live.status()['mapRevision'],revision)
        self.assertEqual((live.directory/'map.html').read_bytes(),changed)

    def test_invalid_snapshot_keeps_the_last_published_files(self):
        from test_ui_localization import artifact
        from localize_map import localize_html
        source=self.root/'map.ko.html'
        source.write_bytes(localize_html(artifact()[0]).encode('utf-8'))
        live=LiveDashboard(self.root/'live',self.db_file,0,self.graph_file,source)
        self.addCleanup(live.close)
        cg.watch_tick(self.db,self.home,dashboard=live)
        before={n:(live.directory/n).read_bytes() for n in ('graph.html','map.html','index.html','status.json')}
        source.write_text('incomplete output',encoding='utf-8')
        with self.assertRaises(ValueError):cg.watch_tick(self.db,self.home,dashboard=live)
        self.assertEqual({n:(live.directory/n).read_bytes() for n in before},before)


if __name__=='__main__':unittest.main()
