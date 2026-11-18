"""The Rust reference of the benchmarks (tools/rust_resolve.py) counts the files that define the items a file names."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\n',
    "src/main.rs": "mod domain;\nmod app;\nfn main() {}\n",
    "src/domain/mod.rs": "mod stats;\nmod timeline;\nmod types;\npub use stats::*;\npub use timeline::*;\npub use types::*;\n",
    "src/domain/stats.rs": "fn parse_time() {}\npub struct Stats;\n",
    "src/domain/timeline.rs": "pub(crate) fn parse_time() {}\npub enum Kind { A, B }\n",
    "src/domain/types.rs": "pub struct Fork;\n",
    "src/app/mod.rs": "mod named;\nmod glob;\n",
    "src/app/named.rs": "use crate::domain::{parse_time, Fork};\npub fn n(_: Fork) { parse_time(); }\n",
    "src/app/glob.rs": (
        "use crate::domain::*;\n"
        "pub enum Local { Stats(u8) }\n"
        "pub fn g(fork: u8) -> Kind { let _ = Fork; Kind::A }\n"
    ),
}

MEMBER_FILES = {
    "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n',
    "src/main.rs": "mod settings;\nmod app;\nfn main() {}\n",
    "src/settings/mod.rs": "mod running;\nmod seed;\nmod plan;\nmod show;\npub mod handle;\npub use running::{Boxed, Running, Short};\n",
    "src/settings/handle.rs": "pub type Handle = super::running::Running;\n",
    "src/settings/running.rs": (
        "pub struct Running { pub idle: u64 }\npub struct Boxed<T>(pub T);\n"
        "impl Running {\n    pub fn defaults() -> Running { Running { idle: 0 } }\n}\n"
        "pub type Short = Running;\n"
    ),
    "src/settings/seed.rs": (
        "use super::running::{Boxed, Running};\n"
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
    "src/settings/show.rs": (
        "use super::running::Running;\npub trait Show { fn show() -> u8; }\n"
        "impl Show for Running {\n    fn show() -> u8 { 1 }\n}\n"
    ),
    "src/app/mod.rs": "mod by_use;\nmod by_path;\nmod by_alias;\nmod by_glob;\nmod generic;\nmod qualified;\nmod by_value;\nmod by_trait;\nmod by_handle;\n",
    "src/app/by_handle.rs": "use crate::settings::handle::Handle;\npub fn f() -> Handle { Handle::defaults() }\n",
    "src/app/by_use.rs": "use crate::settings::Running;\npub fn f() -> Running { Running::from_env() }\n",
    "src/app/by_path.rs": "pub fn f() -> u64 { crate::settings::Running::LIMIT }\n",
    "src/app/by_alias.rs": "use crate::settings::Short;\ntype Mine = crate::settings::Running;\npub fn f() { Short::fresh(); Mine::from_lookup(|_| None); }\n",
    "src/app/by_glob.rs": "use crate::settings::*;\npub fn f() -> Running { Running::from_env() }\n",
    "src/app/generic.rs": "use crate::settings::Boxed;\npub fn f() -> Boxed<u8> { Boxed::<u8>::wrap(1) }\n",
    "src/app/qualified.rs": "use crate::settings::Running;\npub fn f() -> Running { <Running>::fresh() }\n",
    "src/app/by_value.rs": "use crate::settings::Running;\npub fn f(r: &Running) -> u64 { r.plan() }\n",
    "src/app/by_trait.rs": "use crate::settings::Running;\npub fn f() -> Running { Running::defaults() }\n",
}


WORKSPACE_FILES = {
    "Cargo.toml": '[workspace]\nmembers = ["core", "app", "b", "old"]\n\n[workspace.package]\nedition = "2015"\n',
    "core/Cargo.toml": '[package]\nname = "core-pkg"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\nname = "corelib"\n',
    "core/src/lib.rs": "pub mod model;\nmod hidden;\npub use hidden::Entity;\n",
    "core/src/model.rs": "pub struct Row;\n", "core/src/hidden.rs": "pub struct Entity;\n",
    "b/Cargo.toml": '[package]\nname = "real-b"\nversion = "0.1.0"\nedition = "2021"\n',
    "b/src/lib.rs": "pub mod x;\n", "b/src/x.rs": "pub struct Y;\n",
    "app/Cargo.toml": (
        '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dependencies]\ncore-pkg = { path = "../core" }\nrenamed = { package = "real-b", path = "../b" }\n'
    ),
    "app/src/lib.rs": (
        "extern crate self as me;\npub mod a;\nmod facade;\nmod shadow;\nmod variants;\nmod by_self;\nmod scope;\nmod outside;\nmod plain;\n"
        "#[cfg_attr(unix, path = \"sys_unix.rs\")]\n#[cfg_attr(windows, path = \"sys_windows.rs\")]\nmod sys;\n"
        "#[path = \"elsewhere\"]\nmod inline {\n    pub mod child;\n}\n"
    ),
    "app/src/a.rs": "pub struct X;\npub fn f() {}\n",
    "app/src/sys_unix.rs": "pub fn s() {}\n", "app/src/sys_windows.rs": "pub fn s() {}\n",
    "app/src/elsewhere/child.rs": "pub fn c() {}\n",
    "app/src/facade/mod.rs": "mod inner;\nmod kinds;\npub use inner::*;\npub use self::kinds::Kind::*;\n",
    "app/src/facade/inner.rs": "pub struct Thing;\n",
    "app/src/facade/kinds.rs": "pub enum Kind { Alpha, Beta }\n",
    "app/src/b.rs": "pub struct Thing;\n",
    "app/src/scope/mod.rs": "mod restricted;\nmod near;\npub use restricted::*;\n",
    "app/src/scope/restricted.rs": "pub(super) fn helper() {}\npub(in crate::scope) fn scoped() {}\n",
    "app/src/scope/near.rs": "use super::*;\npub fn n() { helper(); scoped(); }\n",
    "app/src/plain.rs": "pub fn helper() {}\npub fn scoped() {}\n",
    "app/src/outside.rs": "use crate::scope::*;\nuse crate::plain::*;\npub fn f() { helper(); scoped(); }\n",
    "app/src/shadow.rs": "use crate::facade::*;\nuse crate::a::X as Thing;\npub fn f(_: Thing) {}\n",
    "app/src/variants.rs": "use crate::facade::*;\npub fn f() -> u8 { match Alpha { Alpha => 1, _ => 2 } }\n",
    "app/src/by_self.rs": "use me::a::X;\npub fn f(_: X) { ::corelib::model::Row; }\n",
    "app/src/main.rs": (
        "extern crate app as other;\nuse corelib::model::Row;\nuse corelib::Entity;\nuse renamed::x::Y;\nuse other::a::X;\n"
        "fn main() { let _ = (Row, Entity, Y, X); }\n"
    ),
    "app/src/bin/two/main.rs": "mod part;\nmod other;\nfn main() {}\n",
    "app/src/bin/two/part.rs": "use crate::other::o;\npub fn p() { o(); }\n", "app/src/bin/two/other.rs": "pub fn o() {}\n",
    "old/Cargo.toml": '[package]\nname = "old"\nversion = "0.1.0"\nedition.workspace = true\n',
    "old/src/lib.rs": "mod a;\nmod b;\n", "old/src/a.rs": "pub struct X;\npub fn f() {}\n",
    "old/src/b/mod.rs": "mod c;\n", "old/src/b/c.rs": "use a::X;\npub fn g(_: X) { ::a::f(); }\n",
}


class RustReferenceTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "the benchmark references run on Linux and macOS (run.sh); the resolver joins POSIX paths")
    def test_the_reference_follows_items_to_their_definers(self) -> None:
        """/* REQ-BENCH-015: both copies of tools/rust_resolve.py give, for a name used through a facade, the file that defines it (to_followed), skipping a private item a glob re-export cannot carry; a glob import (`use crate::domain::*`) gives a glob-use reference for each item its module text names, also as the head of a path (`Kind::A`), and none for the variant of a local enum or a typed local name; the impact benchmark requires both the facade and the definer, and still draws its questions from the facade edges. */"""
        sys.path.insert(0, str(ROOT / "benchmarks" / "impact"))
        import impact100  # noqa: E402

        for copy in ("impact", "graphify"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "tree"
                for name, text in FILES.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
                out = Path(tmp) / "out" / "app.rust_refs.json"
                out.parent.mkdir()
                subprocess.run([sys.executable, "-I", str(ROOT / "benchmarks" / copy / "tools" / "rust_resolve.py"), str(root), str(out)],
                               check=True, capture_output=True)
                data = json.loads(out.read_text(encoding="utf-8"))
                posix = lambda path: path.replace(os.sep, "/")
                named = {(r["text"], posix(r["to_followed"])) for r in data["refs"] if posix(r["frm"]) == "src/app/named.rs"}
                self.assertIn(("crate::domain::parse_time", "src/domain/timeline.rs"), named, copy)
                self.assertIn(("crate::domain::Fork", "src/domain/types.rs"), named, copy)
                globs = {(r["text"].split()[-1], posix(r["to_followed"])) for r in data["refs"] if r["category"] == "glob-use"}
                self.assertEqual({("Fork", "src/domain/types.rs"), ("Kind", "src/domain/timeline.rs")}, globs, copy)
                required = {(posix(a), posix(b)) for a, b in impact100.rust_edges(data)}
                self.assertIn(("src/app/glob.rs", "src/domain/timeline.rs"), required)
                self.assertIn(("src/app/glob.rs", "src/domain/mod.rs"), required)
                drawn = {(posix(a), posix(b)) for a, b in impact100.drawn_from(tmp, "app", "rust", set())}
                self.assertNotIn(("src/app/glob.rs", "src/domain/timeline.rs"), drawn)
                self.assertIn(("src/app/named.rs", "src/domain/mod.rs"), drawn)


    @unittest.skipIf(os.name == "nt", "the benchmark references run on Linux and macOS (run.sh); the resolver joins POSIX paths")
    def test_the_reference_follows_a_type_path_to_the_file_of_the_impl_block(self) -> None:
        """/* REQ-BENCH-016: both copies of tools/rust_resolve.py give a type-member reference for a member named through a type path (`Type::member`, `Self::member`, `<Type>::member`, `Type::<T>::member`, the type named by a `use`, a path, a glob import or a type alias) that an inherent impl block defines in another file than the one that names it: to is the file of the type, to_followed the file of the impl block (the file of the type itself when it defines the member: a type named through an alias of a third module, `Handle::defaults()`, is reached only this way); none for a method called on a value or a trait impl; the impact benchmark requires the edge and does not draw its questions from it. */"""
        sys.path.insert(0, str(ROOT / "benchmarks" / "impact"))
        import impact100  # noqa: E402

        for copy in ("impact", "graphify"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "tree"
                for name, text in MEMBER_FILES.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
                out = Path(tmp) / "out" / "app.rust_refs.json"
                out.parent.mkdir()
                subprocess.run([sys.executable, "-I", str(ROOT / "benchmarks" / copy / "tools" / "rust_resolve.py"), str(root), str(out)],
                               check=True, capture_output=True)
                data = json.loads(out.read_text(encoding="utf-8"))
                short = lambda path: path.replace(os.sep, "/").removeprefix("src/")
                members = {(short(r["frm"]), r["text"], short(r["to"]), short(r["to_followed"]))
                           for r in data["refs"] if r["category"] == "type-member"}
                self.assertEqual({
                    ("app/by_use.rs", "Running::from_env", "settings/running.rs", "settings/seed.rs"),
                    ("app/by_path.rs", "crate::settings::Running::LIMIT", "settings/running.rs", "settings/seed.rs"),
                    ("app/by_alias.rs", "Short::fresh", "settings/running.rs", "settings/plan.rs"),
                    ("app/by_alias.rs", "Mine::from_lookup", "settings/running.rs", "settings/seed.rs"),
                    ("app/by_glob.rs", "Running::from_env", "settings/running.rs", "settings/seed.rs"),
                    ("app/generic.rs", "Boxed::wrap", "settings/running.rs", "settings/seed.rs"),
                    ("app/qualified.rs", "Running::fresh", "settings/running.rs", "settings/plan.rs"),
                    ("settings/plan.rs", "Self::LIMIT", "settings/running.rs", "settings/seed.rs"),
                    ("settings/plan.rs", "Self::defaults", "settings/running.rs", "settings/running.rs"),
                    ("settings/seed.rs", "Running::defaults", "settings/running.rs", "settings/running.rs"),
                    ("app/by_trait.rs", "Running::defaults", "settings/running.rs", "settings/running.rs"),
                    ("app/by_handle.rs", "Handle::defaults", "settings/running.rs", "settings/running.rs"),
                }, members, copy)
                required = {(short(a), short(b)) for a, b in impact100.rust_edges(data)}
                self.assertIn(("app/by_use.rs", "settings/seed.rs"), required)
                self.assertIn(("app/by_handle.rs", "settings/running.rs"), required)
                self.assertNotIn(("app/by_value.rs", "settings/plan.rs"), required)
                self.assertNotIn(("app/by_trait.rs", "settings/show.rs"), required)
                drawn = {(short(a), short(b)) for a, b in impact100.drawn_from(tmp, "app", "rust", set())}
                self.assertNotIn(("app/by_use.rs", "settings/seed.rs"), drawn)
                self.assertIn(("app/by_use.rs", "settings/mod.rs"), drawn)


    @unittest.skipIf(os.name == "nt", "the benchmark references run on Linux and macOS (run.sh); the resolver joins POSIX paths")
    def test_the_reference_follows_cargo_names_editions_and_module_attributes(self) -> None:
        """/* REQ-BENCH-018: both copies of tools/rust_resolve.py resolve a crate named by `[lib] name`, by a dependency renamed with `package = ".."`, by `extern crate a as b;` and `extern crate self as me;`, and by `::name` in code; find the binary of `src/bin/<name>/main.rs` and its modules; start a `use` path and `::a` at the crate root in a package of edition 2015 (inherited from the workspace); declare every file of `#[cfg_attr(cond, path = "..")]` and look the children of an inline module with `#[path = "dir"]` up in that folder; take a variant re-exported with `pub use kinds::Kind::*;` to the file of the enum; give no glob-use reference for a name the module imports by name; and carry a `pub(super)` or `pub(in path)` item of a glob re-exported module only into that scope. */"""
        sys.path.insert(0, str(ROOT / "benchmarks" / "impact"))
        import impact100  # noqa: E402

        for copy in ("impact", "graphify"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "tree"
                for name, text in WORKSPACE_FILES.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
                out = Path(tmp) / "out" / "app.rust_refs.json"
                out.parent.mkdir()
                subprocess.run([sys.executable, "-I", str(ROOT / "benchmarks" / copy / "tools" / "rust_resolve.py"), str(root), str(out)],
                               check=True, capture_output=True)
                data = json.loads(out.read_text(encoding="utf-8"))
                required = {(a.replace(os.sep, "/"), b.replace(os.sep, "/")) for a, b in impact100.rust_edges(data)}
                for pair in (
                    ("app/src/main.rs", "core/src/model.rs"), ("app/src/main.rs", "core/src/lib.rs"), ("app/src/main.rs", "core/src/hidden.rs"),
                    ("app/src/main.rs", "b/src/x.rs"), ("app/src/main.rs", "app/src/a.rs"), ("app/src/by_self.rs", "app/src/a.rs"),
                    ("app/src/by_self.rs", "core/src/model.rs"),
                    ("app/src/bin/two/main.rs", "app/src/bin/two/part.rs"), ("app/src/bin/two/part.rs", "app/src/bin/two/other.rs"),
                    ("old/src/b/c.rs", "old/src/a.rs"),
                    ("app/src/lib.rs", "app/src/sys_unix.rs"), ("app/src/lib.rs", "app/src/sys_windows.rs"),
                    ("app/src/lib.rs", "app/src/elsewhere/child.rs"),
                    ("app/src/variants.rs", "app/src/facade/kinds.rs"), ("app/src/shadow.rs", "app/src/a.rs"),
                ):
                    self.assertIn(pair, required, copy)
                self.assertNotIn(("app/src/shadow.rs", "app/src/facade/inner.rs"), required, copy)
                self.assertNotIn(("app/src/variants.rs", "app/src/facade/inner.rs"), required, copy)
                self.assertIn(("app/src/outside.rs", "app/src/plain.rs"), required, copy)
                self.assertNotIn(("app/src/outside.rs", "app/src/scope/restricted.rs"), required, copy)
                self.assertIn(("app/src/scope/near.rs", "app/src/scope/restricted.rs"), required, copy)
                self.assertEqual([], data["unresolved_mod_decls"], copy)


if __name__ == "__main__":
    unittest.main()
