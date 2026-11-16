"""Which files use exported symbol S: the refs edges that `wingbox entity S` prints."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph, detect_source_language, parse_file


def _graph(files: dict[str, str], cargo: bool = False) -> tuple[list[dict], list[dict]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        if cargo:
            (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n', encoding="utf-8")
        results = []
        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            language = detect_source_language(path.name)
            assert language is not None, relative_path
            results.append(parse_file((str(path), language, root.name)))
        graph = build_graph(results, str(root))
        if not isinstance(graph, Ok):
            raise AssertionError(f"graph build failed: {graph.error}")
        return graph.value.nodes, graph.value.edges


def _users(edges: list[dict], module: str, name: str) -> set[str]:
    """Modules with a refs edge to module::name (the answer of `entity name` for that definition)."""
    return {
        edge["source"].split("::")[0]
        for edge in edges
        if edge["type"] == "REFERENCES" and edge["target"] == f"{module}::{name}"
    }


def _module_pairs(edges: list[dict]) -> set[tuple[str, str]]:
    """Module adjacency that impact walks: every edge except refs reached through a re-export."""
    return {
        (edge["source"].split("::")[0], edge["target"].split("::")[0])
        for edge in edges
        if "reexport" not in edge.get("relations", [])
    }


def _entities(nodes: list[dict], module: str) -> dict[str, dict]:
    node = next(node for node in nodes if node["id"] == module)
    return {entity["name"]: entity for entity in node.get("entities", [])}


class PythonSymbolUsageTests(unittest.TestCase):
    def test_module_level_variables_are_entities(self) -> None:
        """/* REQ-PY-001: public module-level names bound by assignment (plain, annotated, Final, TypeAlias, tuple targets) or listed in __all__ are entities of kind function; private names and bare annotations are not. */"""
        nodes, _edges = _graph({
            "app/config.py": (
                "from typing import Final, TypeAlias\n"
                "__all__ = ['_exported']\n"
                "app = make()\n"
                "ROUTE_COUNT: int = 3\n"
                "LIMIT: Final = 10\n"
                "Pair: TypeAlias = tuple[int, int]\n"
                "first, second = 1, 2\n"
                "_private = 1\n"
                "_exported = 2\n"
                "declared: int\n"
                "if True:\n"
                "    flag = True\n"
                "def make():\n"
                "    local = 1\n"
                "    return local\n"
            ),
        })
        entities = _entities(nodes, "app/config")
        for name in ("app", "ROUTE_COUNT", "LIMIT", "Pair", "first", "second", "_exported", "flag", "make"):
            self.assertIn(name, entities)
            self.assertEqual("function", entities[name]["kind"])
        for name in ("_private", "declared", "local", "__all__"):
            self.assertNotIn(name, entities)
        self.assertEqual(3, entities["app"]["line"])

    @unittest.skipIf(sys.version_info < (3, 12), "the type statement needs Python 3.12")
    def test_type_statement_is_an_entity(self) -> None:
        """/* REQ-PY-002: a `type X = ...` alias (Python 3.12) is an entity like an assignment. */"""
        nodes, _edges = _graph({"app/types.py": "type UserId = int\n"})
        self.assertIn("UserId", _entities(nodes, "app/types"))

    def test_imported_and_qualified_variables_are_referenced(self) -> None:
        """/* REQ-PY-003: `from m import X`, `from pkg import m` + `m.X` and `import pkg.m as mm` + `mm.X` reference X of m, for variables, classes and functions. */"""
        _nodes, edges = _graph({
            "app/__init__.py": "",
            "app/admin.py": "router = Router()\nclass Settings: ...\n",
            "app/main.py": "from app import admin\nfrom . import admin as adm\n\ndef run():\n    return admin.router, adm.Settings\n",
            "app/other.py": "import app.admin as mm\nprint(mm.router)\n",
            "app/direct.py": "from app.admin import router\n",
            "app/deep.py": "import app.admin\nprint(app.admin.Settings)\n",
        })
        self.assertEqual({"app/main", "app/other", "app/direct"}, _users(edges, "app/admin", "router"))
        self.assertEqual({"app/main", "app/deep"}, _users(edges, "app/admin", "Settings"))

    def test_reexports_through_init_lead_to_the_definition(self) -> None:
        """/* REQ-PY-004: a name re-exported by a package __init__.py (from .shared import X) is referenced at its defining file by the re-exporting file and by every importer of the package; module dependencies (impact) do not change. */"""
        files = {
            "pkg/__init__.py": "",
            "pkg/compat/__init__.py": "from .shared import lenient as lenient\nfrom .shared import *\n",
            "pkg/compat/shared.py": "def lenient(): ...\nLIMIT = 3\n",
            "pkg/routing.py": "from pkg.compat import lenient\nfrom pkg import compat\nprint(compat.LIMIT)\n",
            "pkg/utils.py": "from .compat import LIMIT\n",
        }
        _nodes, edges = _graph(files)
        self.assertEqual({"pkg/compat/__init__", "pkg/routing"}, _users(edges, "pkg/compat/shared", "lenient"))
        self.assertEqual({"pkg/routing", "pkg/utils"}, _users(edges, "pkg/compat/shared", "LIMIT"))
        self.assertNotIn(("pkg/routing", "pkg/compat/shared"), _module_pairs(edges))
        self.assertNotIn(("pkg/utils", "pkg/compat/shared"), _module_pairs(edges))

    def test_name_collisions_and_shadowing_name_no_other_file(self) -> None:
        """/* REQ-PY-005: two modules defining the same name are told apart by the import, and a parameter or local variable that shadows an imported module is not a use of the module's names. */"""
        _nodes, edges = _graph({
            "web/__init__.py": "",
            "web/one.py": "app = App()\n",
            "web/two.py": "app = App()\n",
            "web/test_one.py": "from web.one import app\n",
            "web/test_two.py": "from web import two\nprint(two.app)\n",
            "web/shadow.py": "from web import one\n\ndef handler(one):\n    return one.app\n\ndef other():\n    one = load()\n    return one.app\n",
            "web/local.py": "app = 1\nprint(app)\n",
        })
        self.assertEqual({"web/test_one"}, _users(edges, "web/one", "app"))
        self.assertEqual({"web/test_two"}, _users(edges, "web/two", "app"))


class RustSymbolUsageTests(unittest.TestCase):
    def test_glob_imports_reach_definitions_and_reexports(self) -> None:
        """/* REQ-RUST-015: a name used after `use a::b::*` references S where it is defined: in b itself or behind a `pub use x::*` of b; module dependencies (impact) do not change. */"""
        _nodes, edges = _graph({
            "src/lib.rs": "pub mod flow;\npub mod elf;\npub mod linker;\n",
            "src/flow/mod.rs": "mod outcome;\nmod http;\npub use outcome::*;\n",
            "src/flow/outcome.rs": "pub struct TierResult;\npub fn unused() {}\n",
            "src/flow/http.rs": "use super::*;\npub fn get() -> TierResult { TierResult }\n",
            "src/elf.rs": "pub const PT_DYNAMIC: u32 = 2;\n",
            "src/linker.rs": "use crate::elf::*;\nfn emit() -> u32 { PT_DYNAMIC + 1 }\n",
        }, cargo=True)
        self.assertEqual({"src/flow/http"}, _users(edges, "src/flow/outcome", "TierResult"))
        self.assertEqual(set(), _users(edges, "src/flow/outcome", "unused"))
        self.assertEqual({"src/linker"}, _users(edges, "src/elf", "PT_DYNAMIC"))
        self.assertNotIn(("src/flow/http", "src/flow/outcome"), _module_pairs(edges))

    def test_pub_use_chains_and_use_declarations_are_references(self) -> None:
        """/* REQ-RUST-016: `use crate::flow::X` where flow re-exports X (`pub use net::{X}`) references X in net, and a `use` that names S is a reference to S even when S is not used further. */"""
        _nodes, edges = _graph({
            "src/lib.rs": "pub mod flow;\npub mod actions;\npub mod tabs;\npub mod main_loop;\n",
            "src/flow/mod.rs": "pub mod net;\npub use net::{recorded_local_host, RecordedReach};\n",
            "src/flow/net.rs": "pub enum RecordedReach { Local }\npub fn recorded_local_host() {}\n",
            "src/actions.rs": "use crate::flow::RecordedReach;\nfn run(r: RecordedReach) {}\n",
            "src/tabs.rs": "pub struct TabBinding;\n",
            "src/main_loop.rs": "use crate::tabs::TabBinding;\n",
        }, cargo=True)
        self.assertEqual({"src/flow", "src/actions"}, _users(edges, "src/flow/net", "RecordedReach"))
        self.assertEqual({"src/main_loop"}, _users(edges, "src/tabs", "TabBinding"))

    def test_qualified_paths_after_a_module_use_are_references(self) -> None:
        """/* REQ-RUST-017: after `use crate::screenshot` the path screenshot::take_screenshot() references take_screenshot, and super::flow::Flow references Flow where flow re-exports it from. */"""
        _nodes, edges = _graph({
            "src/lib.rs": "pub mod native;\n",
            "src/native/mod.rs": "pub mod screenshot;\npub mod flow;\npub mod actions;\n",
            "src/native/screenshot.rs": "pub fn take_screenshot() {}\n",
            "src/native/flow/mod.rs": "mod model;\npub use model::*;\n",
            "src/native/flow/model.rs": "pub struct Flow;\n",
            "src/native/actions.rs": (
                "use super::screenshot;\n"
                "struct State { flow: Option<Box<super::flow::Flow>> }\n"
                "fn shot() { screenshot::take_screenshot(); }\n"
            ),
        }, cargo=True)
        self.assertEqual({"src/native/actions"}, _users(edges, "src/native/screenshot", "take_screenshot"))
        self.assertEqual({"src/native/actions"}, _users(edges, "src/native/flow/model", "Flow"))

    def test_collisions_and_local_shadowing_name_no_other_file(self) -> None:
        """/* REQ-RUST-018: a glob-imported name that the file defines itself, binds as a local variable or parameter, or that two modules define, is not a reference to the other module's S. */"""
        _nodes, edges = _graph({
            "src/lib.rs": "pub mod io;\npub mod other;\npub mod own;\npub mod local;\npub mod user;\n",
            "src/io.rs": "pub fn w32() {}\npub fn link() {}\n",
            "src/other.rs": "pub fn link() {}\n",
            "src/own.rs": "use crate::io::*;\nfn w32() {}\nfn run() { w32(); }\n",
            "src/local.rs": "use crate::io::*;\nfn run(link: u32) -> u32 { let w32 = 1; w32 + link }\n",
            "src/user.rs": "use crate::other::link;\nfn run() { link(); }\n",
        }, cargo=True)
        self.assertEqual(set(), _users(edges, "src/io", "w32"))
        self.assertEqual(set(), _users(edges, "src/io", "link"))
        self.assertEqual({"src/user"}, _users(edges, "src/other", "link"))


if __name__ == "__main__":
    unittest.main()
