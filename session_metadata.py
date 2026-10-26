"""Read-only Codex lineage and verified local-history boundaries for display.

This adapter never changes collected messages or infers a fork from matching
text. Missing files, unfamiliar metadata and cycles remain explicitly unknown.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading


_PRIMARY_SOURCES = {"cli", "vscode", "exec", "app"}
_HEADER_LIMIT = 1024 * 1024
_NATIVE_COLUMNS = {"id", "source", "rollout_path", "title", "agent_path", "agent_role"}


@contextmanager
def open_rollout(path: Path):
    """Read raw or Zstandard JSONL at uncompressed byte offsets, without extraction."""
    path = Path(path)
    if path.suffix != ".zst":
        with path.open("rb") as stream:
            yield stream
        return
    try:
        from compression import zstd
    except ImportError:
        raise OSError("Compressed rollout reading requires Python 3.14 or newer") from None
    try:
        with zstd.open(path, "rb") as stream:
            yield stream
    except (zstd.ZstdError, EOFError):
        raise OSError("Compressed rollout unavailable") from None


def _signature(path: Path) -> tuple | None:
    try:
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        return None


def _text(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _skip_records(stream, count: int) -> bool:
    """Count complete records without allocating an unbounded tool-output line."""
    for _ in range(count):
        while True:
            chunk = stream.readline(65536)
            if not chunk:
                return False
            if chunk.endswith(b"\n"):
                break
    return True


def _source(value) -> tuple[str, str | None, str | None, str | None]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return ("primary" if value in _PRIMARY_SOURCES else "unknown", None, None, None)
    if isinstance(value, str):
        return ("primary" if value in _PRIMARY_SOURCES else "unknown", None, None, None)
    if isinstance(value, dict) and "subagent" in value:
        agent = value["subagent"]
        spawn = agent.get("thread_spawn", {}) if isinstance(agent, dict) else {}
        if not isinstance(spawn, dict):
            spawn = {}
        return ("subagent", _text(spawn.get("parent_thread_id")),
                _text(spawn.get("agent_path")), _text(spawn.get("agent_role")))
    return ("unknown", None, None, None)


class SessionMetadataReader:
    """Cache native metadata and bounded JSONL header/boundary reads.

    ``read`` accepts collected IDs and optional ``{id: [source_path, ...]}``.
    A boundary is an inclusive byte offset in a file whose session header was
    checked against that exact ID. Callers must additionally verify any chosen
    DB message against its source line before displaying it as an own request.
    """

    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self._lock = threading.Lock()
        self._native_signature = None
        self._native = {}
        self._files = {}

    def _native_rows(self) -> dict:
        candidates = []
        try:
            for path in self.home.glob("state_*.sqlite"):
                match = re.fullmatch(r"state_([0-9]+)\.sqlite", path.name)
                if match:
                    candidates.append((int(match[1]), path))
        except OSError:
            pass
        path = max(candidates, default=(0, None), key=lambda pair: pair[0])[1]
        signature = (str(path), _signature(path), _signature(Path(str(path) + "-wal"))) if path else ()
        if signature == self._native_signature:
            return self._native
        rows = {}
        if path:
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as con:
                    con.execute("PRAGMA query_only=ON")
                    con.row_factory = sqlite3.Row
                    columns = {row["name"] for row in con.execute("PRAGMA table_info(threads)")}
                    if "id" in columns:
                        selected = sorted(columns & _NATIVE_COLUMNS)
                        rows = {row["id"]: dict(row) for row in con.execute(
                            "SELECT " + ",".join(selected) + " FROM threads") if isinstance(row["id"], str)}
            except (OSError, sqlite3.Error):
                # Locked/unavailable metadata is not a reason to mutate the DB
                # or reveal an exception containing local paths/content.
                # Keep earlier verified provenance and retry on the next poll.
                return self._native
        self._native_signature, self._native = signature, rows
        return rows

    def _file(self, filename: str, sid: str) -> dict:
        try:
            path = Path(filename).expanduser().resolve()
        except (OSError, ValueError):
            return {}
        key = str(path)
        cache_key = (key, sid)
        signature = _signature(path)
        previous = self._files.get(cache_key)
        if previous and previous["signature"] == signature:
            result = previous["result"]
            return result if result.get("id") == sid else {}
        result = {}
        if signature:
            try:
                with open_rollout(path) as stream:
                    header = stream.readline(_HEADER_LIMIT + 1)
                    if len(header) > _HEADER_LIMIT or not header.endswith(b"\n"):
                        raise ValueError("unavailable header")
                    event = json.loads(header)
                    payload = event.get("payload") if isinstance(event, dict) else None
                    if event.get("type") != "session_meta" or not isinstance(payload, dict):
                        raise ValueError("unavailable metadata")
                    identifier = _text(payload.get("id")) or _text(payload.get("session_id"))
                    # ``id`` is the canonical rollout/thread identity used by
                    # the collector. Some subagent headers also retain a
                    # different legacy ``session_id`` from inherited context.
                    if identifier != sid:
                        raise ValueError("unrelated source")
                    kind, parent, agent_path, agent_role = _source(payload.get("source"))
                    fork_parent = _text(payload.get("forked_from_id"))
                    parent = parent or _text(payload.get("parent_thread_id")) or fork_parent
                    result = {"id": sid, "sessionKind": kind, "parentSessionId": parent,
                              "agentPath": agent_path or _text(payload.get("agent_path")),
                              "agentRole": agent_role or _text(payload.get("agent_role")),
                              "forked": bool(fork_parent), "forkParentSessionId": fork_parent, "offset": None,
                              "path": key, "headerHash": hashlib.sha256(header).hexdigest()}
                    ordinal = payload.get("subagent_history_start_ordinal")
                    history = payload.get("history_base")
                    if not fork_parent:
                        # With no inherited history reference, the local records
                        # after this verified header belong to this session.
                        if ordinal is None and history is None:
                            result["offset"] = stream.tell()
                        else:
                            result["forked"] = None
                    elif (ordinal is None and isinstance(history, dict)
                          and history.get("thread_id") == fork_parent
                          and type(history.get("end_ordinal_exclusive")) is int
                          and history["end_ordinal_exclusive"] >= 0
                          and type(payload.get("forked_from_ordinal_exclusive")) is int
                          and history["end_ordinal_exclusive"] == payload.get("forked_from_ordinal_exclusive")
                          and type(history.get("end_byte_offset")) is int
                          and history["end_byte_offset"] >= 0):
                        # Referenced-history rollouts contain only their own new
                        # records; the inherited prefix lives in the parent file.
                        result["offset"] = stream.tell()
                    elif history is None and type(ordinal) is int and ordinal >= 1:
                        # Legacy copied-history rollouts use zero-based ordinals
                        # over ALL JSONL records, including session_meta at zero.
                        # A larger file may have been rewritten, not appended.
                        # Recheck changed files; unchanged files were cached above.
                        # Tool outputs and inherited messages are not decoded.
                        if _skip_records(stream, ordinal - 1):
                            result["offset"] = stream.tell()
            except (OSError, ValueError, TypeError, AttributeError):
                result = {}
        self._files[cache_key] = {"signature": signature, "result": result}
        return result

    def read(self, session_ids, source_paths=None, *, include_compressed: bool = False) -> dict:
        """Return display metadata only; never return original message content."""
        with self._lock:
            native = self._native_rows()
            source_paths = source_paths or {}
            requested = list(dict.fromkeys(session_ids))
            all_metadata = {}
            for sid, row in native.items():
                kind, parent, agent_path, agent_role = _source(row.get("source"))
                all_metadata[sid] = {"sessionKind": kind, "parentSessionId": parent,
                                     "agentPath": agent_path or _text(row.get("agent_path")),
                                     "agentRole": agent_role or _text(row.get("agent_role"))}
            for sid in requested:
                row = native.get(sid, {})
                entry = all_metadata.setdefault(sid, {"sessionKind": "unknown", "parentSessionId": None,
                                                      "agentPath": None, "agentRole": None})
                entry.update(forked=None, forkParentSessionId=None, boundaryKnown=False,
                             ownStartOffsets={}, nativeTitle=row.get("title"))
                paths = source_paths.get(sid, [])
                if isinstance(paths, (str, Path)):
                    paths = [paths]
                paths = [*paths, *([row["rollout_path"]] if _text(row.get("rollout_path")) else [])]
                candidates = []
                for filename in paths:
                    path = Path(filename)
                    if path.suffix == ".zst":
                        if include_compressed:
                            candidates.append(path)
                        continue
                    candidates.append(path)
                    # Only the exact compressed sibling of a known missing
                    # rollout is eligible. No directory search or text matching.
                    try:
                        if include_compressed and path.suffix == ".jsonl" and not path.is_file():
                            candidates.append(Path(str(path) + ".zst"))
                    except OSError:
                        pass
                verified = []
                for path in dict.fromkeys(str(path) for path in candidates):
                    info = self._file(path, sid)
                    if info:
                        verified.append(info)
                        if info["offset"] is not None:
                            entry["ownStartOffsets"][info["path"]] = info["offset"]
                if verified:
                    first = verified[0]
                    # Explicit subagent provenance wins over any stale primary
                    # label. Conflicting parents remain unplaced, never guessed.
                    kinds = {entry["sessionKind"], *(info["sessionKind"] for info in verified)}
                    entry["sessionKind"] = "subagent" if "subagent" in kinds else "primary" if "primary" in kinds else "unknown"
                    parents = {value for value in [entry["parentSessionId"], *(info["parentSessionId"] for info in verified)] if value}
                    entry["parentSessionId"] = next(iter(parents)) if len(parents) == 1 else None
                    entry["lineageConflict"] = len(parents) > 1
                    forked = {info["forked"] for info in verified}
                    entry["forked"] = True if True in forked else None if None in forked else False
                    fork_parents = {info["forkParentSessionId"] for info in verified if info["forkParentSessionId"]}
                    entry["forkParentSessionId"] = next(iter(fork_parents)) if len(fork_parents) == 1 else None
                    entry["boundaryKnown"] = bool(entry["ownStartOffsets"])
                    entry["agentPath"] = first.get("agentPath") or entry["agentPath"]
                    entry["agentRole"] = first.get("agentRole") or entry["agentRole"]
            results = {}
            for sid in requested:
                entry = all_metadata[sid]
                seen, current = set(), sid
                root, status = None, "unknown"
                while current not in seen:
                    seen.add(current)
                    ancestor = all_metadata.get(current)
                    if ancestor is None:
                        status = "missing_parent"
                        break
                    if ancestor.get("lineageConflict"):
                        status = "conflict"
                        break
                    # A normal conversation can itself be a user-created fork.
                    # Its helper tasks belong to that conversation, not to the
                    # original conversation that it branched from.
                    if ancestor["sessionKind"] == "primary":
                        root, status = current, "known"
                        break
                    parent = ancestor.get("parentSessionId")
                    if not parent:
                        break
                    current = parent
                else:
                    status = "cycle"
                entry.update(rootSessionId=root, lineageStatus=status,
                             groupSessionId=root or entry["parentSessionId"] or sid)
                title = _text(entry.get("nativeTitle"))
                ancestor_titles = {_text(native.get(ancestor, {}).get("title")) for ancestor in seen if ancestor != sid}
                entry["nativeTitleUsable"] = bool(title and entry["sessionKind"] == "primary"
                    and entry["forked"] is False and entry["boundaryKnown"] and title not in ancestor_titles
                    and title.casefold() not in {"codex session", f"codex session {sid}".casefold(), "new chat", "new conversation"})
                # Include source signatures: replacing a source can invalidate a
                # previously verified title even if its displayed metadata agrees.
                signatures = [(path, self._files[(path, sid)]["signature"]) for path in sorted(entry["ownStartOffsets"])]
                entry["revision"] = hashlib.sha256(json.dumps([entry, signatures], sort_keys=True,
                    ensure_ascii=False).encode("utf-8")).hexdigest()
                results[sid] = dict(entry)
            return results
