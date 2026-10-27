"""Answer evidence cards reuse individually checked quotes without changing graphs."""
from copy import deepcopy
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
from topic_display import set_item_display, display_revision, map_fingerprint, read_topic_display


SID = "98765432-1234-4234-8234-123456789abc"
ANSWER = "PRIVATE FULL PREFIX. First valid answer. Second valid fragment. PRIVATE FULL SUFFIX."


class AnswerEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "collection.db"
        self.con = cg.connect(self.database)
        self.addCleanup(self.con.close)
        with self.con:
            self.con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                             (SID, "C:/Example", "Example", "test", "2026-10-23T00:00:00Z", "", ""))
        self.add("raw-question", "user", "A question requiring an answer", 10)
        self.add("raw-neighbor", "assistant", "An adjacent assistant message is not an answer link", 20)
        self.add("raw-answer", "assistant", ANSWER, 30)
        self.add("raw-next", "user", "A later independent request", 40)
        rows = cg.effective_messages(self.con, SID)
        self.analysis = {"title": "Stored analysis title", "source": {"messageCount": len(rows)},
            "messages": [{"id": f"m{index+1}", "index": index, "speaker": row["role"],
                          "text": row["content"], "timestamp": row["at"]} for index, row in enumerate(rows)],
            "nodes": [{"id": "question", "kind": "item", "classification": "answered_question",
                       "status": "answered", "title": "Stored question title", "evidence": [
                           {"messageId": "m1", "index": 0, "speaker": "user", "quote": "A question", "verified": True},
                           self.evidence("First valid answer.")]}],
            "relations": [{"id": "existing-related", "from": "question", "to": "existing-item", "type": "related"}]}
        self.reader = TimelineReader(self.database)
        self.save()

    @staticmethod
    def evidence(quote, **changes):
        return {"messageId": "m3", "index": 2, "speaker": "assistant", "quote": quote, "verified": True, **changes}

    def add(self, identifier, role, text, second):
        with self.con:
            self.con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (
                identifier, SID, "synthetic.jsonl", second * 100, role, "response_item", text,
                f"2026-10-23T00:00:{second:02d}Z"))

    def save(self, *, baseline=True):
        fingerprint = cg.digest(cg.serialize_transcript(cg.transcript(self.con, SID))) if baseline else ""
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO session_maps VALUES (?,?,?,?)",
                             (SID, fingerprint, json.dumps(self.analysis), "2026-10-23T00:01:00Z"))

    def groups(self):
        return self.reader.read(SID)["analysis"]["assistantEvidence"]

    def test_answer_display_title_is_bound_to_verified_evidence_without_new_nodes_or_quotes(self):
        before=self.reader.read(SID);original=self.database.read_bytes()
        result=set_item_display(self.database,session=SID,item='question',display_title='확인된 답변 표시 제목',
            evidence_message='raw-answer',source_map_sha256=map_fingerprint(self.analysis),expected_revision=display_revision(None))
        self.assertEqual(result['originalTitle'],'어시스턴트 원문 #3')
        self.assertEqual(result['evidenceTitles']['question']['raw-answer'],'확인된 답변 표시 제목')
        after=self.reader.read(SID)
        self.assertEqual(after['analysis']['nodes'],before['analysis']['nodes'])
        self.assertEqual(after['analysis']['relations'],before['analysis']['relations'])
        self.assertEqual(after['analysis']['assistantEvidence'],before['analysis']['assistantEvidence'])
        self.assertEqual(after['messages'],before['messages'])
        self.assertEqual(self.database.read_bytes(),original)
        set_item_display(self.database,session=SID,item='question',display_title=None,evidence_message='raw-answer',
            source_map_sha256=map_fingerprint(self.analysis),expected_revision=result['topicDisplay']['revision'])
        self.assertNotIn('evidenceTitles',read_topic_display(self.database)[SID])

    def test_user_or_neighboring_assistant_messages_cannot_gain_answer_display_labels(self):
        for message in ('raw-question','raw-neighbor','raw-next','absent'):
            with self.subTest(message=message),self.assertRaises(KeyError):
                set_item_display(self.database,session=SID,item='question',display_title='Unjustified label',
                    evidence_message=message,source_map_sha256=map_fingerprint(self.analysis),expected_revision=display_revision(None))
        self.assertFalse(self.database.with_suffix('.topic-display.json').exists())

    def test_question_and_answer_evidence_titles_can_be_restored_independently(self):
        first=set_item_display(self.database,session=SID,item='question',display_title='질문 이름',
            source_map_sha256=map_fingerprint(self.analysis),expected_revision=display_revision(None))
        second=set_item_display(self.database,session=SID,item='question',display_title='답변 이름',evidence_message='raw-answer',
            source_map_sha256=map_fingerprint(self.analysis),expected_revision=first['topicDisplay']['revision'])
        third=set_item_display(self.database,session=SID,item='question',display_title=None,
            source_map_sha256=map_fingerprint(self.analysis),expected_revision=second['topicDisplay']['revision'])
        self.assertNotIn('displayTitle',third['itemLabels']['question'])
        self.assertEqual(third['evidenceTitles']['question']['raw-answer'],'답변 이름')

    def tables(self):
        return {table: [tuple(row) for row in self.con.execute("SELECT * FROM " + table)]
                for table in ("sessions", "sources", "messages", "session_maps", "analyses", "topic_assignments")}

    def test_exact_stored_assistant_evidence_is_used_without_guessing_adjacent_response(self):
        group = self.groups()["question"][0]
        self.assertEqual(group, {"messageId": "raw-answer", "index": 2, "sourceMessageId": "m3",
                                 "sourceMessageIds": ["m3"], "quotes": ["First valid answer."], "evidenceCount": 1})
        self.assertNotEqual(group["messageId"], "raw-neighbor")
        self.assertNotIn("PRIVATE FULL", json.dumps(group))

    def test_every_quote_is_checked_instead_of_reusing_another_quotes_valid_anchor(self):
        self.analysis["nodes"][0]["evidence"].extend([
            self.evidence("Second valid fragment.", verified=False),
            self.evidence("Second valid fragment.", verified=1),
            self.evidence("Second valid fragment.", speaker="user"),
            self.evidence("Second valid fragment.", index=1),
            self.evidence("Second valid fragment.", index=True),
            self.evidence("Second valid fragment.", messageId="unknown-source"),
            self.evidence("A quote that is absent from the actual source"),
            self.evidence(""),
            {"verified": True, "messageId": "m3", "quote": None},
        ])
        self.save()
        group = self.groups()["question"][0]
        self.assertEqual(group["quotes"], ["First valid answer."])
        self.assertEqual(group["evidenceCount"], 1)

    def test_identical_quotes_are_deduplicated_and_distinct_quotes_share_one_message_card(self):
        self.analysis["nodes"][0]["evidence"].extend([
            self.evidence("First valid answer."), self.evidence("Second valid fragment."),
            self.evidence("Second valid fragment.")])
        self.save()
        groups = self.groups()["question"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["quotes"], ["First valid answer.", "Second valid fragment."])
        self.assertEqual(groups[0]["evidenceCount"], 2)

    def test_source_aliases_resolving_to_one_original_message_keep_all_source_ids(self):
        self.analysis["messages"].append({**self.analysis["messages"][2], "id": "chunk2_m1"})
        self.analysis["nodes"][0]["evidence"].append(self.evidence("Second valid fragment.", messageId="chunk2_m1"))
        self.save()
        groups = self.groups()["question"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["sourceMessageId"], "m3")
        self.assertEqual(groups[0]["sourceMessageIds"], ["m3", "chunk2_m1"])
        self.assertEqual(groups[0]["evidenceCount"], 2)

    def test_inserted_earlier_message_relocates_only_by_checked_full_source_identity(self):
        original = self.groups()["question"][0]
        self.add("raw-inserted", "assistant", "An unrelated earlier message", 5)
        moved = self.groups()["question"][0]
        self.assertEqual(original["index"], 2)
        self.assertEqual(moved["index"], 3)
        self.assertEqual(moved["messageId"], original["messageId"])
        self.assertEqual(moved["sourceMessageId"], "m3")
        self.assertEqual(moved["quotes"], original["quotes"])

    def test_matching_quote_alone_cannot_replace_full_source_identity(self):
        self.analysis["messages"][2]["text"] = "A different full message with First valid answer."
        self.save()
        self.assertEqual(self.groups(), {})

    def test_ambiguous_duplicate_source_ids_and_timestamp_mismatch_are_rejected(self):
        original = deepcopy(self.analysis)
        self.analysis["messages"].append(deepcopy(self.analysis["messages"][2]))
        self.save()
        self.assertEqual(self.groups(), {})
        self.analysis = original
        self.analysis["messages"][2]["timestamp"] = "2026-10-22T00:00:00Z"
        self.save()
        self.assertEqual(self.groups(), {})

    def test_matching_duplicate_messages_without_baseline_or_timestamp_remain_unplaced(self):
        self.add("raw-duplicate", "assistant", ANSWER, 50)
        del self.analysis["messages"][2]["timestamp"]
        self.save(baseline=False)
        self.assertEqual(self.groups(), {})

    def test_legacy_source_less_quotes_require_verified_transcript_baseline(self):
        del self.analysis["messages"]
        self.save()
        self.assertEqual(self.groups()["question"][0]["messageId"], "raw-answer")
        self.save(baseline=False)
        self.assertEqual(self.groups(), {})

    def test_open_questions_and_unanswered_status_are_not_presented_as_answers(self):
        node = deepcopy(self.analysis["nodes"][0])
        for classification, status in (("open_question", "open"), ("answered_question", "open"),
                                       ("assistant_statement", "answered")):
            with self.subTest(classification=classification, status=status):
                self.analysis["nodes"][0] = {**node, "classification": classification, "status": status}
                self.save()
                self.assertEqual(self.groups(), {})

    def test_offline_context_contains_only_navigation_metadata_and_counts(self):
        live = self.groups()["question"][0]
        context = self.reader.question_context({SID})
        group = context[SID]["analysis"]["assistantEvidence"]["question"][0]
        self.assertEqual(group, {key: value for key, value in live.items() if key != "quotes"})
        encoded = json.dumps(context)
        self.assertNotIn("First valid answer.", encoded)
        self.assertNotIn("PRIVATE FULL", encoded)
        self.assertNotIn('"quotes"', encoded)
        self.assertTrue(all("content" not in message for message in context[SID]["messages"]))

    def test_views_preserve_all_original_rows_analysis_quotes_ids_and_relations_without_external_calls(self):
        before = self.tables()
        original = deepcopy(self.analysis)
        with patch.object(ap, "auto_analyze", side_effect=AssertionError("Models forbidden")), \
             patch.object(cg.subprocess, "run", side_effect=AssertionError("Processes forbidden")), \
             patch("socket.create_connection", side_effect=AssertionError("APIs forbidden")):
            view = self.reader.read(SID)
            self.reader.question_context({SID})
            self.reader.search("answer")
            self.reader.read()
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.analysis, original)
        self.assertEqual(view["analysis"]["nodes"], original["nodes"])
        self.assertEqual(view["analysis"]["relations"], original["relations"])
        self.assertEqual(view["messages"][2]["content"], ANSWER)

    def test_unchanged_read_reuses_validated_groups_and_changed_map_refreshes_them(self):
        first = self.groups()
        with patch("session_timeline._anchors", side_effect=AssertionError("Unchanged analysis should be cached")):
            self.assertEqual(self.groups(), first)
            self.reader.question_context({SID})
        self.analysis["nodes"][0]["evidence"].append(self.evidence("Second valid fragment."))
        self.save()
        self.assertEqual(self.groups()["question"][0]["evidenceCount"], 2)


if __name__ == "__main__":
    unittest.main()
