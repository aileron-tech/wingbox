"""The search hook and tool calls made from a script (Claude Code Code Mode): no context per call, and a hook
that cannot answer in time gives up silently, because an unfinished hook refuses a call made from code."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import freshness, hook  # noqa: E402
from tests.test_hook import _context, synthetic_graph  # noqa: E402


def payload(cwd: Path, **more: object) -> str:
    return json.dumps({"session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(cwd), "tool_name": "Grep",
                       "tool_input": {"pattern": "handle_3_0"}, **more})


class CallFromCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / ".wingbox").mkdir()
        (self.root / ".wingbox" / "index.sexp").write_text(synthetic_graph(20, 2, 2), encoding="utf-8")

    def test_a_call_made_from_a_script_gets_no_context_and_costs_no_graph_work(self) -> None:
        """/* REQ-INSTALL-040: a Grep, Glob or Read call made from a script (its tool_use_id starts with `repl_`)
        gets no context from the search hook, and the hook neither looks for the graph, nor checks or refreshes it,
        nor reads it: a script may make hundreds of calls, and each context block lands in the model's context. */"""
        self.assertIn("handle_3_0", _context(hook.run_pre_search(payload(self.root), str(self.root))))
        with unittest.mock.patch.object(hook, "find_graph", wraps=hook.find_graph) as found, \
                unittest.mock.patch.object(freshness, "ensure_graph_fresh", wraps=freshness.ensure_graph_fresh) as fresh, \
                unittest.mock.patch.object(hook, "load_graph", wraps=hook.load_graph) as loaded:
            output = hook.run_pre_search(payload(self.root, tool_use_id="repl_cfd2bd11-0000-4000-8000-a6c00a76"), str(self.root))
        self.assertEqual("", output)
        self.assertEqual((0, 0, 0), (found.call_count, fresh.call_count, loaded.call_count))

    def test_a_direct_call_an_absent_id_and_an_unknown_id_keep_the_context(self) -> None:
        """/* REQ-INSTALL-041: a call the model makes itself (tool_use_id `toolu_...`), a call with no tool_use_id
        and a call whose id has any other form get the context as before: only the observed mark of a call from
        code switches it off. */"""
        for more in ({"tool_use_id": "toolu_01V6W9nPcWcpKHyf"}, {}, {"tool_use_id": "call_42"}, {"tool_use_id": 7},
                     {"tool_use_id": "REPL_upper"}, {"tool_use_id": ""}):
            with self.subTest(more=more):
                self.assertIn("handle_3_0", _context(hook.run_pre_search(payload(self.root, **more), str(self.root))))


class TimeBudgetTest(unittest.TestCase):
    def run_hook(self, body: str) -> tuple[subprocess.CompletedProcess, float]:
        script = ("import sys, time\nsys.path.insert(0, %r)\nfrom wingbox import hook\n%s\n"
                  "sys.exit(hook.main(['pre-search']))\n") % (str(REPO / "src"), body)
        started = time.monotonic()
        done = subprocess.run([sys.executable, "-c", script], input='{"tool_name": "Grep", "tool_input": {"pattern": "x"}}',
                              capture_output=True, text=True, timeout=120)
        return done, time.monotonic() - started

    def test_a_hook_that_cannot_answer_in_time_gives_up_silently(self) -> None:
        """/* REQ-INSTALL-042: when the search hook has not answered within its own time budget (well under the
        10 s Claude Code gives it), the process ends with exit code 0 and nothing on stdout: an unfinished hook
        refuses a tool call made from code, a silent one lets it run. */"""
        self.assertLess(hook.BUDGET_SECONDS, 10 / 2 + 0.01)
        done, seconds = self.run_hook("hook.BUDGET_SECONDS = 0.5\nhook.run_pre_search = lambda *a: time.sleep(60) or 'never'")
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        self.assertLess(seconds, 30, "the stuck hook was not ended by its budget")

    def test_an_answer_within_the_budget_is_printed_whole(self) -> None:
        """/* REQ-INSTALL-043: an answer that is ready within the budget is printed whole, and the process does not
        wait for the budget to pass. */"""
        done, seconds = self.run_hook("hook.BUDGET_SECONDS = 20\nhook.run_pre_search = lambda *a: 'x' * 5000")
        self.assertEqual((0, "x" * 5000 + "\n"), (done.returncode, done.stdout))
        self.assertLess(seconds, 15)


if __name__ == "__main__":
    unittest.main()
