"""Synthetic-only E2E server. No production database, credentials or model calls."""

from pathlib import Path
import json, sys, tempfile, time
import threading, os
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import analysis_pipeline as ap
import context_graph as cg
from live_dashboard import LiveDashboard
from source_adapters import scan_claude
from analysis_control import AnalysisControl, write_json


class FakeCredentials:
    supported = True
    kind = 'windows'

    def __init__(self):
        self.key = None

    def read(self, *, presence_only=False):
        return bool(self.key) if presence_only else self.key

    def write(self, key):
        self.key = key

    def delete(self):
        self.key = None


class FixtureControl(AnalysisControl):
    def settings(self):
        return {**super().settings(), 'codexInstalled': True, 'nodeInstalled': True, 'conversationMapReady': True}

    def check_codex(self):
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.root / 'codex-status.json', {'status': 'ready', 'at': time.time()})
        return self.settings()


def fake_analyzer(con, sid, root, out, *, progress):
    time.sleep(1)
    progress('validating')
    time.sleep(1)
    rows = cg.effective_messages(con, sid)
    value = {
        'title': 'Synthetic analysed conversation',
        'nodes': [
            {'id': 'root', 'kind': 'root', 'title': 'Synthetic analysed conversation', 'childIds': ['topic']},
            {
                'id': 'topic',
                'kind': 'topic',
                'title': 'Synthetic generated topic',
                'parentId': 'root',
                'childIds': ['q'],
            },
            {
                'id': 'q',
                'kind': 'item',
                'parentId': 'topic',
                'title': 'Synthetic analysed question',
                'classification': 'open_question',
                'status': 'open',
                'evidence': [
                    {'messageId': 'm1', 'index': 0, 'speaker': 'user', 'quote': rows[0]['content'], 'verified': True}
                ],
            },
        ],
        'relations': [],
        'source': {'messageCount': len(rows)},
        'messages': [
            {'id': f'm{i + 1}', 'index': i, 'speaker': r['role'], 'text': r['content'], 'timestamp': r['at']}
            for i, r in enumerate(rows)
        ],
    }
    cg._save_map(con, sid, value, cg.digest(cg.serialize_transcript(cg.transcript(con, sid))))
    con.execute('INSERT INTO analyses VALUES (?,?,?,?,?)', (sid, 'synthetic', 'ok', cg.now(), 'synthetic'))
    con.commit()


def fake_launch(controller, job_id):
    threading.Thread(
        target=lambda: controller.execute(job_id, analyzer=fake_analyzer, classifier=lambda *_: None), daemon=True
    ).start()
    return SimpleNamespace(pid=os.getpid())


def forbidden(*args, **kwargs):
    raise AssertionError('Models and classification APIs are forbidden in E2E')


ap.auto_analyze = forbidden
ap.assign_topics = forbidden
SID = '11111111-1111-4111-8111-111111111111'
with tempfile.TemporaryDirectory(prefix='session-atlas-e2e-') as directory:
    root = Path(directory)
    home = root / 'codex'
    rollout = home / 'sessions' / f'rollout-{SID}.jsonl'
    rollout.parent.mkdir(parents=True)
    records = [{'type': 'session_meta', 'payload': {'id': SID, 'source': 'cli', 'cwd': 'C:/E2E'}}]
    for i in range(350):
        text = f'Synthetic {"question" if i % 2 == 0 else "answer"} {i // 2:03d} literal <img src=x>.'
        records.append(
            {
                'type': 'response_item',
                'timestamp': f'2026-10-01T00:{i // 60:02d}:{i % 60:02d}Z',
                'payload': {
                    'type': 'message',
                    'role': 'user' if i % 2 == 0 else 'assistant',
                    'content': [{'type': 'input_text', 'text': text}],
                },
            }
        )
    rollout.write_bytes(('\n'.join(json.dumps(r) for r in records) + '\n').encode())
    con = cg.connect(root / 'fixture.db')
    cg.scan(con, home)
    raw_sid = '22222222-2222-4222-8222-222222222222'
    raw_file = home / 'sessions' / f'rollout-{raw_sid}.jsonl'
    raw_records = [{'type': 'session_meta', 'payload': {'id': raw_sid, 'source': 'cli', 'cwd': 'C:/RawFixture'}}]
    for i in range(24):
        raw_records.append(
            {
                'type': 'response_item',
                'timestamp': f'2026-10-02T00:00:{i:02d}Z',
                'payload': {
                    'type': 'message',
                    'role': 'user' if i % 2 == 0 else 'assistant',
                    'content': [{'type': 'input_text', 'text': f'Unanalysed raw message {i}'}],
                },
            }
        )
    raw_file.write_bytes(('\n'.join(json.dumps(r) for r in raw_records) + '\n').encode())
    cg.scan_file(con, raw_file)
    claude_home = root / 'claude'
    claude_file = claude_home / 'projects' / 'fixture' / '33333333-3333-4333-8333-333333333333.jsonl'
    claude_file.parent.mkdir(parents=True)
    claude_records = [
        {
            'sessionId': '33333333-3333-4333-8333-333333333333',
            'uuid': 'native-' + role,
            'parentUuid': None,
            'type': role,
            'cwd': 'C:/ClaudeFixture',
            'timestamp': '2026-10-03T00:00:00Z',
            'message': {'role': role, 'content': 'Synthetic Claude ' + role},
        }
        for role in ('user', 'assistant')
    ]
    claude_file.write_text(''.join(json.dumps(r) + '\n' for r in claude_records))
    scan_claude(con, claude_home)
    rows = cg.effective_messages(con, SID)
    nodes = [
        {'id': 'root', 'kind': 'root', 'title': 'E2E conversation', 'childIds': ['topic']},
        {'id': 'topic', 'kind': 'topic', 'title': 'E2E topic', 'parentId': 'root', 'childIds': []},
    ]
    relations = []
    for i in range(18):
        for offset, kind in [(0, 'answered_question'), (1, 'assistant_statement')]:
            index = i * 2 + offset
            key = ('a' if offset else 'q') + str(i)
            nodes[1]['childIds'].append(key)
            nodes.append(
                {
                    'id': key,
                    'kind': 'item',
                    'parentId': 'topic',
                    'title': f'E2E {"answer" if offset else "question"} {i:02d}',
                    'summary': 'Synthetic stored analysis',
                    'classification': kind,
                    'status': 'answered' if not offset else 'none',
                    'evidence': [
                        {
                            'messageId': f'm{index + 1}',
                            'index': index,
                            'speaker': rows[index]['role'],
                            'quote': rows[index]['content'],
                            'verified': True,
                        }
                    ],
                }
            )
        relations.append({'id': 'r' + str(i), 'from': 'a' + str(i), 'to': 'q' + str(i), 'type': 'answers'})
    value = {
        'title': 'E2E conversation',
        'source': {'messageCount': 350},
        'nodes': nodes,
        'relations': relations,
        'messages': [
            {'id': f'm{i + 1}', 'index': i, 'speaker': r['role'], 'text': r['content'], 'timestamp': r['at']}
            for i, r in enumerate(rows)
        ],
    }
    fixture_map = root / 'map.json'
    fixture_map.write_text(json.dumps(value), encoding='utf-8')
    cg.import_map(con, SID, fixture_map, cg.digest(cg.serialize_transcript(cg.transcript(con, SID))))
    dashboard = LiveDashboard(root / 'live', root / 'fixture.db', 18765, codex_home=home)
    dashboard.analysis = FixtureControl(root / 'fixture.db', home, credentials=FakeCredentials(), launcher=fake_launch)
    cg.publish_dashboard(con, dashboard, read_only_viewer=True)
    dashboard.start()
    try:
        while True:
            time.sleep(1)
            cg.publish_dashboard(con, dashboard, read_only_viewer=True)
    finally:
        dashboard.close()
        con.close()
