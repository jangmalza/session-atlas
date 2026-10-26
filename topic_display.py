"""Local session/topic display preferences; never edit collected source data."""
from __future__ import annotations

import json
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time

from decisions_topics import map_fingerprint


class TopicDisplayConflict(ValueError):
    """A reviewed source or preference changed before an explicit edit."""


_write_lock = threading.Lock()
_NO_NAME_CHANGE = object()


def _display_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(char)<32 or 127<=ord(char)<=159 or char in '\u2028\u2029' for char in value):
        raise ValueError('Invalid display name')
    value = value.strip()
    if not 1 <= len(value) <= 120:
        raise ValueError('Invalid display name')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('Invalid display name') from None
    return value


def display_revision(preference: object) -> str:
    return hashlib.sha256(json.dumps(preference, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


@contextmanager
def _locked_preferences(path: Path):
    """Serialize read/merge/replace across threads and separate viewer processes."""
    if not _write_lock.acquire(timeout=5):
        raise TimeoutError('Display settings busy')
    try:
        with path.with_suffix(path.suffix+'.lock').open('a+b') as lock:
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b'0'); lock.flush()
            deadline = time.monotonic()+5
            while True:
                try:
                    lock.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Display settings busy') from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        _write_lock.release()


def _read_preferences(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schemaVersion':'topic-display-1','sessions':{}}
    except (ValueError, UnicodeError):
        raise TopicDisplayConflict('Display settings need review') from None
    if not isinstance(data, dict) or data.get('schemaVersion') != 'topic-display-1' or not isinstance(data.get('sessions'), dict):
        raise TopicDisplayConflict('Display settings need review')
    return data


def _write_preferences(path: Path, settings: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.'+path.name+'-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_topic_display(database: Path) -> dict:
    """A missing or malformed sidecar leaves every original topic visible."""
    try:
        return _read_preferences(database.with_suffix('.topic-display.json'))['sessions']
    except (OSError, ValueError):
        return {}


def read_session_names(database: Path) -> dict:
    try:
        names = _read_preferences(database.with_suffix('.topic-display.json')).get('sessionNames', {})
        return names if isinstance(names, dict) else {}
    except (OSError, ValueError):
        return {}


def session_display_state(name: object) -> dict:
    status = 'default' if name is None else 'active'
    try:
        if _display_name(name) != name:
            status = 'invalid'
    except ValueError:
        status = 'invalid'
    return {'status':status, 'revision':display_revision(name)}


def set_session_display(database: Path, *, session: str, display_name: object, expected_revision: str) -> dict:
    """An explicit session label works before analysis and survives new messages."""
    if (not isinstance(session, str) or not 1 <= len(session) <= 128
            or not isinstance(expected_revision, str) or not re.fullmatch('[a-f0-9]{64}', expected_revision)):
        raise ValueError('Invalid session display edit')
    name = _display_name(display_name)
    path = database.resolve().with_suffix('.topic-display.json')
    with _locked_preferences(path), closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as con:
        con.execute('PRAGMA query_only=ON'); con.execute('BEGIN')
        if con.execute('SELECT 1 FROM sessions WHERE id=?', (session,)).fetchone() is None:
            raise KeyError('Session not found')
        settings = _read_preferences(path)
        names = settings.get('sessionNames', {})
        if not isinstance(names, dict):
            raise TopicDisplayConflict('Display settings need review')
        previous = names.get(session)
        if display_revision(previous) != expected_revision:
            raise TopicDisplayConflict('Session name changed')
        if session_display_state(previous)['status'] == 'invalid':
            raise TopicDisplayConflict('Display settings need review')
        if name is None:
            names.pop(session, None)
        else:
            names[session] = name
        if names:
            settings['sessionNames'] = names
        else:
            settings.pop('sessionNames', None)
        _write_preferences(path, settings)
        return {'saved':True, 'session':session, 'displayName':name, 'sessionDisplay':session_display_state(name)}


def project_topic_display(analysis: dict, structure: list[dict], preference: object) -> tuple[list[dict], dict]:
    """Apply reviewed topic IDs only to the exact source map they came from."""
    summary = {'status': 'default', 'auxiliaryCount': 0}
    if preference is None:
        return structure, summary
    if not isinstance(preference, dict):
        return structure, {**summary, 'status': 'invalid'}
    if preference.get('sourceMapSha256') != map_fingerprint(analysis):
        return structure, {**summary, 'status': 'stale'}
    identifiers = preference.get('auxiliaryTopicIds')
    primary = preference.get('primaryTopicIds', [])
    names = preference.get('displayNames', {})
    item_titles = preference.get('itemTitles', {})
    evidence_titles = preference.get('evidenceTitles', {})
    topics = [node['id'] for node in structure if node.get('kind') == 'topic']
    items = {node['id']:node for node in analysis.get('nodes', []) if node.get('kind') == 'item'}
    if (not isinstance(identifiers, list) or not all(isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers) or len(set(topics)) != len(topics)
            or not set(identifiers).issubset(topics) or not isinstance(primary, list)
            or not all(isinstance(item, str) for item in primary) or len(set(primary)) != len(primary)
            or not set(primary).issubset(topics) or set(primary).intersection(identifiers)):
        return structure, {**summary, 'status': 'invalid'}
    try:
        if (not isinstance(names, dict) or not set(names).issubset(topics)
                or any(not isinstance(value, str) or _display_name(value) != value for value in names.values())):
            raise ValueError
        if (not isinstance(item_titles, dict) or not set(item_titles).issubset(items)
                or any(not isinstance(value, str) or _display_name(value) != value for value in item_titles.values())
                or not isinstance(evidence_titles, dict) or not set(evidence_titles).issubset(items)):
            raise ValueError
        for key, values in evidence_titles.items():
            if (items[key].get('classification') != 'answered_question' or items[key].get('status') != 'answered'
                    or not isinstance(values, dict) or any(not isinstance(mid, str) or not 1 <= len(mid) <= 128 for mid in values)
                    or any(not isinstance(value, str) or _display_name(value) != value for value in values.values())):
                raise ValueError
    except ValueError:
        return structure, {**summary, 'status': 'invalid'}
    auxiliary = set(identifiers)
    primary = set(primary)
    result = [{**node, **({'displayGroup': 'auxiliary' if node['id'] in auxiliary else 'primary'}
                         if node.get('kind') == 'topic' and node['id'] in auxiliary | primary else {}),
                         **({'displayTitle':names[node['id']]} if node.get('kind') == 'topic' and node['id'] in names else {})}
              for node in structure]
    return result, {'status': 'active', 'auxiliaryCount': len(auxiliary)}


def item_label_metadata(analysis: dict, preference: dict | None, state: dict) -> tuple[dict, dict]:
    """Separate original analysis titles from optional presentation labels."""
    active = preference if state['status'] == 'active' else {}
    overrides = active.get('itemTitles', {})
    labels = {node['id']:{'title':node.get('title') if isinstance(node.get('title'), str) else '',
                         **({'displayTitle':overrides[node['id']]} if node['id'] in overrides else {})}
              for node in analysis.get('nodes', []) if node.get('kind') == 'item'}
    evidence = {key:dict(values) for key, values in active.get('evidenceTitles', {}).items()}
    return labels, evidence


def set_topic_display(database: Path, *, session: str, topic: str, group: str | None = None,
                      display_name: object = _NO_NAME_CHANGE, source_map_sha256: str, expected_revision: str) -> dict:
    return _set_analysis_display(database,session=session,identifier=topic,kind='topic',group=group,
        display_name=display_name,source_map_sha256=source_map_sha256,expected_revision=expected_revision)


def set_item_display(database: Path, *, session: str, item: str, display_title: object,
                     source_map_sha256: str, expected_revision: str, evidence_message: str | None = None) -> dict:
    return _set_analysis_display(database,session=session,identifier=item,kind='item',display_name=display_title,
        evidence_message=evidence_message,source_map_sha256=source_map_sha256,expected_revision=expected_revision)


def _set_analysis_display(database: Path, *, session: str, identifier: str, kind: str,
                          group: str | None = None, display_name: object = _NO_NAME_CHANGE,
                          source_map_sha256: str, expected_revision: str, evidence_message: str | None = None) -> dict:
    """Write only the local sidecar for one existing analysis entry."""
    if (not isinstance(session, str) or not 1 <= len(session) <= 128
            or not isinstance(identifier, str) or not 1 <= len(identifier) <= 256
            or (evidence_message is not None and (kind != 'item' or not isinstance(evidence_message, str) or not 1 <= len(evidence_message) <= 128))
            or (group is not None and (group not in ('primary', 'auxiliary') or display_name is not _NO_NAME_CHANGE))
            or (group is None and display_name is _NO_NAME_CHANGE)
            or not isinstance(source_map_sha256, str) or not re.fullmatch('[a-f0-9]{64}', source_map_sha256)
            or not isinstance(expected_revision, str) or not re.fullmatch('[a-f0-9]{64}', expected_revision)):
        raise ValueError('Invalid display edit')
    name_edit = display_name is not _NO_NAME_CHANGE
    normalized_name = _display_name(display_name) if name_edit else None
    path = database.resolve().with_suffix('.topic-display.json')
    with _locked_preferences(path), closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON'); con.execute('BEGIN')
        row = con.execute('SELECT * FROM session_maps WHERE session_id=?', (session,)).fetchone()
        if row is None:
            raise KeyError('Saved analysis not found')
        analysis = json.loads(row['map_json'])
        if (not isinstance(analysis, dict) or not isinstance(analysis.get('nodes'), list)
                or not all(isinstance(node, dict) and isinstance(node.get('id'), str) for node in analysis['nodes'])
                or len({node['id'] for node in analysis['nodes']}) != len(analysis['nodes'])):
            raise ValueError('Invalid saved topic structure')
        current_source = map_fingerprint(analysis)
        if current_source != source_map_sha256:
            raise TopicDisplayConflict('Analysis changed')
        structure = analysis.get('nodes', [])
        node = next((node for node in structure if node.get('kind') == kind and node.get('id') == identifier), None)
        if node is None:
            raise KeyError('Saved entry not found')
        original_title = node.get('title', '')
        if evidence_message is not None:
            if node.get('classification') != 'answered_question' or node.get('status') != 'answered':
                raise KeyError('Verified answer evidence not found')
            # Reuse the same per-quote validation as the answer cards. No
            # neighboring message, role guess or new graph relation is allowed.
            import context_graph as cg
            from session_timeline import _anchors, _baseline
            rows = cg.effective_messages(con, session)
            evidence = _anchors(rows, analysis, _baseline(rows, session, dict(row), analysis))[2]
            anchor = next((value for value in evidence.get(identifier, []) if value['messageId'] == evidence_message), None)
            if anchor is None:
                raise KeyError('Verified answer evidence not found')
            original_title = f"어시스턴트 원문 #{anchor['index']+1}"
        settings = _read_preferences(path)
        previous = settings['sessions'].get(session)
        if display_revision(previous) != expected_revision:
            raise TopicDisplayConflict('Display settings changed')
        _, state = project_topic_display(analysis, structure, previous)
        if state['status'] == 'invalid':
            raise TopicDisplayConflict('Display settings need review')
        # Outdated preferences are not applied to the new analysis. An explicit
        # edit reviewed against its current hash starts that map's preferences.
        active = previous if state['status'] == 'active' else {}
        auxiliary = list(active.get('auxiliaryTopicIds', []))
        primary = list(active.get('primaryTopicIds', []))
        names = dict(active.get('displayNames', {}))
        item_titles = dict(active.get('itemTitles', {}))
        evidence_titles = {key:dict(values) for key, values in active.get('evidenceTitles', {}).items()}
        target_names = names if kind == 'topic' else item_titles
        target_key = identifier
        if evidence_message is not None:
            target_names = evidence_titles.setdefault(identifier, {})
            target_key = evidence_message
        if name_edit:
            if normalized_name is None or normalized_name == original_title:
                target_names.pop(target_key, None)
            else:
                target_names[target_key] = normalized_name
        else:
            auxiliary = [key for key in auxiliary if key != identifier]
            primary = [key for key in primary if key != identifier]
            (auxiliary if group == 'auxiliary' else primary).append(identifier)
        evidence_titles = {key:values for key, values in evidence_titles.items() if values}
        preference = {'sourceMapSha256':current_source, 'auxiliaryTopicIds':auxiliary}
        if primary:
            preference['primaryTopicIds'] = primary
        if names:
            preference['displayNames'] = names
        if item_titles:
            preference['itemTitles'] = item_titles
        if evidence_titles:
            preference['evidenceTitles'] = evidence_titles
        _, state = project_topic_display(analysis, structure, preference)
        if state['status'] != 'active':
            raise ValueError('Invalid topic structure')
        settings['sessions'][session] = preference
        _write_preferences(path, settings)
        labels, answer_labels = item_label_metadata(analysis, preference, state)
        return {'saved':True, 'session':session, 'topic':identifier if kind=='topic' else None, 'group':group,
                'item':identifier if kind=='item' else None, 'evidenceMessage':evidence_message,
                'displayTitle':target_names.get(target_key), 'originalTitle':original_title,
                'operation':'name' if name_edit else 'group', 'displayName':names.get(identifier),
                'auxiliaryTopicIds':auxiliary, 'primaryTopicIds':primary, 'displayNames':names,
                'itemLabels':labels, 'evidenceTitles':answer_labels,
                'topicDisplay':{**state, 'sourceMapSha256':current_source, 'revision':display_revision(preference)}}
