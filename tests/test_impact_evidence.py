"""`impact` says why each file is on the list: the relation of the edge, the item names, the file it is reached through."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import build_code_graph, code_graph_search
from scripts.build_code_graph import Ok, build_graph

RUST_FILES = {
    "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n',
    "src/main.rs": "mod native;\nmod settings;\nmod store;\nmod many;\nfn main() {}\n",
    "src/native/mod.rs": "mod actions;\nmod flow;\nmod scripts;\n",
    "src/native/scripts.rs": "pub fn script() -> u8 { 1 }\npub fn other() -> u8 { 2 }\n",
    "src/native/actions.rs": "use super::scripts::script;\npub struct HarEntry;\npub fn act() -> u8 { script() + super::scripts::other() }\n",
    # replay.rs names HarEntry only after `use super::*`: its parent imports the name privately.
    "src/native/flow/mod.rs": "mod replay;\nuse crate::native::actions::HarEntry;\npub fn f(_: HarEntry) {}\n",
    "src/native/flow/replay.rs": "use super::*;\npub fn r(_: HarEntry) {}\n",
    "src/settings/mod.rs": "mod running;\nmod seed;\npub use running::Running;\n",
    "src/settings/running.rs": "pub struct Running;\nimpl Running {\n    pub fn defaults() -> Running { Running }\n}\n",
    "src/settings/seed.rs": (
        "use super::running::Running;\n"
        "impl Running {\n    pub fn from_env() -> Running { Running::defaults() }\n    pub fn from_lookup() -> Running { Running }\n}\n"
    ),
    "src/store.rs": "use crate::settings::Running;\npub fn s() -> Running { Running::from_lookup(); Running::from_env() }\n",
    "src/many.rs": "pub struct A;\npub struct B;\npub struct C;\npub struct D;\npub struct E;\n",
    "src/settings/wide.rs": "use crate::many::{A, B, C, D, E};\npub fn w(_: A, _: B, _: C, _: D, _: E) {}\n",
}
TS_FILES = {
    "src/core/util.ts": "export const limit = 3;\n",
    "src/billing/invoice.ts": 'import { limit } from "../core/util";\nexport const invoice = limit;\n',
    "src/billing/report.ts": 'import { limit } from "../core/util";\nexport const report = limit;\n',
    "src/api/handler.ts": 'import { invoice } from "../billing/invoice";\nimport { report } from "../billing/report";\nexport const handle = invoice + report;\n',
}


def loaded_graph(root: Path, files: dict[str, str], language: str) -> tuple[code_graph_search.CodeGraph, str]:
    """Build the graph of a tree, publish it and load it as the search commands do; also the graph text."""
    results = []
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative in files:
        if relative.endswith((".rs", ".ts")):
            results.append(build_code_graph.parse_file((str(root / relative), language, root.name)))
    with redirect_stderr(io.StringIO()):
        built = build_graph(results, str(root))
    if not isinstance(built, Ok):
        raise AssertionError(built.error)
    text = build_code_graph.render_sexp(built.value.nodes, built.value.edges, str(root), "syntax", len(results))
    sexp_path = root / ".wingbox" / "index.sexp"
    sexp_path.parent.mkdir(exist_ok=True)
    sexp_path.write_text(text, encoding="utf-8")
    graph = code_graph_search.CodeGraph()
    if not isinstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk):
        raise AssertionError("the graph does not load")
    return graph, text


def impact(graph: code_graph_search.CodeGraph, target: str, depth: int = 2, sexp: bool = False) -> list[str]:
    output = io.StringIO()
    with redirect_stdout(output):
        code_graph_search.cmd_impact(graph, [target], depth, "flat" if sexp else "human")
    return output.getvalue().splitlines()


def row(lines: list[str], file: str) -> str:
    """The text of the row of a file: what follows the file name."""
    found = [line.split(None, 1)[1] for line in lines if line.startswith(f"  {file} ")]
    if len(found) != 1:
        raise AssertionError(f"{file}: {found} in {lines}")
    return found[0]


class ImpactEvidenceRustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cls.graph, cls.text = loaded_graph(Path(directory).resolve(), RUST_FILES, "rust")
            cls.scripts = impact(cls.graph, "src/native/scripts.rs")
            cls.scripts_facts = impact(cls.graph, "src/native/scripts.rs", sexp=True)
            cls.running = impact(cls.graph, "src/settings/running.rs")
            cls.running_facts = impact(cls.graph, "src/settings/running.rs", sexp=True)
            cls.seed = impact(cls.graph, "src/settings/seed.rs")
            cls.seed_facts = impact(cls.graph, "src/settings/seed.rs", sexp=True)
            cls.many = impact(cls.graph, "src/many.rs", 1)
            cls.many_facts = impact(cls.graph, "src/many.rs", 1, sexp=True)

    def test_a_rust_import_edge_records_the_names_the_resolver_followed(self) -> None:
        """/* REQ-RUST-041: an import edge of a Rust file records the items the resolver followed to its target, one list per way the name arrives: `(:use ..)` an item of the module named in a `use` or a path, `(:reexport "<facade as written>" ..)` an item a facade re-exports, `(:glob "<module of the glob import>" ..)` a name used after a glob import, `(:member "<type as written>" ..)` a member an impl block of the target defines, `(:macro ..)` an exported macro; at most three names, sorted, and `:of <count>` when there are more. A `mod` declaration and an import of the module itself record nothing. */"""
        edges = [line for line in self.text.splitlines() if line.startswith("(imports ")]
        self.assertTrue(any(line.endswith(" (:use other script))") for line in edges), edges)
        self.assertTrue(any(line.endswith(' :item (:reexport "crate::settings" Running))') for line in edges), edges)
        self.assertTrue(any(line.endswith(' :candidate :item (:glob "super" HarEntry))') for line in edges), edges)
        self.assertTrue(any(line.endswith(' :item (:member "Running" from_env from_lookup))') for line in edges), edges)
        self.assertTrue(any(line.endswith(" (:use A B C :of 5))") for line in edges), edges)
        plain = [line for line in edges if "(:" not in line]
        self.assertGreaterEqual(len(plain), 8, edges)

    def test_impact_names_the_item_and_how_it_arrives(self) -> None:
        """/* REQ-CS-076: each row of `impact` says why the file is on the list. Depth 1: `imports it` for an import of the file, `imports <names> from it` for items named in a `use` or a path, `uses <names>, re-exported by <facade>` through a `pub use` facade, `uses <Type>::<member>, defined in an impl block there` for a member of a type, `uses <names> by name after `use <module>::*`` after a glob import; three names at most, then the number of the others. The facts carry the same lists as the edge. */"""
        self.assertEqual("imports other, script from it", row(self.scripts, "src/native/actions.rs"))
        self.assertEqual("imports it", row(self.scripts, "src/native/mod.rs"))
        self.assertEqual("imports Running from it", row(self.running, "src/settings/mod.rs"))
        self.assertEqual("imports it", row(self.seed, "src/settings/mod.rs"))
        self.assertEqual("uses Running, re-exported by crate::settings", row(self.running, "src/store.rs"))
        self.assertEqual("uses Running::from_env, Running::from_lookup, defined in an impl block there", row(self.seed, "src/store.rs"))
        self.assertEqual("imports A, B, C and 2 more from it", row(self.many, "src/settings/wide.rs"))
        self.assertIn("(impact src/native/scripts src/native/actions :lang rs :via imports :depth 1 (:use other script))", self.scripts_facts)
        self.assertIn("(impact src/native/scripts src/native :lang rs :via imports :depth 1)", self.scripts_facts)
        self.assertIn('(impact src/settings/running src/store :lang rs :via imports :depth 1 (:reexport "crate::settings" Running))', self.running_facts)
        self.assertIn('(impact src/settings/seed src/store :lang rs :via imports :depth 1 (:member "Running" from_env from_lookup))', self.seed_facts)
        self.assertIn("(impact src/many src/settings/wide :lang rs :via imports :depth 1 (:use A B C :of 5))", self.many_facts)

    def test_a_deeper_row_names_the_file_it_is_reached_through(self) -> None:
        """/* REQ-CS-077: a row at depth 2 or deeper names the file of the previous depth it is reached through (the first in the order of the list), says how many other files of that depth it also depends on, and gives the relation of that hop: replay.rs, which only has `use super::*`, is listed for scripts.rs as `through src/native/actions.rs: uses HarEntry by name after `use super::*``. The fact carries `:through <module>` and `:more <count>`. */"""
        self.assertEqual("through src/native/actions.rs: uses HarEntry by name after `use super::*`", row(self.scripts, "src/native/flow/replay.rs"))
        self.assertEqual("through src/native/actions.rs: imports HarEntry from it", row(self.scripts, "src/native/flow/mod.rs"))
        self.assertEqual("through src/native/mod.rs: imports it", row(self.scripts, "src/main.rs"))
        self.assertIn(
            '(impact src/native/scripts src/native/flow/replay :lang rs :via imports :depth 2 :through src/native/actions (:glob "super" HarEntry))',
            self.scripts_facts)
        self.assertIn("(impact src/native/scripts src/main :lang rs :via imports :depth 2 :through src/native)", self.scripts_facts)


class ImpactEvidenceTypeScriptTest(unittest.TestCase):
    def test_a_typescript_row_at_depth_2_names_the_file_it_is_reached_through(self) -> None:
        """/* REQ-CS-077: in any language a deeper row names the file it is reached through and how many other files of that depth lead to it: `through src/billing/invoice.ts (or 1 other file): imports it`; the graph of a TypeScript tree records no item lists. */"""
        with tempfile.TemporaryDirectory() as directory:
            graph, text = loaded_graph(Path(directory).resolve(), TS_FILES, "typescript")
            lines = impact(graph, "src/core/util.ts")
            facts = impact(graph, "src/core/util.ts", sexp=True)
        self.assertNotIn("(:", text)
        self.assertEqual("imports it", row(lines, "src/billing/invoice.ts"))
        self.assertEqual("through src/billing/invoice.ts (or 1 other file): imports it", row(lines, "src/api/handler.ts"))
        self.assertIn("(impact src/core/util src/billing/report :lang ts :via imports :depth 1)", facts)
        self.assertIn("(impact src/core/util src/api/handler :lang ts :via imports :depth 2 :through src/billing/invoice :more 1)", facts)


if __name__ == "__main__":
    unittest.main()
