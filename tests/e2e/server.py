"""Synthetic-only E2E server. No production database, credentials or model calls."""
from pathlib import Path
import json,sys,tempfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import context_graph as cg
from live_dashboard import LiveDashboard
from source_adapters import scan_claude

def forbidden(*args,**kwargs):raise AssertionError('Models and classification APIs are forbidden in E2E')
cg.auto_analyze=forbidden;cg.assign_topics=forbidden
SID='11111111-1111-4111-8111-111111111111'
with tempfile.TemporaryDirectory(prefix='session-atlas-e2e-') as directory:
    root=Path(directory);home=root/'codex';rollout=home/'sessions'/f'rollout-{SID}.jsonl';rollout.parent.mkdir(parents=True)
    records=[{'type':'session_meta','payload':{'id':SID,'source':'cli','cwd':'C:/E2E'}}]
    for i in range(350):
        text=f'Synthetic {"question" if i%2==0 else "answer"} {i//2:03d} literal <img src=x>.'
        records.append({'type':'response_item','timestamp':f'2026-10-01T00:{i//60:02d}:{i%60:02d}Z',
                        'payload':{'type':'message','role':'user' if i%2==0 else 'assistant','content':[{'type':'input_text','text':text}]}})
    rollout.write_bytes(('\n'.join(json.dumps(r) for r in records)+'\n').encode())
    con=cg.connect(root/'fixture.db');cg.scan(con,home)
    raw_sid='22222222-2222-4222-8222-222222222222'
    raw_file=home/'sessions'/f'rollout-{raw_sid}.jsonl'
    raw_records=[{'type':'session_meta','payload':{'id':raw_sid,'source':'cli','cwd':'C:/RawFixture'}}]
    for i in range(24):
        raw_records.append({'type':'response_item','timestamp':f'2026-10-02T00:00:{i:02d}Z','payload':{'type':'message','role':'user' if i%2==0 else 'assistant','content':[{'type':'input_text','text':f'Unanalysed raw message {i}'}]}})
    raw_file.write_bytes(('\n'.join(json.dumps(r) for r in raw_records)+'\n').encode());cg.scan_file(con,raw_file)
    claude_home=root/'claude';claude_file=claude_home/'projects'/'fixture'/'33333333-3333-4333-8333-333333333333.jsonl';claude_file.parent.mkdir(parents=True)
    claude_records=[{'sessionId':'33333333-3333-4333-8333-333333333333','uuid':'native-'+role,'parentUuid':None,'type':role,'cwd':'C:/ClaudeFixture','timestamp':'2026-10-03T00:00:00Z','message':{'role':role,'content':'Synthetic Claude '+role}} for role in ('user','assistant')]
    claude_file.write_text(''.join(json.dumps(r)+'\n' for r in claude_records));scan_claude(con,claude_home)
    rows=cg.effective_messages(con,SID)
    nodes=[{'id':'root','kind':'root','title':'E2E conversation','childIds':['topic']},
           {'id':'topic','kind':'topic','title':'E2E topic','parentId':'root','childIds':[]}]
    relations=[]
    for i in range(18):
        for offset,kind in [(0,'answered_question'),(1,'assistant_statement')]:
            index=i*2+offset;key=('a' if offset else 'q')+str(i)
            nodes[1]['childIds'].append(key)
            nodes.append({'id':key,'kind':'item','parentId':'topic','title':f'E2E {"answer" if offset else "question"} {i:02d}',
                          'summary':'Synthetic stored analysis','classification':kind,'status':'answered' if not offset else 'none',
                          'evidence':[{'messageId':f'm{index+1}','index':index,'speaker':rows[index]['role'],'quote':rows[index]['content'],'verified':True}]})
        relations.append({'id':'r'+str(i),'from':'a'+str(i),'to':'q'+str(i),'type':'answers'})
    value={'title':'E2E conversation','source':{'messageCount':350},'nodes':nodes,'relations':relations,
           'messages':[{'id':f'm{i+1}','index':i,'speaker':r['role'],'text':r['content'],'timestamp':r['at']} for i,r in enumerate(rows)]}
    fixture_map=root/'map.json';fixture_map.write_text(json.dumps(value),encoding='utf-8')
    cg.import_map(con,SID,fixture_map,cg.digest(cg.serialize_transcript(cg.transcript(con,SID))))
    dashboard=LiveDashboard(root/'live',root/'fixture.db',18765,codex_home=home)
    cg.publish_dashboard(con,dashboard,read_only_viewer=True);dashboard.start()
    try:
        while True:
            time.sleep(1);cg.publish_dashboard(con,dashboard,read_only_viewer=True)
    finally:dashboard.close();con.close()
