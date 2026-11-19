"""Regressions for defects found by checking the graph against real repositories."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import build_code_graph
from scripts.build_code_graph import Ok, build_graph, discover_files


def _write(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class BinFolderDiscoveryTest(unittest.TestCase):
    def test_a_bin_folder_with_sources_is_discovered(self) -> None:
        """/* REQ-CS-045: a folder named bin is skipped only as a .NET build output; source entry points in src/bin stay in the graph. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, "src/index.ts", "export const server = 1;\n")
            _write(root, "src/bin/main.ts", "import {server} from '../index.js';\nconsole.log(server);\n")
            _write(root, "crate/src/lib.rs", "pub fn run() {}\n")
            _write(root, "crate/src/bin/tool.rs", "fn main() {}\n")
            _write(root, "dotnet/App.csproj", "<Project />\n")
            _write(root, "dotnet/Program.cs", "class Program {}\n")
            _write(root, "dotnet/bin/Debug/Generated.cs", "class Generated {}\n")

            discovered = sorted(
                Path(item[0]).resolve().relative_to(root.resolve()).as_posix() for item in discover_files(str(root))
            )

        self.assertEqual(
            ["crate/src/bin/tool.rs", "crate/src/lib.rs", "dotnet/Program.cs", "src/bin/main.ts", "src/index.ts"],
            discovered,
        )


class ModuleIdTwinsTest(unittest.TestCase):
    def _graph(self, root: Path, files: dict[str, tuple[str, str]]) -> build_code_graph.GraphBuildOutput:
        results = []
        for relative_path, (content, language) in files.items():
            path = _write(root, relative_path, content)
            result = build_code_graph.parse_file((str(path), language, "fixture"))
            self.assertIsNotNone(result)
            results.append(result)
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"graph build failed: {graph_result.error}")
        return graph_result.value

    def test_files_that_differ_only_by_extension_both_become_modules(self) -> None:
        """/* REQ-CS-046: files whose names differ only by extension must both be modules: the first keeps the plain ID, the twin is named by its path. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self._graph(
                root,
                {
                    "src/lib/account.query.test.ts": ("import {load} from './account';\nload();\n", "typescript"),
                    "src/lib/account.query.test.tsx": ("import {load} from './account';\nexport const View = () => load();\n", "typescript"),
                    "src/lib/account.ts": ("export function load() { return 1; }\n", "typescript"),
                    "scripts/steer.py": ("VALUE = 1\n", "python"),
                    "scripts/steer.ts": ("export const VALUE = 1;\n", "typescript"),
                },
            )
            node_ids = sorted(node["id"] for node in graph.nodes)
            imports = {
                (edge["source"], edge["target"].split("::")[0])
                for edge in graph.edges
                if edge["type"] == "IMPORTS"
            }

        self.assertEqual(
            [
                "scripts/steer",
                "scripts/steer.py",
                "src/lib/account",
                "src/lib/account.query.test",
                "src/lib/account.query.test.tsx",
            ],
            node_ids,
        )
        self.assertIn(("src/lib/account.query.test", "src/lib/account"), imports)
        self.assertIn(("src/lib/account.query.test.tsx", "src/lib/account"), imports)


class LayerRulesFromRealRepositoriesTest(unittest.TestCase):
    def _graph(self, root: Path):
        from scripts import code_graph_search

        sexp_path = root / ".wingbox/index.sexp"

        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            "(code-graph :schema 1 :files 6 :nodes 6 :edges 3 :violations 0)\n"
            "(paths\n"
            "  (web\n    (app\n      (models\n        (m0 edit-model-dialog)\n        (m1 edit-model-dialog.test)\n      )\n    )\n"
            "    (components\n      (m2 label)\n    )\n  )\n"
            "  (src\n    (tools\n      (m3 console)\n    )\n    (domain\n      (m4 order)\n    )\n  )\n"
            "  (pkg\n    (m5 ledger)\n  )\n"
            ")\n"
            '(node m0 tsx "general module")\n(node m1 tsx "general module")\n(node m2 tsx "general module")\n'
            '(node m3 ts "general module")\n(node m4 ts "general module")\n(node m5 ts "repository")\n'
            "(imports m0 m2)\n(imports m4 m2)\n(imports m4 m5)\n",
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
        return code_graph_search, graph

    def test_a_jsx_component_is_ui_and_tools_folders_are_not_config(self) -> None:
        """/* REQ-CS-049: a .tsx component is UI whatever its folder is called (a route folder named models is not the domain), and a folder named tools or cli is not configuration. */"""
        with tempfile.TemporaryDirectory() as directory:
            search, graph = self._graph(Path(directory))
            layers = {module: search.module_layer(graph, module) for module in sorted(graph.nodes)}
            basis = search.module_layer_basis(graph, "web/app/models/edit-model-dialog")

        self.assertEqual(
            {
                "pkg/ledger": "infra",
                "src/domain/order": "domain",
                "src/tools/console": "other",
                "web/app/models/edit-model-dialog": "ui",
                "web/app/models/edit-model-dialog.test": "test",
                "web/components/label": "ui",
            },
            layers,
        )
        self.assertEqual("path", basis)
        for module, expected in (("cli/src/native/browser", "other"), ("scripts/release", "config")):
            alone = search.CodeGraph()
            alone.nodes[module] = search.Node(module, "ts", "general module")
            self.assertEqual(expected, search.module_layer(alone, module), module)

    def test_boundaries_separates_folder_based_violations_from_guesses(self) -> None:
        """/* REQ-CS-050: `boundaries` must print a violation only when both layers are certain; a pair that rests on a guessed layer is a candidate. */"""
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as directory:
            search, graph = self._graph(Path(directory))
            output = io.StringIO()
            with redirect_stdout(output):
                search.cmd_boundaries(graph, "all")
            lines = output.getvalue().splitlines()

        self.assertEqual(1, sum(line.startswith("(violation ") for line in lines), lines)
        self.assertTrue(any(line.startswith("(violation src/domain/order web/components/label ") for line in lines), lines)
        self.assertTrue(any(line.startswith("(violation-candidate src/domain/order pkg/ledger ") for line in lines), lines)
        self.assertIn("(boundaries-summary :violations 1 :known 0 :type-only 0 :candidates 1 :checked 6)", lines)


class ImpactTargetTest(unittest.TestCase):
    def test_impact_accepts_index_files_and_extension_twins(self) -> None:
        """/* REQ-CS-048: `impact` must accept the path of an index file (its module is the folder) and of a file named by its full path. */"""
        import io
        from contextlib import redirect_stdout

        from scripts import code_graph_search

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for relative in ("src/lib/index.ts", "src/app.ts", "src/steer.ts", "src/steer.py"):
                _write(root, relative, "")
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                "(code-graph :schema 1 :files 4 :nodes 4 :edges 2 :violations 0)\n"
                "(paths\n  (src\n    (m0 lib)\n    (m1 app)\n    (m2 steer)\n    (m3 steer.py)\n  )\n)\n"
                "(node m0 ts \"general module\")\n(node m1 ts \"general module\")\n"
                "(node m2 ts \"general module\")\n(node m3 py \"general module\")\n"
                "(imports m1 m0)\n(imports m1 m3)\n",
                encoding="utf-8",
            )
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)

            def impact(target: str) -> str:
                output = io.StringIO()
                with redirect_stdout(output):
                    code_graph_search.cmd_impact(graph, [target], 2, "flat")
                return output.getvalue()

            by_index = impact("src/lib/index.ts")
            by_twin = impact("src/steer.py")
            by_plain = impact("src/steer.ts")

        self.assertIn("(impact-root src/lib ", by_index)
        self.assertIn(":direct 1", by_index)
        self.assertIn("(impact-root src/steer.py ", by_twin)
        self.assertIn(":direct 1", by_twin)
        self.assertIn("(impact-root src/steer ", by_plain)
        self.assertIn(":direct 0", by_plain)


class ImpactRelationTest(unittest.TestCase):
    def test_a_dependent_that_imports_the_file_is_reported_as_an_import(self) -> None:
        """/* REQ-CS-053: when a file imports the target and also has another relation with it (data flow, inheritance), `impact`, `deps` and `rdeps` report the relation as imports; another relation is named only when there is no import. */"""
        import io
        from contextlib import redirect_stdout

        from scripts import code_graph_search

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                "(code-graph :schema 1 :files 4 :nodes 4 :edges 5 :violations 0)\n"
                "(paths\n  (app\n    (m0 models)\n    (m1 routes)\n    (m2 forms)\n    (m3 plugin)\n  )\n)\n"
                "(node m0 py \"general module\")\n(node m1 py \"general module\")\n"
                "(node m2 py \"general module\")\n(node m3 py \"general module\")\n"
                "(calls-api m1 m0 :via-ifc)\n(calls-api m3 m0 :via-ifc)\n"
                "(data-flow m2 m0 :validated)\n"
                "(imports m1 m0)\n(imports m2 m0)\n",
                encoding="utf-8",
            )
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
            output = io.StringIO()
            with redirect_stdout(output):
                code_graph_search.cmd_impact(graph, ["app/models"], 1, "flat")

        self.assertIn("(impact app/models app/routes :lang py :via imports :depth 1)", output.getvalue())
        self.assertIn("(impact app/models app/forms :lang py :via imports :depth 1)", output.getvalue())
        self.assertIn("(impact app/models app/plugin :lang py :via calls-api :depth 1)", output.getvalue())
        self.assertEqual({"app/models": "imports"}, code_graph_search.module_dependencies(graph, "app/forms"))


class QuotedBracketTest(unittest.TestCase):
    def test_a_quoted_closing_bracket_inside_a_list_does_not_hang_the_reader(self) -> None:
        """/* REQ-CS-047: a quoted ")" inside a (:methods ...) list must be read as a value; the graph reader must finish and keep the values. */"""
        import threading

        from scripts import code_graph_search

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                "(code-graph :schema 1 :files 2 :nodes 2 :edges 1 :violations 0)\n"
                "(paths\n  (src\n    (m0 links)\n    (m1 page)\n  )\n)\n"
                "(node m0 ts \"general module\")\n"
                "(node m1 ts \"general module\")\n"
                '(fn m0 CLOSING_BRACKETS 130 :eid 04a44cc2 (:methods ")" "]" "}"))\n'
                '(fn m0 OPENERS 131 (:methods "(" "a b"))\n'
                "(imports m1 m0)\n",
                encoding="utf-8",
            )
            graph = code_graph_search.CodeGraph()
            outcome: list[object] = []
            reader = threading.Thread(
                target=lambda: outcome.append(graph.load(str(sexp_path), str(root))), daemon=True
            )
            reader.start()
            reader.join(10)

            self.assertFalse(reader.is_alive(), "the graph reader did not finish")
            self.assertIsInstance(outcome[0], code_graph_search.MapLoadOk)
            methods = {entity.name: entity.methods for entity in graph.entities["src/links"]}

        self.assertEqual({"CLOSING_BRACKETS": [")", "]", "}"], "OPENERS": ["(", "a b"]}, methods)
        self.assertEqual(["m1", "m0", ":flag"], code_graph_search.tokenize("(imports m1 m0 :flag)")[1:])
        self.assertEqual(["x"], code_graph_search.tokenize("(a) x)")[1:])


if __name__ == "__main__":
    unittest.main()


class QualifiedBaseClassTest(unittest.TestCase):
    def _graph(self, root: Path, files: dict[str, str]) -> build_code_graph.GraphBuildOutput:
        results = []
        for relative_path, content in files.items():
            path = _write(root, relative_path, content)
            result = build_code_graph.parse_file((str(path), "python", "fixture"))
            self.assertIsNotNone(result)
            results.append(result)
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"graph build failed: {graph_result.error}")
        return graph_result.value

    def test_a_base_class_reached_through_a_module_follows_that_module(self) -> None:
        """/* REQ-CS-051: a Python base class written as module.Class depends on the module that the name is bound to by an import; a module of an external package gives no edge, even when a local class has the same name. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self._graph(
                root,
                {
                    "micrograd/micrograd/nn.py": "class Module:\n    def parameters(self):\n        return []\n",
                    "minGPT/mingpt/model.py": "import torch.nn as nn\n\n\nclass GPT(nn.Module):\n    pass\n",
                    "nanoGPT/model.py": "import torch\n\n\nclass GPT(torch.nn.Module):\n    pass\n",
                    "app/layers.py": "class Layer:\n    pass\n",
                    "app/aliased.py": "import app.layers as layers\n\n\nclass Aliased(layers.Layer):\n    pass\n",
                    "app/plain.py": "import app.layers\n\n\nclass Plain(app.layers.Layer):\n    pass\n",
                    "app/named.py": "from app import layers as blocks\n\n\nclass Named(blocks.Layer):\n    pass\n",
                    "app/sibling.py": "from . import layers\n\n\nclass Sibling(layers.Layer):\n    pass\n",
                    "app/unbound.py": "class Holder:\n    class Module:\n        pass\n\n\nclass Unbound(Holder.Module):\n    pass\n",
                },
            )

        base_edges = sorted(
            (edge["source"], edge["target"]) for edge in graph.edges if edge["type"] == "CALLS_API"
        )
        references = sorted(
            (edge["source"], edge["target"]) for edge in graph.edges if edge["type"] == "REFERENCES"
        )
        bases = {
            entity["name"]: entity.get("bases", [])
            for node in graph.nodes
            if node["id"] == "minGPT/mingpt/model"
            for entity in node["entities"]
        }

        self.assertEqual(
            [
                ("app/aliased", "app/layers"),
                ("app/named", "app/layers"),
                ("app/plain", "app/layers"),
                ("app/sibling", "app/layers"),
            ],
            base_edges,
        )
        self.assertIn(("app/aliased::Aliased", "app/layers::Layer"), references)
        self.assertEqual({"GPT": ["nn.Module"]}, bases)


class BaseClassByNameTest(unittest.TestCase):
    def _graph(self, root: Path, files: dict[str, tuple[str, str]]) -> build_code_graph.GraphBuildOutput:
        results = []
        for relative_path, (content, language) in files.items():
            path = _write(root, relative_path, content)
            result = build_code_graph.parse_file((str(path), language, "fixture"))
            self.assertIsNotNone(result)
            results.append(result)
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"graph build failed: {graph_result.error}")
        return graph_result.value

    def test_a_base_class_is_never_matched_by_name_across_languages_or_without_an_import(self) -> None:
        """/* REQ-CS-052: a base class that the file does not import is matched by name only inside the same language; in Python and TypeScript, where a name is visible only through an import, only a wildcard import can provide it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self._graph(
                root,
                {
                    "web/errors.ts": ("export class OcrError extends Error {}\n", "typescript"),
                    "acp/types.go": ("package acp\n\ntype Error struct {\n\tMessage string\n}\n", "go"),
                    "web/widget.ts": ("export class Widget {}\n", "typescript"),
                    "web/panel.ts": ("export class Panel extends Widget {}\n", "typescript"),
                    "py/shapes.py": ("class Shape:\n    pass\n", "python"),
                    "py/star.py": ("from py.shapes import *\n\n\nclass Circle(Shape):\n    pass\n", "python"),
                    "py/loose.py": ("class Square(Shape):\n    pass\n", "python"),
                },
            )

        base_edges = sorted(
            (edge["source"], edge["target"]) for edge in graph.edges if edge["type"] == "CALLS_API"
        )

        self.assertEqual([("py/star", "py/shapes")], base_edges)


class TestPurposeTest(unittest.TestCase):
    def test_a_name_that_only_begins_with_test_is_not_a_test(self) -> None:
        """/* REQ-LANG-005: the purpose "test" is given to files in a test folder or named as a test; a file whose name or folder only begins with the letters "test" (test.email.tsx, testclient.py, testimonials/) is not a test. */"""
        purpose = lambda path: build_code_graph.infer_purpose(path, "", "typescript", [])
        not_tests = ["src/emails/test.email.tsx", "pkg/testclient.py", "web/testimonials/card.tsx", "src/contest/latest.ts"]
        tests = [
            "src/render.test.ts",
            "src/render.spec.tsx",
            "tests/render.ts",
            "src/__tests__/render.ts",
            "pkg/test_loader.py",
            "internal/llm/client_test.go",
            "packages/test-utils/render.ts",
            "e2e/login.ts",
        ]
        self.assertEqual([], [path for path in not_tests if "test" in purpose(path).split("; ")])
        self.assertEqual([], [path for path in tests if "test" not in purpose(path).split("; ")])


class TypeOnlyImportTest(unittest.TestCase):
    FILES = {
        "src/repositories/asset.repository.ts": (
            "export type AssetStats = { total: number };\n"
            "export interface AssetRow { id: string }\n"
            "export class AssetRepository {}\n"
            "export function loadAsset(): void {}\n",
            "typescript",
        ),
        "src/dtos/stats.dto.ts": (
            "import { AssetStats, AssetRow } from '../repositories/asset.repository';\n"
            "export class StatsDto { stats?: AssetStats; row?: AssetRow }\n",
            "typescript",
        ),
        "src/dtos/typed.dto.ts": (
            "import type { AssetRepository } from '../repositories/asset.repository';\n"
            "import { type AssetRow } from '../repositories/asset.repository';\n"
            "export class TypedDto { repository?: AssetRepository; row?: AssetRow }\n",
            "typescript",
        ),
        "src/dtos/mixed.dto.ts": (
            "import { AssetStats, loadAsset } from '../repositories/asset.repository';\n"
            "export class MixedDto { stats?: AssetStats; load = loadAsset }\n",
            "typescript",
        ),
        "src/dtos/namespace.dto.ts": (
            "import { AssetStats } from '../repositories/asset.repository';\n"
            "import * as repository from '../repositories/asset.repository';\n"
            "export class NamespaceDto { stats?: AssetStats; load = repository.loadAsset }\n",
            "typescript",
        ),
        "src/dtos/effect.dto.ts": (
            "import '../repositories/asset.repository';\n"
            "export class EffectDto {}\n",
            "typescript",
        ),
    }

    def _build(self, root: Path) -> build_code_graph.GraphBuildOutput:
        results = []
        for relative_path, (content, language) in self.FILES.items():
            path = _write(root, relative_path, content)
            results.append(build_code_graph.parse_file((str(path), language, "fixture")))
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"graph build failed: {graph_result.error}")
        return graph_result.value

    def test_an_import_of_types_only_is_marked_on_the_import_edge(self) -> None:
        """/* REQ-CS-054: a TypeScript import edge is marked type-only when every import of the target is `import type`, or binds only names that the target defines as a type or an interface; a value name, a namespace import or a side-effect import of the same file makes it a normal import. */"""
        with tempfile.TemporaryDirectory() as directory:
            graph = self._build(Path(directory).resolve())

        marked = {
            edge["source"]: bool(edge.get("type_only"))
            for edge in graph.edges
            if edge["type"] == "IMPORTS" and edge["target"] == "src/repositories/asset.repository"
        }
        self.assertEqual(
            {
                "src/dtos/effect.dto": False,
                "src/dtos/mixed.dto": False,
                "src/dtos/namespace.dto": False,
                "src/dtos/stats.dto": True,
                "src/dtos/typed.dto": True,
            },
            marked,
        )

    def test_boundaries_prints_type_only_imports_apart_from_violations(self) -> None:
        """/* REQ-CS-055: `boundaries` must print an import of types only across layers as (violation-type-only ...) and count it as :type-only in the summary, not as a violation. */"""
        import io
        from contextlib import redirect_stdout

        from scripts import code_graph_search

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                "(code-graph :schema 1 :files 3 :nodes 3 :edges 2 :violations 0)\n"
                "(paths\n  (src\n    (dtos\n      (m0 stats.dto)\n      (m1 mixed.dto)\n    )\n"
                "    (repositories\n      (m2 asset.repository)\n    )\n  )\n)\n"
                '(node m0 ts "general module")\n(node m1 ts "general module")\n(node m2 ts "general module")\n'
                "(imports m0 m2 :type-only)\n(imports m1 m2)\n",
                encoding="utf-8",
            )
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
            output = io.StringIO()
            with redirect_stdout(output):
                code_graph_search.cmd_boundaries(graph, "all")
            lines = output.getvalue().splitlines()

        self.assertTrue(any(line.startswith("(violation src/dtos/mixed.dto src/repositories/asset.repository ") for line in lines), lines)
        self.assertTrue(
            any(line.startswith("(violation-type-only src/dtos/stats.dto src/repositories/asset.repository ") for line in lines), lines
        )
        self.assertIn("(boundaries-summary :violations 1 :known 0 :type-only 1 :candidates 0 :checked 3)", lines)

    def test_the_summary_counts_file_pairs_not_lines(self) -> None:
        """/* REQ-CS-058: `boundaries-summary` must count pairs of files, as the report does; a pair that is printed once per relation (imports and data-flow) counts once. */"""
        import io
        from contextlib import redirect_stdout

        from scripts import code_graph_search

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sexp_path = root / ".wingbox/index.sexp"
            sexp_path.parent.mkdir(exist_ok=True)
            sexp_path.write_text(
                "(code-graph :schema 1 :files 2 :nodes 2 :edges 2 :violations 0)\n"
                "(paths\n  (src\n    (domain\n      (m0 order)\n    )\n    (repositories\n      (m1 order.repository)\n    )\n  )\n)\n"
                '(node m0 ts "general module")\n(node m1 ts "general module")\n'
                "(imports m0 m1)\n(data-flow m0 m1)\n",
                encoding="utf-8",
            )
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
            output = io.StringIO()
            with redirect_stdout(output):
                code_graph_search.cmd_boundaries(graph, "all")
            lines = output.getvalue().splitlines()

        self.assertEqual(2, sum(line.startswith("(violation ") for line in lines), lines)
        self.assertIn("(boundaries-summary :violations 1 :known 0 :type-only 0 :candidates 0 :checked 2)", lines)


class AnswerSizeTest(unittest.TestCase):
    def _hub(self, root: Path, dependents: int):
        from scripts import code_graph_search

        _write(root, "src/hub.ts", "")
        _write(root, "src/base.ts", "")
        names = [f"u{index:03d}" for index in range(dependents)]
        for name in names:
            _write(root, f"src/users/{name}.ts", "")
        leaves = "".join(f"      (m{index + 2} {name})\n" for index, name in enumerate(names))
        nodes = "".join(f'(node m{index} ts "general module")\n' for index in range(dependents + 2))
        edges = "(imports m0 m1)\n" + "".join(f"(imports m{index + 2} m0)\n(refs m{index + 2} m0::Hub)\n" for index in range(dependents))
        sexp_path = root / ".wingbox/index.sexp"
        sexp_path.parent.mkdir(exist_ok=True)
        sexp_path.write_text(
            f"(code-graph :schema 1 :files {dependents + 2} :nodes {dependents + 2} :edges {dependents + 1} :violations 0)\n"
            "(paths\n  (src\n    (m0 hub)\n    (m1 base)\n    (users\n" + leaves + "    )\n  )\n)\n"
            + nodes + "(cls m0 Hub 1)\n" + edges,
            encoding="utf-8",
        )
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
        return code_graph_search, graph

    def _lines(self, call) -> list[str]:
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            call()
        return output.getvalue().splitlines()

    def test_analyze_names_each_dependent_once_with_a_path_from_the_root(self) -> None:
        """/* REQ-CS-056: `analyze` must name each dependent file on one line only and print paths relative to the repository root. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            search, graph = self._hub(root, 3)
            lines = self._lines(lambda: search.cmd_analyze(graph, "src/hub"))

        text = "\n".join(lines)
        self.assertNotIn(str(root), text)
        self.assertEqual(1, sum("src/users/u001" in line and not line.startswith(("(key-entity-ref", "(refs")) for line in lines), lines)
        self.assertIn("(analyze-dependent src/hub src/users/u001 :lang ts :via imports refs :file src/users/u001.ts)", lines)
        self.assertIn("(analyze-import src/hub src/base :lang ts :file src/base.ts)", lines)
        self.assertEqual(
            ["(boundary-file src/hub src/hub.ts)", "(boundary-file src/hub src/base.ts)"],
            [line for line in lines if line.startswith("(boundary-file")],
        )
        self.assertFalse(any(line.startswith("(rdep ") for line in lines), lines)
        self.assertIn("(analyze-summary src/hub :entities 1 :imports 1 :dataflows 0 :refs 0 :dependents 3 :boundary-files 2)", lines)

    def test_analyze_and_impact_stop_at_the_file_limit_and_say_how_to_get_the_rest(self) -> None:
        """/* REQ-CS-057: with the list limits (the text of --human, the flat facts of --sexp) `analyze` and `impact` must print the counts and at most 200 dependent files, then one line that gives the number left out and names --all; with --all they print every file. The default form has no such limit (REQ-CS-117). */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            search, graph = self._hub(root, 205)
            analyze = self._lines(lambda: search.cmd_analyze(graph, "src/hub"))
            analyze_all = self._lines(lambda: search.cmd_analyze(graph, "src/hub", show_all=True))
            impact = self._lines(lambda: search.cmd_impact(graph, ["src/hub.ts"], 2, "human"))
            impact_all = self._lines(lambda: search.cmd_impact(graph, ["src/hub.ts"], 2, "human", show_all=True))
            impact_sexp = self._lines(lambda: search.cmd_impact(graph, ["src/hub.ts"], 2, "flat"))

        self.assertEqual(200, sum(line.startswith("(analyze-dependent") for line in analyze))
        self.assertIn('(analyze-more src/hub :dependents 5 :rerun "analyze src/hub --all")', analyze)
        self.assertEqual(20, sum(line.startswith("(key-entity-ref") for line in analyze))
        self.assertIn('(key-entity-more src/hub Hub :modules 185 :rerun "analyze src/hub --all")', analyze)
        self.assertTrue(any(":dependents 205 " in line for line in analyze if line.startswith("(analyze-summary")), analyze[-1])
        self.assertEqual(205, sum(line.startswith("(analyze-dependent") for line in analyze_all))
        self.assertEqual(205, sum(line.startswith("(key-entity-ref") for line in analyze_all))
        self.assertFalse(any("-more " in line for line in analyze_all))

        self.assertEqual(200, sum(line.startswith("  src/users/") and "imports" in line for line in impact))
        self.assertIn("  ... 5 more files: rerun with --all", impact)
        self.assertIn("  direct dependents   205", impact)
        self.assertEqual(205, sum(line.startswith("  src/users/") and "imports" in line for line in impact_all))
        self.assertFalse(any("rerun with --all" in line for line in impact_all))
        self.assertEqual(200, sum(line.startswith("(impact src/hub ") for line in impact_sexp))
        self.assertIn('(impact-more src/hub :hidden 5 :rerun "impact src/hub.ts 2 --all")', impact_sexp)
        self.assertIn("(impact-summary src/hub :direct 205 :total 205 :depth 2)", impact_sexp)
        self.assertEqual(11, sum(line.startswith("(read-first ") for line in impact_sexp))
        self.assertIn("(read-first-more :hidden 195)", impact_sexp)


class PurposeLocationTest(unittest.TestCase):
    def test_folders_above_the_repository_do_not_change_the_purpose(self) -> None:
        """/* REQ-LANG-006: the purpose of a file is inferred from its path inside the repository; folders above the repository root (app, test, domain) must not change it. */"""
        files = {
            "src/billing/invoice.py": "class Invoice:\n    pass\n",
            "src/billing/total.py": "def total(items):\n    return sum(items)\n",
        }
        purposes = []
        with tempfile.TemporaryDirectory() as directory:
            for parent in ("neutral", "app/domain/test/services"):
                root = Path(directory).resolve() / parent / "checkout"
                found = {}
                for relative_path, content in files.items():
                    path = _write(root, relative_path, content)
                    found[relative_path] = build_code_graph.parse_file((str(path), "python", "fixture", str(root))).purpose
                purposes.append(found)

        self.assertEqual(purposes[0], purposes[1])

    def test_a_class_named_like_an_api_is_not_called_an_api_client(self) -> None:
        """/* REQ-LANG-007: a class whose name ends in API (FastAPI, OpenAPI) says nothing about client or server; the module must not get the purpose "API client" from it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = _write(root, "web/applications.py", "class FastAPI:\n    pass\n")
            purpose = build_code_graph.parse_file((str(path), "python", "fixture", str(root))).purpose

        self.assertNotIn("API client", purpose)


class RuntimeModuleReferenceTest(unittest.TestCase):
    def test_import_meta_resolve_of_a_literal_is_a_dependency(self) -> None:
        """/* REQ-TS-RT-001: `import.meta.resolve('<literal>')` names a module loaded at run time (a worker file) and must be recorded as a dependency of the file. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write(root, "src/worker.ts", "export const ready = true;\n")
            path = _write(
                root,
                "src/manager.ts",
                "export function start(): string {\n  return import.meta.resolve('./worker.js');\n}\n",
            )
            result = build_code_graph.parse_file((str(path), "typescript", "fixture", str(root)))

        self.assertEqual(["./worker.js"], result.imports)
