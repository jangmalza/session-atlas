"""Read-only session lineage labels use verified own requests, never inherited ones."""
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from live_dashboard import LiveDashboard
from session_timeline import TimelineReader


PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class SessionLineageTitleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "codex"
        (self.home / "sessions").mkdir(parents=True)
        self.database = self.root / "collection.db"
        self.con = cg.connect(self.database)
        self.addCleanup(self.con.close)
        self.reader = TimelineReader(self.database, self.home)

    @staticmethod
    def event(role, text, second):
        return {"type": "response_item", "timestamp": f"2026-10-23T00:00:{second:02d}Z",
                "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}

    def rollout(self, sid, messages, **meta):
        path = self.home / "sessions" / f"rollout-{sid}.jsonl"
        events = [{"type": "session_meta", "payload": {"id": sid, "source": "cli", "cwd": "C:/Example", **meta}},
                  *messages]
        path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
        cg.scan_file(self.con, path)
        return path

    def native(self, records):
        path = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY,title TEXT,source TEXT,rollout_path TEXT)")
            con.executemany("INSERT OR REPLACE INTO threads VALUES (?,?,?,?)", records)
        return path

    def headers(self):
        return {header["id"]: header for header in self.reader.read()["sessions"]}

    def tables(self):
        return {table: [tuple(row) for row in self.con.execute("SELECT * FROM " + table)]
                for table in ("sessions", "sources", "messages", "session_maps", "analyses", "topic_assignments")}

    def child(self, sid=CHILD, task="하위 작업의 고유 요청"):
        # Ordinals count all records including the session_meta header. Own
        # history starts at ordinal 3, after two inherited message records.
        return self.rollout(sid, [self.event("user", "물려받은 동일 요청", 0),
                                 self.event("assistant", "물려받은 답변", 1),
                                 self.event("user", task, 2)],
                            source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1,
                                                                  "agent_role": "reviewer"}}},
                            forked_from_id=PARENT, subagent_history_start_ordinal=3)

    def test_inherited_requests_are_not_child_titles_and_source_ids_remain_exact(self):
        self.rollout(PARENT, [self.event("user", "물려받은 동일 요청", 0)])
        self.child()
        self.child(OTHER, "서로 다른 두 번째 보조 작업")
        before = self.tables()
        headers = self.headers()
        self.assertEqual(headers[PARENT]["sessionKind"], "primary")
        self.assertEqual(headers[PARENT]["titleSource"], "first_user")
        for sid, title in ((CHILD, "하위 작업의 고유 요청"), (OTHER, "서로 다른 두 번째 보조 작업")):
            self.assertEqual(headers[sid]["title"], title)
            self.assertEqual(headers[sid]["titleSource"], "own_request")
            self.assertEqual(headers[sid]["sessionKind"], "subagent")
            self.assertEqual(headers[sid]["parentSessionId"], PARENT)
            self.assertEqual(headers[sid]["rootSessionId"], PARENT)
            rows = cg.effective_messages(self.con, sid)
            self.assertEqual(headers[sid]["titleMessageId"], rows[2]["id"])
            self.assertEqual([message["content"] for message in self.reader.read(sid)["messages"]],
                             [row["content"] for row in rows])
        self.assertEqual(self.tables(), before)

    def test_primary_fork_uses_verified_local_history_and_keeps_primary_kind(self):
        self.rollout(PARENT, [self.event("user", "상위 요청", 0)])
        self.rollout(CHILD, [self.event("user", "일반 대화 분기 뒤의 요청", 1)],
                     forked_from_id=PARENT, forked_from_ordinal_exclusive=15,
                     history_base={"thread_id": PARENT, "end_ordinal_exclusive": 15, "end_byte_offset": 2000})
        header = self.headers()[CHILD]
        self.assertEqual(header["sessionKind"], "primary")
        self.assertTrue(header["forked"])
        self.assertEqual(header["title"], "일반 대화 분기 뒤의 요청")
        self.assertEqual(header["titleSource"], "own_request")

    def test_request_previews_use_only_verified_own_history_and_keep_exact_message_ids(self):
        path = self.child(task='첫 고유 요청')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(self.event('user', '최근 고유 요청', 3), ensure_ascii=False)+'\n')
        cg.scan_file(self.con, path)
        before = self.tables()
        previews = self.headers()[CHILD]['requestPreviews']
        rows = cg.effective_messages(self.con, CHILD)
        self.assertEqual(previews['scope'], 'own')
        self.assertEqual(previews['first']['messageId'], rows[2]['id'])
        self.assertEqual(previews['latest']['messageId'], rows[3]['id'])
        self.assertNotIn('물려받은', json.dumps(previews, ensure_ascii=False))
        self.assertEqual(self.tables(), before)
        path.unlink()
        unavailable = self.headers()[CHILD]['requestPreviews']
        self.assertEqual(unavailable, {'first':None, 'latest':None, 'scope':'unconfirmed'})

    def test_post_fork_external_page_context_is_skipped_without_changing_raw_records(self):
        wrapper = '<external_codex_apps_open_page source="ambient">상속된 페이지 정보</external_codex_apps_open_page>'
        incomplete = '<external_codex_apps_open_page source="ambient">끝나지 않은 페이지 정보'
        self.rollout(PARENT, [self.event("user", wrapper, 0)])
        self.rollout(CHILD, [self.event("user", wrapper, 1), self.event("user", incomplete, 2),
                             self.event("user", "분기 뒤 실제 요청을 제목으로 사용", 3)],
                     forked_from_id=PARENT, forked_from_ordinal_exclusive=15,
                     history_base={"thread_id": PARENT, "end_ordinal_exclusive": 15, "end_byte_offset": 2000})
        before = self.tables()
        header = self.headers()[CHILD]
        self.assertEqual(header["title"], "분기 뒤 실제 요청을 제목으로 사용")
        self.assertEqual(header["titleMessageId"], cg.effective_messages(self.con, CHILD)[2]["id"])
        self.assertEqual(header["titleSource"], "own_request")
        self.assertEqual(self.tables(), before)

    def test_external_page_wrapper_is_removed_only_as_injected_block_not_inline_user_text(self):
        raw = ('<external_codex_apps_open_page>페이지 문맥</external_codex_apps_open_page>\n'
               '## My request:\n페이지와 함께 전달된 실제 요청')
        self.rollout(PARENT, [self.event("user", raw, 0)])
        self.assertEqual(self.headers()[PARENT]["title"], "페이지와 함께 전달된 실제 요청")
        inline = '이 <external_codex_apps_open_page>예시</external_codex_apps_open_page> 태그를 설명해줘'
        self.rollout(OTHER, [self.event("user", inline, 0)])
        self.assertEqual(self.headers()[OTHER]["title"], inline)
        self.assertEqual(self.reader.read(PARENT)["messages"][0]["content"], raw)

    def test_unavailable_own_request_uses_task_path_name_before_generic_role(self):
        self.rollout(CHILD, [self.event("user", "상속된 첫 요청", 0)],
                     source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1,
                                                           "agent_path": "/root/check_storage", "agent_role": "reviewer"}}},
                     forked_from_id=PARENT)
        header = self.headers()[CHILD]
        self.assertEqual(header["title"], "check_storage · 고유 요청 확인 불가")
        self.assertEqual(header["titleSource"], "fallback")
        self.assertEqual(header["agentPath"], "/root/check_storage")
        self.assertEqual(header["agentRole"], "reviewer")

    def test_known_subagent_missing_boundary_never_uses_inherited_first_request(self):
        self.rollout(CHILD, [self.event("user", "물려받은 동일 요청", 0)],
                     source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1}}},
                     forked_from_id=PARENT)
        header = self.headers()[CHILD]
        self.assertEqual(header["sessionKind"], "subagent")
        self.assertTrue(header["title"].startswith("고유 요청 확인 불가"))
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])

    def test_missing_metadata_is_unknown_and_does_not_infer_a_primary_session(self):
        path = self.rollout(CHILD, [self.event("user", "확인할 수 없는 첫 요청", 0)])
        path.unlink()
        header = self.headers()[CHILD]
        self.assertEqual(header["sessionKind"], "unknown")
        self.assertEqual(header["title"], "고유 요청 확인 불가")
        self.assertIsNone(header["parentSessionId"])
        self.assertIsNone(header["rootSessionId"])

    def test_metadata_change_invalidates_title_cache_without_collected_row_changes(self):
        path = self.rollout(CHILD, [self.event("user", "처음에는 확인 가능한 요청", 0)])
        initial = self.headers()[CHILD]
        before = self.tables()
        path.unlink()
        changed = self.headers()[CHILD]
        self.assertEqual(initial["titleSource"], "first_user")
        self.assertEqual(changed["titleSource"], "fallback")
        self.assertEqual(changed["sessionKind"], "unknown")
        self.assertNotEqual(initial["revision"], changed["revision"])
        self.assertEqual(self.tables(), before)

    def test_changed_raw_jsonl_record_is_not_accepted_as_verified_own_request(self):
        path = self.child()
        original = path.read_text(encoding="utf-8")
        path.write_text(original.replace("하위 작업의 고유 요청", "다른 작업의 고유 요청"), encoding="utf-8")
        before = self.tables()
        header = self.headers()[CHILD]
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])
        self.assertEqual(self.tables(), before)

    def archive_child(self, *, replacement=None):
        path = self.child()
        archive = self.home / "archived_sessions" / path.name
        archive.parent.mkdir(parents=True)
        path.replace(archive)
        if replacement is not None:
            archive.write_text(replacement(archive.read_text(encoding="utf-8")), encoding="utf-8")
        source = json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1}}})
        self.native([(CHILD, "상속된 제목", source, str(archive))])
        return path, archive

    def test_archived_copy_at_same_offset_preserves_own_title_and_original_collected_path(self):
        path, archive = self.archive_child()
        before = self.tables()
        header = self.headers()[CHILD]
        row = cg.effective_messages(self.con, CHILD)[2]
        self.assertEqual(header["title"], "하위 작업의 고유 요청")
        self.assertEqual(header["titleSource"], "own_request")
        self.assertEqual(header["titleMessageId"], row["id"])
        self.assertEqual(row["source_path"], str(path.resolve()))
        self.assertFalse(path.exists())
        self.assertTrue(archive.is_file())
        self.assertEqual(self.tables(), before)

    def test_archive_with_mismatched_record_does_not_supply_own_title(self):
        self.archive_child(replacement=lambda text: text.replace("하위 작업의 고유 요청", "다른 작업의 고유 요청"))
        before = self.tables()
        header = self.headers()[CHILD]
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])
        self.assertEqual(self.tables(), before)

    def test_archive_with_shifted_offsets_is_not_matched_by_text(self):
        self.archive_child(replacement=lambda text: text.replace("물려받은 동일 요청", "분명히 더 길어진 물려받은 동일 요청"))
        header = self.headers()[CHILD]
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])

    def test_archive_for_another_session_is_rejected_even_when_message_text_matches(self):
        self.archive_child(replacement=lambda text: text.replace(CHILD, OTHER))
        header = self.headers()[CHILD]
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])

    def test_matching_text_with_an_unverified_collected_id_is_not_used_as_own_title(self):
        self.child()
        row = cg.effective_messages(self.con, CHILD)[-1]
        with self.con:
            self.con.execute("UPDATE messages SET id=? WHERE id=?", ("unverified-source-id", row["id"]))
        before = self.tables()
        header = self.headers()[CHILD]
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])
        self.assertEqual(self.tables(), before)

    def test_unchanged_lineage_title_is_cached_across_catalog_and_session_reads(self):
        self.child()
        initial = self.headers()[CHILD]
        with patch("session_timeline._request_title", side_effect=AssertionError("Unchanged title must be cached")), \
             patch("session_timeline._verified_own_request", side_effect=AssertionError("Unchanged source must be cached")):
            self.assertEqual(self.headers()[CHILD], initial)
            self.assertEqual(self.reader.read(CHILD)["session"], initial)

    def test_saved_analysis_and_all_original_nodes_relations_and_quotes_are_preserved(self):
        self.child()
        analysis = {"title": "  기존 분석 제목을 유지  ", "nodes": [
            {"id": "q", "kind": "item", "classification": "answered_question", "title": "기존 질문",
             "evidence": [{"messageId": "m3", "quote": "고유 요청", "verified": True}]},
            {"id": "a", "kind": "item", "classification": "assistant_statement", "evidence": []}],
            "relations": [{"id": "link", "from": "a", "to": "q", "type": "answers"}]}
        with self.con:
            self.con.execute("INSERT INTO session_maps VALUES (?,?,?,?)", (CHILD, "", json.dumps(analysis), "now"))
        before = self.tables()
        with patch.object(cg, "auto_analyze", side_effect=AssertionError("Model forbidden")), \
             patch.object(cg.subprocess, "run", side_effect=AssertionError("External process forbidden")), \
             patch("socket.create_connection", side_effect=AssertionError("API forbidden")):
            view = self.reader.read(CHILD)
            self.reader.search("고유 요청")
            self.reader.question_context({CHILD})
        self.assertEqual(view["session"]["title"], analysis["title"])
        self.assertEqual(view["session"]["titleSource"], "analysis")
        self.assertEqual(view["analysis"]["nodes"], analysis["nodes"])
        self.assertEqual(view["analysis"]["relations"], analysis["relations"])
        self.assertEqual(self.tables(), before)

    def test_own_request_title_is_redacted_from_offline_context(self):
        self.child(task="SOURCE ONLY PRIVATE OWN REQUEST")
        self.assertEqual(self.headers()[CHILD]["titleSource"], "own_request")
        context = self.reader.question_context({CHILD})
        self.assertNotIn("SOURCE ONLY PRIVATE OWN REQUEST", json.dumps(context))
        self.assertEqual(context[CHILD]["session"]["titleSource"], "fallback")
        self.assertIsNone(context[CHILD]["session"]["titleMessageId"])

    def test_meaningful_native_primary_title_refreshes_without_db_changes_and_is_not_exported(self):
        path = self.rollout(PARENT, [self.event("user", "처음 사용자 요청", 0)])
        self.native([(PARENT, "직접 정한 대화 제목", "cli", str(path))])
        initial = self.headers()[PARENT]
        self.assertEqual(initial["title"], "직접 정한 대화 제목")
        self.assertEqual(initial["titleSource"], "native")
        before = self.tables()
        self.native([(PARENT, "변경한 대화 제목", "cli", str(path))])
        changed = self.headers()[PARENT]
        self.assertEqual(changed["title"], "변경한 대화 제목")
        self.assertNotEqual(initial["revision"], changed["revision"])
        self.assertNotIn("변경한 대화 제목", json.dumps(self.reader.question_context({PARENT}), ensure_ascii=False))
        self.assertEqual(self.tables(), before)

    def test_native_primary_source_without_header_cannot_claim_first_request_is_own(self):
        path = self.rollout(PARENT, [self.event("user", "부모에게 물려받았을 수 있는 요청", 0)])
        self.native([(PARENT, "부모에게 물려받았을 수 있는 요청", "cli", str(path))])
        path.unlink()
        header = self.headers()[PARENT]
        self.assertEqual(header["sessionKind"], "primary")
        self.assertIsNone(header["forked"])
        self.assertEqual(header["title"], "고유 요청 확인 불가")
        self.assertEqual(header["titleSource"], "fallback")

    def test_live_dashboard_uses_configured_codex_home(self):
        self.child()
        live = LiveDashboard(self.root / "live", self.database, 0, codex_home=self.home)
        self.addCleanup(live.close)
        self.assertEqual(live.timeline.read()["sessions"][0]["titleSource"], "own_request")

    def test_serve_passes_global_codex_home_to_live_dashboard(self):
        self.rollout(PARENT, [self.event("user", "기본 요청", 0)])
        with patch("live_dashboard.LiveDashboard") as constructor, \
             patch.object(cg, "publish_dashboard", side_effect=KeyboardInterrupt), \
             redirect_stdout(io.StringIO()):
            result = cg.main(["--db", str(self.database), "--codex-home", str(self.home), "serve",
                              "--live-dir", str(self.root / "live")])
        self.assertEqual(result, 0)
        self.assertEqual(constructor.call_args.kwargs["codex_home"], self.home)


if __name__ == "__main__":
    unittest.main()
