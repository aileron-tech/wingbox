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

        sexp_path = root / ".code_graph.sexp"
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
        self.assertEqual("other", search._classify_layer("cli/src/native/browser"))
        self.assertEqual("config", search._classify_layer("scripts/release"))

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
        self.assertIn("(boundaries-summary :violations 1 :candidates 1 :checked 6)", lines)


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
            sexp_path = root / ".code_graph.sexp"
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
                    code_graph_search.cmd_impact(graph, target, 2, sexp=True)
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
            sexp_path = root / ".code_graph.sexp"
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
                code_graph_search.cmd_impact(graph, "app/models", 1, sexp=True)

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
            sexp_path = root / ".code_graph.sexp"
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
