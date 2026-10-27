"""Windows subprocess encoding and executable resolution (no model calls)."""
from pathlib import Path
from unittest.mock import patch
import json
import locale
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg

SID = '01234567-89ab-4def-8234-0123456789ab'


class WindowsEncodingTests(unittest.TestCase):
    def test_cp949_rejects_character_but_utf8_pipe_handles_it(self):
        # This text is valid UTF-8, but CP949 cannot encode U+0080.
        sample = 'Codex conversation example \u0080 漢字 😀'
        with self.assertRaises(UnicodeEncodeError):
            sample.encode('cp949')
        code = 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'
        with patch.object(locale, 'getencoding', return_value='cp949'):
            got = subprocess.run(
                [sys.executable, '-c', code], input=sample, text=True,
                encoding='utf-8', errors='replace', capture_output=True, check=True)
        self.assertEqual(got.stdout, sample)

    def test_pipeline_passes_utf8_to_resolved_codex_and_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            con = cg.connect(home / 'knowledge.db')
            try:
                with con:
                    con.execute('INSERT INTO sessions(id,cwd,project,first_seen,last_seen) VALUES(?, ?, ?, ?, ?)',
                                (SID, 'C:/project', 'project', 'x', 'x'))
                    con.execute('INSERT INTO messages(id,session_id,source_path,offset_bytes,role,source_kind,content) VALUES(?,?,?,?,?,?,?)',
                                ('msg1', SID, 'source.jsonl', 100, 'user', 'response_item', 'Check Unicode \u0080 確認'))

                cm = home / 'conversation-map'
                skill = cm / 'plugins/conversation-map/skills/conversation-map'
                (skill / 'scripts').mkdir(parents=True)
                (skill / 'references').mkdir()
                (skill / 'scripts/conversation-map.mjs').write_text('// mock')
                (skill / 'references/analysis.schema.json').write_text('{}')
                codex_executable = str(home / 'Codex CLI' / 'codex.cmd')
                actions = []
                # A language-unspecified cached draft must not bypass the new
                # Korean request or cause source evidence to be translated.
                original = cg.transcript(con, SID)
                schema = Path(cg.__file__).with_name('analysis-output.schema.json')
                legacy_version = 'draft-v2|' + cg.digest(schema.read_text(encoding='utf-8'))
                legacy_key = cg.digest(legacy_version + cg.serialize_transcript(original))[:32]
                cache_dir = home / 'analysis-cache' / cg.digest(SID)[:24]
                cache_dir.mkdir(parents=True)
                legacy_cache = cache_dir / f'1-{legacy_key}.json'
                legacy_text = json.dumps({'nodes': [{'key': 'legacy', 'kind': 'topic',
                                                    'title': 'Old English topic', 'summary': ''}]})
                legacy_cache.write_text(legacy_text, encoding='utf-8')

                def fake_run(cmd, **kw):
                    self.assertEqual(kw.get('encoding'), 'utf-8', f'No explicit UTF-8 for: {cmd}')
                    self.assertEqual(kw.get('errors'), 'replace')
                    actions.append(cmd[0:3])
                    if cmd[1] == 'exec':
                        self.assertEqual(cmd[0], codex_executable)
                        self.assertIn('\u0080', kw['input'])
                        request = kw['input'].split('TRANSCRIPT PART:\n', 1)[0]
                        self.assertIn('Write all generated prose in Korean', request)
                        self.assertIn('evidence quotes', request)
                        self.assertIn('never translate or paraphrase them', request)
                        self.assertIn('enum values', request)
                        chunk = json.loads(kw['input'].split('TRANSCRIPT PART:\n', 1)[1])
                        self.assertEqual(chunk, original)
                        m = chunk['messages'][0]
                        draft = {'schemaVersion': '1', 'title': 'Project',
                                 'coverage': {'status': 'complete'}, 'nodes': [
                                    {'key': 'project', 'parent': None, 'kind': 'topic', 'title': 'Project', 'summary': ''},
                                    {'key': 'unicode-decision', 'parent': 'project', 'kind': 'item',
                                     'classification': 'decision', 'title': 'Handle Unicode', 'summary': 'UTF-8',
                                     'evidence': [{'message': m['id'], 'quote': m['content']}]}
                                 ], 'relations': [], 'notes': []}
                        Path(cmd[cmd.index('-o') + 1]).write_text(json.dumps(draft), encoding='utf-8')
                    elif cmd[0] == 'node' and cmd[2] == 'build':
                        folder = Path(cmd[cmd.index('--out-dir') + 1])
                        folder.mkdir()
                        (folder / 'map.json').write_text(json.dumps({'nodes': [
                            {'id': 'root', 'kind': 'root'},
                            {'id': 'unicode-decision', 'kind': 'item', 'title': 'Handle Unicode',
                             'summary': 'UTF-8', 'classification': 'decision', 'status': 'active'}
                        ], 'relations': []}), encoding='utf-8')
                    return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

                with patch.object(cg.shutil, 'which', side_effect={'codex': codex_executable, 'node': 'node'}.get), \
                     patch.object(cg.subprocess, 'run', side_effect=fake_run):
                    result = ap.auto_analyze(con, SID, cm, home)
                self.assertIn('analyzed & imported', result)
                self.assertEqual([x[0] for x in actions], [codex_executable, 'node', 'node'])
                self.assertEqual(con.execute('select count(*) from session_maps').fetchone()[0], 1)
                self.assertEqual(legacy_cache.read_text(encoding='utf-8'), legacy_text)
                fresh = [path for path in cache_dir.glob('*.json') if path != legacy_cache]
                self.assertEqual(len(fresh), 1)
                cached = json.loads(fresh[0].read_text(encoding='utf-8'))
                self.assertEqual(cached['nodes'][1]['evidence'][0]['quote'], original['messages'][0]['content'])
            finally:
                con.close()


if __name__ == '__main__':
    unittest.main()
