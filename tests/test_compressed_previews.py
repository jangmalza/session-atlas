"""Compressed archive previews require exact identity, offsets and source records."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from session_metadata import SessionMetadataReader, open_rollout
from session_timeline import TimelineReader
try:
    from compression import zstd
except ImportError:
    zstd = None

SID = '22222222-2222-4222-8222-222222222222'
PARENT = '11111111-1111-4111-8111-111111111111'


@unittest.skipUnless(zstd, 'Python 3.14 supplies the optional archive reader')
class CompressedPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.home = self.root / 'codex'
        original = self.home / 'sessions' / ('rollout-' + SID + '.jsonl')
        original.parent.mkdir(parents=True)
        header = {'type':'session_meta', 'payload':{'id':SID, 'source':'cli', 'cwd':'C:/synthetic',
                  'forked_from_id':PARENT, 'forked_from_ordinal_exclusive':20,
                  'history_base':{'thread_id':PARENT, 'end_ordinal_exclusive':20, 'end_byte_offset':800}}}
        def event(role, text, second):
            return {'type':'response_item','timestamp':f'2026-10-25T00:00:0{second}Z',
                    'payload':{'type':'message','role':role,'content':[{'type':'input_text','text':text}]}}
        records = [header, event('user','<environment_context>SKIP CONTEXT</environment_context>',0),
                   event('user','FIRST 고유 요청',1), event('assistant','SYNTHETIC ANSWER',2),
                   event('user','LATEST 고유 요청',3)]
        self.raw = ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records).encode()
        original.write_bytes(self.raw)
        self.database = self.root / 'collection.db'; self.con = cg.connect(self.database)
        self.addCleanup(self.con.close); cg.scan_file(self.con, original)
        self.archive = self.home / 'archived_sessions' / original.name
        self.archive.parent.mkdir(); self.packed = Path(str(self.archive)+'.zst')
        self.packed.write_bytes(zstd.compress(self.raw)); original.unlink()
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as con, con:
            con.execute('CREATE TABLE threads (id TEXT PRIMARY KEY,source TEXT,rollout_path TEXT,title TEXT)')
            con.execute('INSERT INTO threads VALUES (?,?,?,?)',(SID,'cli',str(self.archive),'Inherited native label'))
        self.preferences = self.database.with_suffix('.topic-display.json')
        self.preferences.write_text(json.dumps({'schemaVersion':'topic-display-1','sessions':{},
                                              'sessionNames':{SID:'기존 표시 이름'}}),encoding='utf-8')
        self.reader = TimelineReader(self.database,self.home)

    def snapshot(self):
        return {table:[tuple(r) for r in self.con.execute('SELECT * FROM '+table)]
                for table in ('sessions','messages','sources','analyses','session_maps','topic_assignments')}

    def header(self):
        return self.reader.read()['sessions'][0]

    def test_verified_compressed_previews_preserve_titles_preferences_and_all_source_rows(self):
        before = self.snapshot(); packed = self.packed.read_bytes(); preferences = self.preferences.read_bytes()
        with patch.object(cg,'auto_analyze',side_effect=AssertionError('Models forbidden')), \
             patch.object(cg.subprocess,'run',side_effect=AssertionError('Processes forbidden')), \
             patch('socket.create_connection',side_effect=AssertionError('API forbidden')):
            header = self.header(); offline = self.reader.question_context({SID})[SID]['session']
        rows = cg.effective_messages(self.con,SID)
        self.assertEqual(header['titleSource'],'fallback')
        self.assertEqual(header['title'],'고유 요청 확인 불가')
        self.assertEqual(header['displayTitle'],'기존 표시 이름')
        self.assertEqual(header['requestPreviews']['scope'],'own')
        self.assertEqual(header['requestPreviews']['first']['messageId'],rows[1]['id'])
        self.assertEqual(header['requestPreviews']['latest']['messageId'],rows[3]['id'])
        self.assertNotIn('requestPreviews',offline)
        self.assertNotIn('FIRST',json.dumps(offline)); self.assertNotIn('LATEST',json.dumps(offline))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.packed.read_bytes(),packed); self.assertEqual(self.preferences.read_bytes(),preferences)
        self.assertFalse(self.archive.exists(),'archive must not be extracted to disk')

    def test_unchanged_archives_reuse_metadata_and_preview_cache(self):
        before = self.header()
        with patch('session_metadata.open_rollout',side_effect=AssertionError('Unexpected archive reread')), \
             patch('session_timeline.open_rollout',side_effect=AssertionError('Unexpected source reread')):
            self.assertEqual(self.header(),before)
            self.assertEqual(self.reader.read(SID)['session'],before)

    def test_wrong_session_header_is_rejected_even_when_message_records_match(self):
        self.packed.write_bytes(zstd.compress(self.raw.replace(SID.encode(),PARENT.encode())))
        self.assertIsNone(self.header()['requestPreviews']['first'])
        self.assertIsNone(self.header()['requestPreviews']['latest'])

    def test_shifted_source_offsets_are_not_recovered_by_text_search(self):
        self.packed.write_bytes(zstd.compress(self.raw.replace(b'"source": "cli"',b'"source": "cli"    ')))
        self.assertIsNone(self.header()['requestPreviews']['first'])
        self.assertIsNone(self.header()['requestPreviews']['latest'])

    def test_changed_archive_invalidates_cache_and_removes_unverified_previews(self):
        self.assertIsNotNone(self.header()['requestPreviews']['first'])
        self.packed.write_bytes(b'CORRUPT PRIVATE SOURCE')
        changed = self.header()
        self.assertIsNone(changed['requestPreviews']['first'])
        self.assertIsNone(changed['requestPreviews']['latest'])
        self.assertEqual(changed['displayTitle'],'기존 표시 이름')

    def test_normal_metadata_does_not_silently_reclassify_or_rename_archived_sessions(self):
        reader = SessionMetadataReader(self.home)
        plain = reader.read([SID])[SID]
        recovered = reader.read([SID],include_compressed=True)[SID]
        self.assertFalse(plain['boundaryKnown']); self.assertFalse(plain['nativeTitleUsable'])
        self.assertTrue(recovered['boundaryKnown']); self.assertTrue(recovered['forked'])
        self.assertEqual(reader.read([SID])[SID],plain)


class UnavailableArchiveReaderTests(unittest.TestCase):
    def test_missing_optional_runtime_is_a_safe_local_read_failure(self):
        with patch.dict(sys.modules,{'compression':None}), self.assertRaises(OSError) as raised:
            with open_rollout(Path('synthetic.jsonl.zst')):
                self.fail('Unavailable reader must not yield a stream')
        self.assertIn('Python 3.14',str(raised.exception))


if __name__=='__main__':unittest.main()
