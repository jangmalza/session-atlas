"""Conservative chronology and escaped, source-preserving question presentation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from question_timeline import question_order, render_question_timeline


def message(identifier, index, speaker="user", timestamp="2026-10-20T02:00:00Z"):
    return {"id": identifier, "index": index, "speaker": speaker, "text": "PRIVATE FULL MESSAGE", "timestamp": timestamp}


def question(identifier, references, title="질문", classification="answered_question"):
    return {"id": identifier, "kind": "item", "classification": classification, "title": title,
            "evidence": [{"messageId": mid, "index": index, "speaker": "user", "verified": True,
                          "quote": "PRIVATE QUOTE"} for mid, index in references]}


class QuestionTimelineTests(unittest.TestCase):
    def test_original_indices_order_sparse_embedded_messages_and_earliest_reference(self):
        data = {"messages": [message("late", 600), message("zero", 0), message("early", 10)],
                "nodes": [question("late-question", [("late", 600)]),
                          question("combined-question", [("early", 10), ("zero", 0), ("zero", 0)]),
                          question("early-question", [("early", 10)])]}
        original = deepcopy(data)
        order = question_order(data)
        self.assertEqual([e["id"] for e in order["confirmed"]], ["combined-question", "early-question", "late-question"])
        self.assertEqual([e["message_index"] for e in order["confirmed"]], [0, 10, 600])
        self.assertEqual(order["confirmed"][0]["user_message_count"], 2)
        self.assertEqual(data, original)

    def test_unverified_missing_or_conflicting_metadata_never_confirms_order(self):
        examples = []
        for field, value in (("verified", False), ("verified", "true"), ("speaker", "assistant"),
                             ("index", 5), ("index", True)):
            node = question("q", [("m", 0)])
            node["evidence"][0][field] = value
            examples.append({"messages": [message("m", 0)], "nodes": [node]})
        examples.extend([
            {"messages": [message("m", 0, "assistant")], "nodes": [question("q", [("m", 0)])]},
            {"messages": [], "nodes": [question("q", [("m", 0)])]},
            {"messages": [message("m", True)], "nodes": [question("q", [("m", 0)])]},
            {"messages": [message("m", 0), message("m", 1)], "nodes": [question("q", [("m", 0)])]},
        ])
        for data in examples:
            with self.subTest(data=json.dumps(data)):
                order = question_order(data)
                self.assertEqual(order["confirmed"], [])
                self.assertEqual([e["id"] for e in order["unconfirmed"]], ["q"])

    def test_source_index_without_timestamp_still_confirms_order_and_marks_ties(self):
        data = {"messages": [message("m", 9, timestamp="")],
                "nodes": [question("one", [("m", 9)]), question("two", [("m", 9)])]}
        del data["nodes"][1]["evidence"][0]["index"]
        order = question_order(data)
        self.assertEqual(len(order["confirmed"]), 2)
        self.assertTrue(all(e["same_message"] for e in order["confirmed"]))
        html = render_question_timeline(data)
        self.assertIn("시간 정보 없음", html)
        self.assertIn("메시지 안의 질문 순서는 미확정", html)

    def test_render_escapes_titles_and_links_without_copying_quotes_or_messages(self):
        data = {"messages": [message("m", 0)], "nodes": [
            question('q"x', [("m", 0)], '<img src=x onerror="alert(1)">'),
            question("unknown", [], "확인되지 않은 질문"),
            question("excluded", [("m", 0)], classification="assistant_statement")]}
        original = deepcopy(data)
        html = render_question_timeline(data)
        self.assertIn('href="#node=q%22x"', html)
        self.assertIn('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;', html)
        self.assertNotIn('<img', html)
        self.assertNotIn("PRIVATE QUOTE", html)
        self.assertNotIn("PRIVATE FULL MESSAGE", html)
        self.assertNotIn("excluded", html)
        self.assertIn("시간순 질문 (1)", html)
        self.assertIn("순서 미확정 (1)", html)
        self.assertIn('data-question-state="unconfirmed"', html)
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
