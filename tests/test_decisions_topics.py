"""Explicit, cached topic assignment never rewrites the source conversation map."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import decisions_topics as dt


def sample_map(item_count=2, topic_count=2):
    topics = [{"id": f"topic-{i:03}", "kind": "topic", "parentId": "root",
               "title": f"분류 주제 {i}", "summary": f"이 주제의 포함 범위 {i}",
               "childIds": []} for i in range(topic_count)]
    items = [{"id": f"item-{i:03}", "kind": "item", "parentId": "topic-000",
              "title": f"질문 {i}", "summary": f"보관 방법에 관한 질문 {i}",
              "classification": "answered_question", "status": "resolved",
              "evidence": [{"messageId": f"message-{i}", "speaker": "user",
                            "quote": f"SYNTHETIC QUOTE {i}", "verified": True}]}
             for i in range(item_count)]
    if topics:
        topics[0]["childIds"] = [item["id"] for item in items]
    return {"schemaVersion": "1", "title": "검증용 대화",
            "nodes": [{"id": "root", "kind": "root", "parentId": None,
                       "childIds": [topic["id"] for topic in topics]}] + topics + items,
            "relations": ([{"id": "relation-000", "from": items[1]["id"],
                           "to": items[0]["id"], "type": "answers", "note": "저장된 관계"}]
                          if item_count >= 2 else []),
            "messages": [{"id": f"message-{i}", "speaker": "user", "index": i,
                          "text": f"SYNTHETIC FULL CONVERSATION {i}"} for i in range(item_count)]}


def response_for(payload, choice="t0", confidence=.97):
    return {"answers": [{"name": question["name"], "type": "choice",
                          "choice": choice, "confidence": confidence}
                         for question in payload["questions"]]}


class DecisionsTopicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "cache"
        self.environment = patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-unit-test-only"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def classify(self, data=None, **kwargs):
        return dt.classify_map(sample_map() if data is None else data, self.cache,
                               allow_api=True, **kwargs)

    def test_no_opt_in_does_not_touch_environment_cache_or_transport(self):
        environment = MagicMock()
        environment.get.side_effect = AssertionError("Environment checked before explicit authorization")
        with patch.object(dt, "request_decisions") as request, \
             patch.object(dt.os, "environ", environment):
            with self.assertRaises(dt.DecisionsError):
                dt.classify_map(sample_map(), self.cache)
            request.assert_not_called()
            environment.get.assert_not_called()
            self.assertFalse(self.cache.exists())

    def test_missing_key_rejects_even_with_opt_in_before_writing_cache(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(dt, "request_decisions") as request:
            with self.assertRaises(dt.DecisionsError):
                self.classify()
            request.assert_not_called()
            self.assertFalse(self.cache.exists())

    def test_assignment_preserves_source_nodes_quotes_ids_messages_and_relations(self):
        source = sample_map()
        source["sourcePath"] = "SYNTHETIC PATH SECRET"
        source["nodes"][-1]["provenance"] = {"path": "SYNTHETIC PATH SECRET"}
        original = deepcopy(source)
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            result = self.classify(source)
        self.assertEqual(source, original)
        self.assertEqual(result["schemaVersion"], "topic-assignments-1")
        self.assertEqual(result["sourceMapSha256"], dt.map_fingerprint(original))
        self.assertEqual(result["classifier"], "openai-decisions")
        self.assertEqual(result["model"], "gpt-6-luna")
        self.assertEqual(result["minConfidence"], .85)
        self.assertEqual([a["nodeId"] for a in result["assignments"]], ["item-000", "item-001"])
        self.assertTrue(all(a["status"] == "assigned" and a["topicId"] == "topic-000"
                            for a in result["assignments"]))
        payload = request.call_args.args[0]
        sent = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SYNTHETIC FULL CONVERSATION", sent)
        self.assertNotIn("SYNTHETIC PATH SECRET", sent)
        self.assertNotIn("sk-fake-unit-test-only", sent)
        self.assertIn("SYNTHETIC QUOTE", sent)
        self.assertEqual({q["type"] for q in payload["questions"]}, {"choice"})
        # Distinct batch questions must explicitly target their own input item;
        # response-matching names alone need not be visible to a provider model.
        self.assertEqual(len({q["instructions"] for q in payload["questions"]}),len(payload["questions"]))
        for question in payload["questions"]:
            self.assertIn(f"has name {question['name']}.",question["instructions"])
        values = {c["value"] for c in payload["questions"][0]["choices"]}
        self.assertEqual(values, {"t0", "t1", "new_topic", "review"})

    def test_cache_reuses_assignments_without_network_or_source_text(self):
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            first = self.classify()
            second = self.classify()
        self.assertEqual(first, second)
        self.assertEqual(request.call_count, 1)
        cached = "".join(p.read_text(encoding="utf-8") for p in self.cache.rglob("*.json"))
        self.assertTrue(cached)
        self.assertNotIn("SYNTHETIC QUOTE", cached)
        self.assertNotIn("SYNTHETIC FULL CONVERSATION", cached)
        self.assertNotIn("sk-fake-unit-test-only", cached)
        self.assertNotIn("분류 주제", cached)

    def test_opt_in_is_required_even_for_fully_cached_results(self):
        with patch.object(dt, "request_decisions", side_effect=response_for):
            self.classify()
        with patch.object(dt, "request_decisions") as request:
            with self.assertRaises(dt.DecisionsError):
                dt.classify_map(sample_map(), self.cache, allow_api=False)
            request.assert_not_called()

    def test_changed_item_reclassifies_only_that_item(self):
        source = sample_map()
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            self.classify(source)
            source["nodes"][-1]["summary"] = "변경된 질문 요약"
            self.classify(source)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(request.call_args.args[0]["questions"]), 1)

    def test_changed_topic_reclassifies_all_items(self):
        source = sample_map()
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            self.classify(source)
            source["nodes"][1]["summary"] = "변경된 주제 범위"
            self.classify(source)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(request.call_args.args[0]["questions"]), 2)

    def test_changed_model_or_policy_does_not_reuse_stale_decisions(self):
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            self.classify()
            with patch.object(dt, "MODEL", "synthetic-future-model"):
                self.classify()
            with patch.object(dt, "POLICY_VERSION", "synthetic-future-policy"):
                self.classify()
        self.assertEqual(request.call_count, 3)

    def test_corrupt_cache_cannot_silently_become_an_assignment(self):
        with patch.object(dt, "request_decisions", side_effect=response_for):
            self.classify()
        cached_files = list(self.cache.glob("*.json"))
        self.assertEqual(len(cached_files), 2)
        cached_files[0].write_text('{"choice":"forged-topic"', encoding="utf-8")
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            result = self.classify()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(request.call_args.args[0]["questions"]), 1)
        self.assertTrue(all(a["topicId"] == "topic-000" for a in result["assignments"]))

    def test_raising_threshold_reuses_decision_but_marks_ambiguous_items_for_review(self):
        with patch.object(dt, "request_decisions", side_effect=lambda p: response_for(p, confidence=.90)) as request:
            initial = self.classify(min_confidence=.85)
            cautious = self.classify(min_confidence=.95)
        self.assertEqual(request.call_count, 1)
        self.assertTrue(all(a["status"] == "assigned" for a in initial["assignments"]))
        self.assertTrue(all(a["status"] == "review" and a["topicId"] is None
                            for a in cautious["assignments"]))

    def test_low_confidence_refusal_and_explicit_review_stay_unassigned(self):
        for choice, confidence in (("t0", .84), ("new_topic", .84), ("review", .99)):
            with self.subTest(choice=choice, confidence=confidence):
                cache = Path(self.temp.name) / f"{choice}-{confidence}"
                with patch.object(dt, "request_decisions", side_effect=lambda p: response_for(p, choice, confidence)):
                    result = dt.classify_map(sample_map(), cache, allow_api=True)
                self.assertTrue(all(a["status"] == "review" and a["topicId"] is None
                                    for a in result["assignments"]))
        with patch.object(dt, "request_decisions", side_effect=lambda p: {
            "answers": [{"name": q["name"], "type": "refusal"} for q in p["questions"]]}):
            result = self.classify()
        self.assertTrue(all(a["status"] == "review" and a["topicId"] is None and a["confidence"] is None
                            for a in result["assignments"]))

    def test_new_topic_is_suggestion_and_does_not_create_a_node(self):
        source = sample_map()
        original = deepcopy(source)
        with patch.object(dt, "request_decisions", side_effect=lambda p: response_for(p, "new_topic", .99)):
            result = self.classify(source)
        self.assertEqual(source, original)
        self.assertTrue(all(a["status"] == "new_topic" and a["topicId"] is None
                            for a in result["assignments"]))

    def test_invalid_thresholds_reject_before_network(self):
        for threshold in (-.01, 1.01, float("nan"), float("inf"), True, "0.9"):
            with self.subTest(threshold=threshold), patch.object(dt, "request_decisions") as request:
                with self.assertRaises((dt.DecisionsError, ValueError)):
                    self.classify(min_confidence=threshold)
                request.assert_not_called()

    def test_too_many_topics_reject_without_changing_map_or_calling_api(self):
        source = sample_map(topic_count=254)
        original = deepcopy(source)
        with patch.object(dt, "request_decisions") as request:
            with self.assertRaises(dt.DecisionsError):
                self.classify(source)
            request.assert_not_called()
        self.assertEqual(source, original)

    def test_missing_topics_keep_items_in_review_without_network(self):
        with patch.object(dt, "request_decisions") as request:
            result = self.classify(sample_map(topic_count=0))
            request.assert_not_called()
        self.assertEqual(len(result["assignments"]), 2)
        self.assertTrue(all(a["status"] == "review" and a["topicId"] is None
                            for a in result["assignments"]))

    def test_malformed_response_never_becomes_an_assignment(self):
        def variants(payload):
            valid = response_for(payload)["answers"]
            return [None, {}, {"answers": None}, {"answers": []},
                    {"answers": valid[:-1]}, {"answers": valid + [deepcopy(valid[0])]},
                    {"answers": valid + [{"name": "unknown", "type": "choice", "choice": "t0", "confidence": .99}]},
                    {"answers": [{**a, "choice": "not-a-topic"} for a in valid]},
                    {"answers": [{**a, "type": "score"} for a in valid]},
                    {"answers": [{**a, "confidence": float("nan")} for a in valid]},
                    {"answers": [{**a, "confidence": 10 ** 1000} for a in valid]},
                    {"answers": [{**a, "confidence": 1.1} for a in valid]},
                    {"answers": [{**a, "confidence": True} for a in valid]},
                    {"answers": [{k: v for k, v in a.items() if k != "confidence"} for a in valid]}]
        dummy = {"questions": [{"name": "i0"}, {"name": "i1"}]}
        for index, invalid in enumerate(variants(dummy)):
            with self.subTest(case=index), patch.object(dt, "request_decisions", return_value=invalid):
                with self.assertRaises(dt.DecisionsError):
                    dt.classify_map(sample_map(), Path(self.temp.name) / f"invalid-{index}", allow_api=True)

    def test_duplicate_ids_and_cyclic_topic_hierarchy_reject_before_api(self):
        duplicate = sample_map()
        duplicate["nodes"].append(deepcopy(duplicate["nodes"][-1]))
        cyclic = sample_map()
        cyclic["nodes"][1]["parentId"] = cyclic["nodes"][2]["id"]
        cyclic["nodes"][2]["parentId"] = cyclic["nodes"][1]["id"]
        for source in (duplicate, cyclic):
            original = deepcopy(source)
            with self.subTest(node_count=len(source["nodes"])), \
                 patch.object(dt, "request_decisions") as request:
                with self.assertRaises(dt.DecisionsError):
                    self.classify(source)
                request.assert_not_called()
                self.assertEqual(source, original)

    def test_later_batch_failure_retains_completed_cache_and_returns_no_partial_result(self):
        source = sample_map(item_count=3)
        original = deepcopy(source)
        calls = 0

        def fail_second(payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise dt.DecisionsError("Synthetic transport failure")
            return response_for(payload)

        with patch.object(dt, "request_decisions", side_effect=fail_second):
            with self.assertRaises(dt.DecisionsError):
                self.classify(source, batch_size=1)
        self.assertEqual(source, original)
        with patch.object(dt, "request_decisions", side_effect=response_for) as request:
            result = self.classify(source, batch_size=1)
        self.assertEqual(len(result["assignments"]), 3)
        self.assertEqual(request.call_count, 2)

    def test_transport_is_fixed_https_and_rejects_authorization_redirects(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"answers":[]}'
        with patch.object(dt.urllib.request, "build_opener", return_value=opener) as build, \
             patch.dict(os.environ, {"OPENAI_BASE_URL": "https://untrusted.example.invalid/v1"}):
            self.assertEqual(dt.request_decisions({"model": dt.MODEL, "input": "synthetic input", "questions": []}),
                             {"answers": []})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/decisions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-fake-unit-test-only")
        self.assertGreater(opener.open.call_args.kwargs["timeout"], 0)
        self.assertEqual(response.read.call_args.args, (dt.MAX_RESPONSE_BYTES + 1,))
        handler = build.call_args.args[0]
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(handler.redirect_request(request, None, status, "redirect", {},
                                                          "https://untrusted.example.invalid/collect"))

    def test_transport_http_errors_do_not_log_credential_payload_or_server_body(self):
        captured = io.StringIO()
        for status in (302, 401, 429, 500):
            body = io.BytesIO(b"SYNTHETIC RESPONSE SECRET")
            failure = urllib.error.HTTPError(
                "https://synthetic.example.invalid/SYNTHETIC URL SECRET", status,
                "SYNTHETIC ERROR SECRET sk-fake-unit-test-only", {}, body)
            opener = MagicMock()
            opener.open.side_effect = failure
            with self.subTest(status=status), patch.object(dt.urllib.request, "build_opener", return_value=opener), \
                 redirect_stdout(captured), redirect_stderr(captured):
                with self.assertRaises(dt.DecisionsError) as error:
                    dt.request_decisions({"input": "SYNTHETIC REQUEST SECRET"})
                rendered = str(error.exception)
                self.assertIn(str(status), rendered)
                self.assertNotIn("SYNTHETIC", rendered)
                self.assertNotIn("sk-fake-unit-test-only", rendered)
                self.assertTrue(body.closed)
        self.assertEqual(captured.getvalue(), "")

    def test_transport_invalid_response_and_connection_errors_are_redacted(self):
        failures = [urllib.error.URLError("SYNTHETIC NETWORK SECRET sk-fake-unit-test-only"),
                    ValueError("SYNTHETIC HEADER SECRET sk-fake-unit-test-only"),
                    http.client.IncompleteRead(b"SYNTHETIC PARTIAL RESPONSE SECRET", 80)]
        for failure in failures:
            opener = MagicMock()
            opener.open.side_effect = failure
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(dt.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(dt.DecisionsError) as error:
                    dt.request_decisions({"input": "SYNTHETIC REQUEST SECRET"})
                self.assertNotIn("SYNTHETIC", str(error.exception))
                self.assertNotIn("sk-fake-unit-test-only", str(error.exception))
        for body in (b"SYNTHETIC MALFORMED SECRET", b"[]", b"\xff"):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = body
            with self.subTest(body_kind=len(body)), \
                 patch.object(dt.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(dt.DecisionsError) as error:
                    dt.request_decisions({"input": "SYNTHETIC REQUEST SECRET"})
                self.assertNotIn("SYNTHETIC", str(error.exception))

    def test_transport_missing_key_never_opens_connection(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(dt.urllib.request, "build_opener") as opener:
            with self.assertRaises(dt.DecisionsError):
                dt.request_decisions({"input": "SYNTHETIC REQUEST SECRET"})
            opener.assert_not_called()

    def test_transport_size_limit_rejects_without_echoing_response(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"SYNTHETIC RESPONSE SECRET"
        with patch.object(dt, "MAX_RESPONSE_BYTES", 10), \
             patch.object(dt.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(dt.DecisionsError) as error:
                dt.request_decisions({"input": "SYNTHETIC REQUEST SECRET"})
        self.assertNotIn("SYNTHETIC", str(error.exception))


if __name__ == "__main__":
    unittest.main()
