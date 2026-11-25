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
    sexp_path = root / ".wingbox/index.sexp"
    sexp_path.parent.mkdir(exist_ok=True)
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
        sexp_path = root / ".wingbox/index.sexp"
        sexp_path.parent.mkdir(exist_ok=True)
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
        sexp_path = root / ".wingbox/index.sexp"
        sexp_path.parent.mkdir(exist_ok=True)
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
        self.assertIn("| `shop/utils/money.py` | shared (layered: folder utils/ -> shared) | 3 | 4 |", report)
        self.assertIn(
            "| `shop/domain/order.py` (domain) | `shop/db/order_repo.py` (infra) | layered: domain -> infra, domain imports infrastructure directly |",
            report,
        )
        self.assertIn("- `shop/api/orders.py` (api), depends on 1 module", report)
        self.assertIn("wingbox impact shop/utils/money.py", report)
        self.assertIn("by the rules of the style of their part", report)
        self.assertNotIn("(impact", report)

    def test_text_of_the_repository_is_never_markdown_of_the_report(self) -> None:
        """/* REQ-OUT-9930: REPORT.md shows text that comes from the repository as text: the evidence of a capability break, a pattern break or an advice (a line of source) stands in a code span whose fence is longer than any run of backticks in it, with a pipe escaped and a line break as a space, so an image, a link, an HTML tag or comment in the line stays inside one table cell and is not rendered; in the cells for the advice, its style and its rule and in the title, `<` is written `&lt;` and a backslash, `[` and `!` are escaped. */"""
        evil = '![x](https://tracker.example/p.png) <!-- <img src=x onerror=1> --> `a` ``b`` | [k](javascript:1)'
        hostile = dataclasses.replace(
            self._overview(),
            project="demo <b>!",
            capability_breaks=(overview.CapabilityInfo("shop/api/orders.py", "sql", "api", "violation", 7, evil, "allowed in <infra> [x](y) \\[z](w)"),),
            pattern_breaks=(overview.CapabilityInfo("shop/api/orders.py", "controller-skips-service", "api", "pattern", 9, "line\nbreak\r\n`", "reason"),),
            advice=(overview.AdviceInfo("a1", ".", "fix-capability", "shop/api/orders.py", "move ![it](u) <there>", "layered", "sql in api", "line 7: " + evil),
                    overview.AdviceInfo("a2", ".", "missing-folder", "shop/domain/events", "create it", "layered", "target", "")),
        )
        report = overview.render_report(hostile, "2026-11-15", "9.9.9")
        span = "```![x](https://tracker.example/p.png) <!-- <img src=x onerror=1> --> `a` ``b`` \\| [k](javascript:1)```"
        self.assertTrue(report.startswith("# Wingbox report: demo &lt;b>\\!\n"), report[:60])
        self.assertIn(f"| `shop/api/orders.py` | sql | api | 7 | {span} | allowed in &lt;infra> \\[x](y) \\\\\\[z](w) |", report)
        self.assertIn("| `shop/api/orders.py` | controller-skips-service | api | 9 | `` line break ` `` | reason |", report)
        self.assertIn("| fix-capability | `shop/api/orders.py` | move \\!\\[it](u) &lt;there> | layered: sql in api "
                      "| ```line 7: " + span[3:] + " |", report)
        self.assertIn("| missing-folder | `shop/domain/events` | create it | layered: target |  |", report)
        for line in report.splitlines():
            outside = re.sub(r"(`+).*?\1", "", line)   # what is left of a line without its code spans
            self.assertNotRegex(outside, r"<!--|<img|!\[|(?<!\\)\[[^\]]*\]\(", line)
            if line.startswith("| `shop/api/orders.py` | sql "):
                self.assertEqual(7, len(re.findall(r"(?<!\\)\|", line)), line)   # six cells, however many pipes the evidence holds

    def test_the_command_of_the_next_steps_is_quoted_for_a_shell(self) -> None:
        """/* REQ-OUT-9931: the command REPORT.md prints under "Next steps" is safe to paste into a shell: a file name with spaces or shell characters (`;`, `$`, a quote) is one quoted word of `wingbox impact`, an ordinary path is printed as it is, and a file whose name holds a line break is never put into the command. */"""
        view = self._overview()

        def report_for(name: str) -> str:
            modules = tuple(dataclasses.replace(module, file=name) if module.id == "shop/utils/money" else module for module in view.modules)
            return overview.render_report(dataclasses.replace(view, modules=modules), "2026-11-15", "9.9.9")

        def command(report: str) -> str:
            return report.split("## Next steps", 1)[1].split("```")[1].strip("\n")

        self.assertEqual("wingbox impact shop/utils/money.py", command(report_for("shop/utils/money.py")))
        hostile = "src/core;touch${IFS}PWNED;.js"
        self.assertEqual("wingbox impact 'src/core;touch${IFS}PWNED;.js'", command(report_for(hostile)))
        self.assertEqual("wingbox impact 'my file.py'", command(report_for("my file.py")))
        self.assertEqual("""wingbox impact 'it'"'"'s $(id).py'""", command(report_for("it's $(id).py")))
        import shlex

        for name in (hostile, "my file.py", "it's $(id).py", "a`b`.py", "shop/utils/money.py"):
            self.assertEqual(["wingbox", "impact", name], shlex.split(command(report_for(name))), name)
        broken = report_for("a\n```\nrm -rf x\n```\n.py")
        self.assertNotIn("rm -rf x\n", broken.split("## Next steps", 1)[1])
        self.assertIn("wingbox impact shop/", command(broken))   # the next file of the list stands in

    def test_the_command_the_map_copies_is_quoted_for_a_shell(self) -> None:
        """/* REQ-OUT-9932: the command the map page shows and copies for a selected file, `wingbox impact <file>`, takes the file name through the page's `shellWord`, which gives an ordinary path as it is and any other name as one single-quoted word (a quote inside it closed, escaped and reopened), as Python's `shlex.quote` does; the function is run with node, and that part is skipped, and reported as skipped, where node is not installed. */"""
        import shlex
        import shutil
        import subprocess

        page = map_page.render_map(self._overview(), "2026-11-15", "9.9.9", map_page.read_assets())
        self.assertIn("const command = `wingbox impact ${shellWord(file)}${", page)
        self.assertNotIn("wingbox impact ${file}", page)
        definition = re.search(r"^const shellWord = .*$", page, re.M)
        self.assertIsNotNone(definition)
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed: the quoting function of the page was not run")
        names = ["shop/utils/money.py", "src/core;touch${IFS}PWNED;.js", "my file.py", "it's $(id).py", "a`b`.py", "C:\\dir\\x.py", "x\ny.py", ""]
        script = definition[0] + "\nprocess.stdout.write(JSON.stringify(" + json.dumps(names) + ".map(shellWord)));\n"
        done = subprocess.run([node], input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        quoted = json.loads(done.stdout)
        self.assertEqual([shlex.quote(name) for name in names], quoted)
        self.assertEqual("shop/utils/money.py", quoted[0])
        for name, word in zip(names, quoted):
            self.assertEqual([name], shlex.split(word), name)

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
        self.assertIn([".", "layered", "domain", "infra", "domain imports infrastructure directly"], data["rules"])
        self.assertEqual("layered: folder utils/ -> shared", data["labelRules"][data["modules"][4]["rule"]])
        self.assertEqual([5, 0, 0], data["placed"])
        self.assertEqual(0, data["candidates"])
        self.assertIn("https://d3js.org v7", page)
        self.assertIsNone(re.search(r'<(script|link|img)[^>]+(src|href)="https?://', page))

    def test_the_layers_view_is_brought_to_rest_before_it_is_drawn(self) -> None:
        """/* REQ-OUT-017: in the layers view the map settles the force layout synchronously before drawing (rebuild, layout change and hops change call settle), and choosing a file only redraws: nothing drifts on screen; the graph view keeps its live settling. */"""
        template = map_page.read_assets().template
        settle = template[template.index("function settle("):template.index("function applyForces(")]
        self.assertIn('if (state.layout !== "layers") { sim.alpha(alpha).restart(); return; }', settle)
        self.assertIn("sim.stop();", settle)
        self.assertIn("for (let t = 0; t < 300 && sim.alpha() > 0.02; t++) sim.tick();", settle)
        select = template[template.index("function select("):template.index("function", template.index("function select(") + 10)]
        self.assertIn('if (state.layout === "layers") requestDraw(); else sim.alpha(Math.max(sim.alpha(), 0.55)).restart();', select)
        for name in ("function rebuild(", "function setLayout(", "function setDepth("):
            body = template[template.index(name):template.index("\n}\n", template.index(name))]
            self.assertIn("settle(", body, name)
            self.assertNotIn(".restart()", body, name)

    def test_the_map_opens_on_a_file_with_a_readable_blast_radius(self) -> None:
        """/* REQ-OUT-018: the map opens on the most depended-on file whose 2-hop radius stays under 5 % of the modules; when none qualifies, on the file with the widest radius. */"""
        view = self._overview()
        modules = tuple(
            overview.ModuleInfo(id=f"m{i}", file=f"src/m{i}.py", lang="py", layer="service", layer_basis="path",
                                fan_in=fan_in, fan_out=0, within_two_hops=reach)
            for i, (fan_in, reach) in enumerate([(50, 60)] + [(3, 4)] + [(9, 4)] + [(1, 1)] * 97)
        )
        data = map_page.map_data(dataclasses.replace(view, modules=modules, edges=(), violations=()), "2026-11-16", "9.9.9")
        self.assertEqual(2, data["start"])
        wide = tuple(dataclasses.replace(m, within_two_hops=60) for m in modules[:3])
        data = map_page.map_data(dataclasses.replace(view, modules=wide, edges=(), violations=()), "2026-11-16", "9.9.9")
        self.assertEqual(0, data["start"])

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


def _layered(paths: dict[str, str]) -> dict[str, str]:
    """Layers of module IDs in a graph without architecture facts: one part, the layered style."""
    graph = code_graph_search.CodeGraph()
    for module, lang in paths.items():
        graph.nodes[module] = code_graph_search.Node(module, lang, "general module")
    return {module: code_graph_search.module_layer(graph, module) for module in paths}


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
        self.assertEqual(cases, _layered({path: "ts" for path in cases}))

    def test_migrations_and_seeds_are_infrastructure_even_under_a_schema_folder(self) -> None:
        """/* REQ-OUT-013: a migration or seed file is infrastructure wherever its folder lies; a schema folder around it does not make it domain. */"""
        cases = {
            "src/schema/migrations/1744910873969-InitialMigration": "infra",
            "src/schema/seeds/users": "infra",
            "src/schema/tables/asset.table": "domain",
            "db/migrations/0001_initial": "infra",
        }
        self.assertEqual(cases, _layered({path: "ts" for path in cases}))

    def test_a_folder_names_a_layer_a_file_name_does_not(self) -> None:
        """/* REQ-OUT-015: the index file of a layer folder (controllers/index.ts is the module src/controllers) belongs to that layer; a file named like a layer folder (types.ts, models.py) does not, and a name that only contains a layer word (class3, utils3d/s3) is not that layer. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("src/controllers/index.ts", "src/types.ts", "app/models.py", "src/class3.ts", "s3/bucket.ts"):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text("", encoding="utf-8")
            graph = code_graph_search.CodeGraph()
            graph._root = str(root)
            for module, lang in (("src/controllers", "ts"), ("src/types", "ts"), ("app/models", "py"),
                                 ("src/class3", "ts"), ("s3/bucket", "ts")):
                graph.nodes[module] = code_graph_search.Node(module, lang, "general module")
            layers = {module: code_graph_search.module_layer(graph, module) for module in graph.nodes}
        self.assertEqual(
            {"src/controllers": "api", "src/types": "other", "app/models": "other", "src/class3": "other",
             "s3/bucket": "other"},
            layers,
        )

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
        """/* REQ-OUT-006: `wingbox build` must write .wingbox/REPORT.md and .wingbox/map.html next to the graph. */"""
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
        report = root / ".wingbox" / "REPORT.md"
        page = root / ".wingbox" / "map.html"
        self.assertTrue(report.is_file())
        self.assertTrue(page.is_file())
        self.assertIn("# Wingbox report: " + root.name, report.read_text(encoding="utf-8"))
        self.assertIn(".wingbox/map.html", output.getvalue())


if __name__ == "__main__":
    unittest.main()
