#!/usr/bin/env python3
"""Local, read-only Codex session collector and cross-session Conversation Map graph.

Python 3.10+ standard library. Model calls require an explicit
`analyze --allow-model` request. The collector/watch path never calls a model.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from ui_ko import CLASSIFICATION_KO, RELATION_KO, STATUS_KO

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  cwd TEXT NOT NULL DEFAULT '',
  project TEXT NOT NULL DEFAULT 'unknown',
  branch TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sources (
  path TEXT PRIMARY KEY,
  offset_bytes INTEGER NOT NULL DEFAULT 0,
  session_id TEXT,
  last_scan TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  source_path TEXT NOT NULL,
  offset_bytes INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  source_kind TEXT NOT NULL,
  content TEXT NOT NULL,
  at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_session_offset ON messages(session_id, offset_bytes);
CREATE TABLE IF NOT EXISTS session_maps (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id),
  transcript_sha256 TEXT NOT NULL DEFAULT '',
  map_json TEXT NOT NULL,
  imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id),
  transcript_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  at TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS topic_assignments (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id),
  source_map_sha256 TEXT NOT NULL,
  assignment_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_fingerprints (
  path TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  format TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_observations (
  message_id TEXT NOT NULL REFERENCES messages(id) ON UPDATE CASCADE ON DELETE CASCADE,
  source_path TEXT NOT NULL,
  PRIMARY KEY(message_id,source_path)
);
CREATE TABLE IF NOT EXISTS session_providers (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id),
  provider TEXT NOT NULL,
  native_id TEXT NOT NULL,
  session_kind TEXT NOT NULL,
  parent_session_id TEXT,
  title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS native_message_ids (
  message_id TEXT PRIMARY KEY REFERENCES messages(id) ON UPDATE CASCADE ON DELETE CASCADE,
  native_id TEXT NOT NULL,
  parent_native_id TEXT,
  is_context INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS collector_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def codex_home() -> Path:
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser()
    return Path.home() / ".codex"


def connect(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_file))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def name_project(cwd: str, mappings: dict[str, str] | None = None) -> str:
    if not cwd:
        return "unknown"
    norm = cwd.replace("\\", "/").rstrip("/")
    if mappings:
        for prefix, project in sorted(mappings.items(), key=lambda kv: len(kv[0]), reverse=True):
            p = prefix.replace("\\", "/").rstrip("/")
            if norm.casefold() == p.casefold() or norm.casefold().startswith(p.casefold() + "/"):
                return project
    # Git worktrees may need explicit --project-map to unify under one project.
    return norm.rsplit("/", 1)[-1] or "unknown"


def extract_text(contents: object) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        texts = []
        for c in contents:
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"):
                s = c.get("text")
                if isinstance(s, str):
                    texts.append(s)
        return "\n".join(texts)
    return ""


def message_from_line(row: dict) -> tuple[str, str, str] | None:
    t = row.get("type")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    if t == "response_item" and payload.get("type") == "message":
        role = payload.get("role")
        # Never collect hidden analysis/reasoning or instructions.
        if role not in ("assistant", "user"):
            return None
        if role == "assistant" and (payload.get("phase") == "analysis" or payload.get('channel')=='analysis'):
            return None
        content = extract_text(payload.get("content"))
        if content.strip():
            return role, content, "response_item"
    if t == "event_msg":
        role = {"user_message": "user", "agent_message": "assistant"}.get(payload.get("type"))
        content = payload.get("message")
        if role and isinstance(content, str) and content.strip():
            return role, content, "event_msg"
    return None


def discover(home: Path) -> list[Path]:
    files = []
    for directory in [home / "sessions", home / "archived_sessions"]:
        if directory.exists():
            files.extend(directory.rglob("rollout-*.jsonl"))
            files.extend(directory.rglob("rollout-*.jsonl.zst"))
    return sorted(set(files))


def fallback_session_id(path: Path) -> str:
    # A rollout filename normally ends in the thread UUID.
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl(?:\.zst)?$", path.name, re.I)
    return match.group(1) if match else digest(path.name)[:24]


def scan_file(con: sqlite3.Connection, path: Path, mappings: dict[str, str] | None = None) -> dict:
    path = path.resolve()
    key = str(path)
    stat = path.stat()
    stat_size = stat.st_size
    existing = con.execute("SELECT offset_bytes, session_id FROM sources WHERE path=?", (key,)).fetchone()
    offset = int(existing["offset_bytes"]) if existing else 0
    packed = path.suffix == '.zst'
    signature = con.execute('SELECT * FROM source_fingerprints WHERE path=?',(key,)).fetchone() if packed else None
    if packed and signature and signature['size_bytes']==stat_size and signature['mtime_ns']==stat.st_mtime_ns and existing:
        return {'path':key,'session_id':existing['session_id'],'added_messages':0,'offset':offset}
    if packed or stat_size < offset:  # Packed sizes are not uncompressed byte offsets.
        offset = 0
    initial = existing["session_id"] if existing and existing["session_id"] else fallback_session_id(path)
    processed = 0
    last_offset = offset
    parsed = []
    from session_metadata import open_rollout
    with open_rollout(path) as f:
        f.seek(offset)
        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                # Codex may still be appending this JSON object. Retry next scan.
                break
            last_offset = f.tell()
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # malformed complete line, skip safely
            # Large tool outputs and reasoning records never enter SQLite.
            # Do not retain their complete payloads while tailing long files.
            if isinstance(event, dict) and (event.get("type") == "session_meta" or message_from_line(event) is not None):
                parsed.append((line_start, event))

    sid = initial
    cwd = ""
    branch = ""
    first_time = ""
    for _, event in parsed:
        if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
            meta = event["payload"]
            sid = str(meta.get("id") or meta.get("session_id") or sid)
            cwd = str(meta.get("cwd") or "")
            first_time = str(meta.get("timestamp") or event.get("timestamp") or "")
            if isinstance(meta.get("git"), dict):
                branch = str(meta["git"].get("branch") or "")
            break

    # Archived files can be moved without preserving the prior sources key.
    stored = con.execute("SELECT cwd, branch, project FROM sessions WHERE id=?", (sid,)).fetchone()
    if not cwd and stored:
        cwd = stored["cwd"]
    if not branch and stored:
        branch = stored["branch"]
    project = name_project(cwd, mappings) if cwd else (stored["project"] if stored else "unknown")
    when = now()
    with con:
        con.execute("""INSERT INTO sessions (id,cwd,project,branch,first_seen,last_seen)
                     VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                     cwd=excluded.cwd, project=excluded.project, branch=excluded.branch,
                     last_seen=excluded.last_seen""", (sid, cwd, project, branch, first_time or when, when))
        for position, event in parsed:
            message = message_from_line(event)
            if not message:
                continue
            role, content, kind = message
            mid = digest(f"{sid}|{position}|{kind}|{role}|{content}")
            result = con.execute("""INSERT OR IGNORE INTO messages
                 (id,session_id,source_path,offset_bytes,role,source_kind,content,at)
                 VALUES (?,?,?,?,?,?,?,?)""",
                 (mid, sid, key, position, role, kind, content, str(event.get("timestamp") or "")))
            processed += result.rowcount
            con.execute('INSERT OR IGNORE INTO message_observations VALUES (?,?)',(mid,key))
        con.execute("""INSERT INTO sources (path,offset_bytes,session_id,last_scan)
                     VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                     offset_bytes=excluded.offset_bytes,session_id=excluded.session_id,last_scan=excluded.last_scan""",
                    (key, last_offset, sid, when))
        if packed:
            con.execute('INSERT OR REPLACE INTO source_fingerprints VALUES (?,?,?,?)',
                        (key,stat_size,stat.st_mtime_ns,'jsonl.zst'))
    return {"path": key, "session_id": sid, "added_messages": processed, "offset": last_offset}


def scan(con: sqlite3.Connection, home: Path, mappings: dict[str, str] | None = None, *, on_error=None) -> list[dict]:
    results = []
    for path in discover(home):
        try:
            results.append(scan_file(con, path, mappings))
        except (OSError, sqlite3.Error) as exc:
            if on_error is not None:
                on_error(type(exc).__name__)
            else:
                print(f"warning: {path}: {exc}", file=sys.stderr)
    return results


def _same_turn_message_pair(left: dict, right: dict) -> bool:
    """Recognize adjacent, contemporaneous event/response mirrors conservatively."""
    if ({left["source_kind"], right["source_kind"]} != {"response_item", "event_msg"}
            or left["role"] != right["role"] or left["content"] != right["content"]
            or left["offset_bytes"] >= right["offset_bytes"]):
        return False
    try:
        first = datetime.fromisoformat(left["at"].replace("Z", "+00:00"))
        second = datetime.fromisoformat(right["at"].replace("Z", "+00:00"))
        if first.utcoffset() is None or second.utcoffset() is None:
            return False
        elapsed = (second - first).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return False
    # Mirrors are emitted together. An uncertain match must retain both rows;
    # equal text alone is not evidence that a later request is a duplicate.
    return 0 <= elapsed <= 1


def effective_messages(con: sqlite3.Connection, sid: str) -> list[dict]:
    rows = con.execute("""SELECT * FROM messages WHERE session_id=?
                          ORDER BY at, offset_bytes, id""", (sid,)).fetchall()
    all_messages = [dict(r) for r in rows]
    # Match only adjacent messages in the same source, in file order. Pairing
    # is one-to-one: if either row has two possible partners, retain all of them.
    # This keeps independent repetitions and ambiguous/missing timestamps visible.
    by_source = {}
    for r in all_messages:
        by_source.setdefault(r["source_path"], []).append(r)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='message_observations'").fetchone():
        by_id={r['id']:r for r in all_messages}
        for mid,path in con.execute('SELECT o.message_id,o.source_path FROM message_observations o JOIN messages m ON m.id=o.message_id WHERE m.session_id=?',(sid,)):
            if mid in by_id and path!=by_id[mid]['source_path']:
                by_source.setdefault(path,[]).append(by_id[mid])
    partners = {}
    for source_rows in by_source.values():
        source_rows.sort(key=lambda r: (r["offset_bytes"], r["id"]))
        for left, right in zip(source_rows, source_rows[1:]):
            if _same_turn_message_pair(left, right):
                partners.setdefault(left["id"], set()).add(right["id"])
                partners.setdefault(right["id"], set()).add(left["id"])
    mirrored_events = {r["id"] for r in all_messages if r["source_kind"] == "event_msg"
                       and len(partners.get(r["id"], [])) == 1
                       and len(partners[next(iter(partners[r["id"]]))]) == 1}
    # Archived and live copies can each contain the same transcript events.
    seen = set()
    final = []
    for r in all_messages:
        if r["id"] in mirrored_events:
            continue
        key = (r["offset_bytes"], r["role"], r["content"], r["source_kind"])
        if key in seen:
            continue
        seen.add(key)
        final.append(r)
    return final


def transcript(con: sqlite3.Connection, sid: str) -> dict:
    s = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise ValueError(f"unknown session: {sid}")
    messages = effective_messages(con, sid)
    return {"title": f"Codex session {sid}", "messages": [
        {"id": f"m{i+1}", "role": r["role"], "content": r["content"], "timestamp": r["at"]}
        for i, r in enumerate(messages)
    ]}


def validate_map_minimal(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("map.json must contain a 'nodes' array")
    if not isinstance(data.get("relations", []), list):
        raise ValueError("map.json 'relations' must be an array")
    ids = set()
    for n in data["nodes"]:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            raise ValueError("all map nodes must have string IDs")
        if n["id"] in ids:
            raise ValueError("duplicate map node ID")
        ids.add(n["id"])
    for edge in data.get("relations", []):
        if not isinstance(edge, dict) or edge.get("from") not in ids or edge.get("to") not in ids:
            raise ValueError("map relation contains unknown node IDs")


def import_map(con: sqlite3.Connection, sid: str, map_file: Path, transcript_hash: str = "") -> None:
    if not con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
        raise ValueError(f"unknown session: {sid}; scan sessions first")
    data = json.loads(map_file.read_text(encoding="utf-8"))
    validate_map_minimal(data)
    with con:
        _save_map(con, sid, data, transcript_hash)


def _save_map(con: sqlite3.Connection, sid: str, data: dict, transcript_hash: str) -> None:
    """Caller owns the transaction, so a map and its sidecar commit together."""
    con.execute("""INSERT INTO session_maps(session_id,transcript_sha256,map_json,imported_at)
                   VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
                   transcript_sha256=excluded.transcript_sha256,map_json=excluded.map_json,imported_at=excluded.imported_at""",
                (sid, transcript_hash, json.dumps(data, ensure_ascii=False), now()))


def canonical_title(s: str) -> str:
    norm = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"\s+", " ", norm).strip()


def build_graph(con: sqlite3.Connection, only_project: str | None = None, previous: dict | None = None) -> dict:
    maps = con.execute("""SELECT m.session_id,m.map_json,s.project,s.branch,s.cwd,m.imported_at
                          FROM session_maps m JOIN sessions s ON m.session_id=s.id
                          ORDER BY m.imported_at,m.session_id""").fetchall()
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    projects = set()
    sessions = set()
    previous = previous or {}
    prior_sources = {}
    prior_titles = {}
    reserved_ids = {node["id"] for node in previous.get("nodes", [])}
    for node in previous.get("nodes", []):
        key = (node["project"], node["classification"], canonical_title(node["title"]))
        prior_titles.setdefault(key, set()).add(node["id"])
        for provenance in node.get("provenance", []):
            prior_sources[(provenance["session_id"], provenance["source_node"])] = node
    loaded_maps = [(row, json.loads(row["map_json"])) for row in maps
                   if only_project is None or row["project"] == only_project]
    entries = []
    title_counts = {}
    for row, obj in loaded_maps:
        for node in obj["nodes"]:
            if node.get("kind") in ("root", "topic"):
                continue
            identity = (row["project"], str(node.get("classification") or "unknown"),
                        canonical_title(str(node.get("title") or "Untitled")))
            source = (row["session_id"], node["id"])
            entries.append((source, identity))
            key = (identity, row["session_id"])
            title_counts[key] = title_counts.get(key, 0) + 1
    ambiguous_titles = {identity for (identity, _), count in title_counts.items() if count > 1}
    source_ids = {}
    claimed_sessions = {}

    def allocate_id(identity: tuple, source: tuple, *, distinct: bool) -> str:
        # Keep the established title hash for new, unambiguous identities.
        # Split identities use their immutable source, never iteration order.
        parts = [*identity, *source] if distinct else list(identity)
        candidate = digest(json.dumps(parts, ensure_ascii=False))[:20]
        suffix = 0
        while candidate in reserved_ids:
            parts = [*identity, *source, suffix]
            candidate = digest(json.dumps(parts, ensure_ascii=False))[:20]
            suffix += 1
        reserved_ids.add(candidate)
        return candidate

    # Claim all existing sources before matching new titles. This preserves
    # IDs after translations and prevents a new item from stealing an ID that
    # belongs to another source in the same session. Repair old merged nodes
    # deterministically: the lowest source ID per session keeps the old ID.
    for source, identity in sorted(entries):
        prior = prior_sources.get(source)
        if not prior or (prior["project"], prior["classification"]) != identity[:2]:
            continue
        node_id = prior["id"]
        if source[0] in claimed_sessions.get(node_id, set()):
            node_id = allocate_id(identity, source, distinct=True)
        source_ids[source] = node_id
        claimed_sessions.setdefault(node_id, set()).add(source[0])
        prior_titles.setdefault(identity, set()).add(node_id)

    for source, identity in sorted(entries):
        if source in source_ids:
            continue
        choices = prior_titles.get(identity, set())
        node_id = next(iter(choices)) if len(choices) == 1 else None
        if (identity in ambiguous_titles or node_id is None
                or source[0] in claimed_sessions.get(node_id, set())):
            node_id = allocate_id(identity, source, distinct=identity in ambiguous_titles)
        source_ids[source] = node_id
        claimed_sessions.setdefault(node_id, set()).add(source[0])
        prior_titles.setdefault(identity, set()).add(node_id)
    for row, obj in loaded_maps:
        project, sid = row["project"], row["session_id"]
        if only_project is not None and project != only_project:
            continue
        sessions.add(sid)
        projects.add(project)
        local_to_global = {}
        for n in obj["nodes"]:
            if n.get("kind") in ("root", "topic"):
                continue
            title = str(n.get("title") or "Untitled")
            cls = str(n.get("classification") or "unknown")
            node_id = source_ids[(sid, n["id"])]
            local_to_global[n["id"]] = node_id
            if node_id not in nodes:
                nodes[node_id] = {"id": node_id, "project": project, "title": title,
                                  "classification": cls, "status": n.get("status") or "none",
                                  "summary": str(n.get("summary") or ""),
                                  "provenance": [], "branches": []}
            target = nodes[node_id]
            target["summary"] = str(n.get("summary") or target["summary"])
            if row["branch"] and row["branch"] not in target["branches"]:
                target["branches"].append(row["branch"])
            target["provenance"].append({
                "session_id": sid, "source_node": n["id"], "branch": row["branch"],
                "evidence": [{"quote": str(e.get("quote") or ""), "messageId": str(e.get("messageId") or ""),
                              "verified": bool(e.get("verified"))} for e in n.get("evidence", []) if isinstance(e, dict)][:12]
            })
        for e in obj.get("relations", []):
            frm = local_to_global.get(e.get("from"))
            to = local_to_global.get(e.get("to"))
            if not frm or not to or frm == to:
                continue
            typ = str(e.get("type") or "related")
            eid = digest(f"{frm}|{to}|{typ}")[:20]
            if eid not in edges:
                edges[eid] = {"id": eid, "from": frm, "to": to, "type": typ, "sessions": []}
            if sid not in edges[eid]["sessions"]:
                edges[eid]["sessions"].append(sid)
    prior_node_order = [n["id"] for n in previous.get("nodes", []) if n["id"] in nodes]
    prior_edge_order = [e["id"] for e in previous.get("edges", []) if e["id"] in edges]
    prior_node_ids, prior_edge_ids = set(prior_node_order), set(prior_edge_order)
    result = {"schemaVersion": "mvp-1", "generatedAt": now(),
            "mergePolicy": "same project + classification + NFKC/casefold normalized exact title; "
                           "unique source per session; ambiguous titles remain separate",
            "projects": sorted(projects), "sessionCount": len(sessions),
            "nodes": [nodes[k] for k in prior_node_order] + sorted(
                (n for k,n in nodes.items() if k not in prior_node_ids),
                key=lambda n:(n["project"],n["classification"],n["title"])),
            "edges": [edges[k] for k in prior_edge_order] + [e for k,e in edges.items() if k not in prior_edge_ids]}
    if previous and {k:v for k,v in result.items() if k != "generatedAt"} == {
            k:v for k,v in previous.items() if k != "generatedAt"}:
        result["generatedAt"] = previous.get("generatedAt", result["generatedAt"])
    return result


def render_html(graph: dict, filename: Path, qa_context: dict | None = None) -> None:
    # JSON is inert, but '<' must be escaped to prevent '</script>' breakouts.
    serialized = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    filename.parent.mkdir(parents=True, exist_ok=True)
    labels = json.dumps({"classification": CLASSIFICATION_KO, "status": STATUS_KO,
                         "relation": RELATION_KO}, ensure_ascii=False)
    template = Path(__file__).with_name("graph_view.html").read_text(encoding="utf-8")
    style = "\n".join(Path(__file__).with_name(name).read_text(encoding="utf-8") for name in ("graph_view.css", "ui_theme.css", "graph_design.css", "explorer_tools.css", "mind_tree.css"))
    script = "\n".join(Path(__file__).with_name(name).read_text(encoding="utf-8") for name in
                       ("ui_theme.js", "explorer_tools.js", "raw_turns.js", "mind_tree.js", "graph_view.js")).replace("__KO_LABELS__", labels)
    context = json.dumps(qa_context or {}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # A literal empty metadata script also works with older running collectors.
    template = template.replace('<script type="application/json" id="qa-context">{}</script>',
                                '<script type="application/json" id="qa-context">__QA_CONTEXT__</script>')
    parts = {"__GRAPH_STYLE__": style, "__GRAPH_SCRIPT__": script, "__GRAPH_DATA__": serialized, "__QA_CONTEXT__": context}
    filename.write_text(re.sub(r"__(?:GRAPH_STYLE|GRAPH_SCRIPT|GRAPH_DATA|QA_CONTEXT)__", lambda match: parts[match[0]], template), encoding="utf-8")


def serialize_transcript(trans: dict) -> str:
    """Keep the original message ids for evidence checks across chunks."""
    return json.dumps(trans, ensure_ascii=False, indent=2)


# Only known upstream codes may enter logs. An arbitrary code string is just as
# untrusted as a diagnostic message; new upstream codes are counted as unknown.
_VALIDATION_ERROR_CODES = frozenset({
    "attribution-mismatch", "decision-superseded", "evidence-unknown-message",
    "evidence-wrong-message", "node-cycle", "node-duplicate-key", "node-key-conflict",
    "node-parent-is-item", "node-self-parent", "node-too-deep", "node-unknown-parent",
    "relation-self", "relation-unknown-node", "superseded-by-invalid",
    "superseded-by-not-applicable",
})


def cmd_status(con: sqlite3.Connection) -> None:
    for table in ("sessions", "sources", "messages", "session_maps", "analyses"):
        print(f"{table}: {con.execute('SELECT count(*) FROM '+table).fetchone()[0]}")
    rows = con.execute("SELECT id,project,branch,last_seen FROM sessions ORDER BY last_seen DESC LIMIT 15").fetchall()
    for r in rows:
        print(f"  {r['id']}  project={r['project']}  branch={r['branch']}")


def collection_snapshot(con: sqlite3.Connection) -> dict:
    """Aggregate collection state without publishing any conversation text."""
    maps = {r["session_id"]: r for r in con.execute("SELECT * FROM session_maps")}
    messages = pending_messages = pending_sessions = changed_sessions = 0
    sessions = con.execute("SELECT id FROM sessions ORDER BY id").fetchall()
    for row in sessions:
        exported = transcript(con, row["id"])
        count = len(exported["messages"])
        messages += count
        stored = maps.get(row["id"])
        if count == 0:
            continue
        if stored and stored["transcript_sha256"] == digest(serialize_transcript(exported)):
            continue
        pending_sessions += 1
        if stored:
            changed_sessions += 1
            baseline = json.loads(stored["map_json"]).get("source", {}).get("messageCount", 0)
            baseline = baseline if type(baseline) is int and baseline >= 0 else 0
            pending_messages += max(0, count - baseline)
        else:
            pending_messages += count
    from runtime_status import collector_status
    return {"sessionCount": len(sessions), "messageCount": messages,"collector":collector_status(con),
            "analysedSessionCount": len(maps), "pendingSessionCount": pending_sessions,
            "pendingMessageCount": pending_messages, "changedAnalysedSessionCount": changed_sessions,
            "lastCheckedUtc": now(), "autoAnalysis": False}


def stored_analysis_revision(con: sqlite3.Connection) -> str:
    rows = con.execute("SELECT session_id,map_json FROM session_maps ORDER BY session_id").fetchall()
    assignments = []
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_assignments'").fetchone():
        assignments = [tuple(row) for row in con.execute(
            "SELECT session_id,source_map_sha256,assignment_json FROM topic_assignments ORDER BY session_id")]
    return digest(json.dumps([[tuple(r) for r in rows], assignments], ensure_ascii=False))


def publish_dashboard(con: sqlite3.Connection, dashboard, scan_errors: int = 0, *, read_only_viewer=False) -> None:
    """Project a consistent database snapshot without collecting or analysing."""
    con.execute("SAVEPOINT watch_projection")
    try:
        status = collection_snapshot(con)
        revision = stored_analysis_revision(con)
        graph = dashboard.graph
        if graph is None or revision != dashboard.analysis_revision:
            graph = build_graph(con, previous=graph)
    finally:
        con.execute("RELEASE watch_projection")
    status.update({"scanErrors": scan_errors, "readOnlyViewer": read_only_viewer})
    dashboard.publish(graph, status, revision, render_html)


def watch_tick(con: sqlite3.Connection, home: Path, mappings=None, dashboard=None, *, claude_home=None, interval=10) -> dict:
    """Collect and publish only. This path deliberately has no analyzer call."""
    failures = []
    results = scan(con, home, mappings, on_error=failures.append)
    if claude_home is not None:
        from source_adapters import scan_claude
        results.extend(scan_claude(con,claude_home,mappings,on_error=failures.append))
    added = sum(r["added_messages"] for r in results)
    with con:
        con.execute('INSERT OR REPLACE INTO collector_state VALUES (?,?)',('heartbeat',json.dumps(
            {'at':now(),'pid':os.getpid(),'interval':interval,'scanErrors':len(failures),'addedMessages':added})))
    if dashboard is not None:
        publish_dashboard(con, dashboard, len(failures))
    return {"added_messages": added, "scan_errors": len(failures)}


def options() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Codex Context Graph: local session collector and semantic knowledge map")
    p.add_argument("--db", type=Path, default=Path.home() / ".codex-context-graph" / "knowledge.db")
    p.add_argument("--codex-home", type=Path, default=codex_home())
    p.add_argument("--claude-home", type=Path, help="also collect local Claude Code projects; no model or API calls")
    p.add_argument("--project-map", type=Path, help='JSON mapping of CWD prefixes to unified project names')
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="collect new messages from Codex rollout JSONL files")
    sub.add_parser("status", help="show stored counts")
    backup = sub.add_parser('backup',help='create a local verified database/settings backup')
    backup.add_argument('--output',type=Path,required=True)
    backup.add_argument('--graph-snapshot',type=Path,help='preserve existing merged graph identities in the backup')
    restore = sub.add_parser('restore',help='restore a backup to a NEW database path; never overwrite')
    restore.add_argument('--file',type=Path,required=True)
    restore.add_argument('--output',type=Path,required=True)
    exp = sub.add_parser("export-transcript", help="export a session in Conversation Map JSON format")
    exp.add_argument("--session", required=True); exp.add_argument("--output", type=Path, required=True)
    imp = sub.add_parser("import-map", help="import a validated Conversation Map map.json")
    imp.add_argument("--session", required=True); imp.add_argument("--file", type=Path, required=True)
    graph = sub.add_parser("graph", help="render merged cross-session graph")
    graph.add_argument("--project"); graph.add_argument("--html", type=Path); graph.add_argument("--json", type=Path)
    ana = sub.add_parser("analyze", help="opt-in automatic LLM analysis via Codex + Conversation Map")
    ana.add_argument("--session", help="analyze only this session")
    ana.add_argument("--cm-root", type=Path, required=True, help="checkout root of tonitalej/conversation-map")
    ana.add_argument("--allow-model", action="store_true", help="explicitly allow sending session text to Codex model")
    ana.add_argument("--force", action="store_true")
    ana.add_argument("--topic-classifier", choices=("codex", "decisions"), default="codex",
                     help="decisions: Codex creates topics, Decisions assigns items; requires --allow-api")
    ana.add_argument("--allow-api", action="store_true", help="explicitly allow sending topic/item text to OpenAI Decisions")
    ana.add_argument("--topic-confidence", type=float, default=0.85, help="experimental acceptance threshold (0..1)")
    assign = sub.add_parser("assign-topics", help="explicitly classify an existing saved map; preserve its source data and IDs")
    assign.add_argument("--session", required=True)
    assign.add_argument("--allow-api", action="store_true")
    assign.add_argument("--topic-confidence", type=float, default=0.85)
    watch = sub.add_parser("watch", help="collect session files and optionally serve an auto-updating local dashboard; no models")
    watch.add_argument("--interval", type=float, default=10)
    watch.add_argument("--live-dir", type=Path, help="dedicated output directory for the local live dashboard")
    watch.add_argument("--port", type=int, default=8765, help="loopback server port (0 selects an available port)")
    watch.add_argument("--graph-snapshot", type=Path, help="existing graph HTML/JSON used to preserve projection IDs")
    watch.add_argument("--map-snapshot", type=Path, help="existing Conversation Map HTML to show unchanged")
    # Reject legacy automatic-analysis flags with a clear migration message.
    watch.add_argument("--analyze", action="store_true", help=argparse.SUPPRESS)
    watch.add_argument("--cm-root", type=Path, help=argparse.SUPPRESS)
    watch.add_argument("--allow-model", action="store_true", help=argparse.SUPPRESS)
    serve = sub.add_parser("serve", help="show an existing database through a read-only dashboard; no collection or models")
    serve.add_argument("--live-dir", type=Path, required=True, help="dedicated output directory for the read-only dashboard")
    serve.add_argument("--interval", type=float, default=10)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--graph-snapshot", type=Path)
    serve.add_argument("--map-snapshot", type=Path)
    serve.add_argument("--tailscale-origin", help="exact HTTPS .ts.net origin of a dedicated Tailscale Serve proxy")
    serve.add_argument("--tailscale-user", help="allow only this authenticated Tailscale login; requires --tailscale-origin")
    return p


def main(argv: list[str] | None = None) -> int:
    import analysis_pipeline as ap

    args = options().parse_args(argv)
    if args.command in ('backup','restore'):
        from backup_store import backup,restore
        import zipfile
        try:
            result=backup(args.db,args.output,args.graph_snapshot) if args.command=='backup' else restore(args.file,args.output)
            print(json.dumps(result));return 0
        except (OSError,ValueError,sqlite3.Error,zipfile.BadZipFile,RuntimeError) as error:
            print('Backup operation failed ('+type(error).__name__+'); original data was not overwritten',file=sys.stderr);return 1
    if args.command == "analyze":
        if not args.allow_model:
            print("error: model processing requires explicit --allow-model", file=sys.stderr)
            return 2
        if args.allow_api and args.topic_classifier != "decisions":
            print("error: --allow-api requires --topic-classifier decisions", file=sys.stderr)
            return 2
        if args.topic_classifier == "decisions" and (not args.session or args.force):
            print("error: hybrid analysis requires --session and preserves existing maps; omit --force", file=sys.stderr)
            return 2
    if args.command == "assign-topics" or (args.command == "analyze" and args.topic_classifier == "decisions"):
        try:
            ap._decision_preflight(args.allow_api, args.topic_confidence)
        except (RuntimeError, ValueError) as error:
            print("error: " + str(error), file=sys.stderr)
            return 2
    if args.command in ("watch", "serve"):
        if args.command == "watch" and (args.analyze or args.allow_model or args.cm_root):
            print("error: watch never runs models; use analyze --allow-model only when explicitly requested", file=sys.stderr)
            return 2
        if not math.isfinite(args.interval) or args.interval < 1 or not 0 <= args.port <= 65535:
            print("error: interval must be >= 1 seconds and port must be 0..65535", file=sys.stderr)
            return 2
        if not args.live_dir and (args.graph_snapshot or args.map_snapshot):
            print("error: snapshot options require --live-dir", file=sys.stderr)
            return 2
    if args.command == "serve":
        from live_dashboard import validate_tailscale_access
        try:
            validate_tailscale_access(args.tailscale_origin, args.tailscale_user)
        except ValueError as error:
            print("error: " + str(error), file=sys.stderr)
            return 2
    mappings = None
    if args.project_map:
        mappings = json.loads(args.project_map.read_text(encoding="utf-8"))
        if not isinstance(mappings, dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in mappings.items()):
            print("error: --project-map must be a JSON object of string prefixes to project names", file=sys.stderr)
            return 2
    if args.command == "serve":
        try:
            con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc:
            print("error: cannot open read-only database (" + type(exc).__name__ + ")", file=sys.stderr)
            return 1
    else:
        con = connect(args.db)
    try:
        if args.command == "scan":
            r = scan(con, args.codex_home, mappings)
            if args.claude_home is not None:
                from source_adapters import scan_claude
                r.extend(scan_claude(con,args.claude_home,mappings))
            print(f"scanned {len(r)} files; added {sum(x['added_messages'] for x in r)} messages")
        elif args.command == "status":
            cmd_status(con)
        elif args.command == "export-transcript":
            data = transcript(con, args.session)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            print(f"wrote {args.output} ({len(data['messages'])} messages)")
        elif args.command == "import-map":
            import_map(con, args.session, args.file)
            print(f"imported {args.session}")
        elif args.command == "graph":
            previous = None
            if args.html and args.html.is_file():
                from live_dashboard import load_graph
                previous = load_graph(args.html)
            data = build_graph(con, args.project, previous=previous)
            if not args.json and not args.html:
                print(json.dumps({"projects":data["projects"],"sessions":data["sessionCount"],
                                 "nodes":len(data["nodes"]),"edges":len(data["edges"])},ensure_ascii=False,indent=2))
            if args.json:
                args.json.parent.mkdir(parents=True,exist_ok=True)
                args.json.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
                print(f"wrote {args.json}")
            if args.html:
                render_html(data,args.html)
                print(f"wrote {args.html}")
        elif args.command == "analyze":
            ids = [args.session] if args.session else [r["id"] for r in con.execute("SELECT id FROM sessions")]
            for sid in ids:
                try:
                    print(sid, ap.auto_analyze(con,sid,args.cm_root,args.db.parent,args.force,
                        topic_classifier=args.topic_classifier, allow_api=args.allow_api,
                        topic_confidence=args.topic_confidence))
                except ap.AnalysisError as exc:
                    print(f"failed {sid}: {exc}", file=sys.stderr)
                    return 1
                except (RuntimeError, ValueError, TypeError, KeyError, OSError, sqlite3.Error,
                        subprocess.SubprocessError) as exc:
                    # Provider/parser exceptions may embed source text, paths or
                    # credentials. Only our explicitly safe errors carry detail.
                    print(f"failed {sid}: analysis failed ({type(exc).__name__}); diagnostic details omitted", file=sys.stderr)
                    return 1
        elif args.command == "assign-topics":
            try:
                print(ap.assign_topics(con, args.session, args.db.parent, allow_api=args.allow_api,
                                    min_confidence=args.topic_confidence))
            except (RuntimeError, ValueError) as error:
                print("error: " + str(error), file=sys.stderr)
                return 1
        elif args.command in ("watch", "serve"):
            dashboard = None
            if args.live_dir:
                from live_dashboard import LiveDashboard
                proxy_access = ({"tailscale_origin": args.tailscale_origin, "tailscale_user": args.tailscale_user}
                                if args.command == "serve" else {})
                dashboard = LiveDashboard(args.live_dir, args.db, args.port, args.graph_snapshot, args.map_snapshot,
                                          codex_home=args.codex_home, **proxy_access)
                dashboard.start()
                print("Live dashboard:", dashboard.url, flush=True)
            print(("Watching Codex files" if args.command == "watch" else "Viewing an existing database read-only") +
                  "; Ctrl+C to stop. Automatic model analysis: disabled", flush=True)
            try:
                while True:
                    try:
                        if args.command == "watch":
                            result = watch_tick(con, args.codex_home, mappings, dashboard, claude_home=args.claude_home, interval=args.interval)
                            if result["added_messages"] or result["scan_errors"]:
                                print(now(), "collected", result["added_messages"], "messages; scan errors", result["scan_errors"], flush=True)
                        else:
                            publish_dashboard(con, dashboard, read_only_viewer=True)
                    except Exception as exc:
                        # No source text, paths, model stderr, or credentials in
                        # automatic collector logs or the HTTP status response.
                        if dashboard:
                            dashboard.report_error(now())
                        print("watch update failed (" + type(exc).__name__ + ")", file=sys.stderr, flush=True)
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("stopped")
            finally:
                if dashboard:
                    dashboard.close()
        return 0
    except (ValueError,OSError,json.JSONDecodeError,sqlite3.Error) as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
