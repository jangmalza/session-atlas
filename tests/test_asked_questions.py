"""Items the user asked as a question are found from their own quotes, by marks and endings only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asked_questions import asked_by_user, reads_as_question, speakers_of


class ReadsAsQuestionTests(unittest.TestCase):
    def test_question_marks_and_korean_interrogative_endings_count(self):
        for text in ("프레임 몇정도 뽑을 수 있을까?", "내가 수정했는데 어때보여?", "Is this right?", "맞아?!", "왜 이렇게 나올까요",
                     "이게 되나요", "모델링 상태 어때보여", "이건 뭐야", "진짜인가요", "분류 잘되어있어? 아니면 정리해줘",
                     "가능할까ㅠ", "되는지 궁금해요?", "링크 봐줘 https://x.example/a?b=1 어때?"):
            with self.subTest(text=text):
                self.assertTrue(reads_as_question(text))

    def test_statements_requests_links_and_code_do_not_count(self):
        for text in ("분리 잘되는 것 같아", "진행해줘", "그러니까 안 되니까", "집에 가요", "https://store.example.com/pla-wood?id=5502",
                     "`SELECT ? FROM x`", "", None, 3, "출력하고있는데 손부분이 잘린 것 같아..", "확인해줘 ok"):
            with self.subTest(text=text):
                self.assertFalse(reads_as_question(text))


class AskedByUserTests(unittest.TestCase):
    @staticmethod
    def node(classification, quote, **evidence):
        return {"id": "n", "kind": "item", "classification": classification,
                "evidence": [{"messageId": "m1", "quote": quote, "verified": True, **evidence}]}

    def test_only_items_outside_question_and_action_classes_with_a_verified_user_question_quote(self):
        speakers = {"m1": "user"}
        for classification in ("unknown", "inference", "suggestion", "user_statement", "fact", "assistant_statement"):
            with self.subTest(classification=classification):
                self.assertTrue(asked_by_user(self.node(classification, "왜 이럴까?"), speakers))
        for classification in ("decision", "superseded_decision", "rejected_proposal", "completed_action", "action_item",
                               "open_question", "answered_question"):
            with self.subTest(classification=classification):
                self.assertFalse(asked_by_user(self.node(classification, "왜 이럴까?"), speakers))
        self.assertFalse(asked_by_user(self.node("unknown", "왜 이럴까?"), {"m1": "assistant"}))
        self.assertFalse(asked_by_user(self.node("unknown", "왜 이럴까?", speaker="assistant"), speakers), "the quote's own speaker wins")
        self.assertTrue(asked_by_user(self.node("unknown", "왜 이럴까?", speaker="user"), {}))
        self.assertFalse(asked_by_user(self.node("unknown", "왜 이럴까?", verified=False), speakers))
        self.assertFalse(asked_by_user(self.node("unknown", "그렇게 했어"), speakers))
        self.assertFalse(asked_by_user({"id": "t", "kind": "topic", "classification": "unknown"}, speakers))
        self.assertFalse(asked_by_user(None, speakers))

    def test_speakers_come_from_the_analysis_message_list_and_repeated_ids_are_dropped(self):
        analysis = {"messages": [{"id": "m1", "speaker": "user"}, {"id": "m2", "speaker": "assistant"},
                                 {"id": "m3", "speaker": "user"}, {"id": "m3", "speaker": "assistant"}, "junk", {"id": 5}]}
        self.assertEqual(speakers_of(analysis), {"m1": "user", "m2": "assistant"})
        self.assertEqual(speakers_of({}), {})
        self.assertEqual(speakers_of(None), {})


if __name__ == "__main__":
    unittest.main()
