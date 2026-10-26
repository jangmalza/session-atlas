"""Model-free session labels are read-only excerpts with original source IDs."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from session_timeline import TimelineReader


SID = "12345678-1234-4234-8234-123456789abc"


class SessionTitleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "collection.db"
        self.con = cg.connect(self.database)
        self.addCleanup(self.con.close)
        with self.con:
            self.con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                             (SID, "C:/example", "Example", "test", "2026-10-20T00:00:00Z", "", ""))
        self.reader = TimelineReader(self.database)

    def add(self, text, role="user"):
        index = self.con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        identifier = f"raw-message-{index+1}"
        with self.con:
            self.con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                             (identifier, SID, "synthetic.jsonl", index * 100, role,
                              "response_item", text, f"2026-10-20T00:00:{index:02d}Z"))
        return identifier

    def save(self, value):
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO session_maps VALUES (?,?,?,?)",
                             (SID, "", json.dumps(value, ensure_ascii=False), "2026-10-20T00:00:00Z"))

    def header(self):
        return self.reader.read()["sessions"][0]

    def tables(self):
        return {name: [tuple(row) for row in self.con.execute("SELECT * FROM " + name)]
                for name in ("sessions", "sources", "messages", "session_maps", "analyses", "topic_assignments")}

    def test_first_request_normalizes_korean_markdown_and_whitespace_without_changing_raw_text(self):
        raw = "\ufeff  # 한글 대화 검색\n\n  ## 검색 결과를\t이어서 읽게 해줘  "
        identifier = self.add(raw)
        header = self.header()
        self.assertEqual(header["title"], "한글 대화 검색 검색 결과를 이어서 읽게 해줘")
        self.assertEqual(header["titleSource"], "first_user")
        self.assertEqual(header["titleMessageId"], identifier)
        self.assertEqual(header["firstMessageAt"], "2026-10-20T00:00:00Z")
        message = self.reader.read(SID)["messages"][0]
        self.assertEqual((message["id"], message["content"]), (identifier, raw))

    def test_long_unicode_request_is_bounded_and_html_remains_literal_text(self):
        self.add("한글😀" * 60)
        title = self.header()["title"]
        self.assertEqual(len(title), 80)
        self.assertTrue(title.endswith("…"))
        self.assertEqual(title[:-1], ("한글😀" * 60)[:79])
        with self.con:
            self.con.execute("DELETE FROM messages")
        self.reader = TimelineReader(self.database)
        literal = '<img src=x onerror="alert(1)"> 표시를 고쳐줘 & <script>예시</script>'
        self.add(literal)
        self.assertEqual(self.header()["title"], literal)

    def test_skips_metadata_only_bootstrap_messages_and_uses_exact_request_id(self):
        metadata = [
            '<environment_context><cwd>C:/private</cwd></environment_context>',
            '# AGENTS.md instructions for C:/example\n\n<INSTRUCTIONS>설정 지침</INSTRUCTIONS>',
            '<developer_instructions>도구 지침</developer_instructions>',
            '<permissions_instructions>권한 지침</permissions_instructions>',
            '<sandbox_instructions>환경 지침</sandbox_instructions>',
            '<environment_context>완료되지 않은 환경 지침',
        ]
        for text in metadata:
            self.add(text)
        self.add("도움을 드리겠습니다", "assistant")
        identifier = self.add("세션 목록을 보기 쉽게 정리해줘")
        header = self.header()
        self.assertEqual(header["title"], "세션 목록을 보기 쉽게 정리해줘")
        self.assertEqual(header["titleMessageId"], identifier)
        self.assertEqual(header["messageCount"], len(metadata) + 2)

    def test_removes_known_context_and_request_wrapper_from_same_message_only_for_display(self):
        raw = ('<in-app-browser-context source="ambient-ui-state">\n'
               '# In app browser:\nhttps://example.invalid/private\n</in-app-browser-context>\n\n'
               '## My request:\n# 질문 목록을 시간순으로 보여줘\n')
        identifier = self.add(raw)
        self.assertEqual(self.header()["title"], "질문 목록을 시간순으로 보여줘")
        self.assertEqual(self.reader.read(SID)["messages"][0]["content"], raw)
        self.assertEqual(self.header()["titleMessageId"], identifier)

    def test_inline_context_markup_in_user_request_is_not_treated_as_bootstrap(self):
        raw = "이 <environment_context>예시</environment_context> 태그를 설명해줘"
        self.add(raw)
        self.assertEqual(self.header()["title"], raw)

    def test_assistant_only_and_empty_sessions_have_explicit_fallback(self):
        empty = self.header()
        self.assertEqual(empty["title"], "대화 기록 없음")
        self.assertEqual(empty["titleSource"], "fallback")
        self.assertIsNone(empty["titleMessageId"])
        self.assertIsNone(empty["firstMessageAt"])
        self.add("어시스턴트의 말로 제목을 만들지 않습니다", "assistant")
        self.assertEqual(self.header()["title"], "제목을 확인할 요청 없음")
        self.assertEqual(self.header()["titleSource"], "fallback")

    def test_scaffold_only_session_has_neutral_korean_fallback(self):
        self.add("<environment_context>환경 지침</environment_context>")
        header = self.header()
        self.assertEqual(header["title"], "제목을 확인할 요청 없음")
        self.assertEqual(header["titleSource"], "fallback")
        self.assertIsNone(header["titleMessageId"])

    def test_meaningful_saved_titles_and_root_titles_are_preserved_exactly(self):
        self.add("첫 요청")
        saved = "  기존 분석 제목을 그대로 유지  "
        self.save({"title": saved, "nodes": [{"id": "root", "kind": "root", "title": "다른 제목"}]})
        self.assertEqual(self.header()["title"], saved)
        self.assertEqual(self.header()["titleSource"], "analysis")
        self.assertIsNone(self.header()["titleMessageId"])
        self.assertEqual(self.reader.question_context({SID})[SID]["session"],
                         {key:value for key,value in self.header().items() if key != 'requestPreviews'})
        self.save({"title": " ", "nodes": [{"id": "root", "kind": "root", "title": saved}]})
        self.assertEqual(self.header()["title"], saved)
        self.save({"title": f"Codex session {SID}", "nodes": [{"id": "root", "kind": "root", "title": saved}]})
        self.assertEqual(self.header()["title"], saved)

    def test_generic_saved_session_title_uses_request_without_mutating_analysis(self):
        identifier = self.add("실제 요청에 가까운 제목")
        analysis = {"title": f"Codex session {SID}", "nodes": [{"id": "root", "kind": "root", "title": "Codex session"}]}
        self.save(analysis)
        before = self.tables()
        self.assertEqual(self.header()["titleSource"], "first_user")
        self.assertEqual(self.header()["titleMessageId"], identifier)
        self.assertEqual(self.tables(), before)

    def test_title_is_stable_when_appending_and_refreshes_when_first_request_or_analysis_arrives(self):
        self.add("<environment_context>환경 설정</environment_context>")
        self.assertEqual(self.header()["titleSource"], "fallback")
        identifier = self.add("처음 확인한 사용자 요청")
        initial = self.header()
        self.add("나중에 추가한 전혀 다른 요청")
        appended = self.header()
        self.assertEqual(appended["title"], initial["title"])
        self.assertEqual(appended["titleMessageId"], identifier)
        self.assertNotEqual(appended["revision"], initial["revision"])
        self.save({"title": "나중에 저장한 분석 제목", "nodes": []})
        updated = self.header()
        self.assertEqual(updated["title"], "나중에 저장한 분석 제목")
        self.assertEqual(updated["titleSource"], "analysis")
        self.save({"title": "갱신한 분석 제목", "nodes": []})
        self.assertEqual(self.header()["title"], "갱신한 분석 제목")

    def test_unchanged_catalog_and_other_views_reuse_cleaned_title(self):
        self.add("<environment_context>환경 지침</environment_context>")
        self.add("캐시에서 재사용할 첫 요청")
        initial = self.header()
        with patch("session_timeline._request_title", side_effect=AssertionError("Unchanged title must be cached")):
            self.assertEqual(self.header(), initial)
            self.assertEqual(self.reader.read(SID)["session"], initial)
            offline = self.reader.question_context({SID})[SID]["session"]
            self.assertEqual(offline, {**{k:v for k,v in initial.items() if k != "requestPreviews"}, "title": "Example · 12345678",
                                      "titleSource": "fallback", "titleMessageId": None})
            self.assertEqual(self.reader.search("첫 요청")["results"][0]["sessionTitle"], initial["title"])

    def test_request_previews_use_first_and_latest_user_rows_without_changing_source(self):
        self.add('<environment_context>BOOTSTRAP ONLY</environment_context>')
        first = self.add('## My request:\n첫 원문 요청 ' + '긴 내용 ' * 100)
        self.add('ASSISTANT MUST NOT BE A REQUEST', 'assistant')
        latest = self.add('최근 원문 요청 <img src=x>')
        self.add('<in-app-browser-context>CONTEXT ONLY</in-app-browser-context>')
        before = self.tables()
        previews = self.header()['requestPreviews']
        self.assertEqual(previews['scope'], 'session')
        self.assertEqual(previews['first']['messageId'], first)
        self.assertEqual(previews['first']['index'], 1)
        self.assertEqual(len(previews['first']['text']), 180)
        self.assertEqual(previews['latest']['messageId'], latest)
        self.assertEqual(previews['latest']['text'], '최근 원문 요청 <img src=x>')
        self.assertNotIn('requestPreviews', self.reader.question_context({SID})[SID]['session'])
        self.assertEqual(self.tables(), before)

    def test_request_previews_refresh_on_append_without_replacing_the_first_request(self):
        first = self.add('처음 요청')
        initial = self.header()['requestPreviews']
        self.assertEqual(initial['first'], initial['latest'])
        latest = self.add('나중 요청')
        changed = self.header()['requestPreviews']
        self.assertEqual(changed['first'], initial['first'])
        self.assertEqual(changed['first']['messageId'], first)
        self.assertEqual(changed['latest']['messageId'], latest)

    def test_empty_or_context_only_sessions_do_not_invent_request_previews(self):
        self.add('<environment_context>BOOTSTRAP ONLY</environment_context>')
        self.add('An assistant response', 'assistant')
        self.assertEqual(self.header()['requestPreviews'], {'first':None, 'latest':None, 'scope':'session'})

    def test_catalog_search_and_question_context_preserve_all_source_tables_and_ids(self):
        first_id = self.add("검색 기능의 첫 요청 " + "긴 내용 " * 100)
        answer_id = self.add("검색 기능의 답변", "assistant")
        analysis = {"nodes": [
            {"id": "question", "kind": "item", "classification": "answered_question", "evidence": [
                {"messageId": "m1", "index": 0, "speaker": "user", "quote": "검색 기능", "verified": True}]},
            {"id": "answer", "kind": "item", "classification": "assistant_statement", "evidence": []}],
            "relations": [{"id": "answer-edge", "from": "answer", "to": "question", "type": "answers"}]}
        original = deepcopy(analysis)
        self.save(analysis)
        before = self.tables()
        with patch.object(cg, "auto_analyze", side_effect=AssertionError("Models forbidden")), \
             patch.object(cg.subprocess, "run", side_effect=AssertionError("Processes forbidden")), \
             patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
            catalog = self.reader.read()
            view = self.reader.read(SID)
            context = self.reader.question_context({SID})
            search = self.reader.search("검색 기능")
        self.assertEqual(self.tables(), before)
        self.assertEqual(analysis, original)
        self.assertEqual([message["id"] for message in view["messages"]], [first_id, answer_id])
        self.assertEqual(view["analysis"]["nodes"], analysis["nodes"])
        self.assertEqual(view["analysis"]["relations"], analysis["relations"])
        self.assertEqual(context[SID]["session"]["title"], "Example · 12345678")
        self.assertEqual(context[SID]["session"]["titleSource"], "fallback")
        self.assertIsNone(context[SID]["session"]["titleMessageId"])
        self.assertNotIn("검색 기능의 첫 요청", json.dumps(context, ensure_ascii=False))
        self.assertTrue(all("content" not in message for message in context[SID]["messages"]))
        self.assertNotIn("quote", json.dumps(context))
        self.assertEqual(search["results"][0]["sessionTitle"], catalog["sessions"][0]["title"])


if __name__ == "__main__":
    unittest.main()
