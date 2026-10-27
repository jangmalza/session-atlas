"""Contract checks for strict model output adapted to Conversation Map."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg


class AnalysisNormalizationTests(unittest.TestCase):
    def test_non_decision_items_do_not_keep_supersession_metadata(self):
        for classification in ("suggestion", "action_item", "completed_action", "fact", "unknown"):
            with self.subTest(classification=classification):
                node = {"key": "item", "kind": "item", "classification": classification,
                        "title": "Keep the original claim", "supersededBy": "replacement",
                        "evidence": [{"message": "m1", "quote": "Original evidence"}]}
                original = deepcopy(node)
                normalized = ap.normalize_analysis_output({"nodes": [node], "relations": []})["nodes"][0]
                self.assertNotIn("supersededBy", normalized)
                self.assertEqual(normalized["classification"], classification)
                self.assertEqual(normalized["evidence"], original["evidence"])
                self.assertEqual(node, original)

    def test_decision_replacements_and_explicit_relations_reach_validator(self):
        nodes = [{"key": classification, "kind": "item", "classification": classification,
                  "title": "Storage decision", "supersededBy": "replacement"}
                 for classification in ("decision", "superseded_decision")]
        relations = [{"from": "replacement", "to": "decision", "type": "supersedes"}]
        normalized = ap.normalize_analysis_output({"nodes": nodes, "relations": relations})
        self.assertEqual(normalized["nodes"], nodes)
        self.assertEqual(normalized["relations"], relations)


if __name__ == "__main__":
    unittest.main()
