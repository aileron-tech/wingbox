"""Rust names imported through a module facade (`pub use child::*`) depend on the file that defines them."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph
from scripts.lang.rust import parse_rust_treesitter

# The shape of ccbox's src/domain: a facade that re-exports every child with `pub use child::*`.
FILES = {
    "src/main.rs": "mod app;\nmod domain;\nmod store;\nmod net;\nmod shapes;\nmod platform;\nmod api;\nmod prelude;\nfn main() {}\n",
    "src/domain/mod.rs": (
        "mod types;\nmod stats;\nmod timeline;\nmod gemini;\nmod skill_span;\n"
        "pub use types::*;\npub use stats::*;\npub use timeline::*;\npub use gemini::*;\npub use skill_span::*;\n"
        "pub fn helper() {}\n"
    ),
    "src/domain/types.rs": (
        "pub struct ForkContext;\npub enum ForkCut { Before }\npub const LIMIT: usize = 3;\n"
        "pub type Alias = u8;\npub static NAME: &str = \"x\";\npub trait Render {}\n"
    ),
    "src/domain/timeline.rs": "mod detail;\npub use detail::TimelineItem;\npub(crate) fn parse_time() {}\n",
    # A private item of the same name in a child the facade globs earlier: a glob re-export does not carry it.
    "src/domain/stats.rs": "fn parse_time() {}\npub struct Stats;\n",
    "src/app/time_user.rs": "use crate::domain::{parse_time, Stats};\npub fn t(_: Stats) { parse_time(); }\n",
    "src/domain/timeline/detail.rs": "pub struct TimelineItem;\n",
    "src/domain/gemini.rs": "pub fn gemini_fn() {}\n",
    "src/domain/skill_span.rs": "pub struct Span;\n",
    "src/store/mod.rs": "mod cache;\nmod disk;\npub use cache::Cache;\npub use disk::Disk as Storage;\n",
    "src/store/cache.rs": "pub struct Cache;\n",
    "src/store/disk.rs": "pub struct Disk;\n",
    "src/net/mod.rs": "mod client;\npub(crate) use client::Http;\n",
    "src/net/client.rs": "pub struct Http;\n",
    "src/shapes/mod.rs": "pub mod inline {\n    pub struct Circle;\n}\n",
    "src/platform/mod.rs": "#[path = \"impl_unix.rs\"]\nmod sys;\npub use sys::Fd;\n",
    "src/platform/impl_unix.rs": "pub struct Fd;\n",
    "src/app/mod.rs": (
        "mod fork;\nmod view;\nmod save;\nmod glob_user;\nmod path_user;\nmod helper_user;\nmod unknown_user;\n"
        "mod net_user;\nmod inner;\nmod tools;\nmod shape_user;\nmod platform_user;\nmod api_user;\nmod prelude_user;\n"
        "mod rename_user;\nmod glob_chain_user;\nmod time_user;\npub use tools::Tool;\n"
    ),
    "src/app/tools.rs": "pub struct Tool;\n",
    "src/app/fork.rs": "use crate::domain::{ForkContext, ForkCut, LIMIT, Alias, NAME, Render};\npub fn f(_: ForkContext) {}\n",
    "src/app/view.rs": "use crate::domain::TimelineItem;\npub fn v(_: TimelineItem) {}\n",
    "src/app/save.rs": "use crate::store::Cache;\npub fn s(_: Cache) {}\n",
    "src/app/glob_user.rs": "use crate::domain::*;\npub fn g(_: ForkContext) { gemini_fn(); }\n",
    "src/app/path_user.rs": "use crate::domain;\npub fn p(_: domain::ForkContext) {}\n",
    "src/app/helper_user.rs": "use crate::domain::helper;\npub fn h() { helper(); }\n",
    "src/app/unknown_user.rs": "use crate::domain::Generated;\npub fn u(_: Generated) {}\n",
    "src/app/net_user.rs": "use crate::net::Http;\npub fn n(_: Http) {}\n",
    "src/app/inner.rs": "use super::Tool;\npub fn i(_: Tool) {}\n",
    "src/app/shape_user.rs": "use crate::shapes::inline::Circle;\npub fn c(_: Circle) {}\n",
    "src/app/platform_user.rs": "use crate::platform::Fd;\npub fn d(_: Fd) {}\n",
    # Two facades in a row: api re-exports v1 with a glob, v1 (a mod.rs) re-exports handlers with a glob.
    "src/api/mod.rs": "mod v1;\npub use v1::*;\n",
    "src/api/v1/mod.rs": "mod handlers;\npub use handlers::*;\npub struct Version;\n",
    "src/api/v1/handlers.rs": "pub fn list_sessions() {}\npub struct Handler;\n",
    # A named re-export of a name that is itself re-exported by another facade.
    "src/prelude.rs": "pub use crate::domain::ForkContext;\npub use crate::store::Storage as Store;\n",
    "src/app/api_user.rs": "use crate::api::{list_sessions, Handler, Version};\npub fn a(_: Handler, _: Version) { list_sessions(); }\n",
    "src/app/prelude_user.rs": "use crate::prelude::{ForkContext, Store};\npub fn p(_: ForkContext, _: Store) {}\n",
    "src/app/rename_user.rs": "use crate::store::Storage;\npub fn r(_: Storage) {}\n",
    "src/app/glob_chain_user.rs": "use crate::api::*;\npub fn g(_: Handler) {}\n",
}


class RustReexportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n', encoding="utf-8")
            results = []
            for relative, content in FILES.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                results.append(parse_rust_treesitter(str(path), content, root.name, path.stat().st_mtime))
            errors = io.StringIO()
            with redirect_stderr(errors):
                graph = build_graph(results, str(root))
        if not isinstance(graph, Ok):
            raise AssertionError(graph.error)
        cls.log = errors.getvalue()
        cls.imports = {
            (edge["source"], edge["target"]): tuple(sorted(edge.get("relations", [])))
            for edge in graph.value.edges
            if edge["type"] == "IMPORTS"
        }

    def targets(self, source: str) -> dict[str, tuple[str, ...]]:
        return {target: relations for (src, target), relations in self.imports.items() if src == source}

    def test_a_name_imported_through_a_glob_reexport_depends_on_its_definer(self) -> None:
        """/* REQ-RUST-020: `use crate::m::{A, B}` where m re-exports its children with `pub use child::*` keeps its edge to the facade and adds an edge to the child that defines each name (struct, enum, const, type, static, trait), marked :item (impact follows it), and none to the other children; a name a child re-exports from a grandchild leads to the grandchild. */"""
        fork = self.targets("src/app/fork")
        self.assertEqual((), fork["src/domain"])
        self.assertEqual(("item",), fork["src/domain/types"])
        self.assertNotIn("src/domain/gemini", fork)
        self.assertNotIn("src/domain/skill_span", fork)
        view = self.targets("src/app/view")
        self.assertEqual(("item",), view["src/domain/timeline/detail"])
        self.assertIn("src/domain", view)
        self.assertNotIn("src/domain/timeline", view)

    def test_an_explicit_reexport_and_a_crate_visible_one_lead_to_the_definer(self) -> None:
        """/* REQ-RUST-021: `pub use child::Name` and `pub(crate) use child::Name` lead to the child that defines Name, and `use super::Name` of a name the parent re-exports does too; no other child of the facade gets an edge. */"""
        self.assertEqual(("item",), self.targets("src/app/save")["src/store/cache"])
        self.assertNotIn("src/store/disk", self.targets("src/app/save"))
        self.assertEqual(("item",), self.targets("src/app/net_user")["src/net/client"])
        self.assertEqual(("item",), self.targets("src/app/inner")["src/app/tools"])
        self.assertIn("src/app", self.targets("src/app/inner"))

    def test_a_glob_import_names_only_the_children_whose_items_the_body_uses(self) -> None:
        """/* REQ-RUST-022: `use crate::m::*` keeps its edge to the facade and adds the weaker :item :candidate edge to each child whose item the body names, and to no other child. */"""
        glob = self.targets("src/app/glob_user")
        self.assertEqual((), glob["src/domain"])
        self.assertEqual(("candidate", "item"), glob["src/domain/types"])
        self.assertEqual(("candidate", "item"), glob["src/domain/gemini"])
        self.assertNotIn("src/domain/skill_span", glob)

    def test_a_path_through_an_imported_module_leads_to_the_definer(self) -> None:
        """/* REQ-RUST-023: `use crate::m;` then `m::Name` in the body leads to the child that defines Name. */"""
        self.assertEqual(("item",), self.targets("src/app/path_user")["src/domain/types"])

    def test_a_name_of_the_facade_itself_or_one_that_cannot_be_followed_keeps_the_facade_only(self) -> None:
        """/* REQ-RUST-024: a name defined in the facade itself, a name in an inline module of the facade, a name no file defines (generated by a macro) and a name behind a #[path] module keep only the facade edge; the names that could not be followed are counted in the build log. */"""
        self.assertEqual({"src/domain": ()}, self.targets("src/app/helper_user"))
        self.assertEqual({"src/domain": ()}, self.targets("src/app/unknown_user"))
        self.assertEqual({"src/shapes": ()}, self.targets("src/app/shape_user"))
        self.assertEqual({"src/platform": ()}, self.targets("src/app/platform_user"))
        self.assertIn("Rust: 2 imported name(s) not followed to a definition", self.log)

    def test_reexport_chains_through_two_facades_lead_to_the_definer(self) -> None:
        """/* REQ-RUST-025: a name re-exported through two facades in a row (glob `pub use v1::*` then `pub use handlers::*`, or a named `pub use crate::domain::Name` of a name the next facade re-exports with a glob) gives one :item edge to the file that defines it, besides the edge to the first facade; a name the inner facade defines itself leads to that facade, and the facades in between get no edge. */"""
        api = self.targets("src/app/api_user")
        self.assertEqual({"src/api": (), "src/api/v1/handlers": ("item",), "src/api/v1": ("item",)}, api)
        prelude = self.targets("src/app/prelude_user")
        self.assertEqual(("item",), prelude["src/domain/types"])
        self.assertEqual((), prelude["src/prelude"])
        self.assertNotIn("src/domain", prelude)
        self.assertNotIn("src/store", prelude)
        glob = self.targets("src/app/glob_chain_user")
        self.assertEqual({"src/api": (), "src/api/v1/handlers": ("candidate", "item")}, glob)

    def test_a_renamed_reexport_leads_to_the_definer_of_the_original_name(self) -> None:
        """/* REQ-RUST-026: `pub use child::Name as Alias` leads an importer of Alias to the child that defines Name, also when another facade re-exports Alias under a second name (`pub use crate::store::Storage as Store`). */"""
        self.assertEqual({"src/store": (), "src/store/disk": ("item",)}, self.targets("src/app/rename_user"))
        self.assertEqual(("item",), self.targets("src/app/prelude_user")["src/store/disk"])

    def test_a_private_item_is_not_reexported_by_a_glob(self) -> None:
        """/* REQ-RUST-028: a glob re-export (`pub use child::*`) carries only the items of the child that the facade can see (pub, pub(crate), pub(super), pub(in ...)); a private item of the same name in a child globbed earlier is skipped, and the importer gets its :item edge to the child that defines the visible item (seen in a public Rust repository: a private `fn parse_rfc3339_to_unix_ms` in stats.rs and a pub(crate) one in timeline.rs). */"""
        time_user = self.targets("src/app/time_user")
        self.assertEqual(("item",), time_user["src/domain/timeline"])
        self.assertEqual(("item",), time_user["src/domain/stats"])   # Stats, a public item of stats.rs
        self.assertEqual(("item",), self.targets("src/app/fork")["src/domain/types"])

    def test_the_graph_is_the_same_on_every_build(self) -> None:
        """/* REQ-RUST-027: two builds of the same Rust tree, with the files parsed in a different order, give the same import edges with the same relations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n', encoding="utf-8")
            results = []
            for relative, content in FILES.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                results.append(parse_rust_treesitter(str(path), content, root.name, path.stat().st_mtime))
            graphs = []
            for ordered in (results, list(reversed(results))):
                with redirect_stderr(io.StringIO()):
                    graph = build_graph(ordered, str(root))
                assert isinstance(graph, Ok)
                graphs.append(sorted((edge["source"], edge["target"], tuple(sorted(edge.get("relations", []))))
                                     for edge in graph.value.edges if edge["type"] == "IMPORTS"))
        self.assertEqual(graphs[0], graphs[1])


if __name__ == "__main__":
    unittest.main()
