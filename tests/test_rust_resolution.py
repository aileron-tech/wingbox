"""Rust: which file a module or a name resolves to, by the rules of the compiler and of Cargo."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph
from scripts.lang.rust import parse_rust_treesitter

PACKAGE = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def imports_of(files: dict[str, str]) -> dict[tuple[str, str], tuple[str, ...]]:
    """The import edges of a tree: (source module, target module) -> relations."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        results = []
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        for relative, content in files.items():
            if relative.endswith(".rs"):
                path = root / relative
                results.append(parse_rust_treesitter(str(path), content, root.name, path.stat().st_mtime))
        with redirect_stderr(io.StringIO()):
            graph = build_graph(results, str(root))
    if not isinstance(graph, Ok):
        raise AssertionError(graph.error)
    return {(edge["source"], edge["target"]): tuple(sorted(edge.get("relations", [])))
            for edge in graph.value.edges if edge["type"] == "IMPORTS"}


class RustResolutionTest(unittest.TestCase):
    def test_a_crate_root_of_any_name_owns_its_folder(self) -> None:
        """/* REQ-RUST-032: the root file of a Cargo target looks its modules up in its own folder whatever its name: `mod common;` in src/bin/tool.rs is src/bin/common/mod.rs, `mod common;` in tests/it.rs is tests/common/mod.rs, in examples/demo.rs it is examples/support/mod.rs; `crate::` in a file of such a crate is that crate (src/bin/two/main.rs), not the library. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "pub mod other;\n", "src/other.rs": "pub fn lib_o() {}\n",
            "src/bin/tool.rs": "mod common;\nfn main() { common::h(); }\n",
            "src/bin/common/mod.rs": "pub mod other;\npub fn h() { crate::common::other::o(); }\n",
            "src/bin/common/other.rs": "pub fn o() {}\n",
            "src/bin/two/main.rs": "mod part;\nmod other;\nfn main() {}\n",
            "src/bin/two/part.rs": "use crate::other::o;\npub fn p() { o(); }\n", "src/bin/two/other.rs": "pub fn o() {}\n",
            "tests/it.rs": "mod common;\n#[test]\nfn t() { common::setup(); }\n",
            "tests/common/mod.rs": "pub mod util;\npub fn setup() { crate::common::util::more(); }\n",
            "tests/common/util.rs": "pub fn more() {}\n",
            "examples/demo.rs": "mod support;\nfn main() { support::s(); }\n", "examples/support/mod.rs": "pub fn s() {}\n",
        })
        for pair in (("src/bin/tool", "src/bin/common"), ("src/bin/common", "src/bin/common/other"),
                     ("src/bin/two/main", "src/bin/two/part"), ("src/bin/two/part", "src/bin/two/other"),
                     ("tests/it", "tests/common"), ("tests/common", "tests/common/util"), ("examples/demo", "examples/support")):
            self.assertIn(pair, edges)
        self.assertNotIn(("src/bin/common", "src/other"), edges)
        self.assertNotIn(("src/bin/two/part", "src/other"), edges)
        self.assertNotIn(("src/bin/common", "src/lib"), edges)

    def test_a_named_reexport_of_an_item_a_macro_defines_leads_to_the_module_it_names(self) -> None:
        """/* REQ-RUST-033: `pub use spec::Key;` where spec.rs defines Key only through a macro invocation (`keys! { .. }`) leads a file that names `settings::Key` to spec.rs, marked :item, and to no other child of the facade. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "mod settings;\nmod user;\n",
            "src/settings/mod.rs": "mod spec;\nmod other;\npub use spec::Key;\npub use other::Plain;\n",
            "src/settings/spec.rs": "macro_rules! keys {\n    ($($v:ident),+) => { pub enum Key { $($v),+ } };\n}\nkeys! { A, B }\n",
            "src/settings/other.rs": "pub struct Plain;\n",
            "src/user.rs": "pub fn f(_: crate::settings::Key) {}\n",
        })
        self.assertEqual((), edges[("src/user", "src/settings")])
        self.assertEqual(("item",), edges[("src/user", "src/settings/spec")])
        self.assertNotIn(("src/user", "src/settings/other"), edges)

    def test_a_path_attribute_names_the_file_of_a_module(self) -> None:
        """/* REQ-RUST-034: `#[path = "f.rs"] mod m;` loads f.rs from the folder of the declaring file (also when that file is not a mod.rs), from the inline module's folder when the declaration is inside one, and every candidate of `#[cfg_attr(cond, path = "..")]`; the loaded file owns its folder (`mod sub;` in it lies next to it), paths through the module follow the attribute, an item re-exported from it leads to it, and `#[path = "dir"]` on an inline module sets the folder of its children. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "build.rs": "#[path = \"src/shared.rs\"]\nmod shared;\nfn main() { shared::s(); }\n",
            "src/lib.rs": (
                "#[path = \"impl_unix.rs\"]\nmod imp;\nmod other;\nmod a;\npub mod shared;\nmod facade;\n"
                "#[cfg_attr(unix, path = \"sys_unix.rs\")]\n#[cfg_attr(windows, path = \"sys_windows.rs\")]\nmod sys;\n"
                "mod wrap {\n    #[path = \"x.rs\"]\n    pub mod b;\n}\n"
                "#[path = \"elsewhere\"]\nmod inline {\n    pub mod child;\n}\n"
            ),
            "src/shared.rs": "pub fn s() {}\n",
            "src/impl_unix.rs": "pub mod sub;\npub fn go() {}\npub struct Fd;\n", "src/sub.rs": "pub fn deep() {}\n",
            "src/other.rs": "use crate::imp::go;\npub fn o() { go(); crate::imp::sub::deep(); }\n",
            "src/a.rs": "#[path = \"x/y.rs\"]\npub mod b;\npub fn f() { b::g(); }\n",
            "src/x/y.rs": "pub fn g() {}\n", "src/a/x/y.rs": "pub fn not_this() {}\n",
            "src/sys_unix.rs": "pub fn s() {}\n", "src/sys_windows.rs": "pub fn s() {}\n",
            "src/wrap/x.rs": "pub fn g() {}\n", "src/x.rs": "pub fn not_this() {}\n",
            "src/elsewhere/child.rs": "pub fn c() {}\n",
            "src/facade/mod.rs": "#[path = \"impl_fd.rs\"]\nmod fd;\npub use fd::Handle;\n",
            "src/facade/impl_fd.rs": "pub struct Handle;\n",
            "src/user.rs": "use crate::facade::Handle;\npub fn u(_: Handle) {}\n",
        })
        for pair in (("build", "src/shared"), ("src/lib", "src/impl_unix"), ("src/impl_unix", "src/sub"),
                     ("src/other", "src/impl_unix"), ("src/other", "src/sub"), ("src/a", "src/x/y"),
                     ("src/lib", "src/sys_unix"), ("src/lib", "src/sys_windows"), ("src/lib", "src/wrap/x"),
                     ("src/lib", "src/elsewhere/child"), ("src/facade", "src/facade/impl_fd")):
            self.assertIn(pair, edges)
        self.assertEqual(("item",), edges[("src/user", "src/facade/impl_fd")])
        self.assertNotIn(("src/a", "src/a/x/y"), edges)
        self.assertNotIn(("src/lib", "src/x"), edges)

    def test_a_module_declared_inside_an_inline_module_lies_in_its_folder(self) -> None:
        """/* REQ-RUST-035: `mod outer { mod inner; }` in lib.rs loads outer/inner.rs, in foo.rs it loads foo/wrap/deep.rs; `mod r#type;` loads type.rs and `crate::r#type::T` names it. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "mod outer {\n    pub mod inner;\n}\nmod foo;\nmod r#type;\nmod user;\n",
            "src/outer/inner.rs": "pub fn f() {}\n",
            "src/foo.rs": "pub mod wrap {\n    pub mod deep;\n}\npub fn g() { crate::outer::inner::f(); }\n",
            "src/foo/wrap/deep.rs": "pub fn h() {}\n",
            "src/type.rs": "pub struct T;\n",
            "src/user.rs": "use crate::r#type::T;\npub fn f(_: T) {}\n",
        })
        for pair in (("src/lib", "src/outer/inner"), ("src/foo", "src/foo/wrap/deep"), ("src/foo", "src/outer/inner"),
                     ("src/lib", "src/type"), ("src/user", "src/type")):
            self.assertIn(pair, edges)

    def test_cargo_names_and_paths_of_targets_and_dependencies(self) -> None:
        """/* REQ-RUST-036: a library is named by `[lib] name` and rooted at `[lib] path`; a `[[bin]] path` is a crate root whose modules lie next to it; a dependency declared with `package = ".."` is named by its key; a `#[macro_export]` macro of another workspace crate is found in the file that defines it. */"""
        edges = imports_of({
            "Cargo.toml": '[workspace]\nmembers = ["core", "app", "b"]\n',
            "core/Cargo.toml": '[package]\nname = "core-pkg"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\nname = "corelib"\npath = "source/library.rs"\n',
            "core/source/library.rs": "pub mod a;\nmod inner;\nmod mac;\n",
            "core/source/a.rs": "pub struct X;\n",
            "core/source/inner.rs": "use crate::a::X;\npub fn f(_: X) {}\n",
            "core/source/mac.rs": "#[macro_export]\nmacro_rules! shout {\n    () => { 1u8 };\n}\n",
            "b/Cargo.toml": '[package]\nname = "real-b"\nversion = "0.1.0"\nedition = "2021"\n',
            "b/src/lib.rs": "pub mod x;\n", "b/src/x.rs": "pub struct Y;\n",
            "app/Cargo.toml": (
                '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n\n[[bin]]\nname = "tool"\npath = "cmd/tool.rs"\n\n'
                '[dependencies]\ncore-pkg = { path = "../core" }\nrenamed = { package = "real-b", path = "../b" }\n'
            ),
            "app/cmd/tool.rs": "mod x;\nmod y;\nuse corelib::a::X;\nuse renamed::x::Y;\nfn main() { let _ = (X, Y); corelib::shout!(); }\n",
            "app/cmd/x.rs": "use crate::y::deep;\npub fn run() { deep(); }\n", "app/cmd/y.rs": "pub fn deep() {}\n",
        })
        for pair in (("core/source/library", "core/source/a"), ("core/source/inner", "core/source/a"),
                     ("app/cmd/tool", "core/source/a"), ("app/cmd/tool", "b/src/x"), ("app/cmd/tool", "app/cmd/x"),
                     ("app/cmd/x", "app/cmd/y"), ("app/cmd/tool", "core/source/mac")):
            self.assertIn(pair, edges)

    def test_edition_2015_use_paths_start_at_the_crate_root(self) -> None:
        """/* REQ-RUST-037: in a package of edition 2015 (no `edition` key, or `edition.workspace = true` with 2015 in the workspace) `use a::X;` in any module names crate::a, and `::a::f()` is the crate root; in edition 2018 and later `::app::a::f()` names the library crate app and a `use` path that names no crate and no child module resolves to nothing. */"""
        edges = imports_of({
            "Cargo.toml": '[workspace]\nmembers = ["old", "inherits", "new"]\n\n[workspace.package]\nedition = "2015"\n',
            "old/Cargo.toml": '[package]\nname = "old"\nversion = "0.1.0"\n',
            "old/src/lib.rs": "mod a;\nmod b;\n", "old/src/a.rs": "pub struct X;\npub fn f() {}\n",
            "old/src/b/mod.rs": "mod c;\n", "old/src/b/c.rs": "use a::X;\npub fn g(_: X) { ::a::f(); }\n",
            "inherits/Cargo.toml": '[package]\nname = "inherits"\nversion = "0.1.0"\nedition.workspace = true\n',
            "inherits/src/lib.rs": "mod a;\nmod b;\n", "inherits/src/a.rs": "pub struct X;\n",
            "inherits/src/b/mod.rs": "mod c;\n", "inherits/src/b/c.rs": "use a::X;\npub fn g(_: X) {}\n",
            "new/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n',
            "new/src/lib.rs": "pub mod a;\nmod b;\n", "new/src/a.rs": "pub struct X;\npub fn f() {}\n",
            "new/src/b/mod.rs": "mod c;\n", "new/src/b/c.rs": "use a::X;\n",
            "new/src/main.rs": "use ::app::a::X;\nfn main() { let _ = X; ::app::a::f(); }\n",
        })
        self.assertIn(("old/src/b/c", "old/src/a"), edges)
        self.assertIn(("inherits/src/b/c", "inherits/src/a"), edges)
        self.assertIn(("new/src/main", "new/src/a"), edges)
        self.assertNotIn(("new/src/b/c", "new/src/a"), edges)

    def test_extern_crate_names(self) -> None:
        """/* REQ-RUST-038: `extern crate app as other;` in a crate root makes `other::a::X` name the library crate app, and `extern crate self as me;` makes `me::a::X` name the crate itself. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "extern crate self as me;\npub mod a;\nmod b;\n", "src/a.rs": "pub struct X;\n",
            "src/b.rs": "use me::a::X;\npub fn f(_: X) {}\n",
            "src/main.rs": "extern crate app as other;\nuse other::a::X;\nfn main() { let _ = X; }\n",
        })
        self.assertIn(("src/b", "src/a"), edges)
        self.assertIn(("src/main", "src/a"), edges)

    def test_what_a_glob_import_gives_and_what_shadows_it(self) -> None:
        """/* REQ-RUST-039: after `use crate::facade::*;` a name the file defines itself or imports by name is not taken from the glob (no edge to the facade's child of that name); a variant a facade re-exports with `pub use kinds::Kind::*;` leads to the file of the enum and to no other child; a `macro_rules!` macro re-exported as an item (`pub(crate) use local;`) leads to the file that defines it; a module a facade re-exports under a name (`pub use self::inner as api;`) is followed in `facade::api::X`. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "mod facade;\nmod b;\nmod local_user;\nmod import_user;\nmod variant_user;\nmod macro_user;\nmod module_user;\n",
            "src/facade/mod.rs": (
                "pub mod inner;\nmod second;\nmod kinds;\nmod mac;\n"
                "pub use inner::*;\npub use second::*;\npub use self::kinds::Kind::*;\npub(crate) use mac::local;\n"
                "pub use self::inner as api;\n"
            ),
            "src/facade/inner.rs": "pub struct Thing;\n", "src/facade/second.rs": "pub struct Other;\n",
            "src/facade/kinds.rs": "pub enum Kind { Alpha, Beta }\npub struct Unrelated;\n",
            "src/facade/mac.rs": "macro_rules! local {\n    () => { 1u8 };\n}\npub(crate) use local;\n",
            "src/b.rs": "pub struct Thing;\n",
            "src/local_user.rs": "use crate::facade::*;\npub struct Thing;\npub fn f(_: Thing, _: Other) {}\n",
            "src/import_user.rs": "use crate::facade::*;\nuse crate::b::Thing;\npub fn f(_: Thing) {}\n",
            "src/variant_user.rs": "use crate::facade::*;\npub fn f() -> u8 { match Alpha { Alpha => 1, _ => 2 } }\n",
            "src/macro_user.rs": "use crate::facade::local;\npub fn f() -> u8 { local!() }\n",
            "src/module_user.rs": "use crate::facade::api::Thing;\npub fn f(_: Thing) {}\n",
        })
        self.assertIn(("src/local_user", "src/facade/second"), edges)
        self.assertNotIn(("src/local_user", "src/facade/inner"), edges)
        self.assertIn(("src/import_user", "src/b"), edges)
        self.assertNotIn(("src/import_user", "src/facade/inner"), edges)
        self.assertIn(("src/variant_user", "src/facade/kinds"), edges)
        self.assertNotIn(("src/variant_user", "src/facade/inner"), edges)
        self.assertEqual(("item",), edges[("src/macro_user", "src/facade/mac")])
        self.assertEqual(("item",), edges[("src/module_user", "src/facade/inner")])

    def test_a_glob_reexport_carries_an_item_only_where_it_is_visible(self) -> None:
        """/* REQ-RUST-040: an item declared `pub(super)` or `pub(in path)` in a module a facade re-exports with a glob is carried only to a file inside that scope: a file outside it that glob-imports the facade and another module with a public item of the same name gets no edge to the restricted one, a file inside the scope does; `pub(self)` is private. */"""
        edges = imports_of({
            "Cargo.toml": PACKAGE,
            "src/lib.rs": "mod facade;\nmod other;\nmod user;\n",
            "src/facade/mod.rs": "mod inner;\nmod inner2;\nmod inner3;\nmod near;\npub use inner::*;\npub use inner2::*;\npub use inner3::*;\n",
            "src/facade/inner.rs": "pub fn open() {}\n",
            "src/facade/inner2.rs": "pub(super) fn helper() {}\npub(in crate::facade) fn scoped() {}\n",
            "src/facade/inner3.rs": "pub(self) fn own() {}\n",
            "src/facade/near.rs": "use super::*;\npub fn n() { helper(); scoped(); }\n",
            "src/other.rs": "pub fn helper() {}\npub fn scoped() {}\npub fn own() {}\n",
            "src/user.rs": "use crate::facade::*;\nuse crate::other::*;\npub fn f() { helper(); scoped(); own(); open(); }\n",
        })
        self.assertIn(("src/user", "src/other"), edges)
        self.assertIn(("src/user", "src/facade/inner"), edges)
        self.assertNotIn(("src/user", "src/facade/inner2"), edges)
        self.assertNotIn(("src/user", "src/facade/inner3"), edges)
        self.assertIn(("src/facade/near", "src/facade/inner2"), edges)


if __name__ == "__main__":
    unittest.main()
