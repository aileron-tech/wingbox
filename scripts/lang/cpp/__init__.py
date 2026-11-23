from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import BoundCppLanguage

# A `.h` file is read as C++: nothing in its name says which of the two languages it is written in.
C_SOURCE_EXTENSIONS: tuple[str, ...] = (".c",)
CPP_SOURCE_EXTENSIONS: tuple[str, ...] = (".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp")


def result_language(path: str) -> str:
    """`c` for a `.c` file, `cpp` for every other file of the language."""
    return "c" if path.casefold().endswith(C_SOURCE_EXTENSIONS) else "cpp"


def parse_cpp(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    from .parser import parse_cpp as parse

    return parse(path, content, repo, mtime, result_language(path))


@dataclass(frozen=True)
class CppLanguage:
    name: str = "cpp"
    result_languages: tuple[str, ...] = ("cpp", "c")
    lsp_result_languages: tuple[str, ...] = ("cpp", "c")
    # A change of one of them can change the file an include names: the graph is then rebuilt.
    project_config_names: tuple[str, ...] = ("compile_commands.json", "CMakeLists.txt")
    lsp_command: tuple[str, ...] = ("clangd",)
    lsp_label: str = "C++"
    display_name: str = "C and C++"
    cli_label: str = "cpp"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-cpp", "0.23.4"),
    )
    source_extensions: tuple[str, ...] = C_SOURCE_EXTENSIONS + CPP_SOURCE_EXTENSIONS

    def accepts_file(self, file_name: str) -> bool:
        return file_name.casefold().endswith(self.source_extensions)

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_cpp(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundCppLanguage.create(root, path_to_id, discovery_exclusions)

    def normalize_module_id(self, relative_path: str) -> str:
        # A header and the source file beside it share their stem (`vm.h`, `vm.cpp`) and are two
        # modules: the module id keeps the extension.
        if not self.accepts_file(relative_path):
            raise ValueError(f"C or C++ module path has an unsupported extension: {relative_path}")
        return relative_path

    def output_language_code(self, result_language: str) -> str:
        if result_language not in self.result_languages:
            raise ValueError(f"Unsupported C or C++ result language: {result_language}")
        return result_language

    def lsp_language_id(self, path: str) -> str:
        return result_language(path)


CPP_LANGUAGE = CppLanguage()


__all__ = [
    "CPP_LANGUAGE",
    "CppLanguage",
    "parse_cpp",
    "result_language",
]
