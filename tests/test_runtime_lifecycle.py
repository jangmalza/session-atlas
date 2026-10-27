import json, os, signal, socket, tempfile, time, unittest
from pathlib import Path
import service_runtime as runtime


class RuntimeLifecycleTests(unittest.TestCase):
    def test_managed_worker_starts_recovers_its_child_and_stops_cooperatively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            config = root / 'profile.json'
            config.write_text(
                json.dumps(
                    {
                        'version': 1,
                        'database': str(root / 'db.db'),
                        'codexHome': str(root / 'empty-codex'),
                        'interval': 1,
                        'services': [
                            {'name': 'collector', 'mode': 'watch', 'port': port, 'directory': str(root / 'live')}
                        ],
                    }
                )
            )
            runtime.start(config)
            try:
                deadline = time.monotonic() + 15
                first = None
                while time.monotonic() < deadline:
                    value = runtime.status(config)
                    item = value['services'][0]
                    if item['listening'] and item['process'] and item['process'].get('pid'):
                        first = item['process']['pid']
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(first)
                self.assertEqual(runtime.start(config)['status'], 'already-running')
                os.kill(first, signal.SIGTERM)
                recovered = False
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    item = runtime.status(config)['services'][0]
                    if item['listening'] and item['process'] and item['process'].get('pid') != first:
                        recovered = True
                        break
                    time.sleep(0.1)
                self.assertTrue(recovered, 'supervisor must restart its own failed child')
            finally:
                runtime.stop(config)
                time.sleep(0.2)
            self.assertFalse(runtime.connected(port))
