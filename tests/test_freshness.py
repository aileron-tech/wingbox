"""Graph freshness: the real hooks run as Claude Code runs them (a process, the hook input as JSON on stdin)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli, freshness, mcp_server  # noqa: E402

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def order_total(items):\n    return sum(items)\n",
    "shop/cart.py": "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items)\n",
    "shop/notes.py": "def note():\n    return 'n'\n",
    "web/src/util.ts": "export function fmt(n: number): string { return String(n); }\n",
    "web/src/view.ts": "export function show(n: number) { return n; }\n",
}


# A home folder of the tests: the search hook keeps its once-per-session marks under ~/.wingbox.
_HOME = tempfile.TemporaryDirectory()


def _environment(**extra: str) -> dict[str, str]:
    environment = dict(os.environ, HOME=_HOME.name, USERPROFILE=_HOME.name)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_HOOK_WAIT"] = "120"  # deterministic under load; the timeout rule has its own test
    environment.update(extra)
    return environment


class FreshnessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(FILES)

    def write(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def build(self, *extra: str) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["build", str(self.root), *extra]), 0)

    def hook(self, subcommand: str, payload: dict, **environment: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-m", "wingbox.cli", "hook", subcommand],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self.root,
            env=_environment(**environment),
            timeout=300,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def edited(self, name: str, text: str, tool: str = "Edit") -> None:
        """What Claude Code does for an Edit or Write: change the file, then run the PostToolUse hook."""
        self.write({name: text})
        path = str(self.root / name)
        self.assertEqual(
            self.hook("dirty", {
                "hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(self.root),
                "tool_input": {"file_path": path}, "tool_response": {"filePath": path, "success": True},
            }),
            "",
        )

    def context(self, name: str, **environment: str) -> str:
        """The context the PreToolUse hook gives for a Read of name."""
        output = self.hook("pre-search", {
            "hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": str(self.root),
            "tool_input": {"file_path": str(self.root / name)},
        }, **environment)
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else ""

    def grep(self, pattern: str) -> str:
        output = self.hook("pre-search", {
            "hook_event_name": "PreToolUse", "tool_name": "Grep", "cwd": str(self.root),
            "tool_input": {"pattern": pattern},
        })
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else ""

    def state(self) -> freshness.State:
        return freshness.read_state(str(self.root))

    def wait_until_fresh(self) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if freshness.is_fresh(self.state()) and not freshness.refresh_running(str(self.root)):
                return
            time.sleep(0.1)
        self.fail("the refresh did not finish")


class ClaudeCodeEditsTest(FreshnessTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.build()

    def test_an_edit_that_adds_an_import_is_in_the_graph_before_its_next_use(self) -> None:
        """/* REQ-FRESH-013: after an Edit that adds an import (PostToolUse `hook dirty`), the next PreToolUse context must already count the new dependent; the marks are cleared. */"""
        self.assertIn(":dependents 0", self.context("shop/notes.py"))
        self.edited("shop/cart.py", "from shop.pricing import order_total\nfrom shop.notes import note\n\n\ndef pay(items):\n    return order_total(items) + note()\n")
        self.assertTrue(self.state().dirty)
        self.assertIn(":dependents 1", self.context("shop/notes.py"))
        self.assertTrue(freshness.is_fresh(self.state()))

    def test_a_written_module_appears_and_a_deleted_one_disappears(self) -> None:
        """/* REQ-FRESH-014: a module created by Write must be in the graph at the next use; a deleted module (Bash rm) must be gone from it. */"""
        self.edited("shop/refunds.py", "from shop.pricing import order_total\n\n\ndef refund(items):\n    return -order_total(items)\n", tool="Write")
        self.assertIn("(wingbox-file shop/refunds.py :module shop/refunds", self.context("shop/refunds.py"))
        self.assertIn(":dependents 2", self.context("shop/pricing.py"))
        (self.root / "shop" / "refunds.py").unlink()
        self.assertIn(":dependents 1", self.context("shop/pricing.py"))
        self.assertNotIn("refunds", self.grep("refund"))

    def test_a_renamed_module_is_counted_under_its_new_path_only(self) -> None:
        """/* REQ-FRESH-015: after a rename (git mv) and the importer's edit, the old path must be gone and the new one counted. */"""
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "mv", "shop/pricing.py", "shop/prices.py"], check=True)
        self.edited("shop/cart.py", "from shop.prices import order_total\n\n\ndef pay(items):\n    return order_total(items)\n")
        self.assertIn(":dependents 1", self.context("shop/prices.py"))
        self.assertNotIn("shop/pricing", self.grep("pric"))

    def test_several_edits_take_one_refresh_and_an_unchanged_project_none(self) -> None:
        """/* REQ-FRESH-016: three edits before a search must be refreshed once; searches on an unchanged project must start no refresh at all. */"""
        for count in range(3):
            self.context("shop/cart.py")
        self.assertFalse((self.root / ".wingbox" / freshness.REFRESH_LOG_NAME).exists())
        self.assertEqual(self.state().counters.get("refreshes_partial", 0), 0)
        for name in ("shop/notes.py", "web/src/util.ts", "web/src/view.ts"):
            self.edited(name, (self.root / name).read_text(encoding="utf-8") + "\n// edited\n" if name.endswith(".ts") else "def note():\n    return 'm'\n")
        self.context("shop/cart.py")
        state = self.state()
        self.assertEqual(state.counters.get("refreshes_partial", 0) + state.counters.get("refreshes_build", 0), 1)
        self.assertEqual(state.counters.get("marks"), 3)
        self.assertTrue(freshness.is_fresh(state))

    def test_parallel_edit_hooks_lose_no_change(self) -> None:
        """/* REQ-FRESH-017: eight PostToolUse hooks running at once must leave all eight paths marked. */"""
        names = [f"shop/m{count}.py" for count in range(8)]
        self.write({name: "X = 1\n" for name in names})
        inputs = []
        for name in names:
            handle = tempfile.TemporaryFile()
            handle.write(json.dumps({"tool_name": "Write", "cwd": str(self.root), "tool_input": {"file_path": str(self.root / name)}}).encode())
            handle.seek(0)
            self.addCleanup(handle.close)
            inputs.append(handle)
        processes = [
            subprocess.Popen(
                [sys.executable, "-m", "wingbox.cli", "hook", "dirty"],
                stdin=handle, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=self.root, env=_environment(),
            )
            for handle in inputs
        ]
        for process in processes:
            self.assertEqual(process.wait(timeout=300), 0)
        self.assertEqual(sorted(self.state().changed_files), sorted(names))

    def test_a_failed_refresh_keeps_the_marks_says_so_and_is_not_repeated(self) -> None:
        """/* REQ-FRESH-018: when the refresh fails, the marks must stay, the hook must give no context from the old graph but `(wingbox-graph-stale :reason refresh-failed ...)`, and no second refresh may run until something changes; a later change refreshes again. */"""
        cache = self.root / ".wingbox/cache.json"
        cache.unlink()
        cache.mkdir()  # the builder cannot write its cache: the build fails
        self.edited("shop/notes.py", "def note():\n    return 'x'\n")
        self.assertIn("(wingbox-graph-stale :reason refresh-failed", self.context("shop/notes.py"))
        self.assertIn("shop/notes.py", self.state().changed_files)
        log = (self.root / ".wingbox" / freshness.REFRESH_LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("(wingbox-graph-stale :reason refresh-failed", self.context("shop/cart.py"))
        self.assertEqual(log, (self.root / ".wingbox" / freshness.REFRESH_LOG_NAME).read_text(encoding="utf-8"))
        cache.rmdir()
        self.edited("shop/notes.py", "def note():\n    return 'y'\n")
        self.assertIn("(wingbox-file shop/notes.py", self.context("shop/notes.py"))
        self.assertTrue(freshness.is_fresh(self.state()))

    def test_a_refresh_longer_than_the_wait_skips_the_context_and_finishes_in_the_background(self) -> None:
        """/* REQ-FRESH-019: with WINGBOX_HOOK_WAIT=0 the hook must answer `(wingbox-graph-stale :reason refresh-timeout ...)` without context and keep the marks; the refresh it started must then publish the change and clear them. */"""
        self.edited("shop/cart.py", "from shop.notes import note\n\n\ndef pay(items):\n    return note()\n")
        self.assertIn("(wingbox-graph-stale :reason refresh-timeout", self.context("shop/notes.py", WINGBOX_HOOK_WAIT="0"))
        self.wait_until_fresh()
        self.assertIn(":dependents 1", self.context("shop/notes.py"))

    def test_edits_outside_the_analysed_sources_mark_nothing(self) -> None:
        """/* REQ-FRESH-020: an Edit of a file that is not a source or resolution config (README.md), outside the project, or a failed Write that left the file as it was, must not mark the graph. */"""
        self.edited("README.md", "# shop\n")
        outside = Path(tempfile.mkdtemp()) / "x.py"
        outside.write_text("X = 1\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        self.hook("dirty", {"tool_name": "Write", "cwd": str(self.root), "tool_input": {"file_path": str(outside)}})
        self.hook("dirty", {
            "tool_name": "Write", "cwd": str(self.root), "tool_input": {"file_path": str(self.root / "shop/cart.py")},
            "tool_response": {"success": False, "error": "denied"},
        })
        self.assertFalse(self.state().changed_files)
        self.assertTrue(freshness.is_fresh(self.state()))

    def test_the_mcp_tools_answer_from_the_newest_graph(self) -> None:
        """/* REQ-FRESH-021: an MCP tool called after an edit must answer from the refreshed graph (its cache reloads the new file). */"""
        server = mcp_server.Server(str(self.root))
        before = mcp_server.TOOLS_BY_NAME["analyze_impact"].run(server, {"target": "shop/notes.py"})
        self.assertNotIn("shop/cart", before.texts[0])
        self.edited("shop/cart.py", "from shop.notes import note\n\n\ndef pay(items):\n    return note()\n")
        with redirect_stderr(io.StringIO()):
            after = mcp_server.TOOLS_BY_NAME["analyze_impact"].run(server, {"target": "shop/notes.py"})
        self.assertIn("shop/cart", after.texts[0])
        self.assertNotIn("wingbox-graph-stale", after.texts[0])

    def test_a_query_command_refreshes_and_says_when_it_could_not(self) -> None:
        """/* REQ-FRESH-022: `wingbox impact` after an outside change must answer from the refreshed graph; with WINGBOX_AUTO_REFRESH=0 it must warn on stderr that the graph may miss changes. */"""
        self.write({"shop/cart.py": "from shop.notes import note\n\n\ndef pay(items):\n    return note()\n"})
        command = [sys.executable, "-m", "wingbox.cli", "impact", "shop/notes.py"]
        off = subprocess.run(command, capture_output=True, text=True, cwd=self.root, env=_environment(WINGBOX_AUTO_REFRESH="0"))
        self.assertIn("wingbox-graph-stale :reason auto-refresh-off", off.stderr)
        on = subprocess.run(command, capture_output=True, text=True, cwd=self.root, env=_environment())
        self.assertIn("shop/cart", on.stdout)
        self.assertNotIn("stale", on.stderr)


class OutsideChangesTest(FreshnessTestCase):
    """Changes made by editors, git, scripts or Bash: no PostToolUse hook runs, the file-system check finds them."""

    def setUp(self) -> None:
        super().setUp()
        self.write({".gitignore": ".wingbox/\n"})
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base"],
            check=True,
        )
        self.build()

    def test_a_change_made_through_bash_is_found(self) -> None:
        """/* REQ-FRESH-023: a source changed and a source created without any hook (a shell command) must be in the graph at the next PreToolUse. */"""
        subprocess.run(["sh", "-c", "printf 'from shop.notes import note\\n' >> shop/pricing.py && printf 'import shop.notes\\n' > shop/extra.py"], cwd=self.root, check=True)
        self.assertIn(":dependents 2", self.context("shop/notes.py"))
        self.assertIn("(wingbox-file shop/extra.py", self.context("shop/extra.py"))

    def test_git_checkout_and_a_git_rename_are_found(self) -> None:
        """/* REQ-FRESH-024: a git checkout of a branch that changes, adds and removes sources, and a git mv, must be in the graph at the next PreToolUse. */"""
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(git + ["checkout", "-qb", "feature"], check=True)
        self.write({"shop/cart.py": "from shop.notes import note\n\n\ndef pay(items):\n    return note()\n", "shop/audit.py": "import shop.notes\n"})
        subprocess.run(git + ["rm", "-q", "web/src/view.ts"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "feature"], check=True)
        subprocess.run(git + ["checkout", "-q", "-"], check=True)
        self.assertIn(":dependents 0", self.context("shop/notes.py"))
        subprocess.run(git + ["checkout", "-q", "feature"], check=True)
        self.assertIn(":dependents 2", self.context("shop/notes.py"))
        self.assertNotIn("web/src/view", self.grep("view"))
        subprocess.run(git + ["mv", "shop/audit.py", "shop/audit_log.py"], check=True)
        self.assertIn("(wingbox-file shop/audit_log.py", self.context("shop/audit_log.py"))
        self.assertNotIn("shop/audit ", self.grep("audit"))

    def test_an_import_configuration_change_is_found_and_rebuilt(self) -> None:
        """/* REQ-FRESH-025: adding a tsconfig.json path alias (a resolution config, never patched) must change how imports resolve at the next PreToolUse. */"""
        self.write({"web/src/app.ts": 'import { fmt } from "@app/util";\nexport const v = fmt(1);\n'})
        self.assertIn(":dependents 0", self.context("web/src/util.ts"))
        self.write({"web/tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}})})
        self.assertIn(":dependents 1", self.context("web/src/util.ts"))
        self.assertEqual(self.state().last_refresh.get("kind"), "build")


class FirstBuildTest(FreshnessTestCase):
    def test_without_a_graph_the_hooks_do_nothing_and_the_first_build_starts_fresh(self) -> None:
        """/* REQ-FRESH-026: without a graph the hooks must mark nothing and build nothing (the skill or `wingbox build` makes the first graph); the search hook names the missing graph once (REQ-INSTALL-025) and then says nothing; after `wingbox build` the state is fresh and the snapshot is written. */"""
        self.edited("shop/cart.py", "X = 1\n")
        self.assertIn("(wingbox-no-graph ", self.context("shop/cart.py"))
        self.assertEqual(self.context("shop/cart.py"), "")
        self.assertFalse((self.root / ".wingbox").exists())
        self.assertFalse((self.root / ".wingbox/index.sexp").exists())
        self.build()
        self.assertTrue(freshness.is_fresh(self.state()))
        self.assertIsNotNone(freshness.read_snapshot(str(self.root)))
        self.assertIn("(wingbox-file shop/cart.py", self.context("shop/cart.py"))

    def test_a_graph_without_a_snapshot_is_read_as_it_is_and_never_refreshed(self) -> None:
        """/* REQ-FRESH-027: a graph whose snapshot is missing cannot be confirmed to belong to its folder: the hook must give its context with `(wingbox-graph-unconfirmed :reason no-snapshot ...)`, run no refresh and leave the graph as it is. */"""
        self.build()
        (self.root / ".wingbox" / "graph-snapshot.json").unlink()
        graph = (self.root / ".wingbox" / "index.sexp").read_bytes()
        context = self.context("shop/cart.py")
        self.assertIn("(wingbox-file shop/cart.py", context)
        self.assertIn("(wingbox-graph-unconfirmed :reason no-snapshot", context)
        self.assertFalse((self.root / ".wingbox" / freshness.REFRESH_LOG_NAME).exists())
        self.assertEqual(graph, (self.root / ".wingbox" / "index.sexp").read_bytes())


if __name__ == "__main__":
    unittest.main()


class ConcurrentRefreshTest(FreshnessTestCase):
    def test_two_hooks_at_once_build_once_and_never_for_zero_changed_files(self) -> None:
        """/* REQ-FRESH-041: two search hooks that run at the same time (Claude Code runs Grep and Read in parallel) after one edit both get context; the marked file is refreshed once, and the second refresh, which finds nothing changed once it holds the lock, ends at once without a build (no "build ... for 0 changed files" in .wingbox/refresh.log). */"""
        self.build()
        self.edited("shop/cart.py", "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items) + 1\n")
        payloads = [
            {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": str(self.root), "tool_input": {"file_path": str(self.root / "shop/cart.py")}},
            {"hook_event_name": "PreToolUse", "tool_name": "Grep", "cwd": str(self.root), "tool_input": {"pattern": "order_total"}},
        ]
        processes = [
            subprocess.Popen([sys.executable, "-m", "wingbox.cli", "hook", "pre-search"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=self.root, env=_environment())
            for _ in payloads
        ]
        outputs = [process.communicate(json.dumps(payload), timeout=300)[0] for process, payload in zip(processes, payloads)]
        for output in outputs:
            self.assertIn("wingbox-graph", output)
            self.assertNotIn("wingbox-graph-stale", output)
        self.wait_until_fresh()
        log = (self.root / ".wingbox" / "refresh.log").read_text(encoding="utf-8")
        self.assertNotIn("for 0 changed files", log)
        self.assertEqual(1, log.count("for 1 changed files"), log)

    def test_a_refresh_that_finds_nothing_changed_returns_at_once(self) -> None:
        """/* REQ-FRESH-042: `python -m wingbox.refresh` on a graph with nothing marked returns status 0 in under 100 ms of its own work, writes no build and leaves the graph as it is. */"""
        self.build()
        before = (self.root / ".wingbox" / "index.sexp").read_bytes()
        refresh = cli.load_script("build_code_graph")  # warm the imports the refresh shares
        del refresh
        from wingbox import refresh as refresh_module
        started = time.monotonic()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            status = refresh_module.refresh(str(self.root))
        seconds = time.monotonic() - started
        self.assertEqual(0, status)
        self.assertLess(seconds, 0.1, err.getvalue())
        self.assertNotIn("build", err.getvalue())
        self.assertEqual(before, (self.root / ".wingbox" / "index.sexp").read_bytes())
