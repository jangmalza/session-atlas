import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import context_graph as cg

try:
    from compression import zstd
except ImportError:
    zstd = None


@unittest.skipUnless(zstd, 'Compressed collection requires Python 3.14')
class ArchiveCollectionTests(unittest.TestCase):
    def test_compressed_import_and_unchanged_rescan_preserve_original_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            archive = home / 'archived_sessions'
            archive.mkdir(parents=True)
            sid = '11111111-1111-4111-8111-111111111111'
            raw = archive / f'rollout-{sid}.jsonl'
            packed = Path(str(raw) + '.zst')
            events = [
                {'type': 'session_meta', 'payload': {'id': sid, 'source': 'cli', 'cwd': 'C:/test'}},
                {
                    'type': 'response_item',
                    'timestamp': '2026-01-01T00:00:00Z',
                    'payload': {
                        'type': 'message',
                        'role': 'user',
                        'content': [{'type': 'input_text', 'text': '압축된 요청'}],
                    },
                },
            ]
            content = ('\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n').encode()
            packed.write_bytes(zstd.compress(content))
            con = cg.connect(root / 'test.db')
            try:
                self.assertEqual(sum(r['added_messages'] for r in cg.scan(con, home)), 1)
                before = [tuple(r) for r in con.execute('SELECT * FROM messages')]
                with patch(
                    'session_metadata.open_rollout', side_effect=AssertionError('Unchanged archive must not decompress')
                ):
                    self.assertEqual(cg.scan_file(con, packed)['added_messages'], 0)
                raw.write_bytes(content)
                self.assertEqual(cg.scan_file(con, raw)['added_messages'], 0)
                self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM messages')], before)
                self.assertEqual(packed.read_bytes(), zstd.compress(content))
            finally:
                con.close()

    def test_complete_archive_can_verify_a_mirror_missing_from_a_partial_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sid = '11111111-1111-4111-8111-111111111111'
            raw = root / f'rollout-{sid}.jsonl'
            packed = root / 'archive' / f'rollout-{sid}.jsonl.zst'
            packed.parent.mkdir()
            header = {'type': 'session_meta', 'payload': {'id': sid, 'source': 'cli'}}
            event = {
                'type': 'event_msg',
                'timestamp': '2026-01-01T00:00:00Z',
                'payload': {'type': 'user_message', 'message': 'Repeated'},
            }
            response = {
                'type': 'response_item',
                'timestamp': '2026-01-01T00:00:00.050Z',
                'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Repeated'}]},
            }
            encode = lambda rows: (''.join(json.dumps(r) + '\n' for r in rows)).encode()
            raw.write_bytes(encode([header, event]))
            con = cg.connect(root / 'db.db')
            try:
                cg.scan_file(con, raw)
                before = tuple(con.execute('SELECT * FROM messages').fetchone())
                packed.write_bytes(zstd.compress(encode([header, event, response])))
                cg.scan_file(con, packed)
                self.assertEqual(len(cg.effective_messages(con, sid)), 1)
                self.assertEqual(cg.effective_messages(con, sid)[0]['source_kind'], 'response_item')
                self.assertEqual(
                    tuple(con.execute('SELECT * FROM messages WHERE id=?', (before[0],)).fetchone()), before
                )
            finally:
                con.close()
