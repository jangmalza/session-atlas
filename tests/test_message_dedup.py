"""Mirrored Codex events must never hide independent repeated conversation turns."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg
from session_timeline import TimelineReader


SID = "23456789-1234-4234-8234-123456789abc"


class MessageDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_file = self.root / "collection.db"
        self.db = cg.connect(self.db_file)
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO sessions(id,first_seen,last_seen) VALUES(?,?,?)", (SID, "", ""))
        self.db.commit()

    def add(self, identifier, kind, at, *, offset, text="Repeat request", role="user", source="fixture.jsonl"):
        self.db.execute("""INSERT INTO messages
            (id,session_id,source_path,offset_bytes,role,source_kind,content,at)
            VALUES(?,?,?,?,?,?,?,?)""", (identifier, SID, source, offset, role, kind, text, at))
        self.db.commit()

    def rows(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM messages ORDER BY id")]

    def ids(self):
        before, changes = self.rows(), self.db.total_changes
        result = [row["id"] for row in cg.effective_messages(self.db, SID)]
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.db.total_changes, changes)
        return result

    def test_later_repeated_request_survives_in_transcript_search_and_timeline(self):
        self.add("first", "response_item", "2026-10-20T00:00:00Z", offset=100)
        self.add("answer", "response_item", "2026-10-20T00:00:01Z", offset=200,
                 text="First response", role="assistant")
        self.add("repeat", "event_msg", "2026-10-20T00:01:00Z", offset=300)
        before = self.rows()
        with patch.object(ap, "auto_analyze", side_effect=AssertionError("Models forbidden")), \
                patch.object(cg.subprocess, "run", side_effect=AssertionError("Processes forbidden")):
            self.assertEqual(self.ids(), ["first", "answer", "repeat"])
            self.assertEqual([message["content"] for message in cg.transcript(self.db, SID)["messages"]],
                             ["Repeat request", "First response", "Repeat request"])
            reader = TimelineReader(self.db_file)
            self.assertEqual([message["id"] for message in reader.read(SID)["messages"]],
                             ["first", "answer", "repeat"])
            self.assertEqual(reader.search("Repeat request")["total"], 2)
        self.assertEqual(self.rows(), before)

    def test_adjacent_mirrors_prefer_response_in_either_file_order(self):
        for kinds in (("event_msg", "response_item"), ("response_item", "event_msg")):
            with self.subTest(kinds=kinds):
                self.db.execute("DELETE FROM messages")
                self.add(kinds[0], kinds[0], "2026-10-20T00:00:00.000Z", offset=100)
                self.add(kinds[1], kinds[1], "2026-10-20T00:00:00.050Z", offset=200)
                self.assertEqual(self.ids(), ["response_item"])

    def test_long_adjacent_mirrors_are_not_limited_by_content_byte_length(self):
        self.add("event", "event_msg", "2026-10-20T00:00:00Z", offset=100, text="x" * 20000)
        self.add("canonical", "response_item", "2026-10-20T00:00:00Z", offset=21000, text="x" * 20000)
        self.assertEqual(self.ids(), ["canonical"])

    def test_intervening_conversation_prevents_cross_turn_matching_even_at_same_time(self):
        self.add("first", "response_item", "2026-10-20T00:00:00Z", offset=100)
        self.add("answer", "response_item", "2026-10-20T00:00:00Z", offset=200,
                 text="Different response", role="assistant")
        self.add("repeat", "event_msg", "2026-10-20T00:00:00Z", offset=300)
        self.assertEqual(self.ids(), ["first", "answer", "repeat"])

    def test_missing_invalid_naive_delayed_and_backward_timestamps_are_retained(self):
        for first, second in (("", ""), ("not-a-time", "not-a-time"),
                              ("2026-10-20T00:00:00", "2026-10-20T00:00:00"),
                              ("2026-10-20T00:00:00Z", "2026-10-20T00:00:02Z"),
                              ("2026-10-20T00:00:01Z", "2026-10-20T00:00:00Z")):
            with self.subTest(first=first, second=second):
                self.db.execute("DELETE FROM messages")
                self.add("event", "event_msg", first, offset=100)
                self.add("canonical", "response_item", second, offset=200)
                self.assertEqual(set(self.ids()), {"event", "canonical"})

    def test_two_possible_partners_are_ambiguous_and_all_rows_are_retained(self):
        for kinds in (("event_msg", "response_item", "event_msg"),
                      ("response_item", "event_msg", "response_item")):
            with self.subTest(kinds=kinds):
                self.db.execute("DELETE FROM messages")
                for index, kind in enumerate(kinds):
                    self.add(str(index), kind, "2026-10-20T00:00:00Z", offset=100 * (index + 1))
                self.assertEqual(self.ids(), ["0", "1", "2"])

    def test_one_canonical_row_cannot_suppress_multiple_events(self):
        self.add("canonical", "response_item", "2026-10-20T00:00:00.000Z", offset=100)
        self.add("mirror", "event_msg", "2026-10-20T00:00:00.010Z", offset=200)
        self.add("repeat", "event_msg", "2026-10-20T00:00:00.020Z", offset=300)
        self.assertEqual(self.ids(), ["canonical", "repeat"])

    def test_matching_does_not_cross_source_files(self):
        self.add("event", "event_msg", "2026-10-20T00:00:00Z", offset=100, source="first.jsonl")
        self.add("canonical", "response_item", "2026-10-20T00:00:00Z", offset=200, source="second.jsonl")
        self.assertEqual(self.ids(), ["event", "canonical"])

    def test_archive_copy_and_rescans_keep_canonical_id_and_raw_rows(self):
        home = self.root / "codex"
        source = home / "sessions" / f"rollout-{SID}.jsonl"
        source.parent.mkdir(parents=True)
        events = [
            {"type": "session_meta", "payload": {"id": SID}},
            {"type": "event_msg", "timestamp": "2026-10-20T00:00:00Z",
             "payload": {"type": "user_message", "message": "Archive request"}},
            {"type": "response_item", "timestamp": "2026-10-20T00:00:00Z",
             "payload": {"type": "message", "role": "user", "content": "Archive request"}},
        ]
        source.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        cg.scan(self.db, home)
        raw_before = self.rows()
        canonical_ids = self.ids()
        self.assertEqual(len(raw_before), 2)
        self.assertEqual(len(canonical_ids), 1)
        self.assertEqual(cg.effective_messages(self.db, SID)[0]["source_kind"], "response_item")
        archive = home / "archived_sessions" / source.name
        archive.parent.mkdir()
        archive.write_bytes(source.read_bytes())
        for _ in range(2):
            self.assertTrue(all(result["added_messages"] == 0 for result in cg.scan(self.db, home)))
        self.assertEqual(self.ids(), canonical_ids)
        self.assertEqual(self.rows(), raw_before)

    def test_restored_repeat_does_not_rewrite_saved_evidence_or_retarget_its_anchor(self):
        self.add("first", "response_item", "2026-10-20T00:00:00Z", offset=100)
        self.add("answer", "response_item", "2026-10-20T00:00:01Z", offset=200,
                 text="First response", role="assistant")
        self.add("repeat", "event_msg", "2026-10-20T00:01:00Z", offset=300)
        self.add("later-answer", "response_item", "2026-10-20T00:01:01Z", offset=400,
                 text="Later response", role="assistant")
        # Legacy dedup hid the repeated request: the later answer used to be m3.
        old_rows = [row for row in cg.effective_messages(self.db, SID) if row["id"] != "repeat"]
        old_transcript = {"title": f"Codex session {SID}", "messages": [
            {"id": f"m{index + 1}", "role": row["role"], "content": row["content"], "timestamp": row["at"]}
            for index, row in enumerate(old_rows)]}
        analysis = {"source": {"messageCount": 3}, "nodes": [
            {"id": "answer-node", "kind": "item", "classification": "assistant_statement",
             "title": "Stored answer", "evidence": [
                 {"messageId": "m3", "index": 2, "speaker": "assistant", "quote": "Later response", "verified": True}]}],
            "relations": [], "messages": [
                {"id": message["id"], "index": index, "speaker": message["role"],
                 "text": message["content"], "timestamp": message["timestamp"]}
                for index, message in enumerate(old_transcript["messages"])]}
        self.db.execute("INSERT INTO session_maps VALUES(?,?,?,?)", (
            SID, cg.digest(cg.serialize_transcript(old_transcript)), json.dumps(analysis), "2026-10-20"))
        self.db.commit()
        before = tuple(self.db.execute("SELECT * FROM session_maps").fetchone())
        view = TimelineReader(self.db_file).read(SID)
        self.assertEqual(view["total"], 4)
        self.assertEqual(view["analysis"]["nodes"], analysis["nodes"])
        self.assertEqual(view["analysis"]["relations"], analysis["relations"])
        self.assertEqual(view["analysis"]["anchors"]["answer-node"], [
            {"messageId": "later-answer", "index": 3, "sourceMessageId": "m3"}])
        self.assertEqual(tuple(self.db.execute("SELECT * FROM session_maps").fetchone()), before)


if __name__ == "__main__":
    unittest.main()
