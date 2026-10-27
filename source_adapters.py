"""Local Claude Code adapter; no SDK, CLI, credentials or network calls."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sqlite3

_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def claude_message(record):
    """Keep visible text verbatim; tool/thinking blocks and API errors are excluded."""
    if (
        not isinstance(record, dict)
        or record.get('type') not in ('user', 'assistant')
        or record.get('isApiErrorMessage')
    ):
        return None
    message = record.get('message')
    if not isinstance(message, dict) or message.get('role') != record['type']:
        return None
    content = message.get('content')
    if isinstance(content, list):
        content = '\n'.join(
            block['text']
            for block in content
            if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)
        )
    if not isinstance(content, str) or not content.strip():
        return None
    return record['type'], content


def scan_claude_file(con, path: Path, mappings=None):
    import context_graph as cg

    path = path.resolve()
    key = str(path)
    stat = path.stat()
    previous = con.execute('SELECT offset_bytes FROM sources WHERE path=?', (key,)).fetchone()
    signature = con.execute('SELECT * FROM source_fingerprints WHERE path=?', (key,)).fetchone()
    if (
        previous
        and signature
        and signature['format'] == 'claude-jsonl'
        and signature['size_bytes'] == stat.st_size
        and signature['mtime_ns'] == stat.st_mtime_ns
    ):
        owner = con.execute('SELECT session_id FROM sources WHERE path=?', (key,)).fetchone()[0]
        return {'path': key, 'session_id': owner, 'added_messages': 0, 'offset': previous[0]}
    offset = previous[0] if previous and previous[0] <= stat.st_size else 0
    rows = []
    identifiers = set()
    last = offset
    cwd = ''
    branch = ''
    title = ''
    # Recheck the file identity independently of its checkpoint. A copied file
    # must never be assigned to a different native session by filename alone.
    with path.open('rb') as stream:
        while line := stream.readline():
            start = stream.tell() - len(line)
            if not line.endswith(b'\n'):
                break
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                if start >= offset:
                    last = stream.tell()
                continue
            if not isinstance(record, dict):
                continue
            sid = record.get('sessionId') or record.get('session_id')
            if isinstance(sid, str) and _UUID.fullmatch(sid):
                identifiers.add(sid)
            if isinstance(record.get('cwd'), str):
                cwd = record['cwd'] or cwd
            if isinstance(record.get('gitBranch'), str):
                branch = record['gitBranch'] or branch
            if record.get('type') in ('custom-title', 'ai-title'):
                value = record.get('customTitle') or record.get('aiTitle') or record.get('title')
                if isinstance(value, str):
                    title = value
            if start < offset:
                continue
            last = stream.tell()
            message = claude_message(record)
            if message:
                rows.append((start, record, message))
    if not identifiers and not rows:
        return {'path': key, 'session_id': None, 'added_messages': 0, 'offset': 0}
    if len(identifiers) != 1:
        raise ValueError('Claude session identity is unavailable or ambiguous')
    native = next(iter(identifiers))
    helper = 'subagents' in path.parts or path.stem.startswith('agent-')
    sid = 'claude-' + native if not helper else 'claude-agent-' + _hash(native + '|' + path.stem)[:32]
    parent = 'claude-' + native if helper else None
    when = cg.now()
    added = 0
    with con:
        existing = con.execute('SELECT provider,native_id FROM session_providers WHERE session_id=?', (sid,)).fetchone()
        if con.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone() and (
            not existing or existing['provider'] != 'claude' or existing['native_id'] != native
        ):
            raise ValueError('Session provider identity conflict')
        con.execute(
            '''INSERT INTO sessions(id,cwd,project,branch,first_seen,last_seen) VALUES (?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET cwd=excluded.cwd,project=excluded.project,branch=excluded.branch,last_seen=excluded.last_seen''',
            (
                sid,
                cwd,
                cg.name_project(cwd, mappings),
                branch,
                str(rows[0][1].get('timestamp') or when) if rows else when,
                when,
            ),
        )
        con.execute(
            'INSERT OR REPLACE INTO session_providers VALUES (?,?,?,?,?,?)',
            (sid, 'claude', native, 'subagent' if helper else 'primary', parent, title),
        )
        for position, record, (role, content) in rows:
            native_id = record.get('uuid')
            # Same UUID + exact text survives path copies; differing payloads
            # remain separate audit records instead of overwriting old content.
            mid = _hash(
                f'claude|{sid}|{native_id if isinstance(native_id, str) and native_id else position}|{record.get("parentUuid") or ""}|{role}|{content}'
            )
            is_context = bool(
                record.get('isMeta') or record.get('isCompactSummary') or record.get('isSidechain') and not helper
            )
            if helper and isinstance(native_id, str):
                inherited = con.execute(
                    'SELECT 1 FROM native_message_ids n JOIN messages m ON m.id=n.message_id WHERE m.session_id=? AND n.native_id=? AND m.role=? AND m.content=?',
                    (parent, native_id, role, content),
                ).fetchone()
                is_context = is_context or bool(inherited)
            added += con.execute(
                'INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)',
                (
                    mid,
                    sid,
                    key,
                    position,
                    role,
                    'claude_context' if is_context else 'claude_message',
                    content,
                    str(record.get('timestamp') or ''),
                ),
            ).rowcount
            con.execute(
                'INSERT OR IGNORE INTO native_message_ids VALUES (?,?,?,?)',
                (
                    mid,
                    native_id if isinstance(native_id, str) else '',
                    record.get('parentUuid') if isinstance(record.get('parentUuid'), str) else None,
                    int(is_context),
                ),
            )
        con.execute(
            'INSERT INTO sources VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET offset_bytes=excluded.offset_bytes,session_id=excluded.session_id,last_scan=excluded.last_scan',
            (key, last, sid, when),
        )
        con.execute(
            'INSERT OR REPLACE INTO source_fingerprints VALUES (?,?,?,?)',
            (key, stat.st_size, stat.st_mtime_ns, 'claude-jsonl'),
        )
    return {'path': key, 'session_id': sid, 'added_messages': added, 'offset': last}


def scan_claude(con: sqlite3.Connection, home: Path, mappings=None, *, on_error=None):
    results = []
    for path in sorted((Path(home) / 'projects').rglob('*.jsonl'), key=lambda p: ('subagents' in p.parts, str(p))):
        try:
            results.append(scan_claude_file(con, path, mappings))
        except (OSError, ValueError, sqlite3.Error) as error:
            if on_error:
                on_error(type(error).__name__)
            else:
                import sys

                print('Claude collection skipped a file (' + type(error).__name__ + ')', file=sys.stderr)
    return results
