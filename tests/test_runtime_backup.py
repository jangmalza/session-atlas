from datetime import datetime, timedelta, timezone
from contextlib import closing
import json, tempfile, unittest, sqlite3, zipfile
from pathlib import Path
from unittest.mock import patch
import context_graph as cg
from runtime_status import collector_status
import backup_store as backup
import service_runtime as runtime


class RuntimeBackupTests(unittest.TestCase):
    def test_collector_heartbeat_expires_independently_of_viewer_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            con = cg.connect(root / 'db.db')
            try:
                self.assertEqual(collector_status(con)['state'], 'unknown')
                cg.watch_tick(con, root / 'empty')
                self.assertEqual(collector_status(con)['state'], 'running')
                self.assertEqual(
                    collector_status(con, datetime.now(timezone.utc) + timedelta(minutes=5))['state'], 'stopped'
                )
            finally:
                con.close()

    def test_backup_restore_preserves_rows_settings_and_projection_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'original.db'
            con = cg.connect(db)
            con.execute("INSERT INTO sessions(id,first_seen,last_seen) VALUES ('test','','')")
            con.commit()
            expected = [tuple(r) for r in con.execute('SELECT * FROM sessions')]
            con.close()
            settings = b'{"schemaVersion":"topic-display-1","sessions":{},"sessionNames":{"test":"Alias"}}'
            db.with_suffix('.topic-display.json').write_bytes(settings)
            graph = root / 'graph.json'
            graph.write_text(
                json.dumps({'schemaVersion': 'mvp-1', 'nodes': [], 'edges': [], 'projects': [], 'sessionCount': 0})
            )
            archive = root / 'backup.zip'
            backup.backup(db, archive, graph)
            target = root / 'restored.db'
            backup.restore(archive, target)
            with closing(sqlite3.connect(target)) as restored:
                self.assertEqual([tuple(r) for r in restored.execute('SELECT * FROM sessions')], expected)
            self.assertEqual(target.with_suffix('.topic-display.json').read_bytes(), settings)
            self.assertEqual(json.loads(target.with_suffix('.graph.json').read_text()), json.loads(graph.read_text()))
            with self.assertRaises(FileExistsError):
                backup.restore(archive, db)
            with self.assertRaises(FileExistsError):
                backup.backup(db, archive)
            self.assertEqual(db.with_suffix('.topic-display.json').read_bytes(), settings)

    def test_restore_rejects_unsafe_entries_before_writing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('../outside', 'bad')
            with self.assertRaises(ValueError):
                backup.restore(archive, root / 'new.db')
            self.assertFalse((root / 'new.db').exists())

    def test_runtime_validates_ports_and_never_constructs_model_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'profile.json'
            service = {'name': 'collector', 'mode': 'watch', 'port': 50000, 'directory': str(root / 'live')}
            value = {
                'version': 1,
                'database': str(root / 'db.db'),
                'codexHome': str(root / 'codex'),
                'services': [service],
            }
            path.write_text(json.dumps(value))
            config = runtime.load_config(path)
            args = runtime.arguments(config, service)
            self.assertNotIn('analyze', args)
            self.assertNotIn('--allow-model', args)
            self.assertNotIn('--allow-api', args)
            value['services'].append({**service, 'name': 'duplicate'})
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                runtime.load_config(path)
