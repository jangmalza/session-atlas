"""Protect analysis data while translating standalone viewer presentation."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_graph as cg
from localize_map import localize_html, translate_viewer_script, main as localize_main


def csp_hash(value):
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


def artifact():
    data = {"nodes": [{"id": "n1", "classification": "decision", "title": "Decision",
                       "summary": "Filters and Export", "evidence": [{"messageId": "m1",
                       "quote": "User statement: Search topics, details, quotes", "verified": True}]}],
            "relations": [], "messages": [{"id": "m1", "text": "Original 한국어 원문"}]}
    embedded = json.dumps(data, ensure_ascii=False)
    script = 'const state="decision";const label="Decision";const button="Filters";const count=`Filters (${2})`;'
    style = ':root{--font:system-ui}body{font-family:var(--font)}'
    policy = f"default-src 'none'; script-src 'sha256-{csp_hash(script)}'; style-src 'sha256-{csp_hash(style)}'; base-uri 'none'"
    html = f'''<!doctype html><html lang="en"><head>
<meta http-equiv="Content-Security-Policy" content="{policy}"><style>{style}</style></head><body>
<div id="app"><p class="boot" role="status">Loading interactive map…</p></div>
<section id="static-outline" aria-label="Static outline"><p class="so-note">This is the static outline of the map. The interactive view needs JavaScript.</p>
<span class="so-tag">Decision · Current</span><strong>Decision</strong><p>Filters and Export</p>
<span class="so-who">User, message m1:</span><q>User statement: Search topics, details, quotes</q></section>
<script type="application/json" id="cm-data">{embedded}</script><script>{script}</script></body></html>'''
    return html, embedded


class UILocalizationTests(unittest.TestCase):
    def test_original_claims_quotes_ids_and_json_are_untouched(self):
        original, embedded = artifact()
        localized = localize_html(original)
        self.assertIn(f'id="cm-data">{embedded}</script>', localized)
        self.assertIn('<strong>Decision</strong><p>Filters and Export</p>', localized)
        self.assertIn('<q>User statement: Search topics, details, quotes</q>', localized)
        self.assertIn('<html lang="ko">', localized)
        self.assertIn('결정 · 현재 유효', localized)
        self.assertIn('사용자, 메시지 m1:', localized)
        self.assertIn('const state="decision"', localized)
        self.assertIn('const label="결정"', localized)
        self.assertIn('`필터 (${2})`', localized)

    def test_localized_assets_match_csp_without_weakening_it(self):
        localized = localize_html(artifact()[0])
        script = re.search(r'<script>(.*?)</script>', localized, re.S)[1]
        style = re.search(r'<style>(.*?)</style>', localized, re.S)[1]
        self.assertIn("script-src 'sha256-" + csp_hash(script) + "'", localized)
        self.assertIn("style-src 'sha256-" + csp_hash(style) + "'", localized)
        self.assertIn("default-src 'none'", localized)
        self.assertNotIn('unsafe-inline', localized)

    def test_tampered_assets_fail_closed(self):
        original = artifact()[0].replace('const label="Decision"', 'const label="Altered"')
        with self.assertRaises(ValueError):
            localize_html(original)

    def test_relocalization_is_idempotent(self):
        once = localize_html(artifact()[0])
        self.assertEqual(localize_html(once), once)

    def test_templates_keyboard_codes_and_regex_keep_their_behavior(self):
        source = '''const key="Enter";const filter="decision";const rx=/["'`]/g;
const label=`Message ${id}${index ? ` · #${index}` : ""}`;
const toggle=`${open ? "Collapse" : "Expand"} ${title}`;'''
        result = translate_viewer_script(source)
        self.assertIn('key="Enter"', result)
        self.assertIn('filter="decision"', result)
        self.assertIn('rx=/["\'`]/g', result)
        self.assertIn('`메시지 ${id}${index ? ` · #${index}` : ""}`', result)
        self.assertIn('`${open ? "접기" : "펼치기"} ${title}`', result)

    def test_builtin_graph_keeps_data_and_placeholder_text(self):
        graph = {"projects": ["p"], "sessionCount": 1, "edges": [], "nodes": [
            {"id": "n1", "project": "p", "title": "__KO_LABELS__ Decision", "summary": "Filters",
             "classification": "decision", "status": "active", "provenance": [
                 {"session_id": "s1", "evidence": [{"messageId": "m1", "quote": "User statement", "verified": True}]}]}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "graph.html"
            cg.render_html(graph, output)
            html = output.read_text(encoding="utf-8")
        embedded = re.search(r'id="graph-data">(.*?)</script>', html, re.S)[1]
        self.assertEqual(json.loads(embedded), graph)
        self.assertIn('"decision": "결정"', html)
        self.assertIn('aria-label="프로젝트 필터"', html)

    def test_compiled_concatenations_translate_display_fragments_only(self):
        source = 'const field="messages";const line=n+" messages · "+t+" topics · "+i+" entries";'
        result = translate_viewer_script(source)
        self.assertIn('field="messages"', result)
        self.assertIn('n+"개 메시지 · "+t+"개 주제 · "+i+"개 항목"', result)

    def test_bundled_template_unicode_escapes_are_localized(self):
        source = r'const line=`${n} messages \xB7 ${t} topics \xB7 ${i} entries`;'
        result = translate_viewer_script(source)
        self.assertIn('`${n}개 메시지 · ${t}개 주제 · ${i}개 항목`', result)

    def test_file_command_preserves_crlf_inside_embedded_json(self):
        original, compact = artifact()
        pretty = json.dumps(json.loads(compact), ensure_ascii=False, indent=2).replace("\n", "\r\n")
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.html", Path(directory) / "output.html"
            source.write_bytes(original.replace(compact, pretty).encode("utf-8"))
            with patch.object(sys, "argv", ["localize_map.py", "--input", str(source), "--output", str(output)]):
                self.assertEqual(localize_main(), 0)
            result = output.read_bytes().decode("utf-8")
        self.assertIn(f'id="cm-data">{pretty}</script>', result)

    def test_timeline_links_ordered_questions_and_updates_once_without_data_edits(self):
        original, previous = artifact()
        data = {"messages": [{"id": "m1", "index": 0, "speaker": "user", "text": "PRIVATE SOURCE",
                              "timestamp": "2026-10-20T02:00:00Z"}], "nodes": [
            {"id": "q1", "kind": "item", "classification": "answered_question", "title": "확인된 질문",
             "evidence": [{"messageId": "m1", "index": 0, "speaker": "user", "verified": True, "quote": "PRIVATE"}]},
            {"id": "q2", "kind": "item", "classification": "open_question", "title": "미확정 질문", "evidence": []}]}
        embedded = json.dumps(data, ensure_ascii=False)
        localized = localize_html(original.replace(previous, embedded))
        self.assertIn(f'id="cm-data">{embedded}</script>', localized)
        self.assertIn("시간순 질문 (1)", localized)
        self.assertIn("순서 미확정 (1)", localized)
        self.assertIn('href="#node=q1"', localized)
        self.assertIn('href="#node=q2"', localized)
        self.assertEqual(localize_html(localized), localized)
        self.assertEqual(localized.count('<dialog id="cm-question-order"'), 1)
        self.assertEqual(localized.count('/* context-graph question timeline script: start */'), 1)
        self.assertEqual(localized.count('/* context-graph question timeline style: start */'), 1)


if __name__ == "__main__":
    unittest.main()
