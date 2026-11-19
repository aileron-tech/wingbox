import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Protocol


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "code_graph_search.py"
SPEC = importlib.util.spec_from_file_location("code_graph_search", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load code graph search module from {SCRIPT_PATH}")
code_graph_search = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = code_graph_search
SPEC.loader.exec_module(code_graph_search)


class CodeGraphContract(Protocol):
    def resolve_module(self, query: str) -> list[str]: ...


class CodeGraphSearchAliasTest(unittest.TestCase):
    def _load_graph(self) -> tuple[CodeGraphContract, str]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        sexp_path = Path(tmpdir.name) / ".wingbox/index.sexp"

        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 2 :nodes 2 :edges 1 :violations 0)
                (paths
                  (app-service
                    (app-service
                      (src
                        (workspace-operatives
                          (asp543 workspace-operative.service)
                          (asp544 workspace-operative.service.spec)
                        )
                      )
                    )
                  )
                )
                (node asp543 ts service)
                (node asp544 ts test)
                (cls asp543 WorkspaceOperativeService 16)
                (imports asp544 asp543)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        graph = code_graph_search.CodeGraph()
        load_result = graph.load(str(sexp_path))
        self.assertIsInstance(load_result, code_graph_search.MapLoadOk)
        return graph, str(sexp_path)

    def test_resolve_module_accepts_path_aliases(self) -> None:
        """/* REQ-CS-001: analyze must accept internal path-map aliases returned by older code graphs. */"""
        graph, _sexp_path = self._load_graph()

        self.assertEqual(
            graph.resolve_module("asp543"),
            ["app-service/app-service/src/workspace-operatives/workspace-operative.service"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_analyze(graph, "asp543")

        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            output.getvalue(),
        )

    def test_search_returns_resolved_module_ids(self) -> None:
        """/* REQ-CS-002: search must emit resolved module IDs instead of raw path-map aliases. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_search(graph, "workspace-operative.service", sexp_path)

        lines = output.getvalue().strip().splitlines()
        self.assertEqual(
            lines,
            [
                "(result app-service/app-service/src/workspace-operatives/workspace-operative.service)",
                "(result app-service/app-service/src/workspace-operatives/workspace-operative.service.spec)",
                "(result-count 2)",
            ],
        )

    def test_quick_runs_analyze_for_first_resolved_module(self) -> None:
        """/* REQ-CS-003: quick must provide full analyze output for the first matching module. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_quick(graph, "workspace-operative.service", sexp_path)

        text = output.getvalue()
        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            text,
        )
        self.assertIn(
            "(boundary-file app-service/app-service/src/workspace-operatives/workspace-operative.service",
            text,
        )
        self.assertNotIn("(boundary app-service/app-service/src/workspace-operatives/workspace-operative.service", text)

    def test_quick_resolves_alias_from_raw_search_hits(self) -> None:
        """/* REQ-CS-004: quick must resolve legacy path-map aliases found in raw code-graph facts. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_quick(graph, "WorkspaceOperativeService", sexp_path)

        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            output.getvalue(),
        )

    def test_boundary_files_only_emits_minimal_file_list(self) -> None:
        """/* REQ-CS-005: boundary --files-only must emit the target and directly related files only. */"""
        graph, _sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_boundary(graph, "workspace-operative.service", files_only=True)

        self.assertEqual(
            output.getvalue().strip().splitlines(),
            [
                "app-service/app-service/src/workspace-operatives/workspace-operative.service",
                "app-service/app-service/src/workspace-operatives/workspace-operative.service.spec",
            ],
        )

    def test_rust_query_commands_resolve_physical_source_files(self) -> None:
        """/* REQ-CS-033: Rust query boundaries resolve to physical source paths. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flat_module = root / "src" / "handlers.rs"
            child_module = root / "src" / "handlers" / "payments.rs"
            nested_module = root / "src" / "storage" / "mod.rs"
            flat_module.parent.mkdir(parents=True)
            child_module.parent.mkdir(parents=True)
            nested_module.parent.mkdir(parents=True)
            flat_module.write_text("pub mod payments;\n", encoding="utf-8")
            child_module.write_text("use super::*;\n", encoding="utf-8")
            nested_module.write_text("pub struct Database;\n", encoding="utf-8")
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                textwrap.dedent(
                    """
                    (code-graph :schema 1 :files 3 :nodes 3 :edges 1 :violations 0)
                    (paths
                      (src
                        (r1 handlers)
                        (r3 storage)
                        (handlers
                          (r2 payments)
                        )
                      )
                    )
                    (node r1 rs service)
                    (node r2 rs service)
                    (node r3 rs shared)
                    (imports r2 r1)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            graph = code_graph_search.CodeGraph()
            load_result = graph.load(str(sexp_path), str(root))
            self.assertIsInstance(load_result, code_graph_search.MapLoadOk)

            self.assertEqual(
                str(flat_module),
                code_graph_search._module_id_to_file("src/handlers", str(root)),
            )
            self.assertEqual(
                str(nested_module),
                code_graph_search._module_id_to_file("src/storage", str(root)),
            )

            boundary_output = io.StringIO()
            with redirect_stdout(boundary_output):
                code_graph_search.cmd_boundary(
                    graph,
                    "src/handlers/payments",
                    files_only=True,
                )
            self.assertEqual(
                boundary_output.getvalue().strip().splitlines(),
                [str(child_module), str(flat_module)],
            )

            analyze_output = io.StringIO()
            with redirect_stdout(analyze_output):
                code_graph_search.cmd_analyze(graph, "src/handlers/payments")
            self.assertIn(
                f"(boundary-file src/handlers/payments {child_module.relative_to(root).as_posix()})",
                analyze_output.getvalue(),
            )
            self.assertIn(
                f"(boundary-file src/handlers/payments {flat_module.relative_to(root).as_posix()})",
                analyze_output.getvalue(),
            )

    def test_module_paths_resolve_free_pascal_sources_and_includes(self) -> None:
        """/* REQ-PAS-007: Search results return real Pascal source paths for every supported extension. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        expected = {
            "soft/ipKernel": root / "soft/ipKernel.lpr",
            "soft/uWorker": root / "soft/uWorker.pas",
            "shared/common": root / "shared/common.pp",
            "shared/constants.inc": root / "shared/constants.inc",
        }
        for path in expected.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

        sexp_path = root / ".wingbox/index.sexp"

        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 4 :nodes 4 :edges 0 :violations 0)
                (paths
                  (soft
                    (p1 ipKernel)
                    (p2 uWorker)
                  )
                  (shared
                    (p3 common)
                    (p4 constants.inc)
                  )
                )
                (node p1 pas application)
                (node p2 pas service)
                (node p3 pas shared)
                (node p4 pas shared)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        load_result = graph.load(str(sexp_path), str(root))
        self.assertIsInstance(load_result, code_graph_search.MapLoadOk)

        for module_id, path in expected.items():
            with self.subTest(module_id=module_id):
                self.assertEqual(
                    str(path),
                    code_graph_search._module_id_to_file(module_id, str(root)),
                )
                boundary_output = io.StringIO()
                with redirect_stdout(boundary_output):
                    code_graph_search.cmd_boundary(graph, module_id, files_only=True)
                self.assertEqual(
                    [str(path)],
                    boundary_output.getvalue().strip().splitlines(),
                )

                analyze_output = io.StringIO()
                with redirect_stdout(analyze_output):
                    code_graph_search.cmd_analyze(graph, module_id)
                self.assertIn(
                    f"(boundary-file {module_id} {path.relative_to(root).as_posix()})",
                    analyze_output.getvalue(),
                )

    def test_no_match_includes_suggestions(self) -> None:
        """/* REQ-CS-006: no-match errors must surface actionable module suggestions. */"""
        graph, _sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_analyze(graph, "workspace-operative.controller")

        text = output.getvalue()
        self.assertIn("(error no-match workspace-operative.controller :suggestions", text)
        self.assertIn("workspace-operative.service", text)

    def test_malformed_and_adversarial_patterns_fail_without_traceback(self) -> None:
        """/* REQ-CS-030: unsafe search patterns must fail quickly and clearly. */"""
        _graph, sexp_path = self._load_graph()

        for pattern in ("[", "(a+)+$"):
            with self.subTest(pattern=pattern):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT_PATH),
                        "--map",
                        sexp_path,
                        "search",
                        pattern,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("(error invalid-argument", result.stdout)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_invalid_depth_fails_without_loading_or_traceback(self) -> None:
        """/* REQ-CS-031: invalid numeric query arguments must return typed diagnostics. */"""
        _graph, sexp_path = self._load_graph()

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--map",
                sexp_path,
                "deps",
                "workspace-operative.service",
                "unbounded",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("depth must be an integer", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_qualified_ruby_method_is_not_duplicated_by_class_summary(self) -> None:
        """/* REQ-RUBY-010: Function search returns the precise Ruby method entity once when a class also lists that method. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        sexp_path = Path(tmpdir.name) / ".wingbox/index.sexp"
        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 1 :nodes 1 :edges 0 :violations 0)
                (paths
                  (app
                    (services
                      (charge rb0)
                    )
                  )
                )
                (node rb0 rb service :naming snake_case :pkg example)
                (cls rb0 Billing::Charge 3 (:methods call))
                (fn rb0 Billing::Charge#call 9)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        load_result = graph.load(str(sexp_path))
        self.assertIsInstance(load_result, code_graph_search.MapLoadOk)

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_find_function(graph, "call")

        result_lines = output.getvalue().splitlines()
        self.assertEqual(1, len(result_lines))
        self.assertIn("Billing::Charge#call :line 9", result_lines[0])


class CodeGraphImpactTest(unittest.TestCase):
    def _load_graph(self) -> tuple[CodeGraphContract, str]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        for relative in (
            "app/core/util.py",
            "app/billing/invoice.py",
            "app/billing/report.py",
            "app/api/handler.py",
            "app/api/health.py",
        ):
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("", encoding="utf-8")

        sexp_path = root / ".wingbox/index.sexp"

        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 5 :nodes 5 :edges 4 :violations 0)
                (paths
                  (app
                    (core
                      (m0 util)
                    )
                    (billing
                      (m1 invoice)
                      (m2 report)
                    )
                    (api
                      (m3 handler)
                      (m4 health)
                    )
                  )
                )
                (node m0 py shared)
                (node m1 py service)
                (node m2 py service)
                (node m3 py api)
                (node m4 py api)
                (imports m1 m0)
                (imports m2 m0)
                (imports m3 m1)
                (imports m3 m2)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        load_result = graph.load(str(sexp_path), str(root))
        self.assertIsInstance(load_result, code_graph_search.MapLoadOk)
        return graph, str(root)

    def _impact(self, graph: CodeGraphContract, target: str, depth: int, sexp: bool = False) -> str:
        """The text for people of one file, or with sexp the flat facts of --sexp."""
        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_impact(graph, [target], depth, "flat" if sexp else "human")
        return output.getvalue()

    def test_impact_accepts_file_paths_with_extension(self) -> None:
        """/* REQ-CS-038: impact must accept an ordinary source file path, relative to the root or absolute. */"""
        graph, root = self._load_graph()

        relative = self._impact(graph, "app/core/util.py", 2, sexp=True)
        absolute = self._impact(graph, str(Path(root) / "app" / "core" / "util.py"), 2, sexp=True)

        self.assertIn("(impact-root app/core/util :lang py :file app/core/util.py)", relative)
        self.assertEqual(relative, absolute)

    def test_impact_text_lists_dependents_by_depth_and_files_to_read(self) -> None:
        """/* REQ-CS-039: impact must print a readable summary: counts, dependents per depth, files to read first; since REQ-CS-076 and REQ-CS-077 each row says why the file is on the list. */"""
        graph, _root = self._load_graph()

        self.assertEqual(
            self._impact(graph, "app/core/util.py", 2),
            textwrap.dedent(
                """\
                Impact of changing app/core/util.py
                  direct dependents   2
                  within 2 hops       3

                Depth 1
                  app/billing/invoice.py   imports it
                  app/billing/report.py    imports it

                Depth 2
                  app/api/handler.py   through app/billing/invoice.py (or 1 other file): imports it

                Read first (3 files)
                  app/core/util.py
                  app/billing/invoice.py
                  app/billing/report.py
                """
            ),
        )

    def test_impact_sexp_reports_each_dependent_once_at_its_shortest_depth(self) -> None:
        """/* REQ-CS-040: impact --sexp must emit compact facts with every dependent once, at its shortest depth; a deeper fact names the module it is reached through (REQ-CS-077). */"""
        graph, _root = self._load_graph()

        self.assertEqual(
            self._impact(graph, "app/core/util", 2, sexp=True).splitlines(),
            [
                "(impact-root app/core/util :lang py :file app/core/util.py)",
                "(impact app/core/util app/billing/invoice :lang py :via imports :depth 1)",
                "(impact app/core/util app/billing/report :lang py :via imports :depth 1)",
                "(impact app/core/util app/api/handler :lang py :via imports :depth 2 :through app/billing/invoice :more 1)",
                "(impact-summary app/core/util :direct 2 :total 3 :depth 2)",
                "(read-first app/core/util.py)",
                "(read-first app/billing/invoice.py)",
                "(read-first app/billing/report.py)",
            ],
        )

    def test_impact_depth_limits_the_walk(self) -> None:
        """/* REQ-CS-041: impact must stop at the requested depth. */"""
        graph, _root = self._load_graph()

        output = self._impact(graph, "app/core/util.py", 1, sexp=True)

        self.assertNotIn("app/api/handler", output)
        self.assertIn("(impact-summary app/core/util :direct 2 :total 2 :depth 1)", output)

    def test_impact_reports_unknown_targets_and_isolated_modules(self) -> None:
        """/* REQ-CS-042: impact must report an unknown target as no-match and an unused module as zero dependents. */"""
        graph, _root = self._load_graph()

        self.assertIn("(error no-match", self._impact(graph, "app/missing/thing.py", 2))
        isolated = self._impact(graph, "app/api/health.py", 2)
        self.assertIn("direct dependents   0", isolated)
        self.assertIn("No other module depends on it.", isolated)

    def test_impact_names_the_other_matches_of_an_ambiguous_target(self) -> None:
        """/* REQ-CS-044: impact must say which module it shows when the target matches several modules. */"""
        graph, _root = self._load_graph()

        self.assertEqual(
            self._impact(graph, "app/api/h", 2, sexp=True).splitlines()[0],
            "(impact-ambiguous app/api/h :showing app/api/handler :others app/api/health)",
        )
        self.assertIn(
            "Note: 2 modules match app/api/h; showing app/api/handler. Others: app/api/health",
            self._impact(graph, "app/api/h", 2),
        )

    def test_impact_cli_parses_depth_and_sexp_flag(self) -> None:
        """/* REQ-CS-043: the impact command must accept [depth] after the files and --sexp anywhere, and reject a depth outside its range; since REQ-CS-108 a last argument that is not a number is one more file. */"""
        _graph, root = self._load_graph()
        base = ["--map", str(Path(root) / ".wingbox/index.sexp"), "--root", root]

        output = io.StringIO()
        with redirect_stdout(output):
            status = code_graph_search.main(base + ["impact", "--sexp", "app/core/util.py", "1"])
        self.assertEqual(0, status)
        self.assertIn("(impact-summary app/core/util :direct 2 :total 2 :depth 1)", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            status = code_graph_search.main(base + ["impact", "app/core/util.py", "-1"])
        self.assertEqual(2, status)
        self.assertIn("(error invalid-argument", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            status = code_graph_search.main(base + ["impact", "app/core/util.py", "many"])
        self.assertEqual(1, status)
        self.assertIn("(error no-match many", output.getvalue())


if __name__ == "__main__":
    unittest.main()
