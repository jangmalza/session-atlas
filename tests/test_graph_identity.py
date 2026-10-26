"""Keep source identities and explicit relations intact during graph merging."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg


def item(node_id, title="Local storage", parent="topic-a", message_id="m1"):
    return {"id": node_id, "kind": "item", "parentId": parent, "title": title,
            "classification": "fact", "status": "none", "summary": node_id,
            "evidence": [{"messageId": message_id, "quote": "Synthetic verified evidence " + node_id,
                          "verified": True}]}


def source_ids(graph):
    return {(p["session_id"], p["source_node"]): node["id"]
            for node in graph["nodes"] for p in node["provenance"]}


class GraphIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = cg.connect(Path(temporary.name) / "graph.db")
        self.addCleanup(self.db.close)

    def save(self, sid, items, relations=(), project="Project", imported_at="2026-10-23T00:00:00Z"):
        obj = {"nodes": [{"id": "root", "kind": "root"},
                         {"id": "topic-a", "kind": "topic", "title": "Runtime"},
                         {"id": "topic-b", "kind": "topic", "title": "Installation"}, *items],
               "relations": list(relations)}
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO sessions(id,project,branch,first_seen,last_seen) "
                            "VALUES (?,?,'feature','x','x')", (sid, project))
            self.db.execute("INSERT OR REPLACE INTO session_maps(session_id,map_json,imported_at) "
                            "VALUES (?,?,?)", (sid, json.dumps(obj, ensure_ascii=False), imported_at))
        return obj

    def stored_maps(self):
        return [tuple(row) for row in self.db.execute("SELECT * FROM session_maps ORDER BY session_id")]

    def test_same_session_title_collision_preserves_both_sources_and_relation(self):
        original = self.save("s1", [item("embedded"), item("no-server", parent="topic-b", message_id="m2")],
                             [{"id": "r1", "from": "embedded", "to": "no-server", "type": "supports"}])
        stored = self.stored_maps()
        graph = cg.build_graph(self.db)
        ids = source_ids(graph)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertNotEqual(ids[("s1", "embedded")], ids[("s1", "no-server")])
        self.assertEqual([(e["from"], e["to"], e["type"], e["sessions"]) for e in graph["edges"]],
                         [(ids[("s1", "embedded")], ids[("s1", "no-server")], "supports", ["s1"])])
        evidence = {p["source_node"]: p["evidence"] for n in graph["nodes"] for p in n["provenance"]}
        self.assertEqual(evidence, {n["id"]: n["evidence"] for n in original["nodes"] if n["kind"] == "item"})
        self.assertEqual(self.stored_maps(), stored)
        self.assertEqual(cg.build_graph(self.db, previous=graph), graph)

    def test_fresh_collision_ids_do_not_depend_on_node_or_session_import_order(self):
        a, b, c = item("a"), item("b"), item("c")
        self.save("s1", [b, a], imported_at="2")
        self.save("s2", [c], imported_at="1")
        before = source_ids(cg.build_graph(self.db))
        self.save("s1", [a, b], imported_at="1")
        self.save("s2", [c], imported_at="2")
        self.assertEqual(source_ids(cg.build_graph(self.db)), before)
        self.assertEqual(len(set(before.values())), 3)

    def test_repairs_old_collision_deterministically_and_keeps_existing_id(self):
        a, b = item("a"), item("b", parent="topic-b", message_id="m2")
        relation = {"id": "r1", "from": "a", "to": "b", "type": "supports"}
        self.save("s1", [b, a], [relation])
        distinct = cg.build_graph(self.db)
        legacy = deepcopy(distinct)
        merged = deepcopy(distinct["nodes"][0])
        merged["id"] = "existing-merged-id"
        merged["provenance"] = [p for n in distinct["nodes"] for p in n["provenance"]]
        legacy["nodes"], legacy["edges"] = [merged], []
        repaired = cg.build_graph(self.db, previous=legacy)
        repaired_ids = source_ids(repaired)
        self.assertEqual(repaired_ids[("s1", "a")], "existing-merged-id")
        self.assertNotEqual(repaired_ids[("s1", "a")], repaired_ids[("s1", "b")])
        self.assertEqual(len(repaired["edges"]), 1)
        self.assertEqual(repaired["nodes"][0]["id"], "existing-merged-id")
        legacy["nodes"][0]["provenance"].reverse()
        self.save("s1", [a, b], [relation])
        self.assertEqual(source_ids(cg.build_graph(self.db, previous=legacy)), repaired_ids)
        rebuilt = cg.build_graph(self.db, previous=repaired)
        self.assertEqual(source_ids(rebuilt), repaired_ids)
        self.assertEqual(cg.build_graph(self.db, previous=rebuilt), rebuilt)

    def test_unique_cross_session_title_merge_retains_established_id(self):
        self.save("s1", [item("a")])
        initial = cg.build_graph(self.db)
        expected = cg.digest(json.dumps(["Project", "fact", "local storage"], ensure_ascii=False))[:20]
        self.assertEqual(initial["nodes"][0]["id"], expected)
        self.save("s2", [item("b", title="  ＬＯＣＡＬ storage  ")])
        graph = cg.build_graph(self.db, previous=initial)
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["nodes"][0]["id"], expected)
        self.assertEqual(len(graph["nodes"][0]["provenance"]), 2)

    def test_new_ambiguous_sources_do_not_break_existing_cross_session_binding(self):
        self.save("s1", [item("a")])
        self.save("s2", [item("c")])
        previous = cg.build_graph(self.db)
        old_id = previous["nodes"][0]["id"]
        self.save("s1", [item("b"), item("a")])
        self.save("s3", [item("d")])
        graph = cg.build_graph(self.db, previous=previous)
        ids = source_ids(graph)
        self.assertEqual(ids[("s1", "a")], old_id)
        self.assertEqual(ids[("s2", "c")], old_id)
        self.assertEqual(len(set(ids.values())), 3)
        self.assertEqual(cg.build_graph(self.db, previous=graph), graph)

    def test_translated_distinct_nodes_preserve_ids_and_existing_edge_order(self):
        a, b = item("a", title="Embedded database"), item("b", title="No server")
        relation = {"id": "r1", "from": "a", "to": "b", "type": "supports"}
        self.save("s1", [a, b], [relation])
        previous = cg.build_graph(self.db)
        a["title"] = b["title"] = "동일한 번역 제목"
        self.save("s1", [b, a], [relation])
        graph = cg.build_graph(self.db, previous=previous)
        self.assertEqual(source_ids(graph), source_ids(previous))
        self.assertEqual([n["id"] for n in graph["nodes"]], [n["id"] for n in previous["nodes"]])
        self.assertEqual(graph["edges"], previous["edges"])

    def test_new_item_cannot_steal_translated_source_id_in_same_session(self):
        a = item("a")
        self.save("s1", [a])
        previous = cg.build_graph(self.db)
        a["title"] = "로컬 저장소"
        self.save("s1", [item("0-new"), a],
                  [{"id": "r1", "from": "0-new", "to": "a", "type": "supports"}])
        graph = cg.build_graph(self.db, previous=previous)
        ids = source_ids(graph)
        self.assertEqual(ids[("s1", "a")], previous["nodes"][0]["id"])
        self.assertNotEqual(ids[("s1", "0-new")], ids[("s1", "a")])
        self.assertEqual(len(graph["edges"]), 1)


if __name__ == "__main__":
    unittest.main()
