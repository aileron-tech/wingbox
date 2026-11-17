import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
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


class HookWithoutAGraphHintTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        patcher = unittest.mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def context(self, root: Path, session: str = "s1", tool: str = "Read", **tool_input: object) -> str:
        payload = json.loads(_payload(tool, root, **(tool_input or {"file_path": str(root / "README.md")})))
        payload["session_id"] = session
        return _context(hook.run_pre_search(json.dumps(payload), "/"))

    def repository(self, name: str, marker: str = ".git", files: tuple[str, ...] = ("src/app/orders.py",)) -> Path:
        root = self.base / name
        root.mkdir()
        if marker == ".git":
            (root / ".git").mkdir()
        elif marker:
            (root / marker).write_text("", encoding="utf-8")
        for name in files:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("x = 1\n", encoding="utf-8")
        return root

    def test_a_source_repository_without_a_graph_is_named_once_per_session(self) -> None:
        """/* REQ-INSTALL-025: in a source repository (a git work tree or a folder with a project manifest, holding files of a supported language) without .wingbox/index.sexp, the search hook answers once per session and repository with `(wingbox-no-graph :root <root> :hint ...)` naming `wingbox build`; the next call of the same session says nothing, another session is told again; the marker lives under ~/.wingbox/hook-hints, nothing is written into the repository and no graph is built. */"""
        root = self.repository("shop")
        first = self.context(root / "src", tool="Grep", pattern="orders")
        self.assertIn(f"(wingbox-no-graph :root {root.as_posix()} :hint ", first)
        self.assertIn("wingbox build", first)
        self.assertEqual("", self.context(root, file_path=str(root / "src" / "app" / "orders.py")))
        self.assertIn("(wingbox-no-graph ", self.context(root, session="s2"))
        self.assertFalse((root / ".wingbox").exists())
        self.assertEqual(2, len(list((self.home / ".wingbox" / "hook-hints").iterdir())))
        manifest = self.repository("lib", marker="pyproject.toml", files=("lib/core.py",))
        self.assertIn("(wingbox-no-graph ", self.context(manifest, tool="Glob", pattern="**/*core*.py"))

    def test_no_hint_outside_a_source_repository(self) -> None:
        """/* REQ-INSTALL-026: no hint in a folder that is no repository (no .git, no project manifest), in a repository without a source file of a supported language (only documents), or in the home folder itself even when it is a git work tree; old markers are pruned. */"""
        self.assertEqual("", self.context(self.repository("loose", marker="", files=("notes/script.py",))))
        self.assertEqual("", self.context(self.repository("docs", files=("guide/index.md", "README.md"))))
        (self.home / ".git").mkdir()
        (self.home / "tool.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual("", self.context(self.home))
        hints = self.home / ".wingbox" / "hook-hints"
        hints.mkdir(parents=True, exist_ok=True)
        old = hints / "old"
        old.write_text("", encoding="utf-8")
        os.utime(old, (time.time() - 30 * 86400, time.time() - 30 * 86400))
        self.context(self.repository("app"))
        self.assertFalse(old.exists())

    def test_the_extensions_are_the_builders_and_the_check_is_fast(self) -> None:
        """/* REQ-INSTALL-027: the hook knows a source file by the extensions the builder parses, and the check without a graph stays under 100 ms in a repository of 5,000 files. */"""
        builder = cli.load_script("build_code_graph")
        self.assertEqual(
            {extension for language in builder.LANGUAGES for extension in language.source_extensions}, set(hook.SOURCE_EXTENSIONS)
        )
        root = self.repository("big", files=tuple(f"docs/part{index // 100}/page{index}.md" for index in range(5000)) + ("zz/last.go",))
        started = time.perf_counter()
        for session in range(5):
            self.context(root, session=f"t{session}")
        self.assertLess((time.perf_counter() - started) / 5, 0.1)
