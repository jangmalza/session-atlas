"""Synthetic, read-only lineage and own-history boundary checks."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session_metadata import SessionMetadataReader


def spawn(parent):
    return {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1,
                                           "agent_path": "/root/helper", "agent_role": "reviewer"}}}


def record(kind="response_item", **payload):
    return {"type": kind, "payload": payload}


class SessionMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.reader = SessionMetadataReader(self.home)

    def file(self, sid, records=(), **metadata):
        path = self.home / f"rollout-{sid}.jsonl"
        rows = [record("session_meta", id=sid, source="cli", **metadata), *records]
        raw = [json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n" for row in rows]
        path.write_bytes(b"".join(raw))
        return path, raw

    def native(self, rows, version=5, wal=False):
        path = self.home / f"state_{version}.sqlite"
        con = sqlite3.connect(path)
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE threads(id TEXT,source TEXT,rollout_path TEXT,title TEXT,agent_path TEXT,agent_role TEXT)")
        for sid, source, *extra in rows:
            rest = extra + [None] * (4 - len(extra))
            con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?)", (sid,
                source if isinstance(source, str) else json.dumps(source), *rest))
        con.commit()
        self.addCleanup(con.close)
        return path, con

    def read(self, sid, paths=()):
        return self.reader.read([sid], {sid: paths})[sid]

    def test_primary_header_has_own_start_and_no_content_or_source_writes(self):
        path, raw = self.file("main", [record(role="user", content="synthetic request")])
        before = path.read_bytes()
        meta = self.read("main", [path])
        self.assertEqual(meta["sessionKind"], "primary")
        self.assertEqual(meta["rootSessionId"], "main")
        self.assertFalse(meta["forked"])
        self.assertEqual(meta["ownStartOffsets"], {str(path.resolve()): len(raw[0])})
        self.assertNotIn("synthetic request", json.dumps(meta))
        self.assertEqual(path.read_bytes(), before)

    def test_copied_history_boundary_counts_all_records_including_header(self):
        path, raw = self.file("helper", [record(role="user", content="inherited"),
            record("event_msg", type="task_started"), record(role="user", content="own")],
            forked_from_id="main", subagent_history_start_ordinal=2)
        self.native([("main", "cli"), ("helper", spawn("main"))])
        meta = self.read("helper", [path])
        self.assertEqual(meta["sessionKind"], "subagent")
        self.assertEqual(meta["parentSessionId"], "main")
        self.assertEqual(meta["rootSessionId"], "main")
        self.assertEqual(meta["ownStartOffsets"][str(path)], sum(map(len, raw[:2])))
        self.assertFalse(meta["nativeTitleUsable"])

    def test_referenced_primary_fork_local_records_are_own(self):
        path, raw = self.file("fork", [record(role="user", content="new request")],
            forked_from_id="main", forked_from_ordinal_exclusive=12,
            history_base={"thread_id": "main", "end_ordinal_exclusive": 12, "end_byte_offset": 850})
        self.native([("main", "cli"), ("fork", "vscode")])
        meta = self.read("fork", [path])
        self.assertTrue(meta["forked"])
        self.assertEqual(meta["parentSessionId"], "main")
        self.assertEqual(meta["rootSessionId"], "fork")
        self.assertEqual(meta["ownStartOffsets"][str(path)], len(raw[0]))

    def test_unverified_fork_never_uses_inherited_native_title(self):
        path, _ = self.file("fork", [record(role="user", content="inherited")], forked_from_id="main")
        self.native([("main", "cli", None, "Inherited title"),
                     ("fork", "vscode", str(path), "Inherited title")])
        meta = self.read("fork")
        self.assertTrue(meta["forked"])
        self.assertFalse(meta["boundaryKnown"])
        self.assertFalse(meta["nativeTitleUsable"])
        self.assertEqual(meta["ownStartOffsets"], {})

    def test_invalid_history_reference_does_not_mark_inherited_records_as_own(self):
        for field, value in [("thread_id", "wrong"), ("end_ordinal_exclusive", 9),
                             ("end_byte_offset", -1)]:
            with self.subTest(field=field):
                history = {"thread_id": "main", "end_ordinal_exclusive": 12, "end_byte_offset": 850}
                history[field] = value
                path, _ = self.file("fork", forked_from_id="main", forked_from_ordinal_exclusive=12,
                                    history_base=history)
                self.assertFalse(self.read("fork", [path])["boundaryKnown"])

    def test_partial_or_out_of_range_ordinal_waits_for_complete_records(self):
        path, raw = self.file("helper", forked_from_id="main", subagent_history_start_ordinal=2)
        self.assertFalse(self.read("helper", [path])["boundaryKnown"])
        with path.open("ab") as stream:
            stream.write(b'{"type":"response_item"')
        self.assertFalse(self.read("helper", [path])["boundaryKnown"])
        with path.open("ab") as stream:
            stream.write(b',"payload":{"role":"developer"}}\n')
        meta = self.read("helper", [path])
        self.assertTrue(meta["boundaryKnown"])
        self.assertEqual(meta["ownStartOffsets"][str(path)], path.stat().st_size)

    def test_unknown_sources_schema_and_missing_files_remain_unknown(self):
        self.native([("helper", spawn("absent"), str(self.home / "missing.jsonl"))])
        meta = self.read("helper")
        self.assertEqual(meta["sessionKind"], "subagent")
        self.assertIsNone(meta["forked"])
        self.assertFalse(meta["boundaryKnown"])
        self.assertIsNone(meta["rootSessionId"])
        self.assertEqual(meta["lineageStatus"], "missing_parent")
        self.assertEqual(self.read("absent")["sessionKind"], "unknown")

    def test_missing_optional_native_columns_and_newest_numeric_database(self):
        self.native([("main", "cli")], version=9)
        path = self.home / "state_10.sqlite"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE threads(id TEXT)")
        con.execute("INSERT INTO threads VALUES('main')")
        con.commit()
        con.close()
        self.assertEqual(self.read("main")["sessionKind"], "unknown")
        path, _ = self.file("main")
        self.assertEqual(self.read("main", [path])["sessionKind"], "primary")

    def test_unrelated_header_id_is_rejected_without_poisoning_valid_cache(self):
        path, _ = self.file("other")
        self.assertFalse(self.read("main", [path])["boundaryKnown"])
        self.assertTrue(self.read("other", [path])["boundaryKnown"])
        self.assertFalse(self.read("main", [path])["boundaryKnown"])

    def test_canonical_header_id_takes_precedence_over_legacy_context_session_id(self):
        path, _ = self.file("helper", session_id="parent", forked_from_id="parent",
                            subagent_history_start_ordinal=1)
        self.assertTrue(self.read("helper", [path])["boundaryKnown"])
        self.assertFalse(self.read("parent", [path])["boundaryKnown"])

    def test_parent_provenance_and_cycles_are_not_guessed(self):
        self.native([("a", spawn("b")), ("b", spawn("a"))])
        meta = self.read("a")
        self.assertEqual(meta["parentSessionId"], "b")
        self.assertEqual(meta["lineageStatus"], "cycle")
        self.assertIsNone(meta["rootSessionId"])

    def test_nested_helpers_group_under_nearest_primary_fork(self):
        path, _ = self.file("fork", forked_from_id="original")
        self.native([("original", "cli"), ("fork", "vscode", str(path)),
                     ("helper", spawn("fork")), ("nested", spawn("helper"))])
        metadata = self.reader.read(["original", "fork", "helper", "nested"])
        self.assertEqual(metadata["nested"]["rootSessionId"], "fork")
        self.assertEqual(metadata["helper"]["groupSessionId"], "fork")
        self.assertEqual(metadata["fork"]["parentSessionId"], "original")

    def test_conflicting_native_and_source_parents_do_not_choose_one(self):
        path, _ = self.file("helper", forked_from_id="second")
        self.native([("first", "cli"), ("second", "cli"), ("helper", spawn("first"))])
        meta = self.read("helper", [path])
        self.assertIsNone(meta["parentSessionId"])
        self.assertEqual(meta["lineageStatus"], "conflict")

    def test_unchanged_catalog_reads_do_not_reopen_sources_or_database(self):
        path, _ = self.file("main")
        self.native([("main", "cli", str(path), "Explicit synthetic title")])
        first = self.read("main")
        with patch("session_metadata.sqlite3.connect", side_effect=AssertionError("reopened DB")), \
             patch.object(Path, "open", side_effect=AssertionError("reopened source")):
            self.assertEqual(self.read("main"), first)

    def test_rewrite_with_same_header_and_larger_prefix_recomputes_boundary(self):
        records = [record(role="developer", content="short"), record(role="user", content="own")]
        path, _ = self.file("helper", records, forked_from_id="main", subagent_history_start_ordinal=2)
        first = self.read("helper", [path])
        records[0]["payload"]["content"] = "A longer inherited record" * 100
        path, raw = self.file("helper", records, forked_from_id="main", subagent_history_start_ordinal=2)
        current = self.read("helper", [path])
        self.assertGreater(current["ownStartOffsets"][str(path)], first["ownStartOffsets"][str(path)])
        self.assertEqual(current["ownStartOffsets"][str(path)], sum(map(len, raw[:2])))
        self.assertNotEqual(current["revision"], first["revision"])

    def test_native_wal_update_refreshes_provenance_and_revision_without_writes(self):
        path, con = self.native([("helper", spawn("one")), ("one", "cli"), ("two", "cli")], wal=True)
        before = con.total_changes
        first = self.read("helper")
        self.assertEqual(con.total_changes, before)
        con.execute("UPDATE threads SET source=? WHERE id='helper'", (json.dumps(spawn("two")),))
        con.commit()
        own_changes = con.total_changes
        second = self.read("helper")
        self.assertEqual(second["parentSessionId"], "two")
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual(con.total_changes, own_changes)
        self.assertEqual(con.execute("SELECT count(*) FROM threads").fetchone()[0], 3)

    def test_transient_native_error_keeps_verified_provenance_and_retries(self):
        _, con = self.native([("main", "cli")])
        before = self.read("main")
        con.execute("UPDATE threads SET title='Changed' WHERE id='main'")
        con.commit()
        with patch("session_metadata.sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.read("main")["sessionKind"], "primary")
        self.assertNotEqual(self.read("main")["revision"], before["revision"])


if __name__ == "__main__":
    unittest.main()
