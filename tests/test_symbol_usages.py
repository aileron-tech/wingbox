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


class NameResolutionTests(unittest.TestCase):
    def test_typescript_imports_follow_reexport_chains_to_the_definition(self) -> None:
        """/* REQ-TS-SYM-001: a named, aliased or default import of S through barrels (`export * from`, `export { X as Y } from`, `export * as ns from`) references S in its defining file; the barrels that name S reference it too; module dependencies (impact) do not change. */"""
        _nodes, edges = _graph({
            "src/core/model.ts": "export class Session {}\nexport function open() {}\nexport default function main() {}\n",
            "src/core/index.ts": "export * from './model';\nexport { open as start } from './model';\nexport * as models from './model';\nexport { default } from './model';\n",
            "src/index.ts": "export * from './core';\n",
            "src/app.ts": "import { Session } from './index';\nimport { start as begin } from './core';\nimport run from './core/index';\n",
            "src/other.ts": "import { Session as S2 } from './core/model';\n",
        })
        self.assertEqual({"src/app", "src/other"}, _users(edges, "src/core/model", "Session"))
        self.assertEqual({"src/core", "src/app"}, _users(edges, "src/core/model", "open"))
        self.assertEqual({"src/core", "src/app"}, _users(edges, "src/core/model", "main"))
        self.assertNotIn(("src/app", "src/core/model"), _module_pairs(edges))

    def test_reexport_cycles_end_without_an_answer(self) -> None:
        """/* REQ-LANG-010: re-export cycles (TypeScript `export *` both ways, Python packages importing each other, Rust `pub use` loops) end the resolution; a name defined nowhere names no file. */"""
        _nodes, ts_edges = _graph({
            "a.ts": "export * from './b';\nexport const real = 1;\n",
            "b.ts": "export * from './a';\n",
            "c.ts": "import { missing, real } from './b';\n",
        })
        self.assertEqual({"c"}, _users(ts_edges, "a", "real"))
        self.assertFalse(any(edge["target"].endswith("::missing") for edge in ts_edges))
        _nodes, py_edges = _graph({
            "p/__init__.py": "from .q import *\n",
            "p/q.py": "from p import *\nfrom p import nothing\n",
            "p/user.py": "from p import nothing\n",
        })
        self.assertFalse(any(edge["target"].endswith("::nothing") for edge in py_edges))
        _nodes, rs_edges = _graph({
            "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
            "src/a.rs": "pub use crate::b::*;\n",
            "src/b.rs": "pub use crate::a::*;\npub use crate::a::Ghost;\n",
            "src/c.rs": "use crate::b::Ghost;\n",
        }, cargo=True)
        self.assertFalse(any(edge["target"].endswith("::Ghost") for edge in rs_edges))


class UseSiteTests(unittest.TestCase):
    def test_typescript_namespace_members_and_shadowing(self) -> None:
        """/* REQ-TS-SYM-002: `ns.S` after `import * as ns`, `<ns.Comp />` and `ns.Enum.Member` are uses of S, Comp and Enum; a parameter or local variable named like the namespace shadows it and its members are not uses. */"""
        _nodes, edges = _graph({
            "src/a.ts": "export const config = {};\nexport class Store {}\nexport function helper() {}\nexport enum Mode { A }\n",
            "src/b.ts": "import * as a from './a';\nexport function run() { return a.helper(); }\n",
            "src/c.ts": "import * as a from './a';\nexport function f(a: { config: number }) { return a.config; }\n"
                        "export const g = () => { const a = { Mode: 1 }; return a.Mode; };\n",
            "src/d.tsx": "import * as a from './a';\nexport const V = () => <a.Store />;\nexport const m = a.Mode.A;\n",
            "src/e.ts": "import * as a from './a';\nexport function h({ a }: { a: { helper: () => void } }) { a.helper(); }\n"
                        "export function k() { try { } catch (a) { a.config; } }\n",
        })
        self.assertEqual({"src/b"}, _users(edges, "src/a", "helper"))
        self.assertEqual(set(), _users(edges, "src/a", "config"))
        self.assertEqual({"src/d"}, _users(edges, "src/a", "Store"))
        self.assertEqual({"src/d"}, _users(edges, "src/a", "Mode"))

    def test_typescript_local_names_shadow_imported_names(self) -> None:
        """/* REQ-TS-SYM-003: a TypeScript file that imports S is a user of S (the import names it); a file whose only S is its own declaration, parameter or type parameter is not. */"""
        _nodes, edges = _graph({
            "src/model.ts": "export class Session {}\nexport type Id = string;\n",
            "src/user.ts": "import { Session } from './model';\nexport function run(s: Session) { return s; }\n",
            "src/own.ts": "class Session {}\nexport function f<Id>(x: Id): Session { return new Session(); }\n",
            "src/param.ts": "export function g(Session: number) { return Session; }\n",
        })
        self.assertEqual({"src/user"}, _users(edges, "src/model", "Session"))
        self.assertEqual(set(), _users(edges, "src/model", "Id"))


def _parsed(relative_path: str, content: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        language = detect_source_language(path.name)
        assert language is not None, relative_path
        result = parse_file((str(path), language, "repo"))
    return {
        "entities": {entity["name"]: entity for entity in result.entities},
        "bindings": [(b["local"], b["module"], b["name"], b["export"], b["scope"]) for b in result.bindings],
    }


class SymbolTableTests(unittest.TestCase):
    def test_typescript_symbol_table_lists_every_top_level_definition_and_reexport(self) -> None:
        """/* REQ-LANG-008: the symbol table of a TypeScript file holds every top-level definition (abstract classes, generators, var and let without a value, declare, namespaces, enums with their members) and its bindings: named, aliased, default and namespace imports in scope, `export { X as Y } from`, `export * from`, `export * as ns from` and `export default` for importers only. */"""
        table = _parsed("src/mod.ts", (
            "import D, * as ns from './a';\n"
            "import { X as Y, Z } from './b';\n"
            "export { P as Q, R } from './c';\n"
            "export * from './d';\n"
            "export * as nsx from './e';\n"
            "export abstract class Base {}\n"
            "export function* gen() {}\n"
            "var counter = 1;\n"
            "export let later;\n"
            "export declare const declared: number;\n"
            "namespace Space { export const inner = 1; }\n"
            "export enum Color { Red, Green = 2 }\n"
            "export default function main() {}\n"
        ))
        entities = table["entities"]
        for name, kind in (("Base", "class"), ("gen", "function"), ("counter", "function"), ("later", "function"),
                           ("declared", "function"), ("Space", "module"), ("Color", "enum"), ("main", "function")):
            self.assertEqual(kind, entities[name]["kind"], name)
        self.assertNotIn("inner", entities)
        self.assertEqual(["Red", "Green"], entities["Color"].get("methods"))
        self.assertEqual([
            ("D", "./a", "default", False, True),
            ("ns", "./a", "", False, True),
            ("Y", "./b", "X", False, True),
            ("Z", "./b", "Z", False, True),
            ("Q", "./c", "P", True, False),
            ("R", "./c", "R", True, False),
            ("*", "./d", "*", True, False),
            ("nsx", "./e", "", True, False),
            ("default", "", "main", True, False),
        ], table["bindings"])

    def test_python_and_rust_symbol_tables_list_definitions_and_reexports(self) -> None:
        """/* REQ-LANG-009: the symbol table of a Python file holds its module-level imports as bindings importers see (a package re-export), and that of a Rust file its items (enums with their variants) and its `use` bindings, `pub use` marked as re-exports. */"""
        python = _parsed("pkg/__init__.py", "import os.path\nimport json as j\nfrom .impl import run as start\nfrom .impl import *\n")
        self.assertEqual([
            ("os", "os", "", True, True),
            ("j", "json", "", True, True),
            ("start", ".impl", "run", True, True),
            ("*", ".impl", "*", True, True),
        ], python["bindings"])
        rust = _parsed("src/lib.rs", (
            "mod net;\n"
            "pub use net::{Reach as Route, host};\n"
            "use crate::model::*;\n"
            "pub enum Status { Ready, Done }\n"
            "pub trait Runner {}\n"
            "pub static LIMIT: u32 = 3;\n"
        ))
        self.assertEqual(["Ready", "Done"], rust["entities"]["Status"].get("methods"))
        for name in ("Runner", "LIMIT", "Status"):
            self.assertIn(name, rust["entities"])
        self.assertEqual([
            ("Route", "net", "Reach", True, True),
            ("host", "net", "host", True, True),
            ("*", "crate::model", "*", False, True),
        ], rust["bindings"])


if __name__ == "__main__":
    unittest.main()
