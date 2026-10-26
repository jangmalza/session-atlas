import json,tempfile,unittest
from pathlib import Path
import context_graph as cg
from source_adapters import scan_claude
from session_timeline import TimelineReader

class ClaudeCollectionTests(unittest.TestCase):
    def test_visible_text_native_ids_repetitions_and_provider_namespace_survive_rescans(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'claude';project=home/'projects'/'fixture';project.mkdir(parents=True)
            native='11111111-1111-4111-8111-111111111111';path=project/(native+'.jsonl')
            def row(kind,uid,content,**extra):return {'type':kind,'sessionId':native,'uuid':uid,'parentUuid':None,'cwd':'C:/Fixture','timestamp':'2026-01-01T00:00:00Z','message':{'role':kind,'content':content},**extra}
            rows=[row('user','u1','같은 요청'),row('assistant','a1',[{'type':'thinking','thinking':'HIDDEN THINKING'},{'type':'text','text':'Visible answer'},{'type':'tool_use','input':{'secret':'DO NOT STORE'}}]),row('user','u2','같은 요청'),row('user','tool',[{'type':'tool_result','content':'TOOL OUTPUT'}])]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8');con=cg.connect(root/'db.db')
            try:
                self.assertEqual(sum(r['added_messages'] for r in scan_claude(con,home)),3)
                before=[tuple(r) for r in con.execute('SELECT * FROM messages')]
                self.assertEqual(sum(r['added_messages'] for r in scan_claude(con,home)),0)
                reader=TimelineReader(root/'db.db');header=reader.read()['sessions'][0]
                self.assertEqual(header['id'],'claude-'+native);self.assertEqual(header['nativeSessionId'],native);self.assertEqual(header['provider'],'claude')
                self.assertEqual(header['sessionKind'],'primary')
                view=reader.read(header['id']);self.assertEqual({m['nativeMessageId'] for m in view['messages']},{'u1','u2','a1'})
                self.assertNotIn('HIDDEN THINKING',json.dumps(view));self.assertNotIn('TOOL OUTPUT',json.dumps(view))
                self.assertEqual(reader.search('같은 요청')['total'],2)
                self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM messages')],before)
            finally:con.close()

    def test_helpers_are_separate_and_inherited_native_messages_are_not_preview_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'claude';project=home/'projects'/'fixture';native='11111111-1111-4111-8111-111111111111';helper=project/native/'subagents';helper.mkdir(parents=True)
            def row(uid,text):return {'type':'user','sessionId':native,'uuid':uid,'cwd':'C:/Fixture','message':{'role':'user','content':text}}
            shared=row('same','Inherited request');(project/(native+'.jsonl')).write_text(json.dumps(shared)+'\n')
            (helper/'agent-one.jsonl').write_text(json.dumps(shared)+'\n'+json.dumps(row('own','Own request'))+'\n')
            con=cg.connect(root/'db.db')
            try:
                scan_claude(con,home);headers=TimelineReader(root/'db.db').read()['sessions'];self.assertEqual(len(headers),2)
                child=next(h for h in headers if h['sessionKind']=='subagent')
                self.assertEqual(child['parentSessionId'],'claude-'+native)
                self.assertEqual(child['requestPreviews']['first']['text'],'Own request')
            finally:con.close()
