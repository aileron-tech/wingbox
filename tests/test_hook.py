import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli, freshness, hook  # noqa: E402

code_graph_search = cli.load_script("code_graph_search")


def _context(output: str) -> str:
    return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else ""


def _payload(tool: str, cwd: Path, **tool_input: object) -> str:
    return json.dumps(
        {"session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}
    )


def synthetic_graph(modules: int, functions_per_module: int, imports_per_module: int) -> str:
    """A code graph in the builder's format: path tree, nodes, entities, import and reference edges."""
    lines = [
        "; Code Graph",
        f"(code-graph :schema 1 :files {modules} :nodes {modules} :edges 0 :violations 0)",
        "",
        "; --- paths ---",
        "(paths",
        "  (app",
    ]
    for package in range(0, modules, 100):
        lines.append(f"    (pkg{package // 100}")
        for index in range(package, min(package + 100, modules)):
            lines.append(f"      (m{index} service_{index})")
        lines.append("    )")
    lines += ["  )", ")", "", "; --- nodes ---"]
    lines += [f'(node m{index} py "general module" :naming snake_case :pkg app)' for index in range(modules)]
    lines += ["", "; --- entities ---"]
    for index in range(modules):
        lines += [f"(fn m{index} handle_{index}_{number} {number + 1})" for number in range(functions_per_module)]
    lines += ["", "; --- edges ---"]
    for index in range(1, modules):
        for step in range(1, imports_per_module + 1):
            target = (index * 7 + step * 13) % index
            lines.append(f"(imports m{index} m{target})")
            lines.append(f"(refs m{index} m{target}::handle_{target}_0)")
    return "\n".join(lines) + "\n"


def mark_fresh(root: Path) -> None:
    """A hand-written graph has no sources: give it the snapshot of a build, so the hook does not rebuild it."""
    freshness.write_snapshot(
        str(root),
        freshness.Snapshot(
            {}, {"": os.stat(root).st_mtime_ns}, frozenset({".py"}), frozenset(), (), frozenset(), str(root)
        ),
    )


class HookOnABuiltGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name).resolve() / "shop"
        (root / "shop").mkdir(parents=True)
        (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (root / "shop" / "pricing.py").write_text("def order_total(items):\n    return sum(items)\n", encoding="utf-8")
        for name in ("checkout", "cart"):
            (root / "shop" / f"{name}.py").write_text(
                "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items)\n",
                encoding="utf-8",
            )
        (root / "shop" / "plans.py").write_text("class Subscription:\n    pass\n", encoding="utf-8")
        (root / "shop" / "billing.py").write_text("from shop.plans import Subscription\n\n\ndef renew(s: Subscription):\n    return s\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            status = cli.main(["build", str(root)])
        assert status == 0
        cls.root = root

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_a_grep_pattern_returns_matching_modules_and_definitions(self) -> None:
        """/* REQ-INSTALL-016: the pre-search hook on Grep must answer, as PreToolUse additionalContext, the modules and definitions that match the pattern with their direct dependents. */"""
        output = hook.run_pre_search(_payload("Grep", self.root, pattern="order_total"), "/")
        self.assertEqual("PreToolUse", json.loads(output)["hookSpecificOutput"]["hookEventName"])
        context = _context(output)
        self.assertIn("(wingbox-graph .wingbox/index.sexp)", context)
        self.assertIn("(definition order_total :kind fn :module shop/pricing :line 1 :dependents 2)", context)

        context = _context(hook.run_pre_search(_payload("Glob", self.root, pattern="**/*pricing*.py"), "/"))
        self.assertIn("(module shop/pricing :dependents 2)", context)

    def test_a_pattern_of_code_falls_back_to_its_longest_word(self) -> None:
        """/* REQ-INSTALL-017: a Grep pattern written for source lines (`def\\s+order_total`) must be answered for its longest word when the pattern itself names nothing in the graph. */"""
        context = _context(hook.run_pre_search(_payload("Grep", self.root, pattern="def\\s+order_total\\("), "/"))
        self.assertIn("(definition order_total ", context)

    def test_a_grep_pattern_matches_with_smart_case(self) -> None:
        """/* REQ-INSTALL-024: the Grep hint uses smart case, as the command line matches without case: a pattern with no upper-case letter (escapes such as \\s do not count) matches without case, a pattern with an upper-case letter matches with case, and the tool's case-insensitive flag (-i) always matches without case. */"""
        def context(pattern: str, **flags: object) -> str:
            return _context(hook.run_pre_search(_payload("Grep", self.root, pattern=pattern, **flags), "/"))

        for pattern in ("subscription", "Subscription", "class\\s+subscription", "subscr.*tion"):
            self.assertIn("(definition Subscription ", context(pattern), pattern)
        self.assertEqual("", context("SUBSCRIPTION"))
        self.assertIn("(definition Subscription ", context("SUBSCRIPTION", **{"-i": True}))

    def test_a_read_names_the_dependents_of_the_file_and_the_impact_command(self) -> None:
        """/* REQ-INSTALL-018: the pre-search hook on Read must give the module of the file, its direct dependents and the `wingbox impact` hint; a file outside the graph gives nothing. */"""
        context = _context(hook.run_pre_search(_payload("Read", self.root, file_path=str(self.root / "shop" / "pricing.py")), "/"))
        self.assertIn("(wingbox-file shop/pricing.py :module shop/pricing :dependents 2)", context)
        self.assertIn("wingbox impact shop/pricing.py", context)
        self.assertEqual("", hook.run_pre_search(_payload("Read", self.root, file_path="README.md"), "/"))

    def test_the_graph_in_the_wingbox_folder_is_found(self) -> None:
        """/* REQ-INSTALL-019: the hook must find the graph at .wingbox/index.sexp of the project root above the current folder; a graph that was not built there is read as it is, with a note to rebuild. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / ".git").mkdir()
        (root / ".wingbox").mkdir()
        (root / ".wingbox" / "index.sexp").write_bytes((self.root / ".wingbox/index.sexp").read_bytes())
        (root / "shop").mkdir()
        context = _context(hook.run_pre_search(_payload("Grep", root / "shop", pattern="pricing"), "/"))
        self.assertIn("(wingbox-graph .wingbox/index.sexp)", context)
        self.assertIn("(wingbox-graph-unconfirmed :reason no-snapshot", context)
        self.assertIn("(module shop/pricing :dependents 2)", context)


class HookWithoutAGraphTest(unittest.TestCase):
    def test_no_graph_or_bad_input_gives_no_output_and_exit_0(self) -> None:
        """/* REQ-INSTALL-020: without a graph, on malformed input or an unknown tool, `wingbox hook pre-search` must print nothing on stdout and exit 0, so the tool call is never blocked. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / ".git").mkdir()
        environment = {**os.environ, "PYTHONPATH": str(REPO / "src")}
        for stdin in (
            _payload("Grep", root, pattern="anything"),
            "{not json",
            "",
            "[1, 2]",
            json.dumps({"tool_name": "Grep", "tool_input": "x"}),
            _payload("Bash", root, command="ls"),
        ):
            completed = subprocess.run(
                [sys.executable, "-m", "wingbox.cli", "hook", "pre-search"],
                input=stdin, capture_output=True, text=True, cwd=root, env=environment, timeout=60,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("", completed.stdout)


class HookBudgetTest(unittest.TestCase):
    def test_the_synthetic_graph_loads_in_the_query_engine(self) -> None:
        """/* REQ-INSTALL-021: the synthetic graph of the time-budget test must be a graph the query engine accepts, with the same dependents as the hook counts. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / ".wingbox").mkdir()
        (root / ".wingbox/index.sexp").write_text(synthetic_graph(150, 3, 4), encoding="utf-8")
        graph = code_graph_search.CodeGraph()
        loaded = graph.load(str(root / ".wingbox/index.sexp"), str(root))
        self.assertIsInstance(loaded, code_graph_search.MapLoadOk)
        light = hook.load_graph(str(root), str(root / ".wingbox/index.sexp"))
        for short_id in ("m0", "m3", "m40"):
            module = light.module(short_id)
            expected = len(code_graph_search._collect_rdep_mods(graph, module))
            self.assertEqual(expected, hook.dependents(light, {short_id})[short_id], module)

    def test_a_large_graph_is_answered_within_the_budget_and_the_size_cap(self) -> None:
        """/* REQ-INSTALL-022: on a graph of 8,000 modules, 48,000 definitions and 96,000 edges the hook must answer in under one second of processor time and within 1,500 characters. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / ".git").mkdir()
        (root / ".wingbox").mkdir()
        (root / ".wingbox/index.sexp").write_text(synthetic_graph(8000, 6, 6), encoding="utf-8")
        mark_fresh(root)
        for payload in (
            _payload("Grep", root, pattern="handle_1"),
            _payload("Grep", root, pattern="service"),
            _payload("Read", root, file_path=str(root / "app" / "pkg0" / "service_3.py")),
        ):
            started = time.process_time()
            output = hook.run_pre_search(payload, "/")
            elapsed = time.process_time() - started
            self.assertLess(elapsed, 1.0)
            context = _context(output)
            self.assertTrue(context)
            self.assertLessEqual(len(context), hook.CONTEXT_LIMIT)


if __name__ == "__main__":
    unittest.main()
