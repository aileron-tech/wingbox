"""Rust: a member named through a type path (`Type::member`) depends on the file whose impl block defines it."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph
from scripts.lang.rust import parse_rust_treesitter

FILES = {
    "src/main.rs": "mod settings;\nmod app;\nmod shapes;\nfn main() {}\n",
    "src/settings/mod.rs": (
        "mod running;\nmod seed;\nmod plan;\nmod show;\nmod store;\npub mod handle;\n"
        "pub use running::Running;\npub use running::Running as Live;\npub use running::Short;\n"
    ),
    "src/settings/running.rs": (
        "pub struct Running { pub idle: u64 }\npub struct Boxed<T>(pub T);\n"
        "impl Running {\n    pub fn defaults() -> Running { Running { idle: 0 } }\n}\n"
        "pub type Short = Running;\n"
    ),
    # Inherent impl blocks of a type another file defines.
    "src/settings/seed.rs": (
        "use super::running::Running;\nuse super::running::Boxed;\n"
        "impl Running {\n"
        "    pub const LIMIT: u64 = 3;\n"
        "    pub fn from_env() -> Running { Self::from_lookup(|_| None) }\n"
        "    pub fn from_lookup(_lookup: impl Fn(&str) -> Option<String>) -> Running { Running::defaults() }\n"
        "}\n"
        "impl<T> Boxed<T> {\n    pub fn wrap(value: T) -> Boxed<T> { Boxed(value) }\n}\n"
    ),
    "src/settings/plan.rs": (
        "use super::running::Running;\n"
        "impl Running {\n    pub fn plan(&self) -> u64 { Self::LIMIT }\n    pub fn fresh() -> Running { Self::defaults() }\n}\n"
    ),
    # A trait impl in another file than the type: the path names the type and the trait, not this file.
    "src/settings/show.rs": (
        "use super::running::Running;\npub trait Show { fn show() -> u8; }\n"
        "impl Show for Running {\n    fn show() -> u8 { 1 }\n}\n"
    ),
    "src/settings/store.rs": "pub fn unrelated() {}\n",
    # An alias of the type in a third module: a file that names only the alias reaches the type through its member.
    "src/settings/handle.rs": "pub type Handle = super::running::Running;\n",
    "src/app/by_handle.rs": "use crate::settings::handle::Handle;\npub fn f() -> Handle { Handle::defaults() }\n",
    "src/app/mod.rs": (
        "mod by_use;\nmod by_path;\nmod by_alias;\nmod by_glob;\nmod generic;\nmod qualified;\nmod only_type;\n"
        "mod by_value;\nmod variant;\nmod local_name;\nmod by_handle;\n"
    ),
    "src/app/by_use.rs": "use crate::settings::Running;\npub fn f() -> Running { Running::from_env() }\n",
    "src/app/by_path.rs": "pub fn f() -> u64 { crate::settings::Running::LIMIT }\n",
    "src/app/by_alias.rs": (
        "use crate::settings::{Live, Short};\ntype Mine = crate::settings::Running;\n"
        "pub fn f() { Live::from_env(); Short::fresh(); Mine::from_lookup(|_| None); }\n"
    ),
    "src/app/by_glob.rs": "use crate::settings::*;\npub fn f() -> Running { Running::from_env() }\n",
    "src/app/generic.rs": "use crate::settings::running::Boxed;\npub fn f() -> Boxed<u8> { Boxed::<u8>::wrap(1) }\n",
    "src/app/qualified.rs": "use crate::settings::Running;\npub fn f() -> Running { <Running>::fresh() }\n",
    "src/app/only_type.rs": "use crate::settings::Running;\npub fn f() -> Running { Running::defaults() }\n",
    # A method call on a value needs the type of the value: no edge from names alone.
    "src/app/by_value.rs": "use crate::settings::Running;\npub fn f(r: &Running) -> u64 { r.plan() }\n",
    "src/shapes.rs": "pub enum Kind { Round, Square }\n",
    "src/app/variant.rs": "use crate::shapes::Kind;\npub fn f() -> Kind { Kind::Round }\n",
    # A local of the same name as a member, and a module function of the same name: not members of the type.
    "src/app/local_name.rs": "pub fn from_env() {}\npub fn f() { from_env(); crate::settings::store::unrelated(); }\n",
}


class RustTypeMemberTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")
            results = []
            for relative, content in FILES.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                results.append(parse_rust_treesitter(str(path), content, root.name, path.stat().st_mtime))
            with redirect_stderr(io.StringIO()):
                graph = build_graph(results, str(root))
        if not isinstance(graph, Ok):
            raise AssertionError(graph.error)
        cls.imports = {
            (edge["source"], edge["target"]): tuple(sorted(edge.get("relations", [])))
            for edge in graph.value.edges
            if edge["type"] == "IMPORTS"
        }

    def targets(self, source: str) -> dict[str, tuple[str, ...]]:
        return {target: relations for (src, target), relations in self.imports.items() if src == source}

    def test_a_member_named_through_a_type_path_depends_on_the_file_of_its_impl_block(self) -> None:
        """/* REQ-RUST-031: `Type::member` (an associated function or constant of an inherent impl block that lies in another file than the type) gives an edge, marked :item, to the file whose impl block defines the member, whether the type is named through a `use`, a path through a facade, a glob import, a renaming re-export, a type alias, with generic arguments (`Type::<T>::member`) or as `<Type>::member`; the edge to the file of the type stays. A member the file of the type defines, named through an alias of the type in a third module (`Handle::defaults()`), gives the edge to the file of the type. */"""
        by_use = self.targets("src/app/by_use")
        self.assertEqual(("item",), by_use["src/settings/seed"])
        self.assertEqual(("item",), by_use["src/settings/running"])
        self.assertEqual((), by_use["src/settings"])
        self.assertNotIn("src/settings/plan", by_use)
        self.assertEqual(("item",), self.targets("src/app/by_path")["src/settings/seed"])
        by_alias = self.targets("src/app/by_alias")
        self.assertEqual(("item",), by_alias["src/settings/seed"])
        self.assertEqual(("item",), by_alias["src/settings/plan"])
        self.assertEqual(("item",), self.targets("src/app/by_glob")["src/settings/seed"])
        self.assertEqual(("item",), self.targets("src/app/generic")["src/settings/seed"])
        by_handle = self.targets("src/app/by_handle")
        self.assertEqual((), by_handle["src/settings/handle"])
        self.assertEqual(("item",), by_handle["src/settings/running"])
        qualified = self.targets("src/app/qualified")
        self.assertEqual(("item",), qualified["src/settings/plan"])
        self.assertNotIn("src/settings/seed", qualified)

    def test_self_in_an_impl_block_names_the_members_of_the_other_impl_blocks(self) -> None:
        """/* REQ-RUST-047: `Self::member` inside an impl block of a type names a member another file's impl block of that type defines: plan.rs (`Self::LIMIT`) depends on seed.rs; a member the file of the type defines adds no edge (`Self::defaults()`). */"""
        plan = self.targets("src/settings/plan")
        self.assertEqual(("item",), plan["src/settings/seed"])
        self.assertIn("src/settings/running", plan)
        self.assertNotIn("src/settings/plan", self.targets("src/settings/seed"))

    def test_what_needs_the_type_of_a_value_or_a_trait_gives_no_edge(self) -> None:
        """/* REQ-RUST-048: a member the file of the type defines, a method called on a value (`r.plan()`), an enum variant, a trait impl in another file, a function of the same name as a member and a module function give no edge to an impl file. */"""
        only_type = self.targets("src/app/only_type")
        self.assertNotIn("src/settings/seed", only_type)
        self.assertNotIn("src/settings/plan", only_type)
        self.assertNotIn("src/settings/show", only_type)
        self.assertNotIn("src/settings/plan", self.targets("src/app/by_value"))
        self.assertEqual({"src/shapes": ()}, self.targets("src/app/variant"))
        self.assertEqual({"src/settings/store": ()}, self.targets("src/app/local_name"))
        self.assertNotIn("src/settings/show", self.targets("src/app/by_use"))


if __name__ == "__main__":
    unittest.main()
