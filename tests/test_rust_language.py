from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph
from scripts.lang.interface import FileResult
from scripts.lang.rust import RUST_LANGUAGE, parse_rust_treesitter


class RustLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_rust_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_rust_entities_impls_and_grouped_uses(self) -> None:
        """/* REQ-RUST-001: Rust parsing preserves declarations, impl relations, methods, and use leaves. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "src/lib.rs",
                """
use crate::{model, service::Service};
use super::shared as shared_module;

pub trait Runner {
    fn run(&self);
}

pub struct Worker;
pub enum Status { Ready, Done }
pub type WorkerId = u64;

impl Runner for Worker {
    fn run(&self) {}
}

impl Worker {
    pub fn new() -> Self { Self }
}

pub fn execute() {}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("rust", result.language)
        self.assertEqual(
            ["crate::model", "crate::service::Service", "super::shared"],
            result.imports,
        )
        self.assertEqual("interface", entities["Runner"]["kind"])
        self.assertEqual(["run"], entities["Runner"].get("methods", []))
        self.assertEqual("class", entities["Worker"]["kind"])
        self.assertEqual(["new", "run"], entities["Worker"].get("methods", []))
        self.assertEqual(["Runner"], entities["Worker"].get("bases", []))
        self.assertEqual("enum", entities["Status"]["kind"])
        self.assertEqual("type", entities["WorkerId"]["kind"])
        self.assertEqual("function", entities["execute"]["kind"])
        self.assertEqual(["Runner"], result.exports_abstract)
        self.assertEqual(["Runner"], result.implements)

    def test_graph_resolves_rust_modules_and_workspace_crates(self) -> None:
        """/* REQ-RUST-002: Rust graph edges resolve mod, crate, and workspace-crate paths. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/core-lib", "crates/app"]\n',
                encoding="utf-8",
            )
            core_root = root / "crates" / "core-lib"
            app_root = root / "crates" / "app"
            core_root.mkdir(parents=True)
            app_root.mkdir(parents=True)
            (core_root / "Cargo.toml").write_text(
                '[package]\nname = "core-lib"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            (app_root / "Cargo.toml").write_text(
                '[package]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )

            results = [
                self._parse(
                    root,
                    "crates/core-lib/src/lib.rs",
                    "pub mod model;\n",
                ),
                self._parse(
                    root,
                    "crates/core-lib/src/model.rs",
                    "pub struct Thing;\n",
                ),
                self._parse(
                    root,
                    "crates/app/src/main.rs",
                    "use core_lib::model::Thing;\nmod local;\nuse crate::local::run;\nfn main() {}\n",
                ),
                self._parse(
                    root,
                    "crates/app/src/local.rs",
                    "pub fn run() {}\n",
                ),
            ]

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Rust graph build failed: {graph_result.error}")
            edges = graph_result.value.edges

        imports = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("crates/core-lib/src/lib", "crates/core-lib/src/model"),
                ("crates/app/src/main", "crates/core-lib/src/model"),
                ("crates/app/src/main", "crates/app/src/local"),
            },
            imports,
        )

    def test_graph_resolves_bare_super_glob_to_parent_module(self) -> None:
        """/* REQ-RUST-004: A bare super glob preserves the child-to-parent module dependency. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            results = [
                self._parse(
                    root,
                    "src/lib.rs",
                    "mod handlers;\npub struct RootState;\n",
                ),
                self._parse(
                    root,
                    "src/handlers.rs",
                    "use super::*;\npub mod payments;\npub struct AppState;\n",
                ),
                self._parse(
                    root,
                    "src/handlers/payments.rs",
                    "use super::*;\npub fn process(_: AppState) {}\n",
                ),
            ]

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Rust graph build failed: {graph_result.error}")
            imports = {
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "IMPORTS"
            }

        self.assertIn(
            ("src/handlers/payments", "src/handlers"),
            imports,
        )
        self.assertIn(("src/handlers", "src/lib"), imports)

    def test_super_inside_an_inline_module_is_the_file_itself(self) -> None:
        """/* REQ-RUST-005: Inside an inline module `super` is the enclosing file, so `use super::*` in `mod tests { }` adds no parent edge and `super::super` reaches the parent. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            results = [
                self._parse(root, "src/lib.rs", "mod passes;\n"),
                self._parse(
                    root,
                    "src/passes/mod.rs",
                    "mod dce;\nmod fold;\nmod helpers;\npub fn run_all() {}\n",
                ),
                self._parse(
                    root,
                    "src/passes/dce.rs",
                    "pub fn eliminate() {}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n"
                    "    #[test]\n    fn removes_dead_code() { eliminate(); }\n}\n",
                ),
                self._parse(
                    root,
                    "src/passes/fold.rs",
                    "pub fn fold() {}\n\n#[cfg(test)]\nmod tests {\n    use super::super::helpers::setup;\n"
                    "    use super::super::run_all;\n    use self::local::thing;\n}\n",
                ),
                self._parse(root, "src/passes/helpers.rs", "pub fn setup() {}\n"),
            ]

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Rust graph build failed: {graph_result.error}")
            edges = {
                (edge["source"].split("::")[0], edge["target"].split("::")[0])
                for edge in graph_result.value.edges
            }

        self.assertNotIn(("src/passes/dce", "src/passes"), edges)
        self.assertEqual(set(), {target for source, target in edges if source == "src/passes/dce"})
        self.assertIn(("src/passes/fold", "src/passes/helpers"), edges)
        self.assertIn(("src/passes/fold", "src/passes"), edges)

    def _module_edges(self, root: Path, files: dict[str, str]) -> set[tuple[str, str]]:
        (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n', encoding="utf-8")
        results = [self._parse(root, relative_path, content) for relative_path, content in files.items()]
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"Rust graph build failed: {graph_result.error}")
        return {
            (edge["source"].split("::")[0], edge["target"].split("::")[0])
            for edge in graph_result.value.edges
        }

    def test_paths_used_without_a_use_declaration_are_dependencies(self) -> None:
        """/* REQ-RUST-006: A qualified path written in code (crate::a::b::f(), super::x::Y) is a dependency even with no use declaration. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "mod common;\nmod ir;\n",
                    "src/common/mod.rs": "pub mod fx_hash;\npub mod types;\n",
                    "src/common/fx_hash.rs": "pub struct FxHashMap;\nimpl FxHashMap { pub fn default() -> Self { FxHashMap } }\n",
                    "src/common/types.rs": "pub struct Width(pub u8);\n",
                    "src/ir/mod.rs": "mod stmt;\nmod client;\nmod backend;\n",
                    "src/ir/stmt.rs": (
                        "pub fn lower() {\n"
                        "    let _scope = crate::common::fx_hash::FxHashMap::default();\n"
                        "    let _width: crate::common::types::Width = crate::common::types::Width(8);\n"
                        "}\n"
                    ),
                    "src/ir/client.rs": "pub struct WebDriverClient;\n",
                    "src/ir/backend.rs": (
                        "pub struct Backend { client: super::client::WebDriverClient }\n\n"
                        "#[cfg(test)]\nmod tests {\n    fn build() { let _ = super::super::client::WebDriverClient; }\n}\n"
                    ),
                },
            )

        self.assertIn(("src/ir/stmt", "src/common/fx_hash"), edges)
        self.assertIn(("src/ir/stmt", "src/common/types"), edges)
        self.assertIn(("src/ir/backend", "src/ir/client"), edges)
        self.assertNotIn(("src/ir/backend", "src/ir"), edges)

    def test_super_item_reexported_by_the_parent_depends_on_the_parent(self) -> None:
        """/* REQ-RUST-007: `use super::Item` depends on the parent module even when the parent only re-exports Item. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "mod driver;\n",
                    "src/driver/mod.rs": "mod pipeline;\nmod external_tools;\npub use pipeline::Driver;\n",
                    "src/driver/pipeline.rs": "pub struct Driver;\n",
                    "src/driver/external_tools.rs": "use super::Driver;\npub fn run(_: Driver) {}\n",
                },
            )

        self.assertIn(("src/driver/external_tools", "src/driver"), edges)

    def test_relative_use_reaches_a_grandchild_module(self) -> None:
        """/* REQ-RUST-008: A relative use that starts at a child module (use codegen::emit::X) resolves to the file of the deepest module. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "mod arm;\n",
                    "src/arm/mod.rs": "mod codegen;\npub(crate) use codegen::emit::ArmCodegen;\nuse serde::Serialize;\n",
                    "src/arm/codegen/mod.rs": "pub mod emit;\n",
                    "src/arm/codegen/emit.rs": "pub struct ArmCodegen;\n",
                },
            )

        self.assertIn(("src/arm", "src/arm/codegen/emit"), edges)

    def test_exported_macro_import_points_at_the_file_that_defines_it(self) -> None:
        """/* REQ-RUST-009: `use crate::name` of a #[macro_export] macro depends on the file that defines the macro, not on the crate root. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "mod backend;\n",
                    "src/backend/mod.rs": "pub mod common;\npub mod alu;\n",
                    "src/backend/common.rs": "#[macro_export]\nmacro_rules! emit {\n    () => {};\n}\n",
                    "src/backend/alu.rs": "use crate::emit;\npub fn add() { emit!(); }\n",
                },
            )

        self.assertIn(("src/backend/alu", "src/backend/common"), edges)
        self.assertNotIn(("src/backend/alu", "src/lib"), edges)

    def test_paths_through_imported_modules_and_the_crate_name_are_dependencies(self) -> None:
        """/* REQ-RUST-010: A path that starts with an imported module (x86::linker::link()) or with the crate's own name (app::run() in a binary) is a dependency. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "pub mod backend;\npub fn run() {}\n",
                    "src/backend/mod.rs": "pub mod common;\npub mod x86;\n",
                    "src/backend/x86/mod.rs": "pub mod linker;\n",
                    "src/backend/x86/linker.rs": "pub fn link_shared() {}\n",
                    "src/backend/common.rs": (
                        "use crate::backend::x86;\nuse std::fs;\n"
                        "pub fn link() { x86::linker::link_shared(); let _ = fs::read(\"x\"); }\n"
                    ),
                    "src/main.rs": "fn main() { app::run(); }\n",
                    "src/bin/tool.rs": "fn main() { app::run(); }\n",
                },
            )

        self.assertIn(("src/backend/common", "src/backend/x86/linker"), edges)
        self.assertIn(("src/main", "src/lib"), edges)
        self.assertIn(("src/bin/tool", "src/lib"), edges)

    def test_paths_inside_macro_arguments_are_dependencies(self) -> None:
        """/* REQ-RUST-011: A crate-rooted path inside macro arguments (matches!(x, crate::a::B::C), vec![crate::a::f(); n]) is a dependency. */"""
        with tempfile.TemporaryDirectory() as directory:
            edges = self._module_edges(
                Path(directory),
                {
                    "src/lib.rs": "mod ir;\nmod common;\nmod driver;\n",
                    "src/ir/mod.rs": "pub mod module;\n",
                    "src/ir/module.rs": "pub enum GlobalInit { Zero }\n",
                    "src/common/mod.rs": "pub mod source;\n",
                    "src/common/source.rs": "pub struct Span;\nimpl Span { pub fn dummy() -> Span { Span } }\n",
                    "src/driver/mod.rs": "mod pipeline;\nmod tests_support;\n",
                    "src/driver/tests_support.rs": "pub const LIMIT: usize = 3;\n",
                    "src/driver/pipeline.rs": (
                        "pub fn run(init: u8, n: usize) -> bool {\n"
                        "    let _spans = vec![crate::common::source::Span::dummy(); n];\n"
                        "    assert!(n < super::tests_support::LIMIT);\n"
                        "    matches!(init, crate::ir::module::GlobalInit::Zero)\n"
                        "}\n"
                    ),
                },
            )

        self.assertIn(("src/driver/pipeline", "src/common/source"), edges)
        self.assertIn(("src/driver/pipeline", "src/ir/module"), edges)
        self.assertIn(("src/driver/pipeline", "src/driver/tests_support"), edges)

    def test_rust_language_metadata_matches_rust_analyzer(self) -> None:
        """/* REQ-RUST-003: Rust registration exposes canonical module, output, and LSP metadata. */"""
        self.assertEqual(
            "crates/core/src/lib",
            RUST_LANGUAGE.normalize_module_id("crates/core/src/lib.rs"),
        )
        self.assertEqual("rs", RUST_LANGUAGE.output_language_code("rust"))
        self.assertEqual("rust", RUST_LANGUAGE.lsp_language_id("src/lib.rs"))
        self.assertEqual(("rust-analyzer",), RUST_LANGUAGE.lsp_command)
        self.assertEqual(("Cargo.toml",), RUST_LANGUAGE.project_config_names)

    def test_generic_impl_names_the_trait_and_the_type_not_their_arguments(self) -> None:
        """/* REQ-RUST-012: in `impl From<eyre::Report> for AppError` the trait is From and in `impl Runner for Wrapper<T>` the type is Wrapper; a generic argument is never taken as the trait or the type. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "src/lib.rs",
                """
pub trait Runner {
    fn run(&self);
}

pub enum AppError { Internal(eyre::Report) }
pub struct Wrapper<T> { inner: T }
pub struct Payload;

impl From<eyre::Report> for AppError {
    fn from(err: eyre::Report) -> Self { AppError::Internal(err) }
}

impl<T> Runner for Wrapper<T> {
    fn run(&self) {}
}

impl super::flow::Senders for Payload {
    fn send(&self) {}
}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual(["From"], entities["AppError"].get("bases", []))
        self.assertEqual(["Internal", "from"], entities["AppError"].get("methods", []))
        self.assertEqual(["Runner"], entities["Wrapper"].get("bases", []))
        self.assertEqual(["run"], entities["Wrapper"].get("methods", []))
        self.assertEqual(["Senders"], entities["Payload"].get("bases", []))
        self.assertEqual(["From", "Runner", "Senders"], result.implements)


if __name__ == "__main__":
    unittest.main()
