"""Failure diagnostics must never echo model output, quotes or credentials.

All subprocesses and API access are mocked; inputs are synthetic.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg


PRIVATE = "synthetic-private-conversation-and-credential"
SID = "synthetic-session"


class AnalysisLogPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        con = cg.connect(self.db)
        with con:
            con.execute("INSERT INTO sessions(id,first_seen,last_seen) VALUES (?, 'x', 'x')", (SID,))
            con.execute("""INSERT INTO messages
                (id,session_id,source_path,offset_bytes,role,source_kind,content,at)
                VALUES ('original-message',?,'synthetic',0,'user','response_item',?,'2026-01-01T00:00:00Z')""",
                (SID, PRIVATE))
        con.close()
        self.cm = self.root / "cm"
        skill = self.cm / "plugins" / "conversation-map" / "skills" / "conversation-map"
        (skill / "scripts").mkdir(parents=True)
        (skill / "references").mkdir()
        self.script = skill / "scripts" / "conversation-map.mjs"
        self.script.write_text("// synthetic, never executed", encoding="utf-8")
        (skill / "references" / "analysis.schema.json").write_text("{}", encoding="utf-8")

    def run_failure(self, target, *, failure=None, report=None):
        def fake_run(command, **kwargs):
            stage = "codex" if command[0] == "codex" else command[2]
            if stage == target:
                if failure:
                    raise failure
                return subprocess.CompletedProcess(command, 7,
                    json.dumps(report) if report is not None else PRIVATE, PRIVATE)
            if stage == "codex":
                Path(command[command.index("-o") + 1]).write_text(
                    json.dumps({"nodes": [], "relations": []}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        # Each classifier uses a distinct cache version; no real API preflight.
        for classifier in ("codex", "decisions"):
            with self.subTest(stage=target, classifier=classifier):
                stdout, stderr = io.StringIO(), io.StringIO()
                args = ["--db", str(self.db), "analyze", "--session", SID,
                        "--cm-root", str(self.cm), "--allow-model", "--topic-classifier", classifier]
                if classifier == "decisions":
                    args.append("--allow-api")
                with patch.object(cg.shutil, "which", side_effect=lambda name: name), \
                     patch.object(ap, "_decision_preflight"), \
                     patch.object(ap, "_run", side_effect=fake_run), \
                     patch("decisions_topics.request_decisions") as api, \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(cg.main(args), 1)
                api.assert_not_called()
                self.assertNotIn(PRIVATE, stdout.getvalue() + stderr.getvalue())
                self.assertIn("failed", stderr.getvalue())
        con = cg.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT content FROM messages").fetchone()[0], PRIVATE)
            self.assertEqual(con.execute("SELECT count(*) FROM session_maps").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM analyses").fetchone()[0], 0)
        finally:
            con.close()

    def test_codex_failure_omits_provider_stdout_and_stderr(self):
        self.run_failure("codex")

    def test_check_non_json_failure_omits_diagnostics(self):
        self.run_failure("check")

    def test_build_failure_omits_diagnostics(self):
        self.run_failure("build")

    def test_structured_failure_omits_messages_ids_and_unknown_codes(self):
        self.run_failure("check", report={"errors": [
            {"code": "relation-unknown-node", "message": PRIVATE, "node": PRIVATE},
            {"code": PRIVATE, "message": PRIVATE}]})

    def test_timeouts_and_process_launch_errors_omit_exception_payloads(self):
        for stage in ("codex", "check", "build"):
            for error in (subprocess.TimeoutExpired([PRIVATE], 5, output=PRIVATE, stderr=PRIVATE),
                          subprocess.CalledProcessError(7, [PRIVATE], output=PRIVATE, stderr=PRIVATE),
                          OSError(PRIVATE)):
                with self.subTest(stage=stage, error=type(error).__name__):
                    # Avoid a prior synthetic draft bypassing the Codex stage.
                    with patch.object(cg.Path, "is_file", autospec=True, side_effect=lambda p: False
                                      if "analysis-cache" in p.parts else p.exists()):
                        self.run_failure(stage, failure=error)

    def test_check_exception_and_traceback_retain_only_known_error_codes(self):
        draft = self.root / "draft.json"
        draft.write_text('{"nodes": []}', encoding="utf-8")
        result = subprocess.CompletedProcess([], 2, json.dumps({"errors": [
            {"code": "relation-unknown-node", "message": PRIVATE, "node": PRIVATE},
            {"code": PRIVATE, "message": PRIVATE}]}), PRIVATE)
        with patch.object(ap, "_run", return_value=result):
            try:
                ap.check_with_attribution_repair(self.script, self.root / "transcript.json", [draft])
            except RuntimeError as error:
                message = str(error)
                formatted = traceback.format_exc()
            else:
                self.fail("Invalid relations must remain fatal")
        self.assertIn("relation-unknown-node", message)
        self.assertIn("2 total", message)
        self.assertNotIn(PRIVATE, message + formatted)

    def test_unexpected_analysis_error_is_sanitized_at_cli_boundary(self):
        for error in (ValueError(PRIVATE), KeyError(PRIVATE), TypeError(PRIVATE), OSError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(ap, "auto_analyze", side_effect=error), \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    result = cg.main(["--db", str(self.db), "analyze", "--session", SID,
                                      "--cm-root", str(self.cm), "--allow-model"])
                self.assertEqual(result, 1)
                self.assertNotIn(PRIVATE, stdout.getvalue() + stderr.getvalue())

    def test_malformed_new_draft_is_handled_without_echoing_values(self):
        invalid_drafts = [
            [PRIVATE], {"nodes": PRIVATE}, {"nodes": [PRIVATE]},
            {"nodes": [], "relations": PRIVATE}, {"nodes": [], "relations": [PRIVATE]},
        ]
        for draft in invalid_drafts:
            with self.subTest(shape=type(draft).__name__):
                def fake_run(command, **kwargs):
                    self.assertEqual(command[0], "codex")
                    Path(command[command.index("-o") + 1]).write_text(json.dumps(draft), encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, PRIVATE, PRIVATE)
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(cg.shutil, "which", side_effect=lambda name: name), \
                     patch.object(ap, "_run", side_effect=fake_run) as process, \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    result = cg.main(["--db", str(self.db), "analyze", "--session", SID,
                                      "--cm-root", str(self.cm), "--allow-model"])
                self.assertEqual(result, 1)
                self.assertEqual(process.call_count, 1)
                self.assertIn("invalid analysis JSON", stderr.getvalue())
                self.assertNotIn(PRIVATE, stdout.getvalue() + stderr.getvalue())

    def test_malformed_cached_draft_fails_safely_without_model_retry(self):
        self.run_failure("check")
        for path in (self.root / "analysis-cache").rglob("*.json"):
            path.write_text(json.dumps({"nodes": [], "relations": [PRIVATE]}), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cg.shutil, "which", side_effect=lambda name: name), \
             patch.object(ap, "_run") as process, \
             patch("decisions_topics.request_decisions") as api, \
             redirect_stdout(stdout), redirect_stderr(stderr):
            result = cg.main(["--db", str(self.db), "analyze", "--session", SID,
                              "--cm-root", str(self.cm), "--allow-model"])
        self.assertEqual(result, 1)
        self.assertIn("invalid cached analysis JSON", stderr.getvalue())
        self.assertNotIn(PRIVATE, stdout.getvalue() + stderr.getvalue())
        process.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
