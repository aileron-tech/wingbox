from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import BoundPhpLanguage


@dataclass(frozen=True)
class PhpLanguage:
    name: str = "php"
    result_languages: tuple[str, ...] = ("php", "blade")
    lsp_result_languages: tuple[str, ...] = ("php",)
    project_config_names: tuple[str, ...] = ("composer.json",)
    lsp_command: tuple[str, ...] = ("intelephense", "--stdio")
    lsp_label: str = "PHP"
    display_name: str = "PHP and Laravel"
    cli_label: str = "php"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-php", "0.24.1"),
    )
    source_extensions: tuple[str, ...] = (".php",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(self.source_extensions)

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        if path.endswith(".blade.php"):
            from .blade import parse_blade

            return parse_blade(path, content, repo, mtime)
        from .parser import parse_php_treesitter

        return parse_php_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundPhpLanguage.create(
            root,
            path_to_id,
            discovery_exclusions,
        )

    def normalize_module_id(self, relative_path: str) -> str:
        for extension in (".blade.php", *self.source_extensions):
            if relative_path.endswith(extension):
                return relative_path[: -len(extension)]
        raise ValueError(
            f"PHP module path must end in .php or .blade.php: {relative_path}"
        )

    def output_language_code(self, result_language: str) -> str:
        if result_language == "php":
            return "php"
        if result_language == "blade":
            return "blade"
        raise ValueError(f"Unsupported PHP result language: {result_language}")

    def lsp_language_id(self, path: str) -> str:
        return "php"


PHP_LANGUAGE = PhpLanguage()


__all__ = [
    "PHP_LANGUAGE",
    "PhpLanguage",
]