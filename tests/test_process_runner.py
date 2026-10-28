"""The analysis process runner: codex flags, and a timeout that ends the whole child tree without hanging."""
import subprocess
import sys
import time
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap


class ProcessRunnerTests(unittest.TestCase):
    def test_codex_command_adds_model_and_effort_only_when_chosen(self):
        schema, draft = Path("schema.json"), Path("draft.json")
        plain = ap._codex_command("codex", schema, draft, None, None)
        self.assertEqual(plain[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", plain); self.assertIn("read-only", plain); self.assertNotIn("-m", plain); self.assertNotIn("-c", plain)
        chosen = ap._codex_command("codex", schema, draft, "gpt-6-astra", "medium")
        self.assertEqual(chosen[2:6], ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="medium"'])
        self.assertEqual(chosen[6:], plain[2:], "the rest of the invocation is unchanged")

    def test_runner_returns_output_and_exit_code_like_subprocess_run(self):
        result = ap._run([sys.executable, "-c", "import sys; print('out'); sys.exit(3)"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3); self.assertEqual(result.stdout.strip(), "out")
        piped = ap._run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc", capture_output=True, text=True, timeout=60)
        self.assertEqual(piped.stdout.strip(), "ABC")

    def test_timeout_ends_a_child_that_keeps_the_pipes_open_and_reports_a_timeout(self):
        # The child spawns a grandchild that inherits the pipes and sleeps; a plain kill of the child would leave
        # communicate() waiting on the grandchild for a minute.
        script = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                  "sys.stdout.write('started'); sys.stdout.flush(); time.sleep(60)")
        started = time.time()
        with self.assertRaises(ap.AnalysisError) as error:
            ap._run_analysis_process([sys.executable, "-c", script], stage="chunk 1/1: codex exec", capture_output=True, text=True, timeout=2)
        self.assertLess(time.time() - started, 40, "the grandchild must be ended rather than waited for")
        self.assertEqual(error.exception.code, "codex_timeout")
        self.assertIn("timed out", str(error.exception)); self.assertNotIn("sleep", str(error.exception))

    def test_a_non_codex_timeout_keeps_the_generic_code(self):
        with self.assertRaises(ap.AnalysisError) as error:
            ap._run_analysis_process([sys.executable, "-c", "import time; time.sleep(30)"], stage="Conversation Map check", capture_output=True, text=True, timeout=1)
        self.assertEqual(error.exception.code, "stage_failed")


if __name__ == "__main__":
    unittest.main()
