"""The analysis process runner: codex flags, and limits that end the whole child tree without hanging."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap

# A child that spawns a grandchild inheriting its pipes, records both pids in the file named by argv[1] and then
# stays silent: the shape of codex.cmd wrapped around node, and the case a plain kill or a pipe-draining wait
# gets wrong.
SILENT_TREE = ("import os, subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
               "open(sys.argv[1], 'w').write(f'{os.getpid()} {p.pid}'); time.sleep(60)")


def alive(pid: int) -> bool:
    if os.name == "nt":
        # Bytes, not text: tasklist answers in the console code page, which need not be UTF-8.
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 check=False)
        return f'"{pid}"'.encode() in listing.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def still_alive(pids_file: Path, grace: float = 5.0) -> list[int]:
    """The pids the child recorded that are still running once ``grace`` seconds have passed."""
    pids = [int(pid) for pid in pids_file.read_text().split()]
    deadline = time.time() + grace
    while time.time() < deadline:
        left = [pid for pid in pids if alive(pid)]
        if not left:
            return []
        time.sleep(0.2)
    return [pid for pid in pids if alive(pid)]


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
        result = ap._run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                         capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr.strip()), (3, "out", "err"))
        piped = ap._run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc", capture_output=True, text=True, timeout=60)
        self.assertEqual(piped.stdout.strip(), "ABC")
        # An input larger than a pipe buffer, like an analysis prompt, must not deadlock the runner against the child.
        large = ap._run([sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"], input="x" * 400_000,
                        capture_output=True, text=True, timeout=60)
        self.assertEqual(large.stdout.strip(), "400000")
        raw = ap._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\r\\nbytes')"], capture_output=True, timeout=60)
        self.assertEqual(raw.stdout, b"\xff\r\nbytes", "without text mode the output stays untouched bytes")
        decoded = ap._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\r\\ntext')"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(decoded.stdout, "�\ntext", "text mode decodes with the given errors and translates newlines")

    def test_timeout_ends_a_child_that_keeps_the_pipes_open_and_reports_a_timeout(self):
        # The child spawns a grandchild that inherits the pipes and sleeps; a plain kill of the child would leave
        # the pipe readers waiting on the grandchild for a minute.
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

    def test_wall_clock_deadline_fires_while_the_monotonic_clock_stands_still(self):
        # The machine sleeps mid-run: the monotonic clock behind communicate(timeout=...) stops with it and the wall
        # clock jumps ahead on wake. Here the wall clock jumps an hour into a 30-minute limit as soon as the child
        # has recorded its pids, while the monotonic clock sees only a second or two.
        with tempfile.TemporaryDirectory() as tmp:
            pids = Path(tmp, "pids")

            def asleep() -> float:  # The wall clock jumps an hour once the child has recorded its pids.
                return time.time() + (3600 if pids.is_file() and pids.stat().st_size else 0)

            started = time.time()
            with patch.object(ap, "_wall_clock", asleep), patch.object(ap, "_POLL_SECONDS", 0.2), \
                 self.assertRaises(ap.AnalysisError) as error:
                ap._run_analysis_process([sys.executable, "-c", SILENT_TREE, str(pids)], stage="chunk 1/1: codex exec",
                                         capture_output=True, text=True, timeout=1800)
            self.assertLess(time.time() - started, 20)
            self.assertEqual(error.exception.code, "codex_timeout")
            self.assertIn("timed out", str(error.exception))
            self.assertEqual(still_alive(pids), [], "the child and its grandchild must both be gone")

    def test_idle_watchdog_ends_a_child_tree_that_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pids = Path(tmp, "pids")
            started = time.time()
            with self.assertRaises(ap.AnalysisError) as error:
                ap._run_analysis_process([sys.executable, "-c", SILENT_TREE, str(pids)], stage="chunk 1/1: codex exec",
                                         capture_output=True, text=True, timeout=60, idle_timeout=2)
            self.assertLess(time.time() - started, 20)
            self.assertEqual(error.exception.code, "codex_timeout")
            self.assertIn("wrote nothing for 2 s", str(error.exception)); self.assertNotIn("sleep", str(error.exception))
            self.assertEqual(still_alive(pids), [], "the child and its grandchild must both be gone")

    def test_idle_watchdog_on_a_non_codex_stage_keeps_the_generic_code(self):
        with self.assertRaises(ap.AnalysisError) as error:
            ap._run_analysis_process([sys.executable, "-c", "import time; time.sleep(30)"], stage="Conversation Map build",
                                     capture_output=True, text=True, timeout=60, idle_timeout=1)
        self.assertEqual(error.exception.code, "stage_failed")

    def test_pipe_output_and_watched_file_changes_keep_the_idle_watchdog_quiet(self):
        chatty = "import sys, time\nfor i in range(5):\n    time.sleep(0.3); print(i, flush=True)\n"
        result = ap._run([sys.executable, "-c", chatty], capture_output=True, text=True, timeout=60, idle_timeout=1.5)
        self.assertEqual((result.returncode, result.stdout.split()), (0, ["0", "1", "2", "3", "4"]))
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp, "draft.json")
            writer = f"import time\nfor i in range(5):\n    time.sleep(0.3); open({str(draft)!r}, 'a').write('x')\n"
            result = ap._run([sys.executable, "-c", writer], capture_output=True, text=True, timeout=60, idle_timeout=1.5,
                             watch_paths=[draft])
            self.assertEqual((result.returncode, result.stdout, draft.read_text()), (0, "", "xxxxx"),
                             "a growing draft file counts as progress even when the pipes stay silent")


if __name__ == "__main__":
    unittest.main()
