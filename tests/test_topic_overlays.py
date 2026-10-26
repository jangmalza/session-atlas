"""Sidecar topic projection, immutable sources, and model-free live refresh."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from decisions_topics import map_fingerprint
from live_dashboard import LiveDashboard, embedded_json
from session_timeline import TimelineReader
from topic_overlays import project_structure, read_overlays, store_overlay, validate_overlay

SID = '99999999-9999-4999-8999-999999999999'


def example_map():
    return {'title': 'Synthetic session', 'source': {'messageCount': 2}, 'nodes': [
        {'id': 'root', 'kind': 'root', 'childIds': ['first', 'second']},
        {'id': 'first', 'kind': 'topic', 'title': '기존 주제', 'parentId': 'root', 'childIds': ['question', 'answer']},
        {'id': 'second', 'kind': 'topic', 'title': '다른 주제', 'parentId': 'root', 'childIds': ['fact']},
        {'id': 'question', 'kind': 'item', 'title': '예시 질문', 'summary': '예시 질문 요약',
         'classification': 'answered_question', 'status': 'answered', 'parentId': 'first',
         'evidence': [{'messageId': 'm1', 'index': 0, 'speaker': 'user', 'quote': 'synthetic question', 'verified': True}]},
        {'id': 'answer', 'kind': 'item', 'title': '예시 답변', 'classification': 'assistant_statement', 'parentId': 'first',
         'evidence': [{'messageId': 'm2', 'index': 1, 'speaker': 'assistant', 'quote': 'synthetic answer', 'verified': True}]},
        {'id': 'fact', 'kind': 'item', 'title': '예시 사실', 'classification': 'fact', 'parentId': 'second', 'evidence': []}],
        'relations': [{'id': 'answer-link', 'from': 'answer', 'to': 'question', 'type': 'answers', 'note': 'Original relation'}]}


def example_overlay(source):
    return {'schemaVersion': 'topic-assignments-1', 'sourceMapSha256': map_fingerprint(source),
            'classifier': 'openai-decisions', 'model': 'test-model', 'policyVersion': 'test-policy',
            'minConfidence': 0.85, 'assignments': [
                {'nodeId': 'question', 'topicId': 'second', 'status': 'assigned', 'confidence': 0.95},
                {'nodeId': 'answer', 'topicId': None, 'status': 'review', 'confidence': 0.5},
                {'nodeId': 'fact', 'topicId': None, 'status': 'new_topic', 'confidence': 0.93}]}


class TopicProjectionTests(unittest.TestCase):
    def test_projection_reparents_only_copied_memberships(self):
        source = example_map()
        overlay = example_overlay(source)
        before, saved = deepcopy(source), deepcopy(overlay)
        structure, summary = project_structure(source, overlay)
        by_id = {node['id']: node for node in structure}
        self.assertEqual(set(by_id), {node['id'] for node in source['nodes']})
        self.assertEqual(by_id['root']['childIds'], ['first', 'second', 'answer', 'fact'])
        self.assertEqual(by_id['first']['childIds'], [])
        self.assertEqual(by_id['second']['childIds'], ['question'])
        self.assertEqual(by_id['question']['parentId'], 'second')
        self.assertEqual(by_id['answer']['parentId'], 'root')
        self.assertEqual(by_id['fact']['parentId'], 'root')
        self.assertEqual(by_id['second']['parentId'], 'root')
        self.assertEqual(summary, {'status': 'decisions', 'assigned': 1, 'review': 1, 'newTopic': 1, 'model': 'test-model'})
        self.assertEqual(source, before)
        self.assertEqual(overlay, saved)
        by_id['second']['childIds'].append('only-in-copy')
        self.assertEqual(source, before)
        self.assertNotIn('quote', json.dumps(structure))

    def test_missing_duplicate_unknown_ids_and_invalid_results_are_rejected(self):
        source, original = example_map(), example_overlay(example_map())
        variants = []
        for change in (
            {'nodeId': 'unknown'}, {'nodeId': 'first'}, {'topicId': 'unknown'},
            {'topicId': 'answer'}, {'status': 'other'}, {'confidence': True},
            {'confidence': float('nan')}, {'confidence': 0.4}, {'confidence': None}):
            overlay = deepcopy(original)
            overlay['assignments'][0].update(change)
            variants.append(overlay)
        overlay = deepcopy(original)
        overlay['assignments'].append(deepcopy(overlay['assignments'][0]))
        variants.append(overlay)
        overlay = deepcopy(original)
        overlay['assignments'].pop()
        variants.append(overlay)
        for overlay in variants:
            with self.subTest(assignment=overlay['assignments'][0]), self.assertRaisesRegex(ValueError, '^Invalid or outdated topic assignments\\.$'):
                validate_overlay(source, overlay)

    def test_stale_and_invalid_results_keep_original_structure(self):
        source, overlay = example_map(), example_overlay(example_map())
        expected, summary = project_structure(source)
        self.assertEqual(summary['status'], 'codex')
        overlay['assignments'].pop()
        actual, summary = project_structure(source, overlay)
        self.assertEqual(actual, expected)
        self.assertEqual(summary['status'], 'invalid')
        overlay = example_overlay(source)
        source['nodes'][3]['summary'] = 'Source changed without altering its ID'
        actual, summary = project_structure(source, overlay)
        self.assertEqual(actual, project_structure(source)[0])
        self.assertEqual(summary['status'], 'stale')

    def test_missing_root_keeps_review_items_unassigned(self):
        source = example_map()
        source['nodes'].pop(0)
        structure, summary = project_structure(source, example_overlay(source))
        self.assertIsNone(next(node for node in structure if node['id'] == 'answer')['parentId'])
        self.assertEqual(summary['review'], 1)


class TopicOverlayStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'synthetic.db'
        self.con = cg.connect(self.database)
        self.addCleanup(self.con.close)
        # Also works while the optional schema migration is introduced separately.
        self.con.execute('''CREATE TABLE IF NOT EXISTS topic_assignments(
            session_id TEXT PRIMARY KEY REFERENCES sessions(id), source_map_sha256 TEXT NOT NULL,
            assignment_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        self.con.execute("INSERT INTO sessions(id,project,first_seen,last_seen) VALUES (?,'Example','x','x')", (SID,))
        for index, (role, text) in enumerate([('user', 'synthetic question'), ('assistant', 'synthetic answer')]):
            self.con.execute('''INSERT INTO messages(id,session_id,source_path,offset_bytes,role,source_kind,content,at)
                VALUES(?,?,?,?,?,?,?,?)''', (cg.digest(str(index)), SID, 'synthetic.jsonl', index, role, 'response_item', text,
                                           f'2026-10-23T00:00:0{index}Z'))
        self.source = example_map()
        self.overlay = example_overlay(self.source)
        self.con.execute('INSERT INTO session_maps VALUES (?,?,?,?)',
                         (SID, cg.digest(cg.serialize_transcript(cg.transcript(self.con, SID))), json.dumps(self.source), 'now'))
        self.con.commit()
        self.reader = TimelineReader(self.database)

    def source_rows(self):
        return {table: [tuple(row) for row in self.con.execute('SELECT * FROM ' + table)]
                for table in ('sessions', 'messages', 'session_maps', 'analyses')}

    def test_sidecar_write_is_transaction_owned_and_source_preserving(self):
        before = self.source_rows()
        store_overlay(self.con, SID, self.source, self.overlay, 'now')
        self.assertTrue(self.con.in_transaction)
        self.con.rollback()
        self.assertEqual(read_overlays(self.con), {})
        with self.con:
            store_overlay(self.con, SID, self.source, self.overlay, 'now')
        self.assertEqual(read_overlays(self.con), {SID: self.overlay})
        self.assertEqual(self.source_rows(), before)

    def test_reader_revisions_change_for_overlay_without_mutating_analysis_or_sources(self):
        before = self.source_rows()
        original = self.reader.read(SID)
        with self.con:
            store_overlay(self.con, SID, self.source, self.overlay, 'now')
        current = self.reader.read(SID)
        context = self.reader.question_context({SID})[SID]
        self.assertNotEqual(original['revision'], current['revision'])
        for key in ('nodes', 'relations', 'anchors', 'baselineMessageCount', 'unplacedIds'):
            self.assertEqual(current['analysis'][key], original['analysis'][key])
        self.assertEqual(current['messages'], original['messages'])
        self.assertEqual(current['analysis']['topicAssignment']['status'], 'decisions')
        self.assertEqual(current['session']['topicAssignment'], context['session']['topicAssignment'])
        self.assertEqual(current['analysis']['structure'], context['analysis']['structure'])
        self.assertEqual(self.source_rows(), before)
        self.source['nodes'][3]['summary'] = 'Changed analysis'
        with self.con:
            self.con.execute('UPDATE session_maps SET map_json=? WHERE session_id=?', (json.dumps(self.source), SID))
        stale = self.reader.read(SID)
        self.assertEqual(stale['session']['topicAssignment']['status'], 'stale')
        self.assertEqual(stale['analysis']['structure'], project_structure(self.source)[0])

    def test_legacy_database_without_overlay_table_stays_read_only(self):
        self.con.execute('DROP TABLE topic_assignments')
        self.con.commit()
        self.assertEqual(read_overlays(self.con), {})
        view = self.reader.read(SID)
        self.assertEqual(view['analysis']['structure'], project_structure(self.source)[0])
        self.assertEqual(view['analysis']['topicAssignment']['status'], 'codex')
        self.assertIsNone(self.con.execute("SELECT 1 FROM sqlite_master WHERE name='topic_assignments'").fetchone())

    def test_corrupt_stored_json_is_visible_as_invalid_not_a_false_assignment(self):
        with self.con:
            self.con.execute('INSERT INTO topic_assignments VALUES (?,?,?,?)', (SID, 'hash', '{broken', 'now'))
        view = self.reader.read(SID)
        self.assertEqual(view['analysis']['topicAssignment']['status'], 'invalid')
        self.assertEqual(view['analysis']['structure'], project_structure(self.source)[0])
        with self.con:
            self.con.execute('UPDATE topic_assignments SET assignment_json=?', (json.dumps(self.overlay),))
        self.assertEqual(self.reader.read(SID)['analysis']['topicAssignment']['status'], 'invalid')

    def test_sidecar_refreshes_http_and_offline_context_but_new_messages_do_not_redraw_graph(self):
        dashboard = LiveDashboard(self.root / 'live', self.database, 0)
        self.addCleanup(dashboard.close)
        before = self.source_rows()
        with patch.object(cg, 'auto_analyze', side_effect=AssertionError('Models forbidden')), \
             patch('decisions_topics.request_decisions', side_effect=AssertionError('API forbidden')):
            cg.publish_dashboard(self.con, dashboard)
            dashboard.start()
            initial = (dashboard.directory / 'graph.html').read_text(encoding='utf-8')
            initial_revision = dashboard.status()['graphRevision']
            with self.con:
                store_overlay(self.con, SID, self.source, self.overlay, 'now')
            cg.publish_dashboard(self.con, dashboard)
            refreshed = (dashboard.directory / 'graph.html').read_text(encoding='utf-8')
            self.assertNotEqual(initial_revision, dashboard.status()['graphRevision'])
            self.assertEqual(embedded_json(initial, 'graph-data'), embedded_json(refreshed, 'graph-data'))
            context = embedded_json(refreshed, 'qa-context')[SID]
            self.assertEqual(context['analysis']['topicAssignment']['status'], 'decisions')
            self.assertEqual(context['analysis']['structure'], project_structure(self.source, self.overlay)[0])
            with urlopen(dashboard.url + 'timeline/messages.json?' + urlencode({'session': SID})) as response:
                payload = json.load(response)
            self.assertEqual(payload['analysis']['structure'], context['analysis']['structure'])
            self.assertEqual(payload['analysis']['nodes'], [node for node in self.source['nodes'] if node['kind'] == 'item'])
            self.assertEqual(payload['analysis']['relations'], self.source['relations'])
            self.assertEqual(self.source_rows(), before)
            with self.con:
                self.con.execute('''INSERT INTO messages(id,session_id,source_path,offset_bytes,role,source_kind,content,at)
                    VALUES(?,?,?,3,'user','response_item','new synthetic message','2026-10-23T00:00:03Z')''',
                    (cg.digest('3'), SID, 'synthetic.jsonl'))
            cg.publish_dashboard(self.con, dashboard)
            self.assertEqual((dashboard.directory / 'graph.html').read_text(encoding='utf-8'), refreshed)
            self.assertEqual(dashboard.timeline.read(SID)['analysis']['topicAssignment']['status'], 'decisions')
            # A field excluded from graph-data can still invalidate assignments.
            self.source['nodes'][3]['rationale'] = 'Changed source classification rationale'
            with self.con:
                self.con.execute('UPDATE session_maps SET map_json=? WHERE session_id=?', (json.dumps(self.source), SID))
            cg.publish_dashboard(self.con, dashboard)
            stale_document = (dashboard.directory / 'graph.html').read_text(encoding='utf-8')
            self.assertEqual(embedded_json(stale_document, 'graph-data'), embedded_json(refreshed, 'graph-data'))
            self.assertEqual(embedded_json(stale_document, 'qa-context')[SID]['analysis']['topicAssignment']['status'], 'stale')


if __name__ == '__main__':
    unittest.main()
