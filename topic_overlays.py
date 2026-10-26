"""Display-only topic assignments; never rewrite stored analysis or evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import sqlite3

from decisions_topics import DecisionsError, map_fingerprint


def _probability(value: object) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def validate_overlay(map_data: dict, overlay: dict) -> None:
    """Require exactly one valid result for every source item, with no new IDs."""
    try:
        valid = (isinstance(overlay, dict)
                 and overlay.get("schemaVersion") == "topic-assignments-1"
                 and overlay.get("classifier") == "openai-decisions"
                 and overlay.get("sourceMapSha256") == map_fingerprint(map_data)
                 and all(isinstance(overlay.get(key), str) and overlay[key].strip()
                         for key in ("model", "policyVersion"))
                 and _probability(overlay.get("minConfidence"))
                 and isinstance(overlay.get("assignments"), list))
        if not valid:
            raise ValueError
        nodes = map_data["nodes"]
        if not isinstance(nodes, list):
            raise ValueError
        by_id = {}
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"] or node["id"] in by_id:
                raise ValueError
            by_id[node["id"]] = node
        items = {key for key, node in by_id.items() if node.get("kind") == "item"}
        topics = {key for key, node in by_id.items() if node.get("kind") == "topic"}
        found = set()
        for result in overlay["assignments"]:
            if not isinstance(result, dict) or not {"nodeId", "topicId", "status", "confidence"} <= result.keys():
                raise ValueError
            node_id, topic_id, status = result["nodeId"], result["topicId"], result["status"]
            confidence = result["confidence"]
            if not isinstance(node_id, str) or node_id not in items or node_id in found:
                raise ValueError
            if confidence is not None and not _probability(confidence):
                raise ValueError
            if status == "assigned":
                if not isinstance(topic_id, str) or topic_id not in topics or confidence is None or confidence < overlay["minConfidence"]:
                    raise ValueError
            elif status in ("review", "new_topic"):
                if topic_id is not None:
                    raise ValueError
                if status == "new_topic" and (confidence is None or confidence < overlay["minConfidence"]):
                    raise ValueError
            else:
                raise ValueError
            found.add(node_id)
        if found != items:
            raise ValueError
    except (KeyError, TypeError, ValueError, DecisionsError):
        raise ValueError("Invalid or outdated topic assignments.") from None


def store_overlay(con: sqlite3.Connection, sid: str, map_data: dict, overlay: dict, created_at: str) -> None:
    """Write only the sidecar row; the caller owns commit/rollback."""
    validate_overlay(map_data, overlay)
    con.execute("""INSERT INTO topic_assignments(session_id,source_map_sha256,assignment_json,created_at)
        VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
        source_map_sha256=excluded.source_map_sha256,assignment_json=excluded.assignment_json,
        created_at=excluded.created_at""",
        (sid, overlay["sourceMapSha256"], json.dumps(overlay, ensure_ascii=False, sort_keys=True, allow_nan=False), created_at))


def read_overlays(con: sqlite3.Connection) -> dict[str, dict]:
    """Old read-only databases need no migration to remain viewable."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_assignments'").fetchone():
        return {}
    overlays = {}
    for sid, source_hash, text in con.execute("SELECT session_id,source_map_sha256,assignment_json FROM topic_assignments"):
        try:
            value = json.loads(text)
            if not isinstance(value, dict) or value.get("sourceMapSha256") != source_hash:
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            # Retain an explicit invalid marker instead of silently hiding errors.
            value = {"invalidStoredOverlay": True}
        overlays[sid] = value
    return overlays


def project_structure(map_data: dict, overlay: dict | None = None) -> tuple[list[dict], dict]:
    """Copy hierarchy metadata and apply only validated item membership changes."""
    structure = [{key: deepcopy(node[key]) for key in ("id", "kind", "parentId", "childIds") if key in node} |
                 ({"title": node.get("title", "")} if node.get("kind") == "topic" else {})
                 for node in map_data.get("nodes", [])]
    summary = {"status": "codex", "assigned": 0, "review": 0, "newTopic": 0}
    if overlay is None:
        return structure, summary
    try:
        if isinstance(overlay, dict) and isinstance(overlay.get("sourceMapSha256"), str) and overlay["sourceMapSha256"] != map_fingerprint(map_data):
            summary["status"] = "stale"
            return structure, summary
        validate_overlay(map_data, overlay)
    except (ValueError, DecisionsError):
        summary["status"] = "invalid"
        return structure, summary
    by_id = {node["id"]: node for node in structure}
    item_ids = {node["id"] for node in structure if node.get("kind") == "item"}
    roots = [node["id"] for node in structure if node.get("kind") == "root"]
    root = roots[0] if len(roots) == 1 else None
    # Remove old item membership everywhere; preserve topic/root hierarchy order.
    for node in structure:
        if isinstance(node.get("childIds"), list):
            node["childIds"] = [child for child in node["childIds"] if child not in item_ids]
    summary.update(status="decisions", model=overlay["model"])
    for result in overlay["assignments"]:
        parent = result["topicId"] if result["status"] == "assigned" else root
        by_id[result["nodeId"]]["parentId"] = parent
        if parent is not None:
            children = by_id[parent].setdefault("childIds", [])
            if not isinstance(children, list):
                children = by_id[parent]["childIds"] = []
            children.append(result["nodeId"])
        summary[{"assigned": "assigned", "review": "review", "new_topic": "newTopic"}[result["status"]]] += 1
    return structure, summary
