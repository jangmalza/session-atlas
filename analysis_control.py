"""Explicit, single-session jobs with private staging and atomic result adoption."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from analysis_credentials import WindowsCredentials
from topic_display import _locked_preferences

ACTIVE = {'queued', 'backing_up', 'analyzing', 'validating', 'building', 'classifying', 'saving'}
MODES = {'codex', 'hybrid', 'decisions'}


@contextmanager
def worker_lease(path):
    """Long-lived worker lock, independent of short settings/state locks."""
    with path.open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ControlError(RuntimeError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def read_json(path, default=None):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else (default or {})
    except (OSError, ValueError):
        return default or {}


def write_json(path, value):
    from live_dashboard import atomic_write
    atomic_write(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class AnalysisControl:
    def __init__(self, database, codex_home, *, credentials=None, launcher=None):
        self.database = Path(database).resolve()
        self.codex_home = Path(codex_home).resolve()
        self.root = self.database.parent / (self.database.stem + '.analysis-control')
        self.credentials = credentials or WindowsCredentials(self.database)
        self.launcher = launcher
        self._children = []

    def config(self):
        value = read_json(self.root / 'settings.json')
        if not value.get('cmRoot'):
            import context_graph as cg
            for path in (Path.home() / 'src/conversation-map', Path.home() / 'conversation-map',
                         Path(__file__).resolve().parent.parent / 'conversation-map'):
                try:
                    cg.find_conversation_map(path)
                    value['cmRoot'] = str(path.resolve())
                    break
                except ValueError:
                    pass
        return value

    def settings(self):
        import context_graph as cg
        config = self.config()
        try:
            cg.find_conversation_map(Path(config.get('cmRoot', '')))
            cm_ready = bool(config.get('cmRoot'))
        except ValueError:
            cm_ready = False
        stored = bool(self.credentials.read(presence_only=True))
        inherited = bool(os.environ.get('OPENAI_API_KEY', '').strip())
        auth = read_json(self.root / 'codex-status.json')
        return {'cmRoot': config.get('cmRoot', ''), 'conversationMapReady': cm_ready,
                'codexInstalled': shutil.which('codex') is not None,
                'nodeInstalled': shutil.which('node') is not None,
                'codexAuth': auth.get('status', 'unchecked'), 'codexCheckedAt': auth.get('at'),
                'keyRegistered': stored or inherited, 'keySource': 'windows' if stored else 'environment' if inherited else 'none',
                'keyStorageAvailable': self.credentials.supported, 'keyValidation': 'not_checked',
                'autoAnalysis': False}

    def edit_settings(self, arguments):
        if not isinstance(arguments, dict):
            raise ControlError('invalid_settings')
        action = arguments.get('action')
        self.root.mkdir(parents=True, exist_ok=True)
        with _locked_preferences(self.root / 'control'):
            if self.status().get('state') in ACTIVE:
                raise ControlError('job_busy', 409)
            if action == 'save_path' and set(arguments) == {'action', 'cmRoot'}:
                value = arguments['cmRoot']
                if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not Path(value).is_absolute():
                    raise ControlError('invalid_cm_root')
                import context_graph as cg
                try:
                    cg.find_conversation_map(Path(value))
                except ValueError:
                    raise ControlError('invalid_cm_root') from None
                write_json(self.root / 'settings.json', {'cmRoot': str(Path(value).resolve())})
            elif action == 'save_key' and set(arguments) == {'action', 'key'}:
                key = arguments['key']
                if not isinstance(key, str) or not 16 <= len(key) <= 2048 or not re.fullmatch(r'[!-~]+', key):
                    raise ControlError('invalid_key')
                self.credentials.write(key)
            elif action == 'delete_key' and set(arguments) == {'action'}:
                self.credentials.delete()
            else:
                raise ControlError('invalid_settings')
        return self.settings()

    def check_codex(self):
        executable = shutil.which('codex')
        status = 'missing'
        if executable:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            try:
                result = subprocess.run([executable, 'login', 'status'], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                                        env={**os.environ, 'CODEX_HOME': str(self.codex_home)}, **options)
                status = 'ready' if result.returncode == 0 else 'login_required'
            except (OSError, subprocess.TimeoutExpired):
                status = 'unavailable'
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.root / 'codex-status.json', {'status': status, 'at': time.time()})
        return self.settings()

    def _snapshot(self, session, database=None):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', session):
            raise ControlError('invalid_session')
        import context_graph as cg
        with closing(sqlite3.connect((database or self.database).as_uri() + '?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            con.execute('BEGIN')
            if not con.execute('SELECT 1 FROM sessions WHERE id=?', (session,)).fetchone():
                raise ControlError('session_missing', 404)
            trans = cg.transcript(con, session)
            saved = con.execute('SELECT * FROM session_maps WHERE session_id=?', (session,)).fetchone()
            overlay = con.execute('SELECT * FROM topic_assignments WHERE session_id=?', (session,)).fetchone()
        content = cg.serialize_transcript(trans)
        identity = {'transcript': cg.digest(content), 'map': dict(saved) if saved else None,
                    'overlay': dict(overlay) if overlay else None}
        token = cg.digest(json.dumps(identity, ensure_ascii=False, sort_keys=True))
        return {'revision': token, 'messageCount': len(trans['messages']), 'characterCount': len(content),
                'hasAnalysis': saved is not None, 'transcriptHash': identity['transcript']}

    def preview(self, session, mode):
        if mode not in MODES:
            raise ControlError('invalid_mode')
        value = self._snapshot(session)
        settings = self.settings()
        reason = None
        if not value['messageCount']:
            reason = 'no_messages'
        elif mode != 'decisions' and value['hasAnalysis']:
            reason = 'existing_analysis_preserved'
        elif mode == 'decisions' and not value['hasAnalysis']:
            reason = 'analysis_required'
        elif mode != 'decisions' and not (settings['codexInstalled'] and settings['nodeInstalled'] and settings['conversationMapReady']):
            reason = 'setup_required'
        elif mode != 'decisions' and settings['codexAuth'] != 'ready':
            reason = 'check_codex_required'
        elif mode != 'codex' and not settings['keyRegistered']:
            reason = 'key_required'
        return {**value, 'session': session, 'mode': mode, 'blockedReason': reason,
                'usesModel': mode != 'decisions', 'usesApi': mode != 'codex', 'autoAnalysis': False}

    def status(self):
        value = read_json(self.root / 'job.json', {'state': 'idle'})
        # A receipt in the same transaction as result adoption survives a crash
        # between the DB commit and the progress-file update.
        receipt = None
        if value.get('id'):
            try:
                with closing(sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=2)) as con:
                    if con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_job_results'").fetchone():
                        receipt = con.execute('SELECT completed_at FROM analysis_job_results WHERE job_id=?',(value['id'],)).fetchone()
            except (OSError,sqlite3.Error):
                pass
        if receipt:
            value = {**value, 'state': 'completed', 'finishedAt': receipt[0]}
            value.pop('error', None); value.pop('failedStage', None)
        elif value.get('state') in ACTIVE and not process_alive(value.get('pid')):
            value = {**value, 'state': 'interrupted', 'error': 'worker_stopped'}
        # Only structured metadata is exposed. No paths, provider output or secrets.
        return {key: value[key] for key in ('id', 'session', 'mode', 'state', 'chunk', 'chunks', 'error', 'failedStage', 'startedAt', 'finishedAt') if key in value}

    def submit(self, arguments, graph):
        required = {'session', 'mode', 'revision', 'allowModel', 'allowApi'}
        if not isinstance(arguments, dict) or set(arguments) != required or arguments.get('mode') not in MODES:
            raise ControlError('invalid_request')
        mode = arguments['mode']
        if arguments['allowModel'] is not (mode != 'decisions') or arguments['allowApi'] is not (mode != 'codex'):
            raise ControlError('consent_required', 403)
        self.root.mkdir(parents=True, exist_ok=True)
        with _locked_preferences(self.root / 'control'):
            if self.status().get('state') in ACTIVE:
                raise ControlError('job_busy', 409)
            preview = self.preview(arguments['session'], mode)
            if preview['revision'] != arguments['revision']:
                raise ControlError('source_changed', 409)
            if preview['blockedReason']:
                raise ControlError(preview['blockedReason'], 409)
            job_id = uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            write_json(directory / 'request.json', {**arguments, 'cmRoot': self.config().get('cmRoot', ''),
                       'codexHome': str(self.codex_home)})
            write_json(directory / 'graph.json', graph)
            job = {'id': job_id, 'session': arguments['session'], 'mode': mode, 'state': 'queued',
                   'pid': os.getpid(), 'startedAt': time.time()}
            write_json(self.root / 'job.json', job)
            try:
                if self.launcher:
                    process = self.launcher(self, job_id)
                else:
                    options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
                    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--database', str(self.database), '--job', job_id],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               env={**os.environ, 'PYTHONUTF8': '1'}, **options)
                    self._children = [p for p in self._children if p.poll() is None] + [process]
                    # Reap POSIX exits even when no browser is polling this viewer.
                    threading.Thread(target=process.wait, daemon=True).start()
                job['pid'] = process.pid
                write_json(self.root / 'job.json', job)
            except Exception:
                write_json(self.root / 'job.json', {**job, 'state': 'failed', 'error': 'worker_start_failed'})
                raise ControlError('worker_start_failed', 503) from None
        return self.status()

    def update(self, job_id, state, **fields):
        with _locked_preferences(self.root / 'control'):
            current = read_json(self.root / 'job.json')
            if current.get('id') != job_id:
                raise ControlError('job_replaced', 409)
            write_json(self.root / 'job.json', {**current, 'state': state, **fields})

    def execute(self, job_id, *, analyzer=None, classifier=None):
        import context_graph as cg
        from backup_store import backup, restore
        from topic_overlays import store_overlay
        directory = self.root / job_id
        request = read_json(directory / 'request.json')
        session, mode = request['session'], request['mode']
        stage = 'backing_up'
        owns_job = False
        def progress(state, **fields):
            nonlocal stage
            stage = state
            self.update(job_id, state, **fields)
        try:
            # A second worker cannot execute the same job, even after viewer restarts.
            with worker_lease(directory / 'execution.lock') as acquired:
                if not acquired:
                    return
                current = read_json(self.root / 'job.json')
                if current.get('id') != job_id or current.get('state') != 'queued':
                    return
                owns_job = True
                progress('backing_up')
                backup(self.database, directory / 'before.zip', directory / 'graph.json')
                staged = directory / 'work.db'
                restore(directory / 'before.zip', staged)
                if self._snapshot(session, staged)['revision'] != request['revision']:
                    raise ControlError('source_changed', 409)
                with closing(cg.connect(staged)) as con:
                    if mode != 'decisions':
                        progress('analyzing')
                        # The saved Decisions key is never injected into Codex's environment.
                        (analyzer or cg.auto_analyze)(con, session, Path(request['cmRoot']), self.database.parent,
                                                     progress=progress)
                    if mode != 'codex':
                        progress('classifying')
                        if classifier:
                            classifier(con, session, self.database.parent)
                        else:
                            key = self.credentials.read() or os.environ.get('OPENAI_API_KEY', '').strip()
                            if not key:
                                raise ControlError('key_required')
                            os.environ['OPENAI_API_KEY'] = key
                            cg.assign_topics(con, session, self.database.parent, allow_api=True)
                    saved = con.execute('SELECT * FROM session_maps WHERE session_id=?', (session,)).fetchone()
                    analysis = con.execute('SELECT * FROM analyses WHERE session_id=?', (session,)).fetchone()
                    overlay = con.execute('SELECT * FROM topic_assignments WHERE session_id=?', (session,)).fetchone()
                    if saved is None:
                        raise ControlError('result_missing')
                    map_data = json.loads(saved['map_json'])
                    cg.validate_map_minimal(map_data)
                progress('saving')
                # Never replace the live DB with the staging copy. Append-only source
                # collection and unrelated sessions remain untouched.
                with closing(cg.connect(self.database)) as live, live:
                    live.execute('BEGIN IMMEDIATE')
                    if self._snapshot(session)['revision'] != request['revision']:
                        raise ControlError('source_changed', 409)
                    if mode != 'decisions':
                        if live.execute('SELECT 1 FROM session_maps WHERE session_id=?', (session,)).fetchone():
                            raise ControlError('existing_analysis_preserved', 409)
                        cg._save_map(live, session, map_data, saved['transcript_sha256'])
                        if analysis:
                            live.execute('INSERT INTO analyses VALUES (?,?,?,?,?)', tuple(analysis))
                    if overlay and mode != 'codex':
                        store_overlay(live, session, map_data, json.loads(overlay['assignment_json']), cg.now())
                    live.execute('CREATE TABLE IF NOT EXISTS analysis_job_results (job_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, completed_at REAL NOT NULL)')
                    live.execute('INSERT INTO analysis_job_results VALUES (?,?,?)',(job_id,session,time.time()))
                self.update(job_id, 'completed', finishedAt=time.time())
        except Exception as error:
            code = error.code if isinstance(error, ControlError) else 'stage_failed'
            if owns_job:
                self.update(job_id, 'failed', error=code, failedStage=stage, finishedAt=time.time())
        finally:
            # Backup remains private; disposable staging copies are no longer needed.
            if owns_job:
                for name in ('work.db', 'work.db-wal', 'work.db-shm', 'work.topic-display.json', 'work.graph.json', 'graph.json'):
                    (directory / name).unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}', args.job):
        return 2
    controller = AnalysisControl(args.database, Path.home() / '.codex')
    request = read_json(controller.root / args.job / 'request.json')
    os.environ['CODEX_HOME'] = request['codexHome']
    controller.execute(args.job)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
