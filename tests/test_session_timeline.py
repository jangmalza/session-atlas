"""Timeline provenance, append/reorder handling, paging and read-only HTTP."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg
from live_dashboard import LiveDashboard, embedded_json
from decisions_topics import map_fingerprint
from topic_display import display_revision, read_session_names, set_session_display
from session_timeline import TimelineReader

SID = '12345678-1234-4234-8234-123456789abc'


class SessionTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'codex'
        self.rollout = self.home/'sessions'/f'rollout-{SID}.jsonl'
        self.rollout.parent.mkdir(parents=True)
        self.rollout.write_text(json.dumps({'type':'session_meta','payload':{'id':SID,'cwd':'C:/demo/Example'}})+'\n',encoding='utf-8')
        self.append('user', '예시 질문 원문', 10)
        self.append('assistant', '예시 답변 원문', 20)
        self.db_file = self.root/'collection.db'
        self.db = cg.connect(self.db_file)
        self.addCleanup(self.db.close)
        cg.scan(self.db, self.home)
        self.transcript_hash = cg.digest(cg.serialize_transcript(cg.transcript(self.db, SID)))
        self.original_rows = cg.effective_messages(self.db, SID)
        self.analysis = {'title':'타임라인 예시', 'source':{'messageCount':2}, 'nodes':[
            {'id':'question','kind':'item','classification':'answered_question','status':'answered','title':'예시 질문',
             'summary':'저장된 질문 요약', 'rationale':'원문에 질문이 있음',
             'evidence':[{'messageId':'m1','index':0,'speaker':'user','quote':'예시 질문','verified':True}]},
            {'id':'answer','kind':'item','classification':'assistant_statement','status':'none','title':'예시 답변',
             'evidence':[{'messageId':'m2','index':1,'speaker':'assistant','quote':'예시 답변','verified':True}]},
            {'id':'unplaced','kind':'item','classification':'inference','status':'none','title':'시점 없는 기존 분석','evidence':[]}],
             'relations':[{'id':'answer-link','from':'answer','to':'question','type':'answers','note':'기존 연결'}],
             'messages':[{'id':f'm{i+1}','index':i,'speaker':row['role'],'text':row['content'],'timestamp':row['at']}
                         for i,row in enumerate(self.original_rows)]}
        self.import_analysis(self.analysis)
        self.reader = TimelineReader(self.db_file)

    def append(self, role, text, second):
        event = {'type':'response_item','timestamp':f'2026-10-20T00:00:{second:02d}Z',
                 'payload':{'type':'message','role':role,'content':[{'type':'input_text' if role=='user' else 'output_text','text':text}]}}
        with self.rollout.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')

    def import_analysis(self, value):
        path = self.root/'analysis.json'
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        cg.import_map(self.db, SID, path, self.transcript_hash)

    def unchanged_tables(self):
        return {table:[tuple(row) for row in self.db.execute('SELECT * FROM '+table)]
                for table in ('messages','session_maps','analyses')}

    def test_existing_analysis_is_joined_to_raw_messages_without_models_or_mutations(self):
        before = self.unchanged_tables()
        with patch.object(ap, 'auto_analyze', side_effect=AssertionError('Models forbidden')), \
             patch.object(cg.subprocess, 'run', side_effect=AssertionError('Processes forbidden')):
            catalog = self.reader.read()
            view = self.reader.read(SID)
        self.assertEqual(catalog['sessions'][0]['messageCount'], 2)
        self.assertEqual([message['id'] for message in view['messages']], [row['id'] for row in self.original_rows])
        self.assertEqual([message['content'] for message in view['messages']], [row['content'] for row in self.original_rows])
        self.assertEqual(view['messages'][0]['analysisNodeIds'], ['question'])
        self.assertEqual(view['analysis']['nodes'], self.analysis['nodes'])
        self.assertEqual(view['analysis']['relations'], self.analysis['relations'])
        self.assertEqual(view['analysis']['unplacedIds'], ['unplaced'])
        self.assertEqual(self.unchanged_tables(), before)
        self.assertFalse(view['autoAnalysis'])

    def test_append_and_dedup_refresh_messages_but_keep_saved_analysis_and_original_ids(self):
        original_map = tuple(self.db.execute('SELECT * FROM session_maps').fetchone())
        initial = self.reader.read(SID)
        self.append('user', '새로운 예시 메시지', 30)
        cg.scan(self.db, self.home)
        cg.scan(self.db, self.home)
        updated = self.reader.read(SID)
        self.assertEqual(updated['total'], 3)
        self.assertNotEqual(updated['revision'], initial['revision'])
        self.assertEqual(updated['session']['pendingMessageCount'], 1)
        self.assertEqual(updated['messages'][-1]['analysisState'], 'pending')
        self.assertEqual(updated['messages'][-1]['analysisNodeIds'], [])
        self.assertEqual(updated['analysis']['anchors'], initial['analysis']['anchors'])
        self.assertEqual(tuple(self.db.execute('SELECT * FROM session_maps').fetchone()), original_map)

    def test_question_context_is_read_only_metadata_without_original_text_or_quotes(self):
        before = self.unchanged_tables()
        with patch.object(ap, 'auto_analyze', side_effect=AssertionError('Models forbidden')):
            context = self.reader.question_context({SID})
        self.assertEqual(set(context), {SID})
        self.assertEqual(context[SID]['analysis']['anchors'], self.reader.read(SID)['analysis']['anchors'])
        self.assertEqual(context[SID]['messages'][0], {'id': self.original_rows[0]['id'], 'index': 0,
                                                     'role': 'user', 'timestamp': self.original_rows[0]['at'],
                                                     'hasUserContent': True})
        self.assertNotIn('content', json.dumps(context))
        self.assertNotIn('quote', json.dumps(context))
        self.assertNotIn('text', json.dumps(context))
        self.assertEqual(self.unchanged_tables(), before)
        self.assertEqual(self.reader.question_context({'absent'}), {})

    def test_topic_hierarchy_is_exported_as_stored_without_changing_sources(self):
        modified=deepcopy(self.analysis)
        modified['nodes'][0]['parentId']='topic'
        modified['nodes'].append({'id':'topic','kind':'topic','title':'저장된 주제',
                                  'parentId':None,'childIds':['question'],'summary':'원문을 복사하지 않음'})
        self.import_analysis(modified)
        before=self.unchanged_tables()
        view=self.reader.read(SID)
        context=self.reader.question_context({SID})
        structure=view['analysis']['structure']
        self.assertEqual(context[SID]['analysis']['structure'],structure)
        self.assertEqual(next(node for node in structure if node['id']=='question'),
                         {'id':'question','kind':'item','parentId':'topic'})
        self.assertEqual(next(node for node in structure if node['id']=='topic'),
                         {'id':'topic','kind':'topic','title':'저장된 주제','parentId':None,'childIds':['question']})
        self.assertNotIn('evidence',json.dumps(structure))
        self.assertNotIn('summary',json.dumps(structure))
        self.assertEqual(view['analysis']['nodes'],[node for node in modified['nodes'] if node['kind']=='item'])
        self.assertEqual(self.unchanged_tables(),before)

    def test_user_content_flag_excludes_injected_context_and_keeps_original_messages(self):
        environment='<environment_context>PRIVATE ENVIRONMENT METADATA</environment_context>'
        ambient='<in-app-browser-context source="ambient-ui-state">PRIVATE BROWSER METADATA</in-app-browser-context>'
        request=ambient+'\n\n## My request:\nKeep the actual user request.'
        self.append('user', environment, 30)
        self.append('user', ambient, 40)
        self.append('user', request, 50)
        cg.scan(self.db, self.home)
        before=self.unchanged_tables()
        with patch.object(ap, 'auto_analyze', side_effect=AssertionError('Models forbidden')):
            view=self.reader.read(SID, 0, 100)
            offline=self.reader.question_context({SID})[SID]
        self.assertEqual([m['hasUserContent'] for m in view['messages']], [True,False,False,False,True])
        self.assertEqual([m['hasUserContent'] for m in offline['messages']], [True,False,False,False,True])
        self.assertEqual([m['content'] for m in view['messages']][2:], [environment,ambient,request])
        self.assertNotIn('PRIVATE ENVIRONMENT',json.dumps(offline))
        self.assertNotIn('PRIVATE BROWSER',json.dumps(offline))
        self.assertNotIn('Keep the actual user',json.dumps(offline))
        self.assertEqual(self.unchanged_tables(),before)
    def test_explicit_display_preferences_refresh_both_views_without_mutating_source_tables(self):
        analysis=deepcopy(self.analysis)
        analysis['nodes'][0]['parentId']='reviewed-topic'
        analysis['nodes'].extend([
            {'id':'reviewed-topic','kind':'topic','title':'Same title','childIds':['question']},
            {'id':'unrelated-topic','kind':'topic','title':'Same title','childIds':[]}])
        self.import_analysis(analysis)
        before=self.unchanged_tables()
        initial=self.reader.read(SID)
        dashboard=LiveDashboard(self.root/'display-live',self.db_file)
        cg.publish_dashboard(self.db,dashboard)
        original_html=(dashboard.directory/'graph.html').read_text(encoding='utf-8')
        revision=dashboard.status()['graphRevision']
        self.db_file.with_suffix('.topic-display.json').write_text(json.dumps({
            'schemaVersion':'topic-display-1','sessions':{SID:{
                'sourceMapSha256':map_fingerprint(analysis),'auxiliaryTopicIds':['reviewed-topic'],
                'displayNames':{'reviewed-topic':'</script><img src=x> 표시 이름'}}}}),encoding='utf-8')
        with patch.object(ap,'auto_analyze',side_effect=AssertionError('Models forbidden')):
            updated=self.reader.read(SID)
            cg.publish_dashboard(self.db,dashboard)
        structure=updated['analysis']['structure']
        self.assertEqual([node['id'] for node in structure if node.get('displayGroup')=='auxiliary'],['reviewed-topic'])
        displayed=next(node for node in structure if node['id']=='reviewed-topic')
        self.assertEqual(displayed['title'],'Same title')
        self.assertEqual(displayed['displayTitle'],'</script><img src=x> 표시 이름')
        self.assertNotEqual(updated['revision'],initial['revision'])
        self.assertEqual(updated['analysis']['nodes'],initial['analysis']['nodes'])
        self.assertEqual(updated['analysis']['anchors'],initial['analysis']['anchors'])
        self.assertEqual(updated['analysis']['relations'],initial['analysis']['relations'])
        exported=(dashboard.directory/'graph.html').read_text(encoding='utf-8')
        self.assertEqual(embedded_json(exported,'graph-data'),embedded_json(original_html,'graph-data'))
        self.assertEqual(embedded_json(exported,'qa-context')[SID]['analysis']['structure'],structure)
        self.assertNotIn('<img',exported)
        self.assertNotEqual(dashboard.status()['graphRevision'],revision)
        self.assertEqual(self.unchanged_tables(),before)

    def test_session_display_label_updates_views_without_changing_the_original_title_or_messages(self):
        initial=self.reader.read(SID);before=self.unchanged_tables()
        dashboard=LiveDashboard(self.root/'session-name-live',self.db_file)
        cg.publish_dashboard(self.db,dashboard)
        original_html=(dashboard.directory/'graph.html').read_text(encoding='utf-8')
        revision=dashboard.status()['graphRevision']
        with patch.object(ap,'auto_analyze',side_effect=AssertionError('Models forbidden')):
            set_session_display(self.db_file,session=SID,display_name='읽기용 세션 이름',expected_revision=display_revision(None))
            renamed=self.reader.read(SID)
            cg.publish_dashboard(self.db,dashboard)
        self.assertEqual(renamed['session']['title'],initial['session']['title'])
        self.assertEqual(renamed['session']['displayTitle'],'읽기용 세션 이름')
        self.assertEqual(renamed['messages'],initial['messages'])
        self.assertEqual(renamed['analysis'],initial['analysis'])
        self.assertTrue(all(hit['sessionTitle']=='읽기용 세션 이름' for hit in self.reader.search('예시')['results']))
        offline=self.reader.question_context({SID})[SID]
        self.assertEqual(offline['session']['displayTitle'],'읽기용 세션 이름')
        self.assertNotIn('예시 질문 원문',json.dumps(offline,ensure_ascii=False))
        exported=(dashboard.directory/'graph.html').read_text(encoding='utf-8')
        self.assertEqual(embedded_json(exported,'graph-data'),embedded_json(original_html,'graph-data'))
        self.assertNotEqual(dashboard.status()['graphRevision'],revision)
        self.assertEqual(self.unchanged_tables(),before)

    def test_session_alias_survives_appends_and_original_title_changes_until_explicit_reset(self):
        set_session_display(self.db_file,session=SID,display_name='고정 표시 이름',expected_revision=display_revision(None))
        self.append('user','추가 대화 원문',30);cg.scan(self.db,self.home)
        self.assertEqual(self.reader.read(SID)['session']['displayTitle'],'고정 표시 이름')
        changed=deepcopy(self.analysis);changed['title']='Updated source analysis title';self.import_analysis(changed)
        header=self.reader.read(SID)['session']
        self.assertEqual(header['title'],'Updated source analysis title')
        self.assertEqual(header['displayTitle'],'고정 표시 이름')
        before=self.unchanged_tables()
        set_session_display(self.db_file,session=SID,display_name=None,expected_revision=header['sessionDisplay']['revision'])
        restored=self.reader.read(SID)['session']
        self.assertEqual(restored['title'],'Updated source analysis title')
        self.assertNotIn('displayTitle',restored)
        self.assertEqual(self.unchanged_tables(),before)

    def test_shifted_position_uses_unique_full_message_not_reused_positional_id(self):
        self.append('user', '앞에 들어온 다른 메시지', 0)
        cg.scan(self.db, self.home)
        view = self.reader.read(SID)
        self.assertEqual(view['messages'][0]['analysisNodeIds'], [])
        anchor = view['analysis']['anchors']['question'][0]
        self.assertEqual(anchor['messageId'], self.original_rows[0]['id'])
        self.assertEqual(anchor['sourceMessageId'], 'm1')
        self.assertEqual(anchor['index'], 1)
        self.assertEqual(view['analysis']['nodes'][0]['evidence'][0]['index'], 0)

    def test_ambiguous_or_unverified_evidence_is_never_attached_by_guessing(self):
        self.append('user', '예시 질문 원문', 10)
        cg.scan(self.db, self.home)
        view = self.reader.read(SID)
        self.assertEqual(view['analysis']['anchors']['question'], [])
        for change in ({'verified':False},{'speaker':'user'},{'index':999},{'quote':'없는 문장'}):
            with self.subTest(change=change):
                modified = deepcopy(self.analysis)
                modified['nodes'][1]['evidence'][0].update(change)
                self.import_analysis(modified)
                self.assertEqual(self.reader.read(SID)['analysis']['anchors']['answer'], [])

    def test_exact_transcript_hash_can_ground_legacy_map_without_embedded_messages(self):
        modified = deepcopy(self.analysis)
        del modified['messages']
        self.import_analysis(modified)
        self.append('user','새로운 예시 메시지',30)
        cg.scan(self.db, self.home)
        view = self.reader.read(SID)
        self.assertEqual(view['messages'][0]['analysisNodeIds'], ['question'])
        self.assertEqual(view['messages'][-1]['analysisState'], 'pending')

    def test_paging_and_deep_link_are_bounded_and_preserve_source(self):
        tail = self.reader.read(SID, limit=1)
        self.assertEqual(tail['offset'], 1)
        self.assertEqual(len(tail['messages']), 1)
        target = self.original_rows[0]['id']
        self.assertEqual(self.reader.read(SID, limit=1, message=target)['messages'][0]['id'], target)
        for options in ({'limit':0},{'limit':201},{'offset':-1}):
            with self.assertRaises(ValueError): self.reader.read(SID, **options)
        with self.assertRaises(KeyError): self.reader.read('missing-session')

    def test_explicit_reimport_updates_analysis_even_if_graph_does_not_change(self):
        live = LiveDashboard(self.root/'live', self.db_file, 0)
        self.addCleanup(live.close)
        cg.watch_tick(self.db, self.home, dashboard=live)
        initial = live.status()
        modified = deepcopy(self.analysis)
        modified['nodes'][0]['rationale'] = '명시적으로 바꾼 예시 분류 이유'
        self.import_analysis(modified)
        cg.watch_tick(self.db, self.home, dashboard=live)
        self.assertEqual(live.status()['graphRevision'], initial['graphRevision'])
        self.assertNotEqual(live.status()['analysisRevision'], initial['analysisRevision'])
        self.assertEqual(live.timeline.read(SID)['analysis']['nodes'][0]['rationale'], modified['nodes'][0]['rationale'])

    def test_http_views_are_read_only_paged_and_do_not_publish_content_in_status(self):
        before = self.unchanged_tables()
        live = LiveDashboard(self.root/'live', self.db_file, 0)
        self.addCleanup(live.close)
        cg.watch_tick(self.db, self.home, dashboard=live)
        live.start()
        with urlopen(live.url+'timeline/sessions.json') as response:
            self.assertEqual(json.load(response)['sessions'][0]['id'], SID)
        with urlopen(live.url+'timeline/messages.json?'+urlencode({'session':SID,'limit':1})) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(len(json.load(response)['messages']), 1)
        with urlopen(live.url+'status.json') as response:
            self.assertNotIn('예시 질문', response.read().decode())
        for path, expected in [('timeline/messages.json?session=missing',404),
                               ('timeline/messages.json?session='+SID+'&limit=1000',400),
                               ('timeline/messages.json?session='+SID+'&session=other',400),
                               ('timeline/messages.json?session='+SID+'&token=private',400),
                               ('../collection.db',404)]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                urlopen(live.url+path)
            self.assertEqual(raised.exception.code,expected)
            raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(live.url+'timeline/messages.json',data=b'analysis',method='POST'))
        self.assertEqual(raised.exception.code,405)
        raised.exception.close()
        self.assertEqual(self.unchanged_tables(), before)

    def test_serve_never_collects_or_writes_database_or_calls_a_model(self):
        before = self.db_file.read_bytes()
        publish = cg.publish_dashboard
        def checked_publish(con, dashboard, **kwargs):
            self.assertEqual(con.execute('PRAGMA query_only').fetchone()[0], 1)
            return publish(con, dashboard, **kwargs)
        with patch.object(cg, 'scan', side_effect=AssertionError('Collection forbidden')), \
             patch.object(cg, 'connect', side_effect=AssertionError('Write connection forbidden')), \
             patch.object(ap, 'auto_analyze', side_effect=AssertionError('Models forbidden')), \
             patch.object(cg, 'publish_dashboard', side_effect=checked_publish), \
             patch.object(cg.time, 'sleep', side_effect=KeyboardInterrupt), \
             patch.object(LiveDashboard, 'start'), \
             patch.object(LiveDashboard, 'url', 'http://127.0.0.1:0/'), redirect_stdout(io.StringIO()):
            self.assertEqual(cg.main(['--db',str(self.db_file),'serve','--live-dir',str(self.root/'read-only-live')]), 0)
        self.assertEqual(self.db_file.read_bytes(), before)
        status = json.loads((self.root/'read-only-live/status.json').read_text(encoding='utf-8'))
        self.assertTrue(status['readOnlyViewer'])
        self.assertFalse(status['autoAnalysis'])

    def test_fulltext_search_is_literal_korean_and_read_only(self):
        text = '앞부분 ' + '긴 원문입니다. ' * 90 + '찾을말 %_[a] O\'Reilly <img src=x> 끝부분'
        self.append('user', text, 30)
        cg.scan(self.db, self.home)
        before = self.unchanged_tables()
        with patch.object(ap,'auto_analyze',side_effect=AssertionError('Models forbidden')), \
             patch.object(cg.subprocess,'run',side_effect=AssertionError('Processes forbidden')):
            for term in ('찾을말', '%_', '[a]', "o'reilly", '<img src=x>'):
                found = self.reader.search(term)
                self.assertEqual(found['total'],1)
                hit = found['results'][0]
                self.assertIn(term.lower(), hit['snippet'].lower())
                view = self.reader.read(hit['sessionId'], message=hit['id'])
                raw = next(row for row in view['messages'] if row['id']==hit['id'])
                self.assertEqual(raw['content'],text)
                self.assertEqual(view['analysis']['relations'],self.analysis['relations'])
            self.assertEqual(self.reader.search('찾을말',role='assistant')['total'],0)
            self.assertEqual(self.reader.search('찾을말',project='different')['total'],0)
            self.assertEqual(self.reader.search('질문',session=SID)['total'],1)
        self.assertEqual(self.unchanged_tables(),before)

    def test_search_paging_cache_refresh_and_centered_context(self):
        with self.db:
            for index in range(240):
                self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                    (cg.digest('test '+str(index)),SID,'synthetic',index,'user','response_item',
                     f'검색대상 {index}',f'2026-10-21T00:{index//60:02d}:{index%60:02d}Z'))
        first = self.reader.search('검색대상',limit=30)
        second = self.reader.search('검색대상',offset=30,limit=30)
        self.assertEqual(first['total'],240)
        self.assertFalse({r['id'] for r in first['results']} & {r['id'] for r in second['results']})
        target = self.reader.search('검색대상 98')['results'][0]
        view = self.reader.read(SID,message=target['id'],limit=100)
        self.assertEqual(view['offset'],50)
        self.assertEqual(view['messages'][50]['id'],target['id'])
        self.assertEqual(len(view['messages']),100)
        self.append('assistant','새 검색대상',40)
        cg.scan(self.db,self.home)
        self.assertEqual(self.reader.search('검색대상')['total'],241)
        for options in ({'query':''},{'query':'x'*201},{'query':'x','offset':-1},{'query':'x','limit':51},{'query':'x','role':'tool'}):
            with self.assertRaises(ValueError): self.reader.search(**options)

    def test_search_http_post_never_logs_query_or_changes_data(self):
        before = self.unchanged_tables()
        live = LiveDashboard(self.root/'search-live',self.db_file,0)
        self.addCleanup(live.close)
        cg.watch_tick(self.db,self.home,dashboard=live)
        live.start()
        request = Request(live.url+'timeline/search.json', data=json.dumps({'query':'질문'}).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request) as response:
            self.assertEqual(json.load(response)['total'],1)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        for arguments, headers, code in [({}, {},400),({'query':'질문'}, {'Origin':'https://untrusted.example'},403)]:
            with self.assertRaises(HTTPError) as raised:
                urlopen(Request(live.url+'timeline/search.json',data=json.dumps(arguments).encode(),headers=headers))
            self.assertEqual(raised.exception.code,code)
            raised.exception.close()
        self.assertEqual(self.unchanged_tables(),before)

    def test_search_exposes_only_verified_source_item_ids_for_map_navigation(self):
        before=self.unchanged_tables()
        question=self.reader.search('예시 질문')['results'][0]
        answer=self.reader.search('예시 답변')['results'][0]
        self.assertEqual(question['analysisNodeIds'],['question'])
        self.assertEqual(answer['analysisNodeIds'],['answer'])
        self.assertEqual(self.unchanged_tables(),before)
        changed=deepcopy(self.analysis);changed['nodes'][0]['evidence'][0]['verified']=False
        self.import_analysis(changed)
        self.assertEqual(self.reader.search('예시 질문')['results'][0]['analysisNodeIds'],[])


if __name__ == '__main__': unittest.main()
