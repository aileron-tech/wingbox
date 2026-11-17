"""Python modules loaded at run time: importlib, configuration lists and dispatch through a registry."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import build_code_graph, code_graph_search
from scripts.build_code_graph import Ok, build_graph


LOADER = '''import importlib


class PluginLoader:
    def __init__(self):
        self._plugins = {}

    def load(self, names):
        for name in names:
            module = importlib.import_module(f".plugins.{name}", __package__)
            self._plugins[name] = module.Plugin

    def get(self, name):
        return self._plugins[name]


def get_loader():
    return PluginLoader()
'''

CHECKOUT = '''from .loader import get_loader


def total(cart, names):
    loader = get_loader()
    loader.load(names)
    for name in names:
        plugin_cls = loader.get(name)
        plugin = plugin_cls()
        cart = plugin.apply(cart, names)
        plugin.close()
    return cart
'''

PLUGIN = '''class Plugin:
    def apply(self, cart, names):
        return cart
'''

CLOSING_PLUGIN = '''class Plugin:
    def apply(self, cart, names):
        return cart

    def close(self):
        return None
'''

FILES = {
    "shop/__init__.py": "",
    "shop/loader.py": LOADER,
    "shop/checkout.py": CHECKOUT,
    "shop/helpers.py": "def fmt(value):\n    return str(value)\n",
    # The order of the list is the order of the pipeline: tax runs before discount.
    "shop/plugins.json": '{"plugins": [{"name": "tax"}, {"name": "discount"}, {"name": "absent"}], "title": "helpers"}\n',
    "shop/extra.yaml": "plugins:\n  - shipping  # added later\nowner: 'helpers'\n",
    "shop/plugins/__init__.py": "",
    "shop/plugins/tax.py": PLUGIN,
    "shop/plugins/discount.py": CLOSING_PLUGIN,
    "shop/plugins/shipping.py": PLUGIN,
    "shop/plugins/unused.py": PLUGIN,
    "shop/reports_loader.py": "import importlib\n\n\ndef report(kind):\n    return importlib.import_module(\"shop.reports.\" + kind)\n",
    "shop/reports.toml": "[reports]\nformats = [\"csv_report\"]\n",
    "shop/reports/__init__.py": "",
    "shop/reports/csv_report.py": "def render(rows):\n    return rows\n",
    "shop/literal.py": (
        "import importlib\nfrom importlib import import_module\n\n\n"
        "def warm():\n"
        "    importlib.import_module(\"shop.helpers\")\n"
        "    import_module(\".plugins.unused\", __package__)\n"
        "    __import__(\"shop.reports.csv_report\")\n"
    ),
    "shop/unbound.py": (
        "import importlib\n\n\n"
        "def load(name, group, item):\n"
        "    importlib.import_module(name)\n"
        "    importlib.import_module(f\".plugins.{group}.{item}\", __package__)\n"
    ),
    "other/loader.py": "import importlib\n\n\ndef load(name):\n    return importlib.import_module(f\"other.parts.{name}\")\n",
    "other/parts/engine.py": PLUGIN,
    "elsewhere/parts.json": '["engine"]\n',
}


def _write(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _graph(root: Path) -> build_code_graph.GraphBuildOutput:
    results = []
    for relative_path, content in FILES.items():
        path = _write(root, relative_path, content)
        if relative_path.endswith(".py"):
            result = build_code_graph.parse_file((str(path), "python", "fixture"))
            assert result is not None
            results.append(result)
    graph_result = build_graph(results, str(root))
    if not isinstance(graph_result, Ok):
        raise AssertionError(f"graph build failed: {graph_result.error}")
    return graph_result.value


def _pairs(graph: build_code_graph.GraphBuildOutput, edge_type: str) -> set[tuple[str, str]]:
    return {(edge["source"], edge["target"]) for edge in graph.edges if edge["type"] == edge_type}


class DynamicLoadingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls._directory.name).resolve()
        cls.graph = _graph(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_a_literal_module_name_given_to_importlib_is_an_import(self) -> None:
        """/* REQ-PY-001: `importlib.import_module("<literal>")`, `import_module(".<literal>", __package__)` and `__import__("<literal>")` name a local module loaded at run time; each one is an import edge of the calling file. */"""
        imports = _pairs(self.graph, "IMPORTS")
        self.assertIn(("shop/literal", "shop/helpers"), imports)
        self.assertIn(("shop/literal", "shop/plugins/unused"), imports)
        self.assertIn(("shop/literal", "shop/reports/csv_report"), imports)

    def test_a_module_name_filled_from_a_configuration_file_is_a_registry_wire(self) -> None:
        """/* REQ-PY-002: a module name built from a literal package path and one variable (f-string or +) is a registry wire to every module of that package named by a string value of a JSON, YAML or TOML file in the folder of the loading file; values that name no module of that package give nothing. */"""
        wires = _pairs(self.graph, "REGISTRY_WIRES")
        self.assertEqual(
            {
                ("shop/loader", "shop/plugins/tax"),
                ("shop/loader", "shop/plugins/discount"),
                ("shop/loader", "shop/plugins/shipping"),
                ("shop/reports_loader", "shop/reports/csv_report"),
            },
            wires,
        )
        by_target = {edge["target"]: edge for edge in self.graph.edges if edge["type"] == "REGISTRY_WIRES"}
        self.assertEqual("shop/plugins.json", by_target["shop/plugins/tax"]["config"])
        self.assertEqual("shop/extra.yaml", by_target["shop/plugins/shipping"]["config"])
        self.assertEqual("shop/reports.toml", by_target["shop/reports/csv_report"]["config"])
        self.assertNotIn(("shop/loader", "shop/plugins/tax"), _pairs(self.graph, "IMPORTS"))

    def test_a_name_without_a_literal_package_or_a_configuration_beside_the_loader_gives_no_wire(self) -> None:
        """/* REQ-PY-003: a module name that is only a variable, that has more than one variable, or whose values are listed only in a configuration file of another folder gives no edge. */"""
        sources = {source for source, _target in _pairs(self.graph, "REGISTRY_WIRES") | _pairs(self.graph, "IMPORTS")}
        self.assertNotIn("shop/unbound", sources)
        self.assertNotIn("other/loader", sources)

    def test_a_method_called_on_an_object_looked_up_in_a_registry_calls_the_wired_modules(self) -> None:
        """/* REQ-PY-004: in a loop, a method called on an object that a registry module returns for the loop variable is a call into each module the registry wires that defines the method; when the call result replaces one of its arguments (x = obj.m(x, ...)), the modules of one configuration list pass the data on in the order of that list. */"""
        calls = _pairs(self.graph, "CALLS")
        self.assertEqual(
            {
                ("shop/checkout::total", "shop/plugins/tax::apply"),
                ("shop/checkout::total", "shop/plugins/discount::apply"),
                ("shop/checkout::total", "shop/plugins/shipping::apply"),
                ("shop/checkout::total", "shop/plugins/discount::close"),
            },
            calls,
        )
        call_lines = {
            edge["target"]: edge["from_lines"] for edge in self.graph.edges if edge["type"] == "CALLS"
        }
        self.assertEqual([10], call_lines["shop/plugins/tax::apply"])
        pipeline = {
            (edge["source"], edge["target"])
            for edge in self.graph.edges
            if edge["type"] == "DATA_FLOWS_TO" and edge.get("pipeline")
        }
        self.assertEqual({("shop/plugins/tax", "shop/plugins/discount")}, pipeline)

    def test_the_code_graph_writes_the_new_facts_and_impact_follows_them(self) -> None:
        """/* REQ-PY-005: the code graph writes (registry-wires <loader> <module> :config <file>), registry calls as (calls ... :registry) and pipeline order as (data-flow ... :pipeline); `impact` of a wired module names the loader and the caller, and a pipeline step does not make the previous step its dependent. */"""
        sexp = build_code_graph.render_sexp(self.graph.nodes, self.graph.edges, str(self.root), "default", len(self.graph.nodes))
        sexp_path = self.root / "index.sexp"
        sexp_path.write_text(sexp, encoding="utf-8")
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(graph.load(str(sexp_path), str(self.root)), code_graph_search.MapLoadOk)
        facts = {(edge.etype, edge.source, edge.target, tuple(edge.flags)) for edge in graph.all_edges}
        self.assertIn(("registry-wires", "shop/loader", "shop/plugins/tax", (":config",)), facts)
        self.assertIn(("calls", "shop/checkout::total", "shop/plugins/tax::apply", (":lines", ":registry")), facts)
        self.assertIn(("data-flow", "shop/plugins/tax", "shop/plugins/discount", (":pipeline",)), facts)
        self.assertIn("(registry-wires ", sexp)
        self.assertIn(" :config shop/plugins.json)", sexp)

        output = io.StringIO()
        with redirect_stdout(output):
            code_graph_search.cmd_impact(graph, "shop/plugins/discount", 1, sexp=True)
        text = output.getvalue()
        self.assertIn("(impact shop/plugins/discount shop/loader :lang py :via registry-wires :depth 1)", text)
        self.assertIn("(impact shop/plugins/discount shop/checkout :lang py :via calls :depth 1)", text)
        self.assertNotIn("shop/plugins/tax", text)


if __name__ == "__main__":
    unittest.main()
