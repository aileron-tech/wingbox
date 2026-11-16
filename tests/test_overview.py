import dataclasses
import io
import json
import re
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli, map_page, overview  # noqa: E402

code_graph_search = cli.load_script("code_graph_search")


def _write_project(root: Path) -> Path:
    for relative in (
        "shop/api/orders.py",
        "shop/services/checkout.py",
        "shop/domain/order.py",
        "shop/db/order_repo.py",
        "shop/utils/money.py",
    ):
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("", encoding="utf-8")
    sexp_path = root / ".code_graph.sexp"
    sexp_path.write_text(
        textwrap.dedent(
            """
            (code-graph :schema 1 :files 5 :nodes 5 :edges 7 :violations 0)
            (paths
              (shop
                (api
                  (m0 orders)
                )
                (services
                  (m1 checkout)
                )
                (domain
                  (m2 order)
                )
                (db
                  (m3 order_repo)
                )
                (utils
                  (m4 money)
                )
              )
            )
            (node m0 py api)
            (node m1 py service)
            (node m2 py domain)
            (node m3 py infra)
            (node m4 py shared)
            (cls m2 Order 5)
            (fn m4 format_money 3)
            (imports m0 m1)
            (imports m1 m2)
            (imports m1 m3)
            (imports m2 m3)
            (imports m1 m4)
            (imports m2 m4)
            (imports m3 m4)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return sexp_path


class OverviewTest(unittest.TestCase):
    def _overview(self) -> overview.Overview:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        sexp_path = _write_project(root)
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
        return overview.build_overview(graph, code_graph_search, "shop-demo")

    def test_overview_counts_modules_dependencies_layers_and_violations(self) -> None:
        """/* REQ-OUT-001: the overview must count modules, module dependencies, layers and boundary violations. */"""
        view = self._overview()

        self.assertEqual(5, len(view.modules))
        self.assertEqual(7, len(view.edges))
        self.assertEqual(2, view.entity_count)
        self.assertEqual(
            [("api", 1), ("service", 1), ("domain", 1), ("infra", 1), ("shared", 1)],
            list(view.layers),
        )
        self.assertEqual(
            [("shop/domain/order", "shop/db/order_repo", "domain imports infrastructure directly")],
            [(v.source, v.target, v.reason) for v in view.violations],
        )

    def test_overview_ranks_modules_by_dependents(self) -> None:
        """/* REQ-OUT-002: each module must carry its direct dependents, dependencies and 2-hop blast radius. */"""
        by_id = {module.id: module for module in self._overview().modules}

        money = by_id["shop/utils/money"]
        self.assertEqual((3, 0, 4), (money.fan_in, money.fan_out, money.within_two_hops))
        self.assertEqual("shop/utils/money.py", money.file)
        self.assertEqual("shared", money.layer)
        checkout = by_id["shop/services/checkout"]
        self.assertEqual((1, 3), (checkout.fan_in, checkout.fan_out))

    def test_only_folder_based_violations_count_and_role_guesses_are_candidates(self) -> None:
        """/* REQ-OUT-010: a violation counts only when both layers come from folders; role guesses are candidates. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        sexp_path = root / ".code_graph.sexp"
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 4 :nodes 4 :edges 2 :violations 0)
                (paths
                  (shop
                    (domain
                      (m0 order)
                    )
                    (db
                      (m1 order_repo)
                    )
                  )
                  (pkg
                    (m2 pricing)
                    (m3 ledger)
                  )
                )
                (node m0 py "general module")
                (node m1 py "general module")
                (node m2 py "domain logic")
                (node m3 py "repository")
                (imports m0 m1)
                (imports m2 m3)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)

        view = overview.build_overview(graph, code_graph_search, "mixed")

        self.assertEqual([("shop/domain/order", "shop/db/order_repo")], [(v.source, v.target) for v in view.violations])
        self.assertEqual(1, view.violation_candidates)
        self.assertEqual((2, 2, 0), (view.placed_by_path, view.placed_by_role, view.unplaced))
        report = overview.render_report(view, "2026-11-15", "9.9.9")
        self.assertIn("| Boundary violations | 1 |", report)
        self.assertIn("1 more candidate", report)

    def test_type_only_imports_across_layers_are_listed_apart(self) -> None:
        """/* REQ-OUT-014: an import of types only across layers is not counted as a boundary violation; the report lists it in its own table. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        sexp_path = root / ".code_graph.sexp"
        sexp_path.write_text(
            textwrap.dedent(
                """
                (code-graph :schema 1 :files 3 :nodes 3 :edges 2 :violations 0)
                (paths
                  (src
                    (dtos
                      (m0 stats.dto)
                      (m1 mixed.dto)
                    )
                    (repositories
                      (m2 asset.repository)
                    )
                  )
                )
                (node m0 ts "general module")
                (node m1 ts "general module")
                (node m2 ts "general module")
                (imports m0 m2 :type-only)
                (imports m1 m2)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)

        view = overview.build_overview(graph, code_graph_search, "typed")

        self.assertEqual([("src/dtos/mixed.dto", "src/repositories/asset.repository")], [(v.source, v.target) for v in view.violations])
        self.assertEqual(
            [("src/dtos/stats.dto", "src/repositories/asset.repository")],
            [(v.source, v.target) for v in view.type_only_crossings],
        )
        self.assertEqual(0, view.violation_candidates)
        report = overview.render_report(view, "2026-11-15", "9.9.9")
        self.assertIn("| Boundary violations | 1 |", report)
        self.assertIn("1 import of types only crosses a layer boundary", report)
        self.assertIn("| `src/dtos/stats.dto` (domain) | `src/repositories/asset.repository` (infra) |", report)

    def test_report_opens_with_key_findings_in_shares(self) -> None:
        """/* REQ-OUT-011: the report must open with the widest blast radius as a share of the project and the rule coverage. */"""
        report = overview.render_report(self._overview(), "2026-11-15", "9.9.9")

        findings = report.split("## Summary")[0]
        self.assertIn("## Key findings", findings)
        self.assertIn("A change to `shop/utils/money.py` reaches 4 of 5 modules (80%) within 2 hops", findings)
        self.assertNotIn("Static parsing", findings)
        self.assertIn("1 boundary violation", findings)
        self.assertIn("The layer rules place 5 of 5 modules (100%)", findings)

    def test_test_files_stay_out_of_the_risk_lists(self) -> None:
        """/* REQ-OUT-012: test files must not appear among the most depended-on modules or the entry points. */"""
        view = self._overview()
        helper = overview.ModuleInfo(
            id="tests/helpers", file="tests/helpers.py", lang="py", layer="test", layer_basis="path",
            fan_in=40, fan_out=3, within_two_hops=40,
        )
        runner = dataclasses.replace(helper, id="tests/run_all", file="tests/run_all.py", fan_in=0, fan_out=9)
        noisy = dataclasses.replace(view, modules=view.modules + (helper, runner))

        self.assertNotIn("tests/helpers", [module.id for module in overview.most_depended_on(noisy, 15)])
        self.assertNotIn("tests/run_all", [module.id for module in overview.entry_points(noisy, 10)])

    def test_report_is_readable_markdown_for_people(self) -> None:
        """/* REQ-OUT-003: REPORT.md must state the summary, the most depended-on modules, the violations and the entry points. */"""
        report = overview.render_report(self._overview(), "2026-11-15", "9.9.9")

        self.assertTrue(report.startswith("# Wingbox report: shop-demo\n"))
        self.assertIn("| Modules | 5 |", report)
        self.assertIn("| Dependencies between modules | 7 |", report)
        self.assertIn("| Boundary violations | 1 |", report)
        self.assertIn("| `shop/utils/money.py` | shared | 3 | 4 |", report)
        self.assertIn(
            "| `shop/domain/order.py` (domain) | `shop/db/order_repo.py` (infra) | domain imports infrastructure directly |",
            report,
        )
        self.assertIn("- `shop/api/orders.py` (api), depends on 1 module", report)
        self.assertIn("wingbox impact shop/utils/money.py", report)
        self.assertIn("inferred from paths and file roles", report)
        self.assertNotIn("(impact", report)

    def test_map_page_is_a_single_offline_file_with_the_graph_data(self) -> None:
        """/* REQ-OUT-004: map.html must embed the data, the styles, the script and d3, and load nothing from the network. */"""
        page = map_page.render_map(self._overview(), "2026-11-15", "9.9.9", map_page.read_assets())

        self.assertTrue(page.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn("<title>shop-demo - Wingbox map</title>", page)
        self.assertNotIn("__WINGBOX_", page)
        self.assertIn("Permission to use, copy, modify, and/or distribute this software", page)
        data = json.loads(re.search(r'<script id="wingbox-data" type="application/json">(.*?)</script>', page, re.S).group(1))
        self.assertEqual("shop-demo", data["project"])
        self.assertEqual(5, len(data["modules"]))
        self.assertEqual(7, len(data["edges"]))
        self.assertEqual(1, len(data["violations"]))
        self.assertEqual("shop/utils/money.py", data["modules"][4]["file"])
        self.assertEqual([3, 0, 4], [data["modules"][4][key] for key in ("in", "out", "reach")])
        self.assertIn(["domain", "infra", "domain imports infrastructure directly"], data["rules"])
        self.assertEqual([5, 0, 0], data["placed"])
        self.assertEqual(0, data["candidates"])
        self.assertIn("https://d3js.org v7", page)
        self.assertIsNone(re.search(r'<(script|link|img)[^>]+(src|href)="https?://', page))

    def test_map_data_escapes_closing_script_tags(self) -> None:
        """/* REQ-OUT-005: a module name containing </script> must not break out of the embedded data. */"""
        view = self._overview()
        hostile = overview.ModuleInfo(
            id="x", file="x</script><script>alert(1)</script>.py", lang="py", layer="other", layer_basis="none",
            fan_in=0, fan_out=0, within_two_hops=0,
        )
        hostile_view = dataclasses.replace(
            view, project="</title><script>alert(2)</script>", modules=view.modules + (hostile,)
        )
        page = map_page.render_map(hostile_view, "2026-11-15", "9.9.9", map_page.read_assets())
        self.assertEqual(1, page.count("alert(1)"))
        self.assertNotIn("x</script>", page)
        self.assertNotIn("<script>alert(2)", page)


class LayerRulesTest(unittest.TestCase):
    def test_top_level_folders_and_test_file_names_are_recognised(self) -> None:
        """/* REQ-OUT-008: layer rules must apply to top-level folders and must mark test files as tests first. */"""
        cases = {
            "tests/utils": "test",
            "tests/tools/console.test": "test",
            "src/tools/input.spec": "test",
            "internal/llm/client_test": "test",
            "pkg/test_loader": "test",
            "src/__tests__/render": "test",
            "api/orders": "api",
            "services/checkout": "service",
            "src/components/Button": "ui",
            "src/contest/latest": "other",
        }
        self.assertEqual(cases, {path: code_graph_search._classify_layer(path) for path in cases})

    def test_migrations_and_seeds_are_infrastructure_even_under_a_schema_folder(self) -> None:
        """/* REQ-OUT-013: a migration or seed file is infrastructure wherever its folder lies; a schema folder around it does not make it domain. */"""
        cases = {
            "src/schema/migrations/1744910873969-InitialMigration": "infra",
            "src/schema/seeds/users": "infra",
            "src/schema/tables/asset.table": "domain",
            "db/migrations/0001_initial": "infra",
        }
        self.assertEqual(cases, {path: code_graph_search._classify_layer(path) for path in cases})

    def test_a_file_named_like_a_layer_folder_belongs_to_that_layer(self) -> None:
        """/* REQ-OUT-015: a file named like a layer folder (types.ts, models.py) and the index file of such a folder (controllers/index.ts is the module src/controllers) belong to that layer. */"""
        cases = {
            "src/controllers": "api",
            "src/types": "shared",
            "app/models": "domain",
            "pkg/openapi/models": "domain",
            "src/contest/latest": "other",
        }
        self.assertEqual(cases, {path: code_graph_search._classify_layer(path) for path in cases})

    def test_a_python_package_init_takes_the_layer_of_its_package(self) -> None:
        """/* REQ-OUT-016: a Python `__init__.py` is its package: it takes the layer of its folder, or, when no folder rule applies, the layer where most modules of the package are; what it re-exports never decides its layer. */"""
        graph = code_graph_search.CodeGraph()
        modules = {
            "engine/__init__": "router",
            "engine/applications": "general module",
            "engine/params": "general module",
            "engine/storage": "repository",
            "core/__init__": "router",
            "core/billing": "service",
            "core/orders": "service",
            "core/misc": "general module",
            "shop/services/__init__": "router",
            "shop/services/checkout": "general module",
            "tie/__init__": "router",
            "tie/a": "service",
            "tie/b": "repository",
        }
        for module, purpose in modules.items():
            graph.nodes[module] = code_graph_search.Node(module, "py", purpose)

        self.assertEqual(
            {"engine/__init__": "other", "core/__init__": "service", "shop/services/__init__": "service", "tie/__init__": "other"},
            {module: code_graph_search.module_layer(graph, module) for module in modules if module.endswith("__init__")},
        )
        self.assertEqual(
            ["none", "role", "path"],
            [code_graph_search.module_layer_basis(graph, module) for module in ("engine/__init__", "core/__init__", "shop/services/__init__")],
        )

    def test_layer_basis_tells_folder_rules_from_role_guesses(self) -> None:
        """/* REQ-OUT-009: a layer taken from the folder must be told apart from one guessed from the file role. */"""
        graph = code_graph_search.CodeGraph()
        graph.nodes["shop/domain/order"] = code_graph_search.Node("shop/domain/order", "py", "general module")
        graph.nodes["pkg/ledger"] = code_graph_search.Node("pkg/ledger", "py", "repository")
        graph.nodes["pkg/misc"] = code_graph_search.Node("pkg/misc", "py", "general module")

        self.assertEqual(
            ["path", "role", "none"],
            [code_graph_search.module_layer_basis(graph, module) for module in ("shop/domain/order", "pkg/ledger", "pkg/misc")],
        )


class ModuleFileTest(unittest.TestCase):
    def test_go_java_and_csharp_modules_resolve_to_their_source_files(self) -> None:
        """/* REQ-OUT-007: a Go, Java or C# module must be reported with the path of its source file. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        for relative in ("internal/llm/client.go", "app/Billing.java", "Api/Orders.cs"):
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("", encoding="utf-8")

        resolved = [
            Path(code_graph_search._module_id_to_file(module_id, str(root))).relative_to(root).as_posix()
            for module_id in ("internal/llm/client", "app/Billing", "Api/Orders")
        ]

        self.assertEqual(["internal/llm/client.go", "app/Billing.java", "Api/Orders.cs"], resolved)


class BuildOutputsTest(unittest.TestCase):
    def test_wingbox_build_writes_the_report_and_the_map(self) -> None:
        """/* REQ-OUT-006: `wingbox build` must write wingbox-out/REPORT.md and wingbox-out/map.html next to the graph. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        (root / "shop").mkdir()
        (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
        (root / "shop" / "checkout.py").write_text(
            "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n", encoding="utf-8"
        )

        output = io.StringIO()
        with redirect_stdout(output):
            status = cli.main(["build", str(root)])

        self.assertEqual(0, status, output.getvalue())
        report = root / "wingbox-out" / "REPORT.md"
        page = root / "wingbox-out" / "map.html"
        self.assertTrue(report.is_file())
        self.assertTrue(page.is_file())
        self.assertIn("# Wingbox report: " + root.name, report.read_text(encoding="utf-8"))
        self.assertIn("wingbox-out/map.html", output.getvalue())


if __name__ == "__main__":
    unittest.main()
