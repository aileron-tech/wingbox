from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import DART_EXTENSION, BoundDartLanguage


def parse_dart_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    from .parser import parse_dart_treesitter as parse_dart

    return parse_dart(path, content, repo, mtime)


@dataclass(frozen=True)
class DartLanguage:
    name: str = "dart"
    result_languages: tuple[str, ...] = ("dart",)
    lsp_result_languages: tuple[str, ...] = ("dart",)
    project_config_names: tuple[str, ...] = ("pubspec.yaml",)
    lsp_command: tuple[str, ...] = ("dart", "language-server")
    lsp_label: str = "Dart"
    display_name: str = "Dart"
    cli_label: str = "dart"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-dart", "0.1.0"),
    )
    source_extensions: tuple[str, ...] = (".dart",)

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
        return parse_dart_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundDartLanguage.create(root, path_to_id, discovery_exclusions)

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(DART_EXTENSION):
            raise ValueError(f"Dart module path must end in .dart: {relative_path}")
        return relative_path[: -len(DART_EXTENSION)]

    def output_language_code(self, result_language: str) -> str:
        if result_language != "dart":
            raise ValueError(f"Unsupported Dart result language: {result_language}")
        return "dart"

    def lsp_language_id(self, path: str) -> str:
        return "dart"


DART_LANGUAGE = DartLanguage()


__all__ = [
    "DART_LANGUAGE",
    "DartLanguage",
    "parse_dart_treesitter",
]