"""Read-only session/message views joined to existing, source-checked analyses."""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

import context_graph as cg
from topic_overlays import project_structure, read_overlays
from topic_display import display_revision, map_fingerprint, project_topic_display, read_topic_display, read_session_names, session_display_state, item_label_metadata
from ui_ko import CLASSIFICATION_KO, RELATION_KO, STATUS_KO
from session_metadata import open_rollout


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


_TITLE_LIMIT = 80
_CONTEXT_BLOCK = re.compile(
    r"^[ \t]*<(environment_context|in-app-browser-context|developer_instructions|"
    r"permissions_instructions|sandbox_instructions|external_codex_apps_open_page)\b[^>]*>.*?</\1\s*>", re.I | re.S | re.M)
_AGENTS_BLOCK = re.compile(
    r"^\s*#{1,6}\s+AGENTS\.md instructions for[^\n]*\n\s*"
    r"<INSTRUCTIONS\b[^>]*>.*?</INSTRUCTIONS\s*>", re.I | re.S)


def _request_title(content: str, limit: int = _TITLE_LIMIT) -> str:
    """Bounded display excerpt, never a replacement for the collected message.

    Only known injected context wrappers are removed. Ordinary HTML stays
    literal text and must be rendered with textContent by the browser.
    """
    text = content.lstrip("\ufeff")
    text = _AGENTS_BLOCK.sub("", text)
    text = _CONTEXT_BLOCK.sub("", text)
    # Incomplete known bootstrap blocks are not useful session names either.
    if re.match(r"\s*(?:#{1,6}\s+AGENTS\.md instructions for\b|"
                r"<(?:environment_context|in-app-browser-context|developer_instructions|"
                r"permissions_instructions|sandbox_instructions|external_codex_apps_open_page)\b)", text, re.I):
        return ""
    text = re.sub(r"(?im)^\s*#{1,6}\s+My request\s*:\s*$", "", text)
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+", "", text)
    text = " ".join(text.split()).strip()
    return text if len(text) <= limit else text[:limit-1].rstrip() + "…"


def _verified_own_request(row: dict, sid: str, metadata: dict) -> bool:
    """Require the collected row to still match the exact post-fork JSONL record."""
    source = row["source_path"]
    boundaries = metadata.get("ownStartOffsets", {})
    expected_id = cg.digest(f'{sid}|{row["offset_bytes"]}|{row["source_kind"]}|{row["role"]}|{row["content"]}')
    if expected_id != row["id"]:
        return False
    try:
        source_available = Path(source).is_file()
    except OSError:
        source_available = False
    # Codex can move a rollout to its archive without updating collected paths.
    # Only metadata-verified copies are eligible; no text search or relocation
    # is allowed, and the immutable source path/ID in SQLite remains unchanged.
    paths = [source] if source in boundaries and source_available else list(boundaries)
    for path in paths:
        boundary = boundaries.get(path)
        if type(boundary) is not int or row["offset_bytes"] < boundary:
            continue
        try:
            with open_rollout(Path(path)) as stream:
                stream.seek(row["offset_bytes"])
                line = stream.readline()
            if not line.endswith(b"\n"):
                continue
            event = json.loads(line)
            message = cg.message_from_line(event) if isinstance(event, dict) else None
            expected = (row["role"], row["content"], row["source_kind"])
            if message == expected and str(event.get("timestamp") or "") == row["at"]:
                return True
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return False


def _request_previews(rows: list[dict], sid: str, metadata: dict | None) -> dict:
    """Live-only source excerpts; forks must use verified post-boundary requests."""
    own_only = metadata is not None and metadata.get('provider')!='claude' and (metadata.get("forked") is not False
                                         or metadata.get("sessionKind") != "primary")

    def find(indices):
        for index in indices:
            row = rows[index]
            if row["role"] != "user" or row['source_kind']=='claude_context':
                continue
            text = _request_title(row["content"], limit=180)
            if not text or (own_only and not _verified_own_request(row, sid, metadata)):
                continue
            return {"text": text, "messageId": row["id"], "index": index, "timestamp": row["at"]}
        return None

    first = find(range(len(rows)))
    latest = find(range(len(rows)-1, first["index"]-1, -1)) if first else None
    return {"first": first, "latest": latest,
            "scope": "unconfirmed" if own_only and not first else "own" if own_only else "session"}


def _session_title(session, rows: list[dict], analysis: dict, metadata: dict | None = None) -> dict:
    """Prefer saved analysis; a fork title must come from a verified own request."""
    sid = session["id"]
    candidates = [analysis.get("title"), *(
        node.get("title") for node in analysis.get("nodes", []) if node.get("kind") == "root")]
    for title in candidates:
        if (isinstance(title, str) and title.strip()
                and title.strip().casefold() not in {"codex session", f"codex session {sid}".casefold()}):
            return {"title": title, "titleSource": "analysis", "titleMessageId": None}
    needs_boundary = metadata is not None and metadata.get('provider')!='claude' and (metadata.get("forked") is not False
                                               or metadata.get("sessionKind") != "primary")
    native = metadata.get("nativeTitle") if metadata else None
    if not needs_boundary and isinstance(native, str) and metadata.get("nativeTitleUsable"):
        if title := _request_title(native):
            return {"title": title, "titleSource": "native", "titleMessageId": None}
    for row in rows:
        if row["role"] != "user" or row['source_kind']=='claude_context' or (needs_boundary and not _verified_own_request(row, sid, metadata)):
            continue
        if title := _request_title(row["content"]):
            return {"title": title, "titleSource": "own_request" if needs_boundary else "first_user",
                    "titleMessageId": row["id"]}
    if needs_boundary:
        if isinstance(native, str) and metadata.get("nativeTitleUsable") and (title := _request_title(native)):
            return {"title": title, "titleSource": "native", "titleMessageId": None}
        label = "고유 요청 확인 불가" if rows else "대화 기록 없음"
        agent_path = metadata.get("agentPath")
        task_name = agent_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if isinstance(agent_path, str) else ""
        detail = task_name or metadata.get("agentRole")
        if isinstance(detail, str) and detail.strip():
            suffix = " · " + label
            detail = _request_title(detail)
            budget = _TITLE_LIMIT - len(suffix)
            if len(detail) > budget:
                detail = detail[:budget-1].rstrip() + "…"
            if detail:
                label = detail + suffix
        return {"title": label, "titleSource": "fallback", "titleMessageId": None}
    return {"title": "제목을 확인할 요청 없음" if rows else "대화 기록 없음",
            "titleSource": "fallback", "titleMessageId": None}


def _offline_header(header: dict) -> dict:
    """Offline ordering metadata must not gain excerpts from original messages."""
    result = dict(header)
    result.pop("requestPreviews", None)
    if result.get("titleSource") in {"first_user", "own_request", "native"}:
        result.update(title=f'{header["project"]} · {header["id"][:8]}',
                      titleSource="fallback", titleMessageId=None)
    return result


def _structure(analysis: dict) -> list[dict]:
    """Copy only stored topic hierarchy and item membership, without interpreting it."""
    return project_structure(analysis)[0]


def _transcript(rows: list[dict], sid: str) -> dict:
    return {"title": f"Codex session {sid}", "messages": [
        {"id": f"m{i+1}", "role": row["role"], "content": row["content"], "timestamp": row["at"]}
        for i, row in enumerate(rows)]}


def _baseline(rows: list[dict], sid: str, stored: dict, analysis: dict) -> int | None:
    expected = stored.get("transcript_sha256")
    if not expected:
        return None
    if cg.digest(cg.serialize_transcript(_transcript(rows, sid))) == expected:
        return len(rows)
    count = analysis.get("source", {}).get("messageCount")
    if type(count) is int and 0 <= count <= len(rows):
        if cg.digest(cg.serialize_transcript(_transcript(rows[:count], sid))) == expected:
            return count
    return None


def _anchors(rows: list[dict], analysis: dict, baseline: int | None) -> tuple[dict, dict, dict]:
    """Recheck full message identity and literal quotes; never infer new links.

    Analysis m1/m2 IDs are positional. An inserted earlier message must not
    silently attach a saved interpretation to the new occupant of that position.
    A unique complete message match can relocate its display anchor; otherwise
    the analysis remains visible in the unplaced section.
    """
    sources, repeated = {}, set()
    for message in analysis.get("messages", []):
        if not isinstance(message, dict) or not isinstance(message.get("id"), str):
            continue
        if message["id"] in sources:
            repeated.add(message["id"])
        sources[message["id"]] = message
    candidates = {}
    for index, row in enumerate(rows):
        candidates.setdefault((row["role"], row["content"]), []).append(index)
    by_message, by_node, assistant_evidence = {}, {}, {}
    for node in analysis.get("nodes", []):
        if node.get("kind") != "item":
            continue
        found = []
        answer_groups = {}
        for evidence in node.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("verified") is not True:
                continue
            quote, identifier = evidence.get("quote"), evidence.get("messageId")
            if not isinstance(quote, str) or not quote or not isinstance(identifier, str) or identifier in repeated:
                continue
            source = sources.get(identifier)
            index = None
            if source is not None:
                source_index = source.get("index")
                if type(source_index) is not int or source_index < 0:
                    continue
                if "index" in evidence and (type(evidence["index"]) is not int or evidence["index"] != source_index):
                    continue
                if "speaker" in evidence and evidence["speaker"] != source.get("speaker"):
                    continue
                if not isinstance(source.get("speaker"), str) or not isinstance(source.get("text"), str):
                    continue
                possible = candidates.get((source["speaker"], source["text"]), [])
                if source.get("timestamp"):
                    possible = [i for i in possible if rows[i]["at"] == source["timestamp"]]
                if len(possible) == 1:
                    index = possible[0]
                elif baseline is not None and source_index < baseline and source_index in possible:
                    index = source_index
            elif baseline is not None:
                match = re.fullmatch(r"m([1-9][0-9]*)", identifier)
                if match and int(match[1]) <= baseline:
                    index = int(match[1]) - 1
                    if "index" in evidence and (type(evidence["index"]) is not int or evidence["index"] != index):
                        continue
            if index is None or quote not in rows[index]["content"]:
                continue
            if "speaker" in evidence and evidence["speaker"] != rows[index]["role"]:
                continue
            anchor = {"messageId": rows[index]["id"], "index": index, "sourceMessageId": identifier}
            if anchor not in found:
                found.append(anchor)
            targets = by_message.setdefault(rows[index]["id"], [])
            if node["id"] not in targets:
                targets.append(node["id"])
            # Build display evidence at the same per-quote validation boundary.
            # A valid quote must never lend its anchor to another invalid quote
            # in the node, and neighboring messages are never inferred answers.
            if (node.get("classification") == "answered_question" and node.get("status") == "answered"
                    and rows[index]["role"] == "assistant"):
                group = answer_groups.setdefault(rows[index]["id"], {
                    **anchor, "sourceMessageIds": [], "quotes": [], "evidenceCount": 0})
                if identifier not in group["sourceMessageIds"]:
                    group["sourceMessageIds"].append(identifier)
                if quote not in group["quotes"]:
                    group["quotes"].append(quote)
                    group["evidenceCount"] += 1
        by_node[node["id"]] = sorted(found, key=lambda a: a["index"])
        if answer_groups:
            assistant_evidence[node["id"]] = sorted(answer_groups.values(), key=lambda group: group["index"])
    return by_message, by_node, assistant_evidence


def _offline_assistant_evidence(groups: dict) -> dict:
    """Keep source navigation/counts in offline HTML without copying any quotes."""
    return {node_id: [{key: value for key, value in group.items() if key != "quotes"} for group in values]
            for node_id, values in groups.items()}


class TimelineReader:
    """Use separate read-only SQLite snapshots for HTTP threads; no model calls."""
    def __init__(self, database: Path, codex_home: Path | None = None):
        self.database = database.resolve()
        self._lock = threading.Lock()
        self._cache = {}
        # Explicit opt-in keeps existing standalone/read-only imports independent
        # of another machine's Codex files. Live viewers pass their configured home.
        from session_metadata import SessionMetadataReader
        self._metadata = SessionMetadataReader(codex_home) if codex_home is not None else None

    def topic_assignment_revision(self) -> str:
        """Refresh sidecar projections, including stale results, without messages."""
        with self._lock, closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            overlays = read_overlays(con)
            display = read_topic_display(self.database)
            session_names = read_session_names(self.database)
            if not overlays and not display and not session_names:
                return ""
            source_maps = {sid: value for sid, value in con.execute("SELECT session_id,map_json FROM session_maps")
                           if sid in overlays or sid in display}
            return _hash([overlays, display, source_maps, session_names])

    def _entries(self, con: sqlite3.Connection, only: set[str] | None = None) -> list[dict]:
        if only is not None and not only:return []
        params=tuple(sorted(only)) if only is not None else ()
        where=' WHERE s.id IN ('+','.join('?' for _ in params)+')' if only is not None else ''
        stored = {row["session_id"]: dict(row) for row in con.execute("SELECT * FROM session_maps") if only is None or row['session_id'] in only}
        has_providers=con.execute("SELECT 1 FROM sqlite_master WHERE name='session_providers'").fetchone()
        providers={r['session_id']:dict(r) for r in con.execute('SELECT * FROM session_providers')} if has_providers else {}
        overlays = read_overlays(con)
        display_preferences = read_topic_display(self.database)
        session_names = read_session_names(self.database)
        sessions = con.execute("""SELECT s.id,s.project,s.branch,s.first_seen,
            count(m.id) AS raw_count,max(m.rowid) AS last_row
            FROM sessions s LEFT JOIN messages m ON m.session_id=s.id"""+where+
            " GROUP BY s.id ORDER BY s.id",params).fetchall()
        metadata = {}
        preview_metadata = {}
        if self._metadata is not None:
            paths = {}
            for sid, path in con.execute("SELECT session_id,source_path FROM messages GROUP BY session_id,source_path "
                                         "UNION SELECT session_id,path FROM sources"):
                paths.setdefault(sid, []).append(path)
            metadata = self._metadata.read([session["id"] for session in sessions if session['id'] not in providers], source_paths=paths)
            missing_primary = [sid for sid, value in metadata.items()
                               if value.get("sessionKind") == "primary" and not value.get("boundaryKnown")]
            if missing_primary:
                # Recover previews only. Existing titles, grouping and native
                # metadata keep their original projection and display settings.
                preview_metadata = self._metadata.read(missing_primary, source_paths=paths, include_compressed=True)
        entries = []
        retained = set()
        for session in sessions:
            sid = session["id"]
            retained.add(sid)
            old = self._cache.get(sid, {})
            raw_key = (session["raw_count"], session["last_row"])
            rows = old["rows"] if old.get("raw_key") == raw_key else cg.effective_messages(con, sid)
            user_content = old["user_content"] if old.get("raw_key") == raw_key and "user_content" in old else [
                row["role"] == "user" and row['source_kind']!='claude_context' and bool(_request_title(row["content"])) for row in rows]
            saved = stored.get(sid, {})
            lineage = metadata.get(sid, {}) if self._metadata is not None else None
            provider=providers.get(sid)
            if provider:
                lineage={'sessionKind':provider['session_kind'],'parentSessionId':provider['parent_session_id'],
                         'groupSessionId':provider['parent_session_id'] or sid,'rootSessionId':provider['parent_session_id'] or sid,
                         'lineageStatus':'known','forked':False,'boundaryKnown':True,
                         'nativeTitle':provider['title'],'nativeTitleUsable':bool(provider['title']), 'provider':'claude'}
            metadata_revision = lineage.get("revision", _hash(lineage)) if lineage is not None else ""
            preview_lineage = preview_metadata.get(sid, lineage)
            preview_metadata_revision = preview_lineage.get("revision", _hash(preview_lineage)) if preview_lineage is not None else ""
            map_json = saved.get("map_json", "")
            analysis = old["analysis"] if old.get("map_json") == map_json else json.loads(map_json) if map_json else {}
            overlay = overlays.get(sid)
            display_preference = display_preferences.get(sid)
            session_name = session_names.get(sid)
            session_display = session_display_state(session_name)
            message_key = [row["id"] for row in rows]
            revision = _hash([message_key, map_json, saved.get("transcript_sha256"), saved.get("imported_at"), overlay,
                              metadata_revision, display_preference, session_name,
                              *([preview_metadata_revision] if sid in preview_metadata else [])])
            if old.get("revision") == revision:
                baseline = old["baseline"]
                by_message, by_node = old["by_message"], old["by_node"]
                assistant_evidence = old["assistant_evidence"]
            else:
                baseline = _baseline(rows, sid, saved, analysis) if saved else 0
                by_message, by_node, assistant_evidence = _anchors(rows, analysis, baseline)
            if (old.get("map_json") == map_json and old.get("overlay") == overlay
                    and old.get("display_preference") == display_preference and "structure" in old):
                structure, assignment = old["structure"], old["topic_assignment"]
                topic_display = old["topic_display"]
                item_labels, evidence_titles = old['item_labels'], old['evidence_titles']
            else:
                structure, assignment = project_structure(analysis, overlay)
                structure, topic_display = project_topic_display(analysis, structure, display_preference)
                topic_display = {**topic_display, 'sourceMapSha256':map_fingerprint(analysis) if saved else '',
                                 'revision':display_revision(display_preference)}
                item_labels, evidence_titles = item_label_metadata(analysis, display_preference, topic_display)
            if (old.get("raw_key") == raw_key and old.get("map_json") == map_json
                    and old.get("metadata_revision") == metadata_revision and "display_title" in old):
                display_title = old["display_title"]
            else:
                display_title = _session_title(session, rows, analysis, lineage)
            if (old.get("raw_key") == raw_key and old.get("preview_metadata_revision") == preview_metadata_revision
                    and "request_previews" in old):
                request_previews = old["request_previews"]
            else:
                request_previews = _request_previews(rows, sid, preview_lineage)
            items = [n for n in analysis.get("nodes", []) if n.get("kind") == "item"]
            header = {"id": sid, "project": session["project"], "branch": session["branch"],
                      "provider":provider['provider'] if provider else 'codex',
                      "nativeSessionId":provider['native_id'] if provider else sid,
                      **display_title, "requestPreviews": request_previews,
                      **({'displayTitle':session_name} if session_display['status']=='active' else {}),
                      'sessionDisplay':session_display,
                      "sessionKind": (lineage or {}).get("sessionKind", "unknown"),
                      "parentSessionId": (lineage or {}).get("parentSessionId"),
                      "rootSessionId": (lineage or {}).get("rootSessionId"),
                      "groupSessionId": (lineage or {}).get("groupSessionId", sid),
                      "lineageStatus": (lineage or {}).get("lineageStatus", "unknown"),
                      "forked": (lineage or {}).get("forked"),
                      "ownRequestBoundaryKnown": bool((lineage or {}).get("boundaryKnown")),
                      "agentRole": (lineage or {}).get("agentRole", ""),
                      "agentPath": (lineage or {}).get("agentPath", ""),
                      "firstMessageAt": rows[0]["at"] if rows else None,
                      "messageCount": len(rows), "lastMessageAt": rows[-1]["at"] if rows else session["first_seen"],
                      "hasAnalysis": bool(saved), "analysisItemCount": len(items),
                      "topicAssignment": assignment,
                      "topicDisplay": topic_display,
                      "importedAt": saved.get("imported_at", ""), "revision": revision,
                      "pendingMessageCount": len(rows) - baseline if baseline is not None else None}
            entry = {"header": header, "rows": rows, "analysis": analysis, "map_json": map_json,
                     "display_title": display_title, "request_previews": request_previews,
                     "preview_metadata_revision": preview_metadata_revision,
                     "metadata_revision": metadata_revision,
                     "structure": structure, "topic_assignment": assignment, "overlay": overlay,
                     "display_preference": display_preference, "topic_display": topic_display,
                     "item_labels":item_labels, "evidence_titles":evidence_titles,
                     "raw_key": raw_key, "revision": revision, "baseline": baseline, "user_content": user_content,
                     "by_message": by_message, "by_node": by_node, "assistant_evidence": assistant_evidence}
            self._cache[sid] = entry
            entries.append(entry)
        if only is None:self._cache = {sid: entry for sid, entry in self._cache.items() if sid in retained}
        return sorted(entries, key=lambda e: (bool(e["rows"]), e["header"]["lastMessageAt"], e["header"]["id"]), reverse=True)

    def read(self, session: str | None = None, offset: int | None = None, limit: int = 100,
             message: str | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200 or (offset is not None and (type(offset) is not int or offset < 0)):
            raise ValueError("Invalid timeline page")
        with self._lock, closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            entries = self._entries(con,{session} if session is not None else None)
            if session is None:
                headers = [entry["header"] for entry in entries]
                return {"sessions": headers, "revision": _hash(headers), "autoAnalysis": False}
            entry = next((entry for entry in entries if entry["header"]["id"] == session), None)
            if entry is None:
                raise KeyError("Unknown timeline session")
            rows, analysis, baseline = entry["rows"], entry["analysis"], entry["baseline"]
            native={r['message_id']:dict(r) for r in con.execute('SELECT n.* FROM native_message_ids n JOIN messages m ON m.id=n.message_id WHERE m.session_id=?',(session,))} if con.execute("SELECT 1 FROM sqlite_master WHERE name='native_message_ids'").fetchone() else {}
            start = max(0, len(rows) - limit) if offset is None else min(offset, max(0, len(rows) - 1))
            if message is not None:
                target = next((i for i, row in enumerate(rows) if row["id"] == message), None)
                if target is None:
                    raise KeyError("Unknown timeline message")
                start = min(max(0, target - limit // 2), max(0, len(rows) - limit))
            messages = []
            for index in range(start, min(len(rows), start + limit)):
                row = rows[index]
                references = entry["by_message"].get(row["id"], [])
                state = "linked" if references else "analysed" if baseline is not None and index < baseline else "pending" if baseline is not None else "unconfirmed"
                messages.append({"id": row["id"], "index": index, "transcriptId": f"m{index+1}",
                                 **({'nativeMessageId':native[row['id']]['native_id'],'parentNativeId':native[row['id']]['parent_native_id']} if row['id'] in native else {}),
                                 "role": row["role"], "timestamp": row["at"], "content": row["content"],
                                 "hasUserContent": entry["user_content"][index],
                                 "analysisNodeIds": references, "analysisState": state})
            nodes = [node for node in analysis.get("nodes", []) if node.get("kind") == "item"]
            return {"session": entry["header"], "revision": entry["revision"], "offset": start, "limit": limit,
                    "total": len(rows), "messages": messages, "autoAnalysis": False,
                    "analysis": {"nodes": nodes, "relations": analysis.get("relations", []), "structure": entry["structure"],
                                 "topicAssignment": entry["topic_assignment"],
                                 "assistantEvidence": entry["assistant_evidence"],
                                 "itemLabels":entry['item_labels'], "evidenceTitles":entry['evidence_titles'],
                                 "anchors": entry["by_node"], "baselineMessageCount": baseline,
                                 "unplacedIds": [node["id"] for node in nodes if not entry["by_node"].get(node["id"])]}}

    def question_context(self, sessions: set[str], *, live: bool = False) -> dict:
        """Export anchors and stored analysis titles, without source-derived excerpts."""
        with self._lock, closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            return {entry["header"]["id"]: {
                "session": entry['header'] if live else _offline_header(entry["header"]),
                "revision":entry['revision'],
                "messages": [{"id": row["id"], "index": index, "role": row["role"], "timestamp": row["at"],
                              "hasUserContent": entry["user_content"][index]}
                             for index, row in enumerate(entry["rows"])],
                "analysis": {"anchors": entry["by_node"],
                             "itemLabels":entry['item_labels'], "evidenceTitles":entry['evidence_titles'],
                             "assistantEvidence": entry['assistant_evidence'] if live else _offline_assistant_evidence(entry["assistant_evidence"]),
                             "structure": entry["structure"], "topicAssignment": entry["topic_assignment"], "relations": [
                    {key: relation[key] for key in ("id", "from", "to", "type") if key in relation}
                    for relation in entry["analysis"].get("relations", [])]}}
                for entry in self._entries(con,sessions)}

    def turns(self, session: str) -> dict:
        """Chronological user/assistant groups, explicitly NOT semantic answers."""
        with self._lock,closing(sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=5)) as con:
            con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
            entries=self._entries(con,{session})
            if not entries:raise KeyError(session)
            entry=entries[0];turns=[];current=None
            for index,row in enumerate(entry['rows']):
                item={'messageId':row['id'],'index':index,'timestamp':row['at'],
                      'preview':_request_title(row['content'],limit=180)}
                if row['role']=='user' and entry['user_content'][index]:
                    current={'request':item,'responses':[]};turns.append(current)
                elif row['role']=='assistant' and row['source_kind']!='claude_context' and current is not None:
                    current['responses'].append(item)
            return {'session':entry['header'],'revision':entry['revision'],'kind':'chronological','turns':turns,'autoAnalysis':False}


    def search(self, query: str, *, project: str = "", role: str = "", session: str = "",
               offset: int = 0, limit: int = 30) -> dict:
        """Literal, case-insensitive search over deduplicated original messages.

        No FTS tables or writes: short Korean queries and punctuation work as
        literal text too. Results identify immutable collected message IDs.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("Invalid search text")
        if role not in ("", "user", "assistant") or not isinstance(project, str) or not isinstance(session, str):
            raise ValueError("Invalid search filter")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid search page")
        expression = re.compile(re.escape(query.strip()), re.IGNORECASE)
        hits, total = [], 0
        with self._lock, closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            # Select matching sessions before loading their full metadata/maps.
            # Python's literal regex retains existing Unicode search semantics.
            con.create_function('literal_match',1,lambda content:int(bool(expression.search(content or ''))))
            clauses=['literal_match(m.content)'];parameters=[]
            for column,value in [('s.project',project),('m.role',role),('m.session_id',session)]:
                if value:clauses.append(column+'=?');parameters.append(value)
            matching={row[0] for row in con.execute('SELECT DISTINCT m.session_id FROM messages m JOIN sessions s ON s.id=m.session_id WHERE '+' AND '.join(clauses),parameters)}
            for entry in self._entries(con,matching):
                header = entry["header"]
                if (project and header["project"] != project) or (session and header["id"] != session):
                    continue
                for index, row in enumerate(entry["rows"]):
                    if role and row["role"] != role:
                        continue
                    match = expression.search(row["content"])
                    if not match:
                        continue
                    if offset <= total < offset + limit:
                        start, end = max(0, match.start()-90), min(len(row["content"]), match.end()+110)
                        hits.append({"id":row["id"], "sessionId":header["id"], "sessionTitle":header.get("displayTitle") or header["title"],
                                     "project":header["project"], "role":row["role"], "timestamp":row["at"], "index":index,
                                     "snippet":row["content"][start:end], "snippetStart":start,
                                     "matchStart":match.start()-start, "matchEnd":match.end()-start,
                                     "analysisNodeIds":list(entry['by_message'].get(row['id'], []))})
                    total += 1
        return {"results":hits, "total":total, "offset":offset, "limit":limit, "autoAnalysis":False}


def timeline_html() -> bytes:
    script = (Path(__file__).with_name("ui_theme.js").read_text(encoding="utf-8") + "\n" + Path(__file__).with_name("session_timeline.js").read_text(encoding="utf-8")).strip()
    style = "\n".join(Path(__file__).with_name(name).read_text(encoding="utf-8") for name in ("session_timeline.css", "ui_theme.css", "timeline_design.css")).strip()
    hashes = [base64.b64encode(hashlib.sha256(asset.encode()).digest()).decode() for asset in (script, style)]
    policy = (f"default-src 'none'; script-src 'sha256-{hashes[0]}'; style-src 'sha256-{hashes[1]}'; "
              "connect-src 'self'; base-uri 'none'; form-action 'none'")
    labels = json.dumps({"classification": CLASSIFICATION_KO, "status": STATUS_KO, "relation": RELATION_KO}, ensure_ascii=False)
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}"><meta name="referrer" content="no-referrer">
<title>세션 · 메시지 타임라인</title><style>{style}</style></head><body>
<header><div><span class="eyebrow">SOURCE / 원문 기록</span><h1>대화를 이어 읽기</h1><p>원문을 검색하고, 앞뒤 맥락과 저장된 분석을 함께 확인하세요.</p></div>
<div class="reading-controls"><button id="toggle-sessions" type="button" aria-expanded="true">세션 목록 숨기기</button>
<label>글자 크기 <select id="reading-size" aria-label="글자 크기"><option value="normal">보통</option><option value="large">크게</option><option value="largest">아주 크게</option></select></label>
<span id="timeline-state" role="status">불러오는 중</span></div></header>
<section class="fulltext-search" aria-label="전체 원문 검색"><form id="fulltext-form">
<label for="fulltext-query">전체 원문 검색</label><input id="fulltext-query" type="search" maxlength="200" required placeholder="기억나는 단어나 문장을 입력하세요" autocomplete="off">
<select id="fulltext-scope" aria-label="검색 범위"><option value="all">모든 세션</option><option value="session">현재 세션</option></select>
<select id="fulltext-role" aria-label="검색할 화자"><option value="">모든 화자</option><option value="user">사용자</option><option value="assistant">어시스턴트</option></select>
<button type="submit">원문 검색</button><button id="reopen-search" type="button" hidden>검색 결과 다시 보기</button></form></section>
<dialog id="search-dialog" aria-labelledby="search-title"><div class="search-dialog-head"><h2 id="search-title">원문 검색 결과</h2><button id="close-search" type="button">검색 닫기</button></div>
<p id="search-summary" role="status"></p><p class="muted">메시지를 선택하면 해당 위치와 앞뒤 대화를 함께 엽니다. 분석 여부와 관계없이 원문 전체를 검색합니다.</p>
<div id="search-results"></div><div class="search-pages"><button id="search-previous" type="button">이전 검색 결과</button><button id="search-next" type="button">다음 검색 결과</button></div></dialog>
<div class="timeline-layout"><aside class="sessions" aria-label="세션 목록"><h2>수집한 세션</h2>
<input id="session-search" type="search" aria-label="세션 찾기" placeholder="제목·첫 요청·프로젝트 검색">
<select id="session-kind" aria-label="대화 종류"><option value="primary">일반 대화</option><option value="subagent">보조 작업</option><option value="unknown">분류 미확정</option></select>
<label id="session-parent-filter" hidden>상위 대화<select id="session-parent" aria-label="보조 작업의 상위 대화"></select></label>
<select id="session-project" aria-label="타임라인 프로젝트 필터"><option value="">모든 프로젝트</option></select>
<label class="check"><input id="analysed-only" type="checkbox">분석 결과 있는 세션</label>
<p id="session-count" class="muted"></p><div id="session-list"></div></aside>
<main class="conversation"><section class="conversation-head"><h2 id="conversation-title">세션을 선택하세요</h2><p id="conversation-meta" class="muted"></p>
<div class="view-switch" role="group" aria-label="대화 보기 방식"><button id="view-conversation" type="button" aria-pressed="true">대화 흐름</button><button id="view-analysis" type="button" aria-pressed="false">정리된 내용</button></div>
<div class="paging" id="message-paging"><button id="older" type="button">이전 100개</button><button id="newer" type="button">다음 100개</button><button id="latest" type="button">최신 메시지</button><label class="check"><input id="follow-latest" type="checkbox" checked>새 메시지 따라가기</label></div>
<div id="analysis-filters" role="group" aria-label="정리된 내용 분류" hidden></div>
<p id="timeline-summary" class="muted"></p><button id="unplaced" type="button" hidden></button></section>
<div id="message-list" tabindex="0" aria-label="시간순 메시지 목록"><p class="empty">대화 기록을 준비하고 있습니다.</p></div>
<div id="analysis-overview" tabindex="0" aria-label="정리된 내용 목록" hidden></div></main>
<aside id="analysis-panel" class="analysis-detail" aria-label="분석 내용과 근거" hidden><div class="analysis-panel-head"><h2>분석 내용과 근거</h2><button id="close-analysis" type="button" aria-label="상세 닫기">닫기</button></div><div id="analysis-detail"></div></aside></div>
<script type="application/json" id="timeline-labels">{labels}</script><script>{script}</script></body></html>'''.encode('utf-8')
