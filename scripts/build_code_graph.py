#!/usr/bin/env python3
"""
Code Graph Builder -- Architecture Graph Generator.

Generates ``.wingbox/index.sexp``, a flat S-expression graph of all modules,
entities, and relationships in a codebase. Each line is a self-contained
fact, queryable with ``rg`` or ``scripts/code_graph_search.py``.

Based on the Theory of Code Space (ToCS) methodology for reducing the
"Active-Passive Gap" in AI code agents.

Parsers
-------
- **TypeScript/TSX**: tree-sitter (``tree-sitter-typescript``). Handles
  decorators, heritage, all methods, object literals, arrow functions.
- **Python**: stdlib ``ast``. Full class/function/import extraction.
- **Rust**: tree-sitter (``tree-sitter-rust``). Handles Cargo crates,
  declarations, implementations, methods, and ``use``/``mod`` dependencies.
- **C#**: tree-sitter (``tree-sitter-c-sharp``). Handles namespaces, types,
  methods, attributes, inheritance, and local type dependencies.
- **Java**: tree-sitter (``tree-sitter-java``). Handles packages, types,
  methods, annotations, inheritance, and local type dependencies.
- **Go**: tree-sitter (``tree-sitter-go``). Handles modules, packages, types,
  functions, receiver methods, and local package dependencies.
- **Ruby**: tree-sitter (``tree-sitter-ruby``). Handles Ruby declarations,
  Rails/Zeitwerk constants, mixins, associations, jobs, mailers, and specs.
- **Pascal**: dependency-free structural parser. Handles Free Pascal/Lazarus
  units, programs, uses/includes, types, inheritance, methods, and routines.
- **Dart**: tree-sitter (``tree-sitter-dart``). Handles lib/ packages found
  through pubspec.yaml, classes, mixins, enums, extensions, members,
  inheritance, imports/exports, and part/part of libraries.

Modes
-----
**Default** (fast, ~5-8s for full workspace):
    Parses all supported Python, TypeScript, Rust, C#, Java, Go, Ruby, and
    Pascal source files and builds import/reference/data-flow edges.

**LSP-enhanced** (``--lsp``, ~1-5min):
    After default pass, starts ``typescript-language-server`` and
    ``pyright-langserver``, ``rust-analyzer``, ``csharp-ls``, ``jdtls``,
    ``gopls``, or ``ruby-lsp`` per project to query call hierarchy and references. Adds precise
    ``calls`` edges and ``:lsp`` annotations.

Edge Types
----------
- ``imports`` -- module imports module. ``:via-base`` if target exports
  abstract types.
- ``calls-api`` -- implements/extends abstract type. ``:via-ifc``.
- ``data-flow`` -- the target is a validation module: its syntax tree defines a
  validator, named after the mark, ``:validation-module :evidence "<kind>
  <name>"`` (the importer is not shown to call it); or the target is the next
  step of a configured pipeline, ``:pipeline``.
- ``registry-wires`` -- module loads module by a name read from a configuration
  file beside it (Python ``importlib``). ``:config <path>``.
- ``refs`` -- entity-level cross-file reference (``Module::Entity``).
- ``calls`` -- function calls function with call-site lines: from LSP
  (``:lsp``), or through a registry that wires the target (``:registry``).

Output Format
-------------
Flat S-expression facts (``.wingbox/index.sexp``)::

    (node module/id ts "purpose description")
    (cls module/id ClassName 49 (:deco Injectable) (:methods create update delete))
    (fn module/id functionName 126)
    (imports module/a module/b :via-base)
    (refs module/a module/b::EntityName)
    (calls module/a::fnA module/b::fnB :lines 52 67 :lsp)

Requirements
------------
Python indexing uses the standard library only. Install exact parser packages
only for languages present in the target repository::

    python3 -m pip install -r requirements/typescript.txt
    python3 -m pip install -r requirements/rust.txt
    python3 -m pip install -r requirements/csharp.txt
    python3 -m pip install -r requirements/java.txt
    python3 -m pip install -r requirements/go.txt
    python3 -m pip install -r requirements/ruby.txt

The builder detects missing or incompatible packages before publication and
prints only the commands required by the discovered languages.

The installed ``wingbox`` package carries the parsers of every language, and a
parser is imported only from the environment that runs the builder. Where the
parser of C#, Java, Go, Ruby, PHP or Dart is missing all the same (a broken
install, Dart on Windows arm64), the files of that language are skipped with a
warning that names the pip extra, ``pip install "wingbox[dart]"``, and recorded
in ``.wingbox/graph-skipped.sexp``; the other languages are mapped.

Optional for ``--lsp``: ``typescript-language-server``, ``pyright-langserver``,
``rust-analyzer``, ``csharp-ls``, ``jdtls``, ``gopls``, or ``ruby-lsp``.

Usage
-----
::

    python3 /absolute/path/to/scripts/build_code_graph.py \
        --root /absolute/path/to/project
    python3 /absolute/path/to/scripts/build_code_graph.py \
        --root /absolute/path/to/project --full
    python3 /absolute/path/to/scripts/build_code_graph.py \
        --root /absolute/path/to/project --lsp
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Generic, Iterable, Mapping, Optional, Sequence, TypeVar
from urllib.parse import quote, unquote

try:
    if __package__:
        from .lang import (
            LANGUAGES,
            PIP_EXTRA_LANGUAGES,
            BoundLanguage,
            DiscoveryExclusions,
            FileResult,
            Language,
            language_for_file,
            language_for_name,
            language_for_result,
        )
        from .lang.config_values import CONFIG_SUFFIXES, ConfigValues, read_config_values
        from .lang.purpose import infer_purpose, scan_root
        from .lang.discovery import is_excluded_directory, is_excluded_path
        from .lang.interface import EntityPayload
        from .lang.symbols import SymbolIndex, supports_symbols
        from .lang.source import file_hash
        from .lang.typescript.javascript import is_minified_javascript
        from .git_ignore import (
            GitIgnoreUnavailable,
            load_git_ignored_paths,
        )
        from . import layer_guess, portable
        from .architecture.config import ConfigErr, plan_from_config, read_config
        from .architecture.baseline import read_baseline_terms, terms_sexp
        from .architecture.facts import (
            BASELINE_FILE, GraphArchitecture, graph_architecture, read_baseline_sexp, read_capability_baseline,
        )
        from .architecture.capabilities import Finding as _finding, detect as detect_capabilities, supported as capability_language
        from .architecture.library import load_library
        from .architecture.plan import RepositoryPlan, detect_plan
        from .architecture.repository import list_files, text_reader
        from .architecture.decisions import STORE_FILE, learned_layers, read_store
        from .architecture.plan import apply_learned
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from lang import (
            LANGUAGES,
            PIP_EXTRA_LANGUAGES,
            BoundLanguage,
            DiscoveryExclusions,
            FileResult,
            Language,
            language_for_file,
            language_for_name,
            language_for_result,
        )
        from lang.config_values import CONFIG_SUFFIXES, ConfigValues, read_config_values
        from lang.purpose import infer_purpose, scan_root
        from lang.discovery import is_excluded_directory, is_excluded_path
        from lang.interface import EntityPayload
        from lang.symbols import SymbolIndex, supports_symbols
        from lang.source import file_hash
        from lang.typescript.javascript import is_minified_javascript
        from git_ignore import (
            GitIgnoreUnavailable,
            load_git_ignored_paths,
        )
        import layer_guess
        import portable
        from architecture.config import ConfigErr, plan_from_config, read_config
        from architecture.baseline import read_baseline_terms, terms_sexp
        from architecture.facts import (
            BASELINE_FILE, GraphArchitecture, graph_architecture, read_baseline_sexp, read_capability_baseline,
        )
        from architecture.capabilities import Finding as _finding, detect as detect_capabilities, supported as capability_language
        from architecture.library import load_library
        from architecture.plan import RepositoryPlan, detect_plan
        from architecture.repository import list_files, text_reader
        from architecture.decisions import STORE_FILE, learned_layers, read_store
        from architecture.plan import apply_learned
except ModuleNotFoundError as dependency_error:
    print(
        f"Error: missing Python dependency {dependency_error.name}. "
        "Install pinned dependencies with "
        "`python3 -m pip install -r requirements.txt`.",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "dist",
    ".next",
    ".turbo",
    "build",
    "obj",
    "coverage",
    ".tox",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "vendor",
    ".wingbox",
    ".worktrees",
    ".ralphy-worktrees",
    ".ralphy-sandboxes",
    # What Wingbox writes into a project: never sources, and a file it adds there must not
    # change the repository ignore set (and with it the cache fingerprint).
    ".wingbox",
}
DEFAULT_DISCOVERY_EXCLUSIONS = DiscoveryExclusions(
    directory_names=frozenset(SKIP_DIRS),
    paths=frozenset(),
)

# Everything a build writes lives in ROOT/.wingbox/, which also marks the project root.
WINGBOX_DIRECTORY = ".wingbox"
INDEX_FILE = "index.sexp"
CACHE_FILE = "cache.json"
GITIGNORE_MARK = "# Written by Wingbox: the files it generates."
GITIGNORE_TEXT = GITIGNORE_MARK + """ Files you write here (config.yaml, ...)
# stay visible to git; the whole folder can be ignored as well. This file is generated too.
.gitignore
index*
cache*
REPORT.md
map.html
graph-*
refresh.*
*.lock
*.tmp
"""


# Source files are read as UTF-8 with an optional byte order mark: Visual Studio and some Windows editors
# write one, Python's own tokenizer accepts it, and left in the text it makes ast.parse fail (issue #105).
SOURCE_ENCODING = "utf-8-sig"


def wingbox_directory(root: str) -> str:
    return os.path.join(root, WINGBOX_DIRECTORY)


def index_path(root: str) -> str:
    """The code graph of the project at root."""
    return os.path.join(wingbox_directory(root), INDEX_FILE)


def cache_path(root: str) -> str:
    return os.path.join(wingbox_directory(root), CACHE_FILE)


def prepare_wingbox_directory(root: str) -> None:
    """Create ROOT/.wingbox/ and its .gitignore (kept when a person rewrote it)."""
    folder = wingbox_directory(root)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, ".gitignore")
    # Two processes may prepare the folder at once (two MCP servers started together): portable.read_text
    # waits a moment on Windows while the other one is replacing the file.
    current = portable.read_text(path)
    if current is None or (current.startswith(GITIGNORE_MARK) and current != GITIGNORE_TEXT):
        _atomic_write_text(path, GITIGNORE_TEXT)
# 2: purposes come from the path inside the repository; TypeScript results carry import_forms.
# 3: Python results carry module_loads and registry_calls.
CACHE_SCHEMA_VERSION = 6  # 6: validation is read from the syntax tree and stored with its evidence
MAP_SCHEMA_VERSION = 1
BUILDER_CONFIG_VERSION = "2026-11-16-php"

# Default timeout for LSP requests (seconds)
LSP_REQUEST_TIMEOUT = 15.0
# Timeout for LSP server initialization (seconds)
LSP_INIT_TIMEOUT = 60.0

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result = Ok[T] | Err[E]


@dataclass(frozen=True)
class NodeIdCollision:
    node_id: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class GraphBuildError:
    collisions: tuple[NodeIdCollision, ...]


@dataclass(frozen=True)
class GraphBuildOutput:
    nodes: list[dict]
    edges: list[dict]
    fragments: Mapping[str, Any] = field(default_factory=dict)  # node ID -> ModuleFragment, when uses were resolved


@dataclass
class LspEdge:
    """An edge discovered by LSP analysis."""

    source: str  # "node_id::entity_name" or "node_id"
    target: str  # "node_id::entity_name" or "node_id"
    edge_type: str  # "CALLS", "REFERENCES"
    metadata: dict = field(default_factory=dict)


@dataclass
class ProjectGroup:
    """A group of files belonging to the same LSP project."""

    root: str  # absolute path to project root
    language: str  # language adapter name
    config_file: str  # language-owned project configuration
    files: list[FileResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def path_to_uri(path: str) -> str:
    """Convert an absolute file path to a file:// URI."""
    abs_path = os.path.abspath(path)
    return "file://" + quote(abs_path, safe="/:")


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute file path."""
    if uri.startswith("file://"):
        return unquote(uri[7:])
    return uri


def find_entity_column(content: str, entity_name: str, line_1based: int) -> int:
    """Find the 0-based column of *entity_name* on the given 1-based line."""
    lines = content.split("\n")
    if line_1based < 1 or line_1based > len(lines):
        return 0
    line_text = lines[line_1based - 1]
    idx = line_text.find(entity_name)
    return max(idx, 0)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def parse_file(args: tuple[str, ...]) -> FileResult:
    """Parse one file: (path, language, repository name[, scan root])."""
    path, language_name, repo, *rest = args
    mtime = os.path.getmtime(path)
    with open(path, "r", encoding=SOURCE_ENCODING, errors="replace") as source_file:
        content = source_file.read()
    with scan_root(rest[0] if rest else ""):
        return language_for_name(language_name).parse(path, content, repo, mtime)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def detect_source_language(filename: str) -> Optional[str]:
    """Return the supported parser language for a source filename."""
    language = language_for_file(filename)
    return language.name if language is not None else None


# Files outside the sources that change how imports resolve; a change to one of
# them is never patched into a graph: it takes an incremental build.
RESOLUTION_CONFIG_NAMES = frozenset(
    {
        "package.json",
        ".gitignore",
        "pyproject.toml",
        "setup.cfg",
        "pyrightconfig.json",
        "Cargo.toml",
        "go.mod",
        "go.work",
        "Gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    }
    | {name for language in LANGUAGES for name in language.project_config_names}
)
RESOLUTION_CONFIG_PATTERNS = (("tsconfig", ".json"), ("jsconfig", ".json"), ("", ".csproj"), ("", ".sln"))
SNAPSHOT_SCHEMA_VERSION = 1


def is_resolution_config(name: str) -> bool:
    return name in RESOLUTION_CONFIG_NAMES or any(
        name.startswith(prefix) and name.endswith(suffix) for prefix, suffix in RESOLUTION_CONFIG_PATTERNS
    )


@dataclass
class TreeSnapshot:
    """What discovery saw: (mtime_ns, size) of every source and resolution config, mtime_ns of every folder."""

    files: dict[str, tuple[int, int]] = field(default_factory=dict)
    directories: dict[str, int] = field(default_factory=dict)
    # Folders of Python module loaders: their JSON, YAML and TOML files name the modules a registry
    # loads (registry-wires), so a change to one of them changes the graph.
    data_folders: set[str] = field(default_factory=set)


def snapshot_json(snapshot: TreeSnapshot, skip_directories: frozenset[str], root: str) -> str:
    """The snapshot file that `wingbox` compares the tree with to find changes made outside it.

    root is the folder the graph was built from: a .wingbox/ folder copied or moved elsewhere is
    never refreshed against the folder it landed in.
    """
    return json.dumps(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "root": root,
            "extensions": sorted({extension for language in LANGUAGES for extension in language.source_extensions}),
            "config_names": sorted(RESOLUTION_CONFIG_NAMES),
            "config_patterns": [list(pair) for pair in RESOLUTION_CONFIG_PATTERNS],
            "skip_dirs": sorted(skip_directories),
            "files": {path: list(value) for path, value in sorted(snapshot.files.items())},
            "directories": dict(sorted(snapshot.directories.items())),
            "data_folders": sorted(snapshot.data_folders),
        },
        separators=(",", ":"),
    )


def loader_folders(root: str, results: Iterable[FileResult]) -> set[str]:
    """Folders (relative, with /) of the files that load modules by a name read at run time."""
    folders = {os.path.relpath(os.path.dirname(result.path), root).replace(os.sep, "/") for result in results if result.module_loads}
    return {"" if folder == "." else folder for folder in folders}


def record_loader_configs(root: str, snapshot: TreeSnapshot, results: Iterable[FileResult]) -> None:
    """Add the configuration files beside each module loader, and its folder, to the snapshot."""
    for folder in sorted(loader_folders(root, results)):
        snapshot.data_folders.add(folder)
        directory = os.path.join(root, *folder.split("/")) if folder else root
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.endswith(CONFIG_SUFFIXES) and entry.is_file(follow_symlinks=False):
                status = entry.stat(follow_symlinks=False)
                snapshot.files[f"{folder}/{entry.name}" if folder else entry.name] = (status.st_mtime_ns, status.st_size)


def discover_files(
    root: str,
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
    snapshot: Optional[TreeSnapshot] = None,
) -> list[tuple[str, str, str]]:
    """Source files under root: (path, language, repository). With snapshot, also record what was seen."""
    results: list[tuple[str, str, str]] = []
    root_path = Path(root).resolve()

    def _relative(path: str) -> str:
        return os.path.relpath(path, root_path).replace(os.sep, "/")

    def _record_file(entry: os.DirEntry) -> None:
        if snapshot is not None:
            status = entry.stat(follow_symlinks=False)
            snapshot.files[_relative(entry.path)] = (status.st_mtime_ns, status.st_size)

    def _walk(dir_path: Path, repo: str):
        if snapshot is not None:
            relative = _relative(str(dir_path))
            snapshot.directories["" if relative == "." else relative] = os.stat(dir_path).st_mtime_ns
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if is_excluded_directory(exclusions, str(dir_path), entry.name):
                            continue
                        child_repo = repo
                        if (
                            entry.name != ".git"
                            and (dir_path / entry.name / ".git").exists()
                        ):
                            child_repo = entry.name
                        _walk(dir_path / entry.name, child_repo)
                    elif entry.is_file(follow_symlinks=False):
                        if is_excluded_path(exclusions, entry.path):
                            continue
                        language = language_for_file(entry.name)
                        if language is not None and language.name == "pascal":
                            relative_parent_parts = Path(entry.path).relative_to(
                                root_path
                            ).parts[:-1]
                            if any(
                                part.casefold() == "backup"
                                for part in relative_parent_parts
                            ):
                                continue
                        if (
                            language is not None
                            and language.name == "typescript"
                            and is_minified_javascript(entry.path)
                        ):
                            continue
                        if language is not None:
                            results.append((entry.path, language.name, repo))
                            _record_file(entry)
                        elif is_resolution_config(entry.name):
                            _record_file(entry)
        except PermissionError as exc:
            print(
                f"[code-graph] WARNING: could not scan {dir_path}: {exc}",
                file=sys.stderr,
            )

    _walk(root_path, root_path.name)
    return results


# ---------------------------------------------------------------------------
# Incremental cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanguageDependencyIssue:
    language: Language
    unavailable: tuple[str, ...]


def _active_languages(language_names: frozenset[str]) -> tuple[Language, ...]:
    return tuple(language for language in LANGUAGES if language.name in language_names)


def _dependency_issues(
    language_names: frozenset[str],
) -> tuple[LanguageDependencyIssue, ...]:
    issues: list[LanguageDependencyIssue] = []
    for language in _active_languages(language_names):
        unavailable: list[str] = []
        for dependency in language.dependencies:
            try:
                installed_version = importlib.metadata.version(dependency.distribution)
            except importlib.metadata.PackageNotFoundError:
                unavailable.append(f"{dependency.requirement} (missing)")
                continue
            if installed_version != dependency.version:
                unavailable.append(
                    f"{dependency.requirement} (installed {installed_version})"
                )
        if unavailable:
            issues.append(LanguageDependencyIssue(language, tuple(unavailable)))
    return tuple(issues)


# Languages whose parser is also a pip extra of the same name. Where the parser is missing (a broken
# install, Dart on Windows arm64), their files are left out of the graph with a warning; the build goes on.
OPTIONAL_LANGUAGE_EXTRAS: dict[str, str] = {name: name for name in PIP_EXTRA_LANGUAGES}
# What the last build left out, for the readers of the graph: one `(skipped-language ...)` fact per language.
SKIPPED_FILE = "graph-skipped.sexp"


def install_command(language_name: str) -> str:
    """The command that installs a missing parser: the pip extra of its language."""
    return f'pip install "wingbox[{OPTIONAL_LANGUAGE_EXTRAS[language_name]}]"'


def skipped_path(root: str) -> str:
    return os.path.join(wingbox_directory(root), SKIPPED_FILE)


def skipped_language_facts(skipped: Sequence[tuple[str, int]]) -> str:
    """The record of the languages a build left out: (name, files) -> one fact per line. `:add` holds the
    command that installs the parser, as a quoted string."""
    return "".join(
        '(skipped-language {} :files {} :add "{}")\n'.format(name, files, install_command(name).replace('"', '\\"'))
        for name, files in skipped
    )


def _publish_skipped(root: str, skipped: Sequence[tuple[str, int]]) -> None:
    """Write the record beside the graph, or remove the one of an earlier build when nothing was skipped."""
    path = skipped_path(root)
    if skipped:
        _atomic_write_text(path, skipped_language_facts(skipped))
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _requirements_command(language_name: str) -> Optional[str]:
    """The pip command of a clone for one language; None in the installed package, which has no requirements files."""
    path = Path(__file__).resolve().parent.parent / "requirements" / f"{language_name}.txt"
    if not path.is_file():
        return None
    return f'{"python" if portable.WINDOWS else "python3"} -m pip install -r "{path.as_posix()}"'


def _report_skipped_optional_languages(
    issues: tuple[LanguageDependencyIssue, ...],
    counts: Mapping[str, int],
) -> None:
    for issue in issues:
        name = issue.language.name
        files = counts.get(name, 0)
        from_clone = _requirements_command(name)
        print(
            f"[code-graph] WARNING: {issue.language.display_name} files are skipped "
            f"({files} file{'' if files == 1 else 's'}): {', '.join(issue.unavailable)}. "
            f"To include them: {install_command(name)}"
            + (f" (from a clone: {from_clone})" if from_clone else ""),
            file=sys.stderr,
        )


def _report_dependency_issues(
    issues: tuple[LanguageDependencyIssue, ...],
) -> None:
    print(
        "[code-graph] ERROR: parser dependencies are missing or incompatible:",
        file=sys.stderr,
    )
    for issue in issues:
        print(
            f"[code-graph]   {issue.language.display_name}: {', '.join(issue.unavailable)}",
            file=sys.stderr,
        )
    print(
        "[code-graph] Install only the detected language dependencies:",
        file=sys.stderr,
    )
    for issue in issues:
        if issue.language.name in OPTIONAL_LANGUAGE_EXTRAS:
            print(f"[code-graph]   {install_command(issue.language.name)}", file=sys.stderr)
        from_clone = _requirements_command(issue.language.name)
        if from_clone:
            print(f"[code-graph]   {from_clone}", file=sys.stderr)
        elif issue.language.name not in OPTIONAL_LANGUAGE_EXTRAS:
            print(
                f"[code-graph]   reinstall wingbox: {', '.join(dep.requirement for dep in issue.language.dependencies)} "
                "are its own dependencies",
                file=sys.stderr,
            )
    print(
        "[code-graph] ERROR: no cache or map was published",
        file=sys.stderr,
    )


def _builder_fingerprint(
    language_names: frozenset[str],
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> str:
    """Fingerprint active parsers, dependencies, and shared configuration."""
    active_languages = _active_languages(language_names)
    dependency_packages = sorted(
        {
            dependency.distribution
            for language in active_languages
            for dependency in language.dependencies
        }
    )
    dependency_versions = "|".join(
        f"{name}={importlib.metadata.version(name)}" for name in dependency_packages
    )
    config = "|".join(
        (
            BUILDER_CONFIG_VERSION,
            ",".join(sorted(language_names)),
            dependency_versions,
            ",".join(sorted(exclusions.directory_names)),
            ",".join(sorted(exclusions.paths)),
        )
    )
    digest = hashlib.sha256()
    digest.update(config.encode("utf-8"))
    language_root = Path(__file__).parent / "lang"
    source_paths = [
        Path(__file__),
        Path(__file__).parent / "git_ignore.py",
        *sorted(language_root.glob("*.py")),
    ]
    for language in active_languages:
        source_paths.extend(sorted((language_root / language.name).rglob("*.py")))
    for source_path in source_paths:
        digest.update(str(source_path.relative_to(Path(__file__).parent)).encode())
        with open(source_path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: str, content: str) -> None:
    """Atomically replace a text file after durable same-directory staging."""
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary_path = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        # newline="\n": the graph and the reports are the same bytes on every system.
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        portable.replace(temporary_path, path)
        portable.fsync_directory(directory)
    except OSError:
        if os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError as cleanup_error:
                print(
                    f"[code-graph] ERROR: could not remove {temporary_path}: {cleanup_error}",
                    file=sys.stderr,
                )
        raise


def load_cache(
    root: str,
    language_names: frozenset[str],
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> dict:
    """Load compatible cache entries or rebuild with an explicit warning."""
    path = cache_path(root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[code-graph] WARNING: could not load cache {path}: {exc}; rebuilding",
            file=sys.stderr,
        )
        return {}

    if not isinstance(cache, dict):
        print(
            f"[code-graph] WARNING: incompatible cache {path}: top-level value must be an object; rebuilding",
            file=sys.stderr,
        )
        return {}
    expected_fingerprint = _builder_fingerprint(
        language_names,
        exclusions,
    )
    if (
        cache.get("schema_version") != CACHE_SCHEMA_VERSION
        or cache.get("builder_fingerprint") != expected_fingerprint
        or not isinstance(cache.get("entries"), dict)
    ):
        print(
            f"[code-graph] WARNING: incompatible cache {path}: schema or builder fingerprint changed; rebuilding",
            file=sys.stderr,
        )
        return {}
    entries = cache["entries"]
    if isinstance(entries, dict):
        return entries
    return {}


def save_cache(
    root: str,
    entries: dict,
    language_names: frozenset[str],
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> None:
    cache = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "builder_fingerprint": _builder_fingerprint(
            language_names,
            exclusions,
        ),
        "entries": entries,
    }
    serialized = json.dumps(cache, sort_keys=True, separators=(",", ":")) + "\n"
    _atomic_write_text(cache_path(root), serialized)


def decode_cache_entry(entry: object) -> Result[FileResult, str]:
    """Validate a cached parse result at the persistence boundary."""
    if not isinstance(entry, dict):
        return Err("cache entry must be an object")
    raw_result = entry.get("result")
    if not isinstance(raw_result, dict):
        return Err("cache entry result must be an object")

    string_fields = (
        "path",
        "language",
        "repo",
        "content_hash",
        "purpose",
        "naming_convention",
    )
    for field_name in string_fields:
        if not isinstance(raw_result.get(field_name), str):
            return Err(f"cache result field {field_name} must be a string")
    if not isinstance(raw_result.get("mtime"), (int, float)):
        return Err("cache result field mtime must be numeric")
    if not isinstance(raw_result.get("has_validation"), bool):
        return Err("cache result field has_validation must be boolean")
    if not isinstance(raw_result.get("validation_evidence", ""), str):
        return Err("cache result field validation_evidence must be a string")
    list_fields = (
        "imports",
        "exports_abstract",
        "implements",
        "extends",
        "entities",
        "imported_names",
        "exported_names",
        "relations",
    )
    for field_name in list_fields:
        if not isinstance(raw_result.get(field_name), list):
            return Err(f"cache result field {field_name} must be a list")

    for field_name in ("module_loads", "registry_calls", "type_members"):
        if not isinstance(raw_result.get(field_name, []), list):
            return Err(f"cache result field {field_name} must be a list")
    if not isinstance(raw_result.get("import_forms", {}), dict):
        return Err("cache result field import_forms must be an object")
    for field_name in ("bindings", "uses"):
        if not isinstance(raw_result.get(field_name, []), list):
            return Err(f"cache result field {field_name} must be a list")

    try:
        return Ok(FileResult(**raw_result))
    except TypeError as error:
        return Err(f"cache result shape is invalid: {error}")


def is_cache_entry_current(path: str, entry: object) -> bool:
    """Return whether a cache entry matches the file's complete content hash."""
    if not isinstance(entry, dict):
        return False
    result = entry.get("result")
    if not isinstance(result, dict):
        return False
    cached_hash = result.get("content_hash")
    if not isinstance(cached_hash, str) or not cached_hash:
        return False
    current_hash = file_hash(path)
    return bool(current_hash) and current_hash == cached_hash


# ---------------------------------------------------------------------------
# Import resolution is owned by the bound language adapters.


def make_node_id(path: str, root: str) -> str:
    relative_path = os.path.relpath(path, root).replace(os.sep, "/")
    language = language_for_file(os.path.basename(path))
    if language is None:
        raise ValueError(f"Unsupported source path: {relative_path}")
    return language.normalize_module_id(relative_path)


# Extension order in which a module ID is looked up on disk (code_graph_search._module_id_to_file):
# when several files share one ID, the first in this order keeps it.
_NODE_ID_EXTENSION_ORDER = (
    ".ts", ".tsx", ".js", ".jsx", ".py", ".rb", ".rake", ".rs",
    ".blade.php", ".php", ".pas", ".pp", ".lpr", ".inc", ".go", ".java", ".cs",
)


def _node_id_owner_rank(relative_path: str, node_id: str) -> tuple[int, int, str]:
    stem, extension = os.path.splitext(relative_path)
    order = (
        _NODE_ID_EXTENSION_ORDER.index(extension)
        if extension in _NODE_ID_EXTENSION_ORDER
        else len(_NODE_ID_EXTENSION_ORDER)
    )
    return (0 if stem == node_id else 1, order, relative_path)


def assign_node_ids(paths: list[str], root: str) -> dict[str, str]:
    """Give every source file its own module ID.

    The ID is the path without its extension. Files that would share one (x.test.ts
    next to x.test.tsx, tool.py next to tool.ts, pkg.py next to pkg/index.py) are all
    kept: the first one keeps the plain ID and each other file is named by its full
    relative path, extension included.
    """
    paths_by_id: dict[str, list[str]] = {}
    for path in paths:
        paths_by_id.setdefault(make_node_id(path, root), []).append(path)

    assigned: dict[str, str] = {}
    for node_id, sharing in paths_by_id.items():
        relative = {path: os.path.relpath(path, root).replace(os.sep, "/") for path in sharing}
        ranked = sorted(sharing, key=lambda path: _node_id_owner_rank(relative[path], node_id))
        assigned[ranked[0]] = node_id
        for path in ranked[1:]:
            assigned[path] = relative[path]
    return assigned


# Graph building
# ---------------------------------------------------------------------------


def _build_node_indexes(
    results: list[FileResult],
    root: str,
) -> Result[tuple[dict[str, str], dict[str, FileResult]], GraphBuildError]:
    """Build collision-free module indexes before any graph facts are emitted."""
    path_to_id = assign_node_ids([result.path for result in results], root)
    paths_by_id: dict[str, list[str]] = {}
    for path, node_id in path_to_id.items():
        relative_path = os.path.relpath(path, root).replace(os.sep, "/")
        paths_by_id.setdefault(node_id, []).append(relative_path)

    collisions = tuple(
        NodeIdCollision(node_id, tuple(sorted(paths)))
        for node_id, paths in sorted(paths_by_id.items())
        if len(paths) > 1
    )
    if collisions:
        return Err(GraphBuildError(collisions))

    id_to_result: dict[str, FileResult] = {}
    ordered_paths: dict[str, str] = {}
    for result in sorted(results, key=lambda item: path_to_id[item.path]):
        ordered_paths[result.path] = path_to_id[result.path]
        id_to_result[path_to_id[result.path]] = result
    return Ok((ordered_paths, id_to_result))


def _imported_name_parts(raw: object) -> Optional[tuple[str, str]]:
    """Read the local name and module from a validated import descriptor."""
    if not isinstance(raw, dict):
        return None
    local_name = raw.get("local")
    module = raw.get("module")
    if not isinstance(local_name, str) or not isinstance(module, str):
        return None
    return local_name, module


# Languages where a name is visible in a file only through an import or a
# definition in that file. A base class is never guessed by its name there.
_IMPORT_SCOPED_LANGUAGES = frozenset({"python", "typescript"})


def _wildcard_import_targets(
    source: FileResult,
    bound_language: BoundLanguage,
) -> set[str]:
    """Modules whose every public name the file imports (``from m import *``)."""
    targets: set[str] = set()
    for imported_name in source.imported_names:
        imported_parts = _imported_name_parts(imported_name)
        if imported_parts is None or imported_parts[0] != "*":
            continue
        target_id = bound_language.resolve_import(imported_parts[1], source.path)
        if target_id is not None:
            targets.add(target_id)
    return targets


def _resolve_provider(
    symbol: str,
    source: FileResult,
    bound_language: BoundLanguage,
    provider_candidates: dict[str, set[str]],
    id_to_result: Mapping[str, FileResult],
) -> Optional[str]:
    """Resolve a provider through an explicit import, otherwise fail closed.

    Without an import the only provider of that name in the same language is
    accepted; in an import-scoped language it must also come from a wildcard
    import of the file.
    """
    for imported_name in source.imported_names:
        imported_parts = _imported_name_parts(imported_name)
        if imported_parts is None:
            continue
        local_name, module = imported_parts
        if local_name != symbol:
            continue
        return bound_language.resolve_import(module, source.path)

    own_language = language_for_result(source.language).name
    candidates = {
        candidate
        for candidate in provider_candidates.get(symbol, set())
        if language_for_result(id_to_result[candidate].language).name == own_language
    }
    if own_language in _IMPORT_SCOPED_LANGUAGES:
        candidates &= _wildcard_import_targets(source, bound_language)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


_TYPE_ENTITY_KINDS = frozenset({"interface", "type"})


def _imports_types_only(
    source: FileResult,
    target_id: str,
    target: FileResult,
    bound_language: BoundLanguage,
    resolved: Optional[Mapping[str, Optional[str]]] = None,
) -> bool:
    """True when source imports target for types only (TypeScript erases such an import).

    Every specifier of source that resolves to target must be imported for
    names only; a name of a statement that is not `import type` must be a
    type or an interface of target.
    """
    forms = source.import_forms
    if not forms:
        return False
    # `resolved` (specifier -> module) is computed once per source file: resolving every import again for each
    # target made a rebuild quadratic in the imports of a file.
    specifiers = {
        specifier
        for specifier in source.imports
        if (resolved[specifier] if resolved is not None and specifier in resolved
            else bound_language.resolve_import(specifier, source.path)) == target_id
    }
    if not specifiers or any(specifier not in forms for specifier in specifiers):
        return False
    type_names = {entity["name"] for entity in target.entities if entity["kind"] in _TYPE_ENTITY_KINDS}
    return all(
        _resolve_target_entity(target.entities, name["original"], name["local"]) in type_names
        for name in source.imported_names
        if name["module"] in specifiers and forms[name["module"]] == "named"
    )


def _resolve_target_entity(
    entities: Sequence[EntityPayload],
    original_name: str,
    local_name: str,
) -> str | None:
    exact_matches = [
        entity["name"]
        for entity in entities
        if entity["name"] == original_name
        or (original_name == "default" and entity["name"] == local_name)
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if exact_matches:
        return None
    short_matches = [
        entity["name"]
        for entity in entities
        if entity["name"].rsplit("::", 1)[-1] == original_name
    ]
    return short_matches[0] if len(short_matches) == 1 else None


ResolvedSite = tuple[int, int, str, str, str]  # line, column, defining module, name, kind of use


@dataclass(frozen=True)
class UnresolvedCalls:
    """Method calls of one module that no receiver type resolves: their number, and the
    sites of those whose method name a class of the repository defines (by name)."""

    count: int
    sites: dict[str, list[tuple[int, int]]]


def _resolve_symbol_uses(
    id_to_result: Mapping[str, FileResult],
    bound_languages: Mapping[str, BoundLanguage],
    resolve_import: Optional[Callable[[str, str, str], Optional[str]]] = None,
    modules: Optional[frozenset[str]] = None,
) -> tuple[dict[str, list[ResolvedSite]], dict[str, UnresolvedCalls]]:
    """Every use site resolved to its definition: module -> sorted (line, column, module, name, kind),
    and the unresolved method calls per module (see _module_uses)."""
    index = SymbolIndex(
        id_to_result,
        resolve_import or (
            lambda language, spec, path: bound_languages[language_for_result(language).name].resolve_import(spec, path)
        ),
    )
    methods = frozenset(method for result in id_to_result.values() for method in class_methods(result))
    resolved_by_module: dict[str, list[ResolvedSite]] = {}
    unresolved_calls: dict[str, UnresolvedCalls] = {}
    for node_id, result in id_to_result.items():
        if not supports_symbols(result.language) or (modules is not None and node_id not in modules):
            continue
        found, unresolved, _members = _module_uses(index, methods, node_id, result)
        if found:
            resolved_by_module[node_id] = found
        if unresolved:
            unresolved_calls[node_id] = unresolved
    return resolved_by_module, unresolved_calls


# The kind of a use, as one letter after its line:column in the graph; none for a plain reference.
_USE_KIND_LETTERS = {"import": "i", "re-export": "r", "call": "c", "type": "t", "extends": "e"}


def _symbol_usage_edges(
    resolved_by_module: Mapping[str, list[ResolvedSite]],
    edges: list[dict],
) -> list[dict]:
    """refs edges from every file to each definition in another file that it uses.

    A file that reaches the definition only through a re-export (a package
    __init__.py, a Rust `pub use`) has no dependency on the defining module: its
    edge carries the relation "reexport", which module-level queries (impact,
    deps) do not walk, so they keep following imports.
    """
    adjacent = {
        (edge["source"].split("::")[0], edge["target"].split("::")[0])
        for edge in edges
    }
    found: list[dict] = []
    for node_id, uses in resolved_by_module.items():
        # A method (Class.method) is a use site only; its class has its own refs.
        targets = {
            (module, name) for _line, _column, module, name, _kind in uses if module != node_id and "." not in name
        }
        for module, name in sorted(targets):
            edge: dict[str, Any] = {
                "source": node_id,
                "target": f"{module}::{name}",
                "type": "REFERENCES",
            }
            if (node_id, module) not in adjacent:
                edge["relations"] = ["reexport"]
            found.append(edge)
    return found


class Trace:
    """What the resolution of one module read besides its own parse result.

    modules: other modules whose parse results it looked at; specs: the module
    names as written that it resolved; names: the provider names it looked up.
    A graph update redoes a module when one of these may have changed.
    """

    __slots__ = ("modules", "specs", "names")

    def __init__(self) -> None:
        self.modules: set[str] = set()
        self.specs: set[str] = set()
        self.names: set[str] = set()


class _TracedLanguage:
    """A bound language that records the module names it is asked to resolve."""

    def __init__(self, bound: BoundLanguage, trace: Trace) -> None:
        self._bound = bound
        self._trace = trace

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        self._trace.specs.add(import_name)
        return self._bound.resolve_import(import_name, source_path)

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        # Submodules named in `from package import name`: the answer depends on the paths of both.
        for imported_name in result.imported_names:
            self._trace.specs.add(str(imported_name.get("module", "")))
            self._trace.specs.add(str(imported_name.get("original", "")))
        return self._bound.resolve_additional_imports(result)

    def resolve_named_imports(
        self, result: FileResult, results: Mapping[str, FileResult]
    ) -> tuple[Mapping[str, frozenset[str]], int, Mapping[str, tuple[tuple[str, str, tuple[str, ...]], ...]]]:
        # The resolver is this traced one: every module name asked, also those of the facades, is recorded.
        named = getattr(self._bound, "resolve_named_imports", None)
        return named(result, results, self.resolve_import) if named is not None else ({}, 0, {})


class _TracedResults(Mapping[str, FileResult]):
    """The parse results of the graph; records which modules are read."""

    def __init__(self, results: Mapping[str, FileResult], trace: Trace) -> None:
        self._results = results
        self._trace = trace

    def __getitem__(self, key: str) -> FileResult:
        self._trace.modules.add(key)
        return self._results[key]

    def get(self, key: str, default: Any = None) -> Any:
        self._trace.modules.add(key)
        return self._results.get(key, default)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self._trace.modules.add(key)
        return key in self._results

    def __iter__(self):
        return iter(self._results)

    def __len__(self) -> int:
        return len(self._results)


class _TracedNames:
    """provider_candidates: records the names looked up."""

    def __init__(self, names: Mapping[str, set[str]], trace: Trace) -> None:
        self._names = names
        self._trace = trace

    def get(self, name: str, default: set[str]) -> set[str]:
        self._trace.names.add(name)
        return self._names.get(name, default)


@dataclass(frozen=True)
class GraphContext:
    """Everything the resolution of one module may read: the indexes, the bound languages, global name maps."""

    root: str
    path_to_id: dict[str, str]
    id_to_result: dict[str, FileResult]
    bound_languages: dict[str, BoundLanguage]
    provider_candidates: dict[str, set[str]]
    methods: frozenset[str]
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS   # what bound the languages, for spawned workers


@dataclass(frozen=True)
class ModuleFragment:
    """The facts of one module: its node, its edges, its resolved uses, and what producing them read."""

    node: dict
    edges: list[dict]
    uses: Optional[list[ResolvedSite]]  # None: uses not resolved (reused from the use file)
    unresolved: Optional[UnresolvedCalls]
    members: frozenset[str]  # method names of its unresolved calls
    modules: frozenset[str]
    specs: frozenset[str]
    names: frozenset[str]


def provider_names(result: FileResult) -> set[str]:
    """The names under which a module is a provider candidate (provider_candidates keys)."""
    names = set(result.exports_abstract) | set(result.exported_names)
    for entity in result.entities:
        if isinstance(entity, dict) and entity.get("kind") == "interface" and isinstance(entity.get("name"), str):
            names.add(entity["name"])
    return names


def class_methods(result: FileResult) -> set[str]:
    if not supports_symbols(result.language):
        return set()
    return {
        method
        for entity in result.entities
        if entity["kind"] in ("class", "enum", "interface")
        for method in entity.get("methods", [])
    }


def graph_context(
    results: list[FileResult],
    root: str,
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> Result[GraphContext, GraphBuildError]:
    index_result = _build_node_indexes(results, root)
    if isinstance(index_result, Err):
        return index_result
    path_to_id, id_to_result = index_result.value

    result_languages = {result.language for result in results}
    bound_languages = {
        language.name: language.bind(root, path_to_id, exclusions)
        for language in LANGUAGES
        if result_languages.intersection(language.result_languages)
    }

    # Map every abstract/interface name to every provider. Resolution prefers
    # the consumer's explicit import and otherwise accepts only one candidate.
    provider_candidates: dict[str, set[str]] = {}
    for nid, r in id_to_result.items():
        for ab in r.exports_abstract:
            provider_candidates.setdefault(ab, set()).add(nid)

    for nid, r in id_to_result.items():
        for ent in r.entities:
            if not isinstance(ent, dict):
                continue
            ent_kind = ent.get("kind")
            ent_name = ent.get("name")
            if ent_kind == "interface" and isinstance(ent_name, str) and ent_name:
                provider_candidates.setdefault(ent_name, set()).add(nid)

    entity_candidates: dict[str, set[str]] = {}
    for nid, r in id_to_result.items():
        for name in r.exported_names:
            entity_candidates.setdefault(name, set()).add(nid)

    all_bases: set[str] = set()
    for r in id_to_result.values():
        for base in r.implements + r.extends:
            all_bases.add(base)
    for base_name in all_bases:
        provider_candidates.setdefault(base_name, set()).update(
            entity_candidates.get(base_name, set())
        )
    methods = frozenset(method for result in id_to_result.values() for method in class_methods(result))
    return Ok(GraphContext(root, path_to_id, id_to_result, bound_languages, provider_candidates, methods, exclusions))


def symbol_index(context: GraphContext) -> SymbolIndex:
    bound_languages = context.bound_languages
    return SymbolIndex(
        context.id_to_result,
        lambda language, spec, path: bound_languages[language_for_result(language).name].resolve_import(spec, path),
        trace=True,
    )


def module_node(node_id: str, r: FileResult, root: str, purpose: str) -> dict:
    """The node of a module: its parse result and the purpose resolution settled on."""
    node: dict = {
        "id": node_id,
        "language": r.language,
        "path": os.path.relpath(r.path, root).replace(os.sep, "/"),   # repository-relative, POSIX on every system
        "repo": r.repo,
        "invariant": {
            "naming": r.naming_convention,
            "package": r.repo,
        },
        "purpose": purpose,
    }
    if r.entities:
        node["entities"] = r.entities
    return node


# An import edge records at most this many of the names followed to its target, and their number.
_EDGE_NAME_LIMIT = 3
# Ways a name arrives that an import of the module itself keeps (the others belong to item edges).
_PLAIN_IMPORT_WAYS = frozenset({"use", "macro"})


def _edge_names_sexp(names: Any) -> str:
    """` (:use A B) (:glob "super" C :of 5)`: the names an import edge followed, one list per way they arrive."""
    parts = []
    for way, through, shown, count in names or ():
        head = f'(:{way} "{through}"' if through else f"(:{way}"
        more = f" :of {count}" if count > len(shown) else ""
        parts.append(f" {head} {' '.join(shown)}{more})")
    return "".join(parts)


def _module_facts(
    node_id: str,
    r: FileResult,
    root: str,
    bound_language: Any,
    id_to_result: Mapping[str, FileResult],
    provider_candidates: Any,
) -> tuple[dict, list[dict]]:
    """The node of one module and the edges that leave it (imports, calls-api, data-flow, references)."""
    edges: list[dict] = []
    seen_edges: set[tuple] = set()  # every key starts with this module: per-module dedup is the global one
    rel_path = os.path.relpath(r.path, root)

    # Gap 3 fix: enrich purpose with entity-name heuristics
    purpose = r.purpose
    if r.entities and purpose in (
        "general module",
        "NestJS wiring module",
        "NestJS injectable",
    ):
        enhanced = infer_purpose(
            rel_path, "", r.language, [entity for entity in r.entities if entity["kind"] != "variable"]
        )
        if enhanced not in (
            "general module",
            "NestJS wiring module",
            "NestJS injectable",
        ):
            purpose = enhanced

    node = module_node(node_id, r, root, purpose)

    # Resolve all module dependencies before emission so relationship
    # metadata is merged onto one deterministic import edge.
    resolved_imports: dict[str, set[str]] = {}
    additional_targets = set(bound_language.resolve_additional_imports(r))
    for target_id in sorted(additional_targets):
        if target_id != node_id:
            resolved_imports.setdefault(target_id, set())
    for imp in r.imports:
        target_id = bound_language.resolve_import(imp, r.path)
        if target_id and target_id != node_id:
            resolved_imports.setdefault(target_id, set())
    for relation in r.relations:
        target_id = bound_language.resolve_import(
            relation["target"],
            r.path,
        )
        if target_id and target_id != node_id:
            resolved_imports.setdefault(target_id, set()).add(relation["relation"])
    # Rust: the files that define names imported through a module facade (`pub use child::*`); the facade edge
    # stays. A file the module also imports directly keeps its plain edge.
    followed_names: dict[str, list[list[Any]]] = {}
    if hasattr(bound_language, "resolve_named_imports"):
        named, not_followed, routes = bound_language.resolve_named_imports(r, id_to_result)
        plain = set(resolved_imports)
        for target_id, relations in sorted(named.items()):
            if target_id != node_id and target_id not in plain:
                resolved_imports.setdefault(target_id, set()).update(relations)
        if not_followed:
            node["unfollowed_names"] = not_followed   # counted in the build log, not written to the graph
        # The names the resolver followed to each target, by the way they arrive: what `impact` shows as the
        # reason of the edge. A file imported as a module keeps the names its imports name in it and its macros.
        for target_id, target_routes in routes.items():
            kept = [route for route in target_routes if target_id not in plain or route[0] in _PLAIN_IMPORT_WAYS]
            if kept and target_id != node_id:
                followed_names[target_id] = [
                    [way, through, list(names[:_EDGE_NAME_LIMIT]), len(names)] for way, through, names in kept]

    local_entities = {entity["name"] for entity in r.entities}
    for relation in r.relations:
        source_entity = relation.get("source_entity", "")
        target_entity = relation.get("target_entity", "")
        if not source_entity or not target_entity:
            continue
        target_id = bound_language.resolve_import(
            relation["target"],
            r.path,
        )
        if (
            target_id != node_id
            or source_entity not in local_entities
            or target_entity not in local_entities
        ):
            continue
        source_ref = f"{node_id}::{source_entity}"
        target_ref = f"{node_id}::{target_entity}"
        edge_key = (source_ref, target_ref, "REFERENCES")
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            {
                "source": source_ref,
                "target": target_ref,
                "type": "REFERENCES",
                "relations": [relation["relation"]],
            }
        )

    # -- module-level IMPORTS edges --
    for target_id, relations in sorted(resolved_imports.items()):
        edge_key = (node_id, target_id, "IMPORTS")
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        target_r = id_to_result.get(target_id)
        via_base = bool(target_r and target_r.exports_abstract)
        edge: dict[str, Any] = {
            "source": node_id,
            "target": target_id,
            "type": "IMPORTS",
            "via_base": via_base,
            "relations": sorted(relations),
        }
        if (
            target_r is not None
            and not relations
            and target_id not in additional_targets
            and _imports_types_only(r, target_id, target_r, bound_language)
        ):
            edge["type_only"] = True
        if target_id in followed_names:
            edge["names"] = followed_names[target_id]
        edges.append(edge)

    # -- CALLS_API edges (implements/extends + entity :bases) --
    # Source 1: FileResult.implements + extends (from heritage clauses)
    calls_api_targets: set[str] = set()
    for iface in r.implements + r.extends:
        provider_id = _resolve_provider(
            iface,
            r,
            bound_language,
            provider_candidates,
            id_to_result,
        )
        if provider_id and provider_id != node_id:
            calls_api_targets.add(provider_id)

    # Source 2: Entity :bases cross-reference (catches bases not in
    # heritage clauses, and bases defined in other modules)
    for ent in r.entities:
        for base_name in ent.get("bases", []):
            provider_id = _resolve_provider(
                base_name,
                r,
                bound_language,
                provider_candidates,
                id_to_result,
            )
            if provider_id and provider_id != node_id:
                calls_api_targets.add(provider_id)

    for provider_id in calls_api_targets:
        edge_key = (node_id, provider_id, "CALLS_API")
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edges.append(
                {
                    "source": node_id,
                    "target": provider_id,
                    "type": "CALLS_API",
                    "via_interface": True,
                }
            )

    # -- DATA_FLOWS_TO edges --
    for imp in r.imports:
        target_id = bound_language.resolve_import(imp, r.path)
        if target_id and target_id != node_id:
            target_r = id_to_result.get(target_id)
            # The mark states what the target's syntax tree holds; without evidence there is no fact.
            if target_r and target_r.has_validation and target_r.validation_evidence:
                edge_key = (node_id, target_id, "DATA_FLOWS_TO")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        {
                            "source": node_id,
                            "target": target_id,
                            "type": "DATA_FLOWS_TO",
                            "validation_evidence": target_r.validation_evidence,
                        }
                    )

    # -- entity-level REFERENCES edges --
    # For each imported name, link source_entity -> target_entity
    for imp_name in r.imported_names:
        target_id = bound_language.resolve_import(
            imp_name["module"],
            r.path,
        )
        if not target_id or target_id == node_id:
            continue
        target_r = id_to_result.get(target_id)
        if not target_r:
            continue

        orig = imp_name["original"]
        local = imp_name["local"]

        # Find matching entity in target
        target_entity = _resolve_target_entity(
            target_r.entities,
            orig,
            local,
        )
        if not target_entity:
            # Still useful: the name is exported even if not a class/function entity
            if orig in target_r.exported_names:
                target_entity = orig
            elif orig == "default" and target_r.exported_names:
                target_entity = target_r.exported_names[0]
            else:
                continue

        # Find which local entities reference this imported name
        referencing_entities = _find_local_references(r.entities, local)

        if referencing_entities:
            for ref_ent in referencing_entities:
                edge_key = (
                    f"{node_id}::{ref_ent}",
                    f"{target_id}::{target_entity}",
                    "REFERENCES",
                )
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        {
                            "source": f"{node_id}::{ref_ent}",
                            "target": f"{target_id}::{target_entity}",
                            "type": "REFERENCES",
                        }
                    )
        else:
            # Module-level reference (not inside a specific entity)
            edge_key = (node_id, f"{target_id}::{target_entity}", "REFERENCES")
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "source": node_id,
                        "target": f"{target_id}::{target_entity}",
                        "type": "REFERENCES",
                    }
                )
    return node, edges


def _module_uses(
    index: SymbolIndex,
    methods: frozenset[str],
    node_id: str,
    result: FileResult,
) -> tuple[Optional[list[ResolvedSite]], Optional[UnresolvedCalls], frozenset[str]]:
    """The uses of one module resolved to their definitions, its unresolved method calls and their names.

    The method calls whose receiver type is not in sight or does not define the
    method stay unresolved, never guessed. Their sites are kept only for a
    method name that some class of the repository defines (methods), the calls
    that `usages Class.method` lists as unresolved.
    """
    found: set[ResolvedSite] = set()
    count = 0
    members: set[str] = set()
    sites: dict[str, list[tuple[int, int]]] = {}
    for use in result.uses:
        resolved = index.resolve(node_id, use)
        if resolved is not None:
            found.add((resolved.line, resolved.column, resolved.module, resolved.name, resolved.kind))
        elif use.get("member"):
            count += 1
            members.add(use["member"])
            if use["member"] in methods:
                line, column = use["at"][-1]
                sites.setdefault(use["member"], []).append((line, column))
    return (sorted(found) if found else None), (UnresolvedCalls(count, sites) if count else None), frozenset(members)


def module_fragment(context: GraphContext, index: Optional[SymbolIndex], node_id: str) -> ModuleFragment:
    """The facts of one module and what they were read from. index None: uses are not resolved."""
    result = context.id_to_result[node_id]
    trace = Trace()
    language = _TracedLanguage(context.bound_languages[language_for_result(result.language).name], trace)
    node, edges = _module_facts(
        node_id,
        result,
        context.root,
        language,
        _TracedResults(context.id_to_result, trace),
        _TracedNames(context.provider_candidates, trace),
    )
    uses: Optional[list[ResolvedSite]] = None
    unresolved: Optional[UnresolvedCalls] = None
    members: frozenset[str] = frozenset()
    if index is not None and supports_symbols(result.language):
        index.begin()
        uses, unresolved, members = _module_uses(index, context.methods, node_id, result)
        for kind, value in index.end():
            (trace.modules if kind == "m" else trace.specs).add(value)
    trace.modules.discard(node_id)
    return ModuleFragment(
        node, edges, uses, unresolved, members,
        frozenset(trace.modules), frozenset(trace.specs), frozenset(trace.names),
    )


def _fragment_worker(results: list[FileResult], root: str, exclusions: DiscoveryExclusions, resolve_uses: bool) -> None:
    """Initializer of a spawned worker: it builds the same context as the parent from the parse results."""
    global _FORK_CONTEXT
    built = graph_context(results, root, exclusions)
    assert isinstance(built, Ok), "the parent built this context without a collision"
    _FORK_CONTEXT = (built.value, resolve_uses)


def _fragment_chunk(node_ids: list[str]) -> list[ModuleFragment]:
    """Worker side of a parallel resolution: the context comes from the forked parent, or from
    _fragment_worker in a spawned process."""
    context, resolve_uses = _FORK_CONTEXT
    index = symbol_index(context) if resolve_uses else None
    return [module_fragment(context, index, node_id) for node_id in node_ids]


_FORK_CONTEXT: Any = None
PARALLEL_MIN_MODULES = 400  # below this the workers cost more than they save
_CHUNK_MODULES = 64


def module_fragments(
    context: GraphContext,
    node_ids: Sequence[str],
    resolve_uses: bool,
    jobs: int = 1,
) -> dict[str, ModuleFragment]:
    """The fragments of node_ids, in their order; with jobs > 1, in worker processes (forked, or spawned
    where fork does not exist: each spawned worker builds the context again from the parse results).

    Each answer depends only on the context, never on which other modules the
    same process resolved before (the symbol index memoises an answer only when
    no cycle cut shortened it), so the result is the same either way.
    """
    global _FORK_CONTEXT
    ordered = list(node_ids)
    if jobs <= 1 or len(ordered) < PARALLEL_MIN_MODULES:
        index = symbol_index(context) if resolve_uses else None
        return {node_id: module_fragment(context, index, node_id) for node_id in ordered}
    chunks = [ordered[start : start + _CHUNK_MODULES] for start in range(0, len(ordered), _CHUNK_MODULES)]
    method = portable.start_method(prefer_fork=True)
    _FORK_CONTEXT = (context, resolve_uses)
    spawned = {} if method == "fork" else {
        "initializer": _fragment_worker,
        "initargs": (list(context.id_to_result.values()), context.root, context.exclusions, resolve_uses),
    }
    try:
        with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context(method), **spawned) as pool:
            parts = list(pool.map(_fragment_chunk, chunks))
    except (OSError, BrokenProcessPool) as error:
        print(f"[code-graph] WARNING: parallel resolution failed ({error}); resolving in one process", file=sys.stderr)
        return module_fragments(context, ordered, resolve_uses, 1)
    finally:
        _FORK_CONTEXT = None
    return {node_id: fragment for chunk, part in zip(chunks, parts) for node_id, fragment in zip(chunk, part)}


def assemble_graph(
    context: GraphContext,
    results: list[FileResult],
    fragments: Mapping[str, ModuleFragment],
    reused: Optional["ReusedUses"] = None,
) -> GraphBuildOutput:
    """The graph from the fragment of every module (in context order) and the facts that need all of them."""
    id_to_result = context.id_to_result
    nodes: list[dict] = []
    edges: list[dict] = []
    for node_id in id_to_result:
        fragment = fragments[node_id]
        nodes.append(dict(fragment.node))
        edges.extend(fragment.edges)
    unfollowed = sum(node.pop("unfollowed_names", 0) for node in nodes)
    if unfollowed:
        print(f"[code-graph] Rust: {unfollowed} imported name(s) not followed to a definition (made by a macro, behind "
              "#[path] or cfg); their importers keep the edge to the module facade", file=sys.stderr)

    referenced = {
        (edge["source"].split("::")[0], edge["target"])
        for edge in edges
        if edge["type"] == "REFERENCES"
    }
    if reused is None:
        resolved_uses = {node_id: fragments[node_id].uses for node_id in id_to_result if fragments[node_id].uses}
        unresolved_calls = {
            node_id: fragments[node_id].unresolved for node_id in id_to_result if fragments[node_id].unresolved
        }
    else:
        _sorted_nodes, path_to_sid = _path_ids(nodes)
        sid_to_id = {sid: node_id for node_id, sid in path_to_sid.items()}
        resolved_uses = {
            sid_to_id[source]: [(0, 0, sid_to_id.get(module, module), name, "") for module, name in pairs]
            for source, pairs in reused.targets.items()
            if source in sid_to_id
        }
        unresolved_calls = {}
    for edge in _symbol_usage_edges(resolved_uses, edges):
        if (edge["source"], edge["target"]) not in referenced:
            edges.append(edge)
    for node in nodes:
        if node["id"] in resolved_uses and reused is None:
            node["uses"] = resolved_uses[node["id"]]
        if node["id"] in unresolved_calls:
            node["unresolved_method_calls"] = unresolved_calls[node["id"]]

    seen_edges = {
        (edge["source"], edge["target"], edge["type"]) for edge in edges
    }
    edges.extend(_registry_edges(context, edges))
    seen_edges.update((edge["source"], edge["target"], edge["type"]) for edge in edges)
    # -- Gap 2: BOUNDARY violation detection --
    # -- HTTP_CALLS edges (cross-service HTTP client detection) --
    # Detect modules with entities named *Client, *ClientService, *ClientApi,
    # *External, *Remote and infer the target service from the class name.
    _HTTP_CLIENT_SUFFIXES = (
        "Client",
        "ClientService",
        "ClientApi",
        "External",
        "Remote",
    )
    _REPO_NAMES = {r.repo for r in results}
    for node_id, r in id_to_result.items():
        for ent in r.entities:
            ent_name = ent.get("name", "")
            if not any(ent_name.endswith(sfx) for sfx in _HTTP_CLIENT_SUFFIXES):
                continue
            # Extract target service name from class name:
            # AgentExternalClient -> agent, McpHubClientService -> mcp-hub
            # LLMClientRemote -> llm
            stem = ent_name
            for sfx in sorted(_HTTP_CLIENT_SUFFIXES, key=len, reverse=True):
                if stem.endswith(sfx):
                    stem = stem[: -len(sfx)]
                    break
            if not stem:
                continue
            # Convert PascalCase to kebab-case for repo matching
            kebab = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", stem).lower()
            # Try to match against known repo names
            target_repo = None
            for repo in _REPO_NAMES:
                if repo == r.repo:
                    continue  # skip self
                repo_lower = repo.lower()
                if (
                    kebab == repo_lower
                    or kebab.startswith(repo_lower)
                    or repo_lower.startswith(kebab)
                ):
                    target_repo = repo
                    break
            if target_repo:
                edge_key = (node_id, target_repo, "HTTP_CALLS")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        {
                            "source": node_id,
                            "target": target_repo,
                            "type": "HTTP_CALLS",
                            "transport": "http",
                            "client_entity": ent_name,
                        }
                    )

    # Domain modules should not import directly from infrastructure without
    # going through an interface (via_base). Flag violations.
    for edge in edges:
        if edge["type"] != "IMPORTS":
            continue
        src = edge["source"]
        tgt = edge["target"]
        src_purpose = id_to_result.get(src, None)
        tgt_purpose = id_to_result.get(tgt, None)
        if not src_purpose or not tgt_purpose:
            continue
        src_p = (src_purpose.purpose or "").lower()
        tgt_p = (tgt_purpose.purpose or "").lower()
        # Domain importing infrastructure without going through base class
        if "domain" in src_p and "infrastructure" in tgt_p and not edge.get("via_base"):
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": "VIOLATION",
                    "rule": "boundary",
                    "detail": "domain imports infrastructure without abstract base",
                }
            )
        # UI/component importing infrastructure directly
        if (
            "ui component" in src_p
            and "infrastructure" in tgt_p
            and not edge.get("via_base")
        ):
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": "VIOLATION",
                    "rule": "boundary",
                    "detail": "UI imports infrastructure without abstract base",
                }
            )

    return GraphBuildOutput(nodes, edges, dict(fragments) if reused is None else {})


def build_graph(
    results: list[FileResult],
    root: str,
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
    reused: Optional["ReusedUses"] = None,
    jobs: int = 1,
) -> Result[GraphBuildOutput, GraphBuildError]:
    """The graph of results. With reused (a use file built from the same inputs), the refs edges of
    use sites come from it and no use is resolved; nodes then carry no use positions.
    jobs > 1 resolves the modules in that many forked processes; the graph is the same."""
    context_result = graph_context(results, root, exclusions)
    if isinstance(context_result, Err):
        return context_result
    context = context_result.value
    fragments = module_fragments(context, list(context.id_to_result), reused is None, jobs)
    return Ok(assemble_graph(context, results, fragments, reused))


FRAGMENTS_SCHEMA_VERSION = 1


def fragment_record(node_id: str, fragment: ModuleFragment) -> list:
    unresolved = fragment.unresolved
    return [
        node_id,
        fragment.node["path"],
        fragment.node["purpose"],
        fragment.edges,
        fragment.uses,
        None if unresolved is None else [unresolved.count, unresolved.sites],
        sorted(fragment.members),
        sorted(fragment.modules),
        sorted(fragment.specs),
        sorted(fragment.names),
    ]


def write_fragments(
    path: str,
    key: str,
    fragments: Mapping[str, ModuleFragment],
    root: str,
    exclusions: DiscoveryExclusions,
    unchanged: Optional[Mapping[str, str]] = None,
) -> None:
    """The fragment store: one header line (its key and the exclusions of the build), then one JSON line
    per module, for a later partial update."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = json.dumps(
        {
            "schema_version": FRAGMENTS_SCHEMA_VERSION,
            "key": key,
            "skip_dirs": sorted(exclusions.directory_names),
            "ignored": sorted(os.path.relpath(item, root) for item in exclusions.paths),
        },
        separators=(",", ":"),
    )
    kept = unchanged or {}  # node ID -> its line in the store read before: same facts, same text
    lines = [header] + [
        kept[node_id] if node_id in kept else json.dumps(fragment_record(node_id, fragment), separators=(",", ":"))
        for node_id, fragment in fragments.items()
    ]
    _atomic_write_text(path, "\n".join(lines) + "\n")


def fragments_key(path: str) -> Optional[str]:
    """The key (symbol_index_key of its inputs) of a fragment store; None when missing or of another schema."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            header = json.loads(handle.readline() or "null")
    except (OSError, ValueError):
        return None
    if not isinstance(header, dict) or header.get("schema_version") != FRAGMENTS_SCHEMA_VERSION:
        return None
    key = header.get("key")
    return key if isinstance(key, str) else None


@dataclass(frozen=True)
class StoredFragment:
    line: str  # the store line it was read from
    path: str
    purpose: str
    edges: list[dict]
    uses: Optional[list[ResolvedSite]]
    unresolved: Optional[UnresolvedCalls]
    members: frozenset[str]
    modules: frozenset[str]
    specs: frozenset[str]
    names: frozenset[str]

    def fragment(self, node_id: str, result: FileResult, root: str) -> ModuleFragment:
        return ModuleFragment(
            module_node(node_id, result, root, self.purpose),
            self.edges, self.uses, self.unresolved, self.members, self.modules, self.specs, self.names,
        )


def read_fragments(path: str, root: str) -> Result[tuple[str, DiscoveryExclusions, dict[str, StoredFragment]], str]:
    """The key, the exclusions and the fragments of a store."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        return Err(f"cannot read {path}: {error}")
    key = fragments_key(path)
    if key is None:
        return Err("no fragment store of this schema")
    stored: dict[str, StoredFragment] = {}
    try:
        header = json.loads(lines[0])
        exclusions = DiscoveryExclusions(
            directory_names=frozenset(header["skip_dirs"]),
            paths=frozenset(os.path.normcase(os.path.join(root, item)) for item in header["ignored"]),
        )
        for line in lines[1:]:
            node_id, rel, purpose, edges, uses, unresolved, members, modules, specs, names = json.loads(line)
            stored[node_id] = StoredFragment(
                line,
                rel,
                purpose,
                edges,
                None if uses is None else [tuple(site) for site in uses],
                None if unresolved is None else UnresolvedCalls(
                    unresolved[0], {member: [tuple(site) for site in found] for member, found in unresolved[1].items()}
                ),
                frozenset(members),
                frozenset(modules),
                frozenset(specs),
                frozenset(names),
            )
    except (ValueError, TypeError, IndexError, AttributeError, KeyError) as error:
        return Err(f"invalid fragment store: {error}")
    return Ok((key, exclusions, stored))


def _registry_edges(context: GraphContext, edges: list[dict]) -> list[dict]:
    """The registry-wires edges, the calls dispatched through a registry and the pipeline data flows.

    They read configuration files and other modules' entities, so they are facts of the whole
    graph, made after the per-module fragments. An existing data-flow between two consecutive
    pipeline steps is replaced in edges by a copy with the pipeline flag.
    """
    id_to_result = context.id_to_result
    wires_by_loader = _registry_wires(id_to_result, context.bound_languages, context.root)
    added: list[dict] = []
    pipeline_pairs: set[tuple[str, str]] = set()
    for node_id, result in id_to_result.items():
        for wire in wires_by_loader.get(node_id, ()):
            added.append({"source": node_id, "target": wire.target, "type": "REGISTRY_WIRES", "config": wire.config})
        if not result.registry_calls:
            continue
        bound_language = context.bound_languages[language_for_result(result.language).name]
        calls, folds = _registry_call_edges(node_id, result, bound_language, wires_by_loader, id_to_result)
        pipeline_pairs.update(folds)
        for (source_ref, target_ref), lines in sorted(calls.items()):
            added.append(
                {"source": source_ref, "target": target_ref, "type": "CALLS", "from_lines": lines, "registry": True}
            )
    data_flows = {
        (edge["source"], edge["target"]): position
        for position, edge in enumerate(edges)
        if edge["type"] == "DATA_FLOWS_TO"
    }
    for source_id, target_id in sorted(pipeline_pairs):
        position = data_flows.get((source_id, target_id))
        if position is not None:
            # a copy: the fragment's own edge stays as the module produced it
            edges[position] = {**edges[position], "pipeline": True}
            continue
        added.append({"source": source_id, "target": target_id, "type": "DATA_FLOWS_TO", "pipeline": True})
    return added


@dataclass(frozen=True)
class RegistryWire:
    """A module that a loader loads by a name taken from a configuration file."""

    target: str
    config: str
    order: int


def _registry_wires(
    id_to_result: Mapping[str, FileResult],
    bound_languages: Mapping[str, BoundLanguage],
    root: str,
) -> dict[str, tuple[RegistryWire, ...]]:
    """Fill each load pattern with the names of the configuration files beside the loader.

    A name counts only when the filled module name resolves to a module of the
    graph; the order is the position of the name in its configuration file.
    """
    config_by_directory: dict[str, tuple[ConfigValues, ...]] = {}
    wires: dict[str, tuple[RegistryWire, ...]] = {}
    for node_id, result in id_to_result.items():
        if not result.module_loads:
            continue
        bound_language = bound_languages[language_for_result(result.language).name]
        directory = os.path.dirname(result.path)
        if directory not in config_by_directory:
            config_by_directory[directory] = read_config_values(directory)
        found: dict[str, RegistryWire] = {}
        for load in result.module_loads:
            for config in config_by_directory[directory]:
                config_path = os.path.relpath(config.path, root).replace(os.sep, "/")
                for order, value in enumerate(config.values):
                    target_id = bound_language.resolve_import(load["pattern"].replace("{}", value, 1), result.path)
                    if target_id and target_id != node_id and target_id not in found:
                        found[target_id] = RegistryWire(target_id, config_path, order)
        if found:
            wires[node_id] = tuple(found.values())
    return wires


def _defines(result: FileResult, name: str) -> bool:
    """Whether a module defines a function of that name or a class with that method."""
    return any(
        (entity.get("kind") == "function" and entity.get("name") == name)
        or (entity.get("kind") == "class" and name in entity.get("methods", []))
        for entity in result.entities
    )


def _registry_call_edges(
    node_id: str,
    result: FileResult,
    bound_language: BoundLanguage,
    wires_by_loader: Mapping[str, tuple[RegistryWire, ...]],
    id_to_result: Mapping[str, FileResult],
) -> tuple[dict[tuple[str, str], list[int]], set[tuple[str, str]]]:
    """Calls into the modules a registry wires, and the pipeline pairs of folding calls.

    Returns the call lines by (source entity, target entity) and the ordered
    pairs of consecutive modules of one configuration list.
    """
    calls: dict[tuple[str, str], list[int]] = {}
    folds: set[tuple[str, str]] = set()
    for call in result.registry_calls:
        registry_id = next(
            (target for target in (bound_language.resolve_import(name, result.path) for name in call["modules"]) if target),
            None,
        )
        wired = [
            wire
            for wire in wires_by_loader.get(registry_id or "", ())
            if wire.target != node_id and _defines(id_to_result[wire.target], call["method"])
        ]
        for wire in wired:
            key = (f"{node_id}::{call['source_entity']}", f"{wire.target}::{call['method']}")
            calls[key] = sorted(set(calls.get(key, [])) | {call["line"]})
        if call["fold"]:
            for config in sorted({wire.config for wire in wired}):
                steps = sorted((wire for wire in wired if wire.config == config), key=lambda wire: wire.order)
                folds.update((first.target, second.target) for first, second in zip(steps, steps[1:]))
    return calls, folds


def _find_local_references(
    entities: Sequence[EntityPayload],
    name: str,
) -> list[str]:
    """
    Heuristic: check which local entities might reference the imported name.
    If a class extends/implements the name, or has it in bases, it references it.
    """
    refs = []
    for ent in entities:
        # Class extends/implements
        if name in ent.get("bases", []):
            refs.append(ent["name"])
        # Decorator usage
        if name in ent.get("decorators", []):
            refs.append(ent["name"])
    return refs


# ---------------------------------------------------------------------------
# LSP Client -- JSON-RPC over stdio
# ---------------------------------------------------------------------------


class LspClient:
    """
    Minimal Language Server Protocol client using JSON-RPC over stdio.

    Starts an LSP server as a subprocess, communicates via Content-Length
    framed JSON-RPC messages, and dispatches responses to waiting callers
    via a background reader thread.

    Supports:
    - initialize / initialized / shutdown / exit lifecycle
    - textDocument/didOpen, textDocument/didClose
    - textDocument/prepareCallHierarchy
    - callHierarchy/outgoingCalls, callHierarchy/incomingCalls
    - textDocument/references
    """

    def __init__(self, cmd: list[str], cwd: str):
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            **portable.hidden_process_options(),
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        self._stderr = self._proc.stderr
        self._msg_id = 0
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._errors: dict[int, Any] = {}
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

    # -- Transport layer --

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _send(self, msg: dict) -> None:
        """Send a JSON-RPC message with Content-Length header."""
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._stdin.write(header + body)
            self._stdin.flush()
        except (BrokenPipeError, OSError):
            self._alive = False

    def _read_loop(self) -> None:
        """Background thread: read Content-Length framed messages from stdout."""
        # Use os.read() for non-blocking reads. Python's BufferedReader.read(n)
        # blocks until exactly n bytes are available, which deadlocks on pipes.
        fd = self._stdout.fileno()
        buf = b""
        while self._alive:
            try:
                # Read until we have the full header separator
                while b"\r\n\r\n" not in buf:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        self._alive = False
                        self._wake_all()
                        return
                    buf += chunk

                header_end = buf.index(b"\r\n\r\n")
                headers = buf[:header_end].decode("ascii")
                buf = buf[header_end + 4 :]

                content_length = 0
                for line in headers.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())

                # Read body
                while len(buf) < content_length:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        self._alive = False
                        self._wake_all()
                        return
                    buf += chunk

                body = buf[:content_length]
                buf = buf[content_length:]

                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                # Dispatch response
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._events:
                    if "result" in msg:
                        self._results[msg_id] = msg["result"]
                    elif "error" in msg:
                        self._errors[msg_id] = msg["error"]
                    else:
                        # Null result is valid (e.g., empty references)
                        self._results[msg_id] = None
                    self._events[msg_id].set()
                # Server-initiated notifications (diagnostics, progress) are ignored.

            except Exception:
                if self._alive:
                    continue
                return

    def _drain_stderr(self) -> None:
        """Drain stderr to prevent buffer deadlock."""
        fd = self._stderr.fileno()
        try:
            while self._alive:
                data = os.read(fd, 65536)
                if not data:
                    return
        except Exception:
            pass

    def _wake_all(self) -> None:
        """Wake all pending requests (server died or shutting down)."""
        for event in self._events.values():
            event.set()

    # -- Request / Notification helpers --

    def _request(
        self, method: str, params: Any, timeout: float = LSP_REQUEST_TIMEOUT
    ) -> Any:
        """Send a request and wait for the response (blocking)."""
        if not self._alive:
            return None
        msg_id = self._next_id()
        event = threading.Event()
        self._events[msg_id] = event
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        if event.wait(timeout):
            self._events.pop(msg_id, None)
            if msg_id in self._errors:
                self._errors.pop(msg_id)
                return None
            return self._results.pop(msg_id, None)
        # Timeout
        self._events.pop(msg_id, None)
        return None

    def _request_fire(self, method: str, params: Any) -> int:
        """Send a request without waiting. Returns the message ID for later collection."""
        if not self._alive:
            return -1
        msg_id = self._next_id()
        event = threading.Event()
        self._events[msg_id] = event
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return msg_id

    def _request_collect(
        self, msg_id: int, timeout: float = LSP_REQUEST_TIMEOUT
    ) -> Any:
        """Wait for a previously fired request's response."""
        if msg_id < 0 or msg_id not in self._events:
            return None
        event = self._events[msg_id]
        if event.wait(timeout):
            self._events.pop(msg_id, None)
            if msg_id in self._errors:
                self._errors.pop(msg_id)
                return None
            return self._results.pop(msg_id, None)
        self._events.pop(msg_id, None)
        return None

    def _notify(self, method: str, params: Any) -> None:
        """Send a notification (no response expected)."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- LSP lifecycle --

    def initialize(self, root_uri: str) -> bool:
        """Send initialize request. Returns True on success."""
        result = self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "callHierarchy": {
                            "dynamicRegistration": False,
                        },
                        "references": {
                            "dynamicRegistration": False,
                        },
                        "synchronization": {
                            "didOpen": True,
                            "didClose": True,
                        },
                    },
                },
                "workspaceFolders": [
                    {"uri": root_uri, "name": os.path.basename(uri_to_path(root_uri))}
                ],
            },
            timeout=LSP_INIT_TIMEOUT,
        )
        if result is not None:
            self._notify("initialized", {})
            return True
        return False

    def shutdown(self) -> None:
        """Gracefully shut down the LSP server."""
        if self._alive:
            self._request("shutdown", None, timeout=5.0)
            self._notify("exit", None)
        self._alive = False
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return self._alive and self._proc.poll() is None

    # -- Document operations --

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify server that a document was opened."""
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                },
            },
        )

    def did_close(self, uri: str) -> None:
        """Notify server that a document was closed."""
        self._notify(
            "textDocument/didClose",
            {
                "textDocument": {"uri": uri},
            },
        )

    # -- Query operations --

    def document_symbols(
        self,
        uri: str,
        timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """
        Get all symbols in a document. Returns a tree of DocumentSymbol objects.

        Each symbol has: name, kind (int), range, selectionRange, children.
        SymbolKind: 5=Class, 6=Method, 11=Interface, 12=Function, 13=Variable,
                    10=Enum, 26=TypeParameter, 15=Namespace.
        """
        result = self._request(
            "textDocument/documentSymbol",
            {
                "textDocument": {"uri": uri},
            },
            timeout=timeout,
        )
        return result if isinstance(result, list) else []

    def references(
        self,
        uri: str,
        line: int,
        character: int,
        timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """
        Find all references to the symbol at position.
        Returns list of Location {uri, range}.
        """
        result = self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": False},
            },
            timeout=timeout,
        )
        return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Project discovery for LSP
# ---------------------------------------------------------------------------


def discover_projects(
    root: str,
    file_results: list[FileResult],
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> list[ProjectGroup]:
    """Group source files by the closest language-owned project config."""
    configs_by_language: dict[str, list[str]] = {
        language.name: [] for language in LANGUAGES
    }
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = [
            name
            for name in directory_names
            if not is_excluded_directory(exclusions, directory_path, name)
        ]
        for file_name in file_names:
            config_path = os.path.join(directory_path, file_name)
            if is_excluded_path(exclusions, config_path):
                continue
            for language in LANGUAGES:
                if language.accepts_project_config(file_name):
                    configs_by_language[language.name].append(config_path)

    projects: list[ProjectGroup] = []
    assigned_files: set[str] = set()
    for language in LANGUAGES:
        configs = sorted(
            configs_by_language[language.name],
            key=lambda path: -path.count(os.sep),
        )
        for config_path in configs:
            project_root = os.path.dirname(config_path)
            project = ProjectGroup(
                root=project_root,
                language=language.name,
                config_file=config_path,
            )
            for result in file_results:
                if (
                    result.language in language.lsp_result_languages
                    and result.path not in assigned_files
                    and result.path.startswith(project_root + os.sep)
                ):
                    project.files.append(result)
                    assigned_files.add(result.path)
            if project.files:
                projects.append(project)

    return projects


def _language_id(language: str, path: str) -> str:
    """Map a parsed result language to its LSP languageId."""
    return language_for_result(language).lsp_language_id(path)


# ---------------------------------------------------------------------------
# LSP analysis -- call hierarchy + references
# ---------------------------------------------------------------------------


# LSP SymbolKind constants
_SK_CLASS = 5
_SK_METHOD = 6
_SK_PROPERTY = 7
_SK_ENUM = 10
_SK_INTERFACE = 11
_SK_FUNCTION = 12
_SK_VARIABLE = 13
_SK_CONSTANT = 14
_SK_ENUM_MEMBER = 22
_SK_TYPE_PARAMETER = 26

_SK_TO_KIND = {
    _SK_CLASS: "class",
    _SK_METHOD: "method",
    _SK_INTERFACE: "interface",
    _SK_FUNCTION: "function",
    _SK_VARIABLE: "function",  # const exports
    _SK_CONSTANT: "function",
    _SK_ENUM: "enum",
    _SK_TYPE_PARAMETER: "type",
    _SK_PROPERTY: "function",
}


def _sym_line(sym: dict) -> int:
    """Extract 1-based line from a DocumentSymbol or SymbolInformation."""
    # DocumentSymbol format: { selectionRange: { start: { line } } }
    sel = sym.get("selectionRange")
    if sel:
        return sel.get("start", {}).get("line", 0) + 1
    # SymbolInformation format: { location: { range: { start: { line } } } }
    loc = sym.get("location")
    if loc:
        return loc.get("range", {}).get("start", {}).get("line", 0) + 1
    # Fallback to range
    rng = sym.get("range")
    if rng:
        return rng.get("start", {}).get("line", 0) + 1
    return 0


def _lsp_symbols_to_entities(
    symbols: list[dict],
) -> tuple[list[EntityPayload], list[str]]:
    """
    Convert LSP symbol results into Entity dicts and exported names.

    Handles both DocumentSymbol (nested, has children) and SymbolInformation
    (flat, has location) formats. Filters to top-level definitions only:
    classes, interfaces, functions, types, enums, and exported constants.
    """
    entities: list[EntityPayload] = []
    exported_names: list[str] = []
    seen_names: set[str] = set()

    # Detect format: DocumentSymbol has "children" or "selectionRange",
    # SymbolInformation has "location"
    is_doc_symbol = any("selectionRange" in s or "children" in s for s in symbols[:5])

    if is_doc_symbol:
        # DocumentSymbol: only process top-level symbols
        for sym in symbols:
            _process_doc_symbol(sym, entities, exported_names, seen_names)
    else:
        # SymbolInformation: flat list, filter to top-level kinds
        for sym in symbols:
            # Skip methods/properties (they belong to a class, not top-level)
            if sym.get("containerName", ""):
                continue
            _process_symbol_info(sym, entities, exported_names, seen_names)

    return entities, exported_names


def _process_doc_symbol(
    sym: dict,
    entities: list[EntityPayload],
    exported_names: list[str],
    seen_names: set[str],
) -> None:
    """Process a single DocumentSymbol (with children)."""
    kind_int = sym.get("kind", 0)
    name = sym.get("name", "")
    line = _sym_line(sym)

    if not name or name in seen_names:
        return

    children = sym.get("children", [])

    if kind_int in (_SK_CLASS, _SK_INTERFACE):
        methods = []
        for child in children:
            child_kind = child.get("kind", 0)
            child_name = child.get("name", "")
            if child_kind in (_SK_METHOD, _SK_PROPERTY, _SK_FUNCTION) and child_name:
                methods.append(child_name)
        kind = "class" if kind_int == _SK_CLASS else "interface"
        entities.append(
            {
                "name": name,
                "kind": kind,
                "line": line,
                "methods": methods,
                "bases": [],
                "decorators": [],
            }
        )
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int == _SK_ENUM:
        members = [
            c.get("name", "") for c in children if c.get("kind") == _SK_ENUM_MEMBER
        ]
        entities.append(
            {
                "name": name,
                "kind": "enum",
                "line": line,
                "methods": members,
                "bases": [],
                "decorators": [],
            }
        )
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int == _SK_FUNCTION:
        entities.append(
            {
                "name": name,
                "kind": "function",
                "line": line,
                "methods": [],
                "bases": [],
                "decorators": [],
            }
        )
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int in (_SK_VARIABLE, _SK_CONSTANT):
        # Only capture exported-looking names (PascalCase or camelCase identifiers)
        # Skip destructured locals, parameters, etc.
        if len(name) >= 2 and (name[0].isupper() or not name.startswith("_")):
            ent_kind = "type" if name[0].isupper() and not children else "function"
            methods = []
            if children:
                for child in children:
                    child_name = child.get("name", "")
                    if child_name:
                        methods.append(child_name)
            entities.append(
                {
                    "name": name,
                    "kind": ent_kind,
                    "line": line,
                    "methods": methods,
                    "bases": [],
                    "decorators": [],
                }
            )
            seen_names.add(name)
            exported_names.append(name)

    elif kind_int == _SK_TYPE_PARAMETER:
        entities.append(
            {
                "name": name,
                "kind": "type",
                "line": line,
                "methods": [],
                "bases": [],
                "decorators": [],
            }
        )
        seen_names.add(name)
        exported_names.append(name)


def _process_symbol_info(
    sym: dict,
    entities: list[EntityPayload],
    exported_names: list[str],
    seen_names: set[str],
) -> None:
    """Process a single SymbolInformation (flat, no children)."""
    kind_int = sym.get("kind", 0)
    name = sym.get("name", "")
    line = _sym_line(sym)

    if not name or name in seen_names:
        return

    kind = _SK_TO_KIND.get(kind_int)
    if not kind:
        return

    entities.append(
        {
            "name": name,
            "kind": kind,
            "line": line,
            "methods": [],
            "bases": [],
            "decorators": [],
        }
    )
    seen_names.add(name)
    exported_names.append(name)


def _drain_prepare_batch(
    client: "LspClient",
    batch: list[tuple[int, str, EntityPayload]],
    path_to_node_id: dict[str, str],
    source_node_id: str,
    lsp_edges: list[LspEdge],
    timeout: float,
) -> int:
    """
    Collect prepareCallHierarchy results, pipeline outgoingCalls, collect those.

    Returns number of CALLS edges found.
    """
    calls_found = 0
    # Collect prepare results and fire outgoingCalls
    outgoing_pending: list[tuple[int, str, str]] = []  # (msg_id, ent_name, node_id)
    for mid, ent_name, _ent in batch:
        items = client._request_collect(mid, timeout=timeout)
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("name") != ent_name:
                continue
            oc_mid = client._request_fire("callHierarchy/outgoingCalls", {"item": item})
            if oc_mid >= 0:
                outgoing_pending.append((oc_mid, ent_name, source_node_id))

    # Collect outgoingCalls results
    for oc_mid, ent_name, node_id in outgoing_pending:
        outgoing = client._request_collect(oc_mid, timeout=timeout)
        if not isinstance(outgoing, list):
            continue
        for call in outgoing:
            target = call.get("to", {})
            target_uri = target.get("uri", "")
            target_name = target.get("name", "")
            target_path = uri_to_path(target_uri)
            target_node_id = path_to_node_id.get(target_path)
            if not target_node_id:
                continue
            from_ranges = [fr["start"]["line"] + 1 for fr in call.get("fromRanges", [])]
            lsp_edges.append(
                LspEdge(
                    source=f"{node_id}::{ent_name}",
                    target=f"{target_node_id}::{target_name}",
                    edge_type="CALLS",
                    metadata={"from_lines": from_ranges, "lsp_verified": True},
                )
            )
            calls_found += 1
    return calls_found


def _partition_lsp_batch(
    pending_prepare: list[tuple[int, str, EntityPayload]],
    pending_refs: list[tuple[int, str, str]],
    batch_size: int,
) -> tuple[
    list[tuple[int, str, EntityPayload]],
    list[tuple[int, str, str]],
    list[tuple[int, str, EntityPayload]],
    list[tuple[int, str, str]],
]:
    """Take a bounded mixed LSP batch while guaranteeing queue progress."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    prepare_count = min(len(pending_prepare), batch_size // 2)
    reference_count = min(len(pending_refs), batch_size - prepare_count)
    remaining_capacity = batch_size - prepare_count - reference_count
    prepare_count += min(len(pending_prepare) - prepare_count, remaining_capacity)
    return (
        pending_prepare[:prepare_count],
        pending_refs[:reference_count],
        pending_prepare[prepare_count:],
        pending_refs[reference_count:],
    )


def _drain_reference_batch(
    client: "LspClient",
    batch: list[tuple[int, str, str]],
    path_to_node_id: dict[str, str],
    node_id_to_result: dict[str, FileResult],
    lsp_edges: list[LspEdge],
    timeout: float,
) -> int:
    """Collect reference requests and append resolved cross-module edges."""
    refs_found = 0
    for mid, ent_name, src_node_id in batch:
        ref_locations = client._request_collect(mid, timeout=timeout)
        if not isinstance(ref_locations, list):
            continue
        for loc in ref_locations:
            ref_uri = loc.get("uri", "")
            ref_path = uri_to_path(ref_uri)
            ref_node_id = path_to_node_id.get(ref_path)
            if not ref_node_id or ref_node_id == src_node_id:
                continue
            ref_result = node_id_to_result.get(ref_node_id)
            ref_line_0 = loc.get("range", {}).get("start", {}).get("line", 0)
            ref_entity_name = None
            if ref_result:
                ref_entity_name = _find_enclosing_entity(
                    ref_result.entities,
                    ref_line_0,
                )
            source_key = (
                f"{ref_node_id}::{ref_entity_name}" if ref_entity_name else ref_node_id
            )
            target_key = f"{src_node_id}::{ent_name}"
            lsp_edges.append(
                LspEdge(
                    source=source_key,
                    target=target_key,
                    edge_type="REFERENCES",
                    metadata={"lsp_verified": True, "ref_line": ref_line_0 + 1},
                )
            )
            refs_found += 1
    return refs_found


def _find_enclosing_entity(
    entities: Sequence[EntityPayload],
    line_0based: int,
) -> Optional[str]:
    """
    Find the entity that encloses the given 0-based line.

    Uses a simple heuristic: the entity whose line is closest to (but not
    after) the target line.
    """
    best: Optional[EntityPayload] = None
    for ent in entities:
        ent_line_0 = ent["line"] - 1  # entity lines are 1-based
        if ent_line_0 <= line_0based:
            if best is None or ent_line_0 > best["line"] - 1:
                best = ent
    return best["name"] if best else None


def analyze_project_lsp(
    project: ProjectGroup,
    root: str,
    path_to_node_id: dict[str, str],
    node_id_to_result: dict[str, FileResult],
    request_timeout: float,
) -> list[LspEdge]:
    """
    Analyze a single project with LSP for precise call graph and references.

    Returns a list of LspEdge instances to merge into the main graph.
    """
    language = language_for_name(project.language)
    cmd = list(language.lsp_command)

    # Check server availability
    server_bin = cmd[0]
    from shutil import which

    if not which(server_bin):
        print(f"  [lsp] WARNING: {server_bin} not found, skipping {project.root}")
        return []

    proj_name = os.path.relpath(project.root, root)
    print(f"  [lsp] Starting {server_bin} for {proj_name} ({len(project.files)} files)")

    client = LspClient(cmd, cwd=project.root)
    lsp_edges: list[LspEdge] = []

    try:
        # Initialize
        root_uri = path_to_uri(project.root)
        if not client.initialize(root_uri):
            print(f"  [lsp] WARNING: initialize failed for {proj_name}")
            return []

        # Open all project files (batch)
        file_contents: dict[str, str] = {}
        for r in project.files:
            try:
                with open(r.path, "r", encoding=SOURCE_ENCODING, errors="replace") as f:
                    content = f.read()
                file_contents[r.path] = content
                uri = path_to_uri(r.path)
                lang_id = _language_id(r.language, r.path)
                client.did_open(uri, lang_id, content)
            except OSError as error:
                print(
                    f"  [lsp] WARNING: could not open {r.path}: {error}",
                    file=sys.stderr,
                )
                continue

        # Give the server a moment to process opened files
        time.sleep(1.0)

        # -- Phase 1: Merge LSP documentSymbol with regex entities --
        # LSP gives precise line numbers and finds entities regex misses
        # (object literals, Zod schemas). Regex gives method lists via
        # brace scanning. Merge: take LSP entities as base, carry over
        # regex methods for classes/interfaces that LSP returns without children.
        symbols_replaced = 0
        for r in project.files:
            if not client.alive:
                break
            node_id = path_to_node_id.get(r.path)
            if not node_id:
                continue
            uri = path_to_uri(r.path)
            lsp_syms = client.document_symbols(uri, timeout=request_timeout)
            if lsp_syms:
                new_entities, new_exports = _lsp_symbols_to_entities(lsp_syms)
                # Build regex method index: entity_name -> methods list
                regex_methods: dict[str, list[str]] = {}
                for old_ent in r.entities:
                    methods = old_ent.get("methods", [])
                    if methods:
                        regex_methods[old_ent["name"]] = methods
                # Merge: carry over regex methods when LSP has none
                for ent in new_entities:
                    if ent["kind"] in ("class", "interface") and not ent.get("methods"):
                        regex_m = regex_methods.get(ent["name"])
                        if regex_m:
                            ent["methods"] = regex_m
                    # Also carry over bases/decorators from regex
                    if not ent.get("bases") or not ent.get("decorators"):
                        for old_ent in r.entities:
                            if old_ent["name"] == ent["name"]:
                                if not ent.get("bases") and old_ent.get("bases"):
                                    ent["bases"] = old_ent.get("bases", [])
                                if not ent.get("decorators") and old_ent.get(
                                    "decorators"
                                ):
                                    ent["decorators"] = old_ent.get(
                                        "decorators",
                                        [],
                                    )
                                break
                # Add regex entities not found by LSP (e.g., Zod schemas)
                lsp_names = {e["name"] for e in new_entities}
                for old_ent in r.entities:
                    if old_ent["name"] not in lsp_names:
                        new_entities.append(old_ent)
                        if old_ent["name"] not in new_exports:
                            new_exports.append(old_ent["name"])
                r.entities = new_entities
                r.exported_names = list(set(new_exports))
                symbols_replaced += 1

        if symbols_replaced:
            print(
                f"  [lsp] {proj_name}: merged entities in {symbols_replaced} files via documentSymbol"
            )

        # -- Phase 2: Pipelined call hierarchy + references --
        # Strategy: for each file, fire all prepareCallHierarchy and
        # references requests at once (pipelined), then collect results.
        # This overlaps network round-trips with server processing.
        total_entities = sum(len(r.entities) for r in project.files)
        processed = 0
        calls_found = 0
        refs_found = 0
        PIPELINE_BATCH = 40  # max concurrent in-flight requests

        for r in project.files:
            if not client.alive:
                print(f"  [lsp] WARNING: server died while processing {proj_name}")
                break

            content = file_contents.get(r.path)
            if not content:
                continue

            node_id = path_to_node_id.get(r.path)
            if not node_id:
                continue

            uri = path_to_uri(r.path)

            # -- Batch 1: fire prepareCallHierarchy + references for all entities --
            pending_prepare: list[tuple[int, str, EntityPayload]] = []
            pending_refs: list[tuple[int, str, str]] = []  # (msg_id, ent_name, node_id)

            for ent in r.entities:
                ent_name = ent["name"]
                ent_line_0 = ent["line"] - 1
                ent_col = find_entity_column(content, ent_name, ent["line"])

                if ent["kind"] in ("function", "class"):
                    mid = client._request_fire(
                        "textDocument/prepareCallHierarchy",
                        {
                            "textDocument": {"uri": uri},
                            "position": {"line": ent_line_0, "character": ent_col},
                        },
                    )
                    if mid >= 0:
                        pending_prepare.append((mid, ent_name, ent))

                if ent_name in r.exported_names:
                    mid = client._request_fire(
                        "textDocument/references",
                        {
                            "textDocument": {"uri": uri},
                            "position": {"line": ent_line_0, "character": ent_col},
                            "context": {"includeDeclaration": False},
                        },
                    )
                    if mid >= 0:
                        pending_refs.append((mid, ent_name, node_id))

                # Drain pipeline if too many in-flight
                while len(pending_prepare) + len(pending_refs) >= PIPELINE_BATCH:
                    prepare_batch, reference_batch, pending_prepare, pending_refs = (
                        _partition_lsp_batch(
                            pending_prepare,
                            pending_refs,
                            PIPELINE_BATCH,
                        )
                    )
                    calls_found += _drain_prepare_batch(
                        client,
                        prepare_batch,
                        path_to_node_id,
                        node_id,
                        lsp_edges,
                        request_timeout,
                    )
                    refs_found += _drain_reference_batch(
                        client,
                        reference_batch,
                        path_to_node_id,
                        node_id_to_result,
                        lsp_edges,
                        request_timeout,
                    )

            # -- Collect remaining prepareCallHierarchy results --
            calls_found += _drain_prepare_batch(
                client,
                pending_prepare,
                path_to_node_id,
                node_id,
                lsp_edges,
                request_timeout,
            )

            # -- Collect remaining references results --
            refs_found += _drain_reference_batch(
                client,
                pending_refs,
                path_to_node_id,
                node_id_to_result,
                lsp_edges,
                request_timeout,
            )

            processed += len(r.entities)
            if processed % 200 < len(r.entities):
                print(
                    f"  [lsp] {proj_name}: {processed}/{total_entities} entities "
                    f"({calls_found} calls, {refs_found} refs)"
                )

            client.did_close(uri)

        print(
            f"  [lsp] {proj_name}: done -- {calls_found} calls, {refs_found} refs from {processed} entities"
        )

    except Exception as exc:
        print(f"  [lsp] ERROR in {proj_name}: {exc}")
    finally:
        client.shutdown()

    return lsp_edges


# ---------------------------------------------------------------------------
# LSP merge -- integrate LSP edges into the graph
# ---------------------------------------------------------------------------


def merge_lsp_edges(
    edges: list[dict],
    lsp_edges: list[LspEdge],
) -> list[dict]:
    """
    Merge LSP-derived edges into the existing edge list.

    - New CALLS edges are added directly.
    - REFERENCES edges that match existing ones get ``lsp_verified: true``.
    - New REFERENCES edges not found by the regex pass are added.
    """
    # Index existing edges for fast lookup
    existing_keys: dict[tuple, int] = {}
    for idx, edge in enumerate(edges):
        key = (edge["source"], edge["target"], edge["type"])
        existing_keys[key] = idx

    new_edges: list[dict] = []
    verified_count = 0
    new_count = 0

    for le in lsp_edges:
        key = (le.source, le.target, le.edge_type)
        if key in existing_keys:
            # Mark existing edge as LSP-verified
            edges[existing_keys[key]]["lsp_verified"] = True
            verified_count += 1
        else:
            # Add new edge
            edge_dict: dict = {
                "source": le.source,
                "target": le.target,
                "type": le.edge_type,
            }
            edge_dict.update(le.metadata)
            new_edges.append(edge_dict)
            new_count += 1
            # Track so we don't add duplicates
            existing_keys[key] = -1

    print(f"[code-graph] LSP merge: {verified_count} verified, {new_count} new edges")
    return edges + new_edges


# ---------------------------------------------------------------------------
# LSP enrichment orchestrator
# ---------------------------------------------------------------------------


def enrich_with_lsp(
    root: str,
    results: list[FileResult],
    nodes: list[dict],
    edges: list[dict],
    request_timeout: float,
    exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
) -> tuple[list[dict], list[dict]]:
    """Run language-owned LSP analysis and merge its graph evidence."""
    # Build lookup tables
    path_to_node_id = assign_node_ids([r.path for r in results], root)
    node_id_to_result: dict[str, FileResult] = {path_to_node_id[r.path]: r for r in results}

    # Discover projects
    projects = discover_projects(root, results, exclusions)
    if not projects:
        config_names = ", ".join(
            sorted(
                {
                    config_name
                    for language in LANGUAGES
                    for config_name in language.project_config_names
                }
            )
        )
        print(
            f"[code-graph] No LSP-compatible projects found (expected one of: {config_names})"
        )
        return nodes, edges

    language_stats: list[str] = []
    for language in LANGUAGES:
        language_projects = [
            project for project in projects if project.language == language.name
        ]
        if language_projects:
            file_count = sum(len(project.files) for project in language_projects)
            language_stats.append(
                f"{len(language_projects)} {language.lsp_label}/{file_count} files"
            )
    print(f"[code-graph] LSP: {len(projects)} projects ({', '.join(language_stats)})")

    all_lsp_edges: list[LspEdge] = []

    for project in projects:
        proj_edges = analyze_project_lsp(
            project,
            root,
            path_to_node_id,
            node_id_to_result,
            request_timeout,
        )
        all_lsp_edges.extend(proj_edges)

    if all_lsp_edges:
        edges = merge_lsp_edges(edges, all_lsp_edges)

    # Rebuild nodes with LSP-replaced entities
    node_by_id = {n["id"]: n for n in nodes}
    for r in results:
        nid = path_to_node_id.get(r.path)
        if nid and nid in node_by_id:
            node_by_id[nid]["entities"] = r.entities
    nodes = list(node_by_id.values())

    return nodes, edges


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _sexp_escape(s: str) -> str:
    """Escape a string for S-expression output (double-quote delimited)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _entity_id(module_path: str, entity_name: str) -> str:
    """Generate a short unique ID for an entity based on path + name SHA."""
    raw = f"{module_path}::{entity_name}"
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Path trie for compressed path definitions
# ---------------------------------------------------------------------------


def _build_path_tree(path_sid_pairs: list[tuple[str, str]]) -> list[str]:
    """
    Build a compressed path trie and emit as indented S-expression tree.

    Shares common path prefixes to reduce repetition. Collapses single-child
    chains into combined segments (path compression).

    Output format::

        (paths
          (agent-hub/packages/agent-research/src
            (ah0 ReasoningEngine)
            (ah1 ResearchAgent))
          (app-service/app-service/src/operatives
            (asp340 operatives.controller)))
    """
    # Build trie: dict of dicts, leaves are {"_sid": "ah0"}
    # A node can be BOTH a directory (has children) and a leaf (has _sid)
    # when index.ts strips to the directory name.
    root: dict = {}
    for full_path, sid in path_sid_pairs:
        segments = full_path.split("/")
        node = root
        for seg in segments[:-1]:
            node = node.setdefault(seg, {})
        # Leaf: store sid. If the key already exists as a dict (directory),
        # merge _sid into it rather than overwriting children.
        leaf_key = segments[-1]
        existing = node.get(leaf_key)
        if isinstance(existing, dict) and "_sid" not in existing:
            existing["_sid"] = sid
        else:
            node[leaf_key] = {"_sid": sid}

    # Compress: collapse single-child internal nodes
    def compress(node: dict) -> dict:
        result: dict = {}
        for key, child in node.items():
            if isinstance(child, dict) and "_sid" in child:
                result[key] = child  # leaf, keep as-is
            elif isinstance(child, dict):
                compressed = compress(child)
                # If single non-leaf child, merge keys
                while len(compressed) == 1:
                    only_key, only_val = next(iter(compressed.items()))
                    if isinstance(only_val, dict) and "_sid" not in only_val:
                        key = f"{key}/{only_key}"
                        compressed = only_val
                    else:
                        break
                result[key] = compressed
            else:
                result[key] = child
        return result

    compressed = compress(root)

    # Emit as indented S-expressions
    def emit(node: dict, depth: int) -> list[str]:
        lines: list[str] = []
        indent = "  " * depth
        for key in sorted(node.keys()):
            if key == "_sid":
                continue  # handled by parent
            child = node[key]
            if isinstance(child, dict) and "_sid" in child:
                # Check if this is BOTH a leaf and a directory (has other keys)
                other_keys = [k for k in child if k != "_sid"]
                if other_keys:
                    # Emit the _sid as a :self entry, then recurse for children
                    lines.append(f"{indent}({key}")
                    lines.append(f"{indent}  ({child['_sid']} :self)")
                    lines.extend(emit(child, depth + 1))
                    lines.append(f"{indent})")
                else:
                    lines.append(f"{indent}({child['_sid']} {key})")
            elif isinstance(child, dict):
                lines.append(f"{indent}({key}")
                lines.extend(emit(child, depth + 1))
                lines.append(f"{indent})")
        return lines

    return emit(compressed, 1)


def _is_safe_path_atom(path: str) -> bool:
    """Return whether every path segment is safe in the unquoted trie syntax."""
    return bool(re.fullmatch(r"[A-Za-z0-9_./@+-]+", path))


def _path_ids(nodes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Nodes sorted by ID and the short ID of each (repo prefix + sequence number), as the graph names them."""
    sorted_nodes = sorted(nodes, key=lambda n: n["id"])

    # Build path map: full path -> short prefixed ID (repo-prefix + seq number)
    # e.g. ah0 = agent-hub first module, dr42 = drive 43rd module
    _REPO_PREFIX: dict[str, str] = {}
    _repo_counters: dict[str, int] = {}
    path_to_sid: dict[str, str] = {}

    for node in sorted_nodes:
        repo = node.get("repo", "unknown")
        if repo not in _REPO_PREFIX:
            # Build 2-char prefix from repo name
            parts = repo.replace("-", " ").split()
            if len(parts) >= 2:
                prefix = parts[0][0] + parts[1][0]
            else:
                prefix = repo[:2]
            # Deduplicate prefixes
            base = prefix
            idx = 2
            while prefix in _REPO_PREFIX.values():
                prefix = (
                    base + repo[idx : idx + 1] if idx < len(repo) else base + str(idx)
                )
                idx += 1
            _REPO_PREFIX[repo] = prefix
            _repo_counters[repo] = 0

        seq = _repo_counters[repo]
        _repo_counters[repo] = seq + 1
        sid = f"{_REPO_PREFIX[repo]}{seq}"
        path_to_sid[node["id"]] = sid

    return sorted_nodes, path_to_sid


def render_sexp(
    nodes: list[dict],
    edges: list[dict],
    _root: str,
    mode: str,
    total_files: int,
    architecture: Optional[GraphArchitecture] = None,
) -> str:
    """
    Write the code graph as flat S-expression facts with path-map compression.

    The file starts with ``(def <id> <full-path>)`` bindings that assign short
    numeric IDs to every module path. All subsequent lines use these IDs instead
    of full paths, reducing file size by ~60-70%.

    Format::

        ; --- paths ---
        (def 0 agent-hub/packages/agent-research/src/ReasoningEngine)
        (def 1 agent-hub/packages/agent-research/src/ResearchAgent)

        ; --- nodes ---
        (node 0 ty "general module" :naming PascalCase :pkg agent-hub)

        ; --- entities ---
        (fn 0 isToolIncluded 5)
        (cls 1 ResearchAgent 322 (:bases BaseAgent) (:methods ...))

        ; --- edges ---
        (imports 1 0 :via-base)
        (refs 1 0::ReasoningSchemaType)

        ; --- violations ---
        (violation boundary 42 99 "domain imports infrastructure without abstract base")
    """
    sorted_nodes, path_to_sid = _path_ids(nodes)

    def _pid(path: str) -> str:
        """Resolve a path (possibly with ::entity suffix) to short ID."""
        if "::" in path:
            mod, ent = path.split("::", 1)
            sid = path_to_sid.get(mod)
            return f"{sid}::{ent}" if sid is not None else path
        sid = path_to_sid.get(path)
        return sid if sid is not None else path

    lines: list[str] = []
    violations = [e for e in edges if e.get("type") == "VIOLATION"]
    # A use reached only through a re-export and a module-level variable live in the use file
    # (.wingbox/index.uses.sexp), which entity and usages read; module queries never need them.
    real_edges = [
        e for e in edges
        if e.get("type") != "VIOLATION"
        and not (e.get("type") == "REFERENCES" and "reexport" in e.get("relations", []))
    ]

    lines.append("; Code Graph")
    lines.append(f"; mode {mode}")
    lines.append(
        f"; {total_files} files {len(sorted_nodes)} nodes {len(real_edges)} edges {len(violations)} violations"
    )
    lines.append(
        f"(code-graph :schema {MAP_SCHEMA_VERSION} :files {total_files} "
        f":nodes {len(sorted_nodes)} :edges {len(real_edges)} "
        f":violations {len(violations)})"
    )
    lines.append("")

    # -- Path tree (compressed trie) --
    lines.append("; --- paths ---")
    safe_nodes = [node for node in sorted_nodes if _is_safe_path_atom(node["id"])]
    unsafe_nodes = [node for node in sorted_nodes if not _is_safe_path_atom(node["id"])]
    for node in unsafe_nodes:
        sid = path_to_sid[node["id"]]
        escaped_path = _sexp_escape(node["id"])
        lines.append(f'(def {sid} "{escaped_path}")')
    trie_pairs = [(node["id"], path_to_sid[node["id"]]) for node in safe_nodes]
    lines.append("(paths")
    lines.extend(_build_path_tree(trie_pairs))
    lines.append(")")
    lines.append("")

    # -- Nodes with invariant (Gap 1 fix) --
    lines.append("; --- nodes ---")
    for node in sorted_nodes:
        sid = path_to_sid[node["id"]]
        language = language_for_result(node["language"])
        lang = language.output_language_code(node["language"])
        purpose = _sexp_escape(node.get("purpose", ""))
        inv = node.get("invariant", {})
        naming = inv.get("naming", "mixed")
        pkg = inv.get("package", "")
        lines.append(f'(node {sid} {lang} "{purpose}" :naming {naming} :pkg {pkg})')
    lines.append("")

    # -- Entities (each has a unique eid based on sha(path::name)) --
    lines.append("; --- entities ---")
    for node in sorted_nodes:
        sid = path_to_sid[node["id"]]
        for ent in node.get("entities", []):
            kind = ent["kind"]
            if kind == "variable":
                continue
            name = ent["name"]
            line_num = ent.get("line", 0)
            eid = _entity_id(node["id"], name)
            kind_tag = {
                "class": "cls",
                "function": "fn",
                "interface": "ifc",
                "module": "mod",
                "type": "typ",
                "enum": "enm",
            }.get(kind, kind[:3])

            bases = ent.get("bases", [])
            deco = ent.get("decorators", [])
            methods = ent.get("methods", [])

            if not bases and not deco and not methods:
                lines.append(f"({kind_tag} {sid} {name} {line_num} :eid {eid})")
            else:
                parts = [f"({kind_tag} {sid} {name} {line_num} :eid {eid}"]
                if bases:
                    parts.append(f"(:bases {' '.join(bases)})")
                if deco:
                    parts.append(f"(:deco {' '.join(deco)})")
                if methods:
                    parts.append(f"(:methods {' '.join(methods)})")
                lines.append(" ".join(parts) + ")")
    lines.append("")

    # -- Edges --
    lines.append("; --- edges ---")
    sorted_edges = sorted(
        real_edges, key=lambda e: (e["type"], e["source"], e["target"])
    )
    for edge in sorted_edges:
        src = _pid(edge["source"])
        tgt = _pid(edge["target"])
        etype = edge["type"]

        if etype == "IMPORTS":
            flags = " :via-base" if edge.get("via_base") else ""
            if edge.get("type_only"):
                flags += " :type-only"
            relation_flags = "".join(
                f" :{relation}" for relation in sorted(edge.get("relations", []))
            )
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(imports {src} {tgt}{flags}{relation_flags}{lsp}{_edge_names_sexp(edge.get('names'))})")
        elif etype == "CALLS_API":
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(calls-api {src} {tgt} :via-ifc{lsp})")
        elif etype == "DATA_FLOWS_TO":
            evidence = edge.get("validation_evidence", "")
            flags = f' :validation-module :evidence "{_sexp_escape(evidence)}"' if evidence else ""
            if edge.get("pipeline"):
                flags += " :pipeline"
            lines.append(f"(data-flow {src} {tgt}{flags})")
        elif etype == "REGISTRY_WIRES":
            config = edge.get("config", "")
            flags = f" :config {config}" if config and _is_safe_path_atom(config) else ""
            lines.append(f"(registry-wires {src} {tgt}{flags})")
        elif etype == "REFERENCES":
            relation_flags = "".join(
                f" :{relation}" for relation in sorted(edge.get("relations", []))
            )
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(refs {src} {tgt}{relation_flags}{lsp})")
        elif etype == "CALLS":
            fl = edge.get("from_lines", [])
            lns = f" :lines {' '.join(str(line) for line in fl)}" if fl else ""
            flags = (" :registry" if edge.get("registry") else "") + (" :lsp" if edge.get("lsp_verified") else "")
            lines.append(f"(calls {src} {tgt}{lns}{flags})")
        elif etype == "HTTP_CALLS":
            client = edge.get("client_entity", "")
            client_str = f" :client {client}" if client else ""
            lines.append(f"(http-calls {src} {tgt} :transport http{client_str})")

    # -- Architecture: parts, their rules, and the layer of every module --
    if architecture is not None:
        lines.append("")
        lines.append("; --- architecture ---")
        lines.extend(architecture.part_lines)
        for node in sorted_nodes:
            labelled = architecture.labels.get(node["id"])
            if labelled is None:
                continue
            part, label = labelled
            lines.append(
                f'(layer {path_to_sid[node["id"]]} {label.layer} :part "{_sexp_escape(part)}" :style {label.style} '
                f':rule "{_sexp_escape(label.rule)}" :match {label.match}{" :composition-root" if label.root else ""}'
                f'{_learned_from(label.rule)})'
            )
        terms = architecture.terms or {}
        for part, source, target, baseline in architecture.known:
            if source in path_to_sid and target in path_to_sid:
                # Owner, reason and end date of an entry of .wingbox/baseline.sexp: a query decides by its own day.
                stated = terms.get(("baseline", part, source, target)) if baseline == BASELINE_FILE else None
                lines.append(
                    f'(arch-known "{_sexp_escape(part)}" {path_to_sid[source]} {path_to_sid[target]} "{_sexp_escape(baseline)}"'
                    f'{terms_sexp(stated) if stated else ""})'
                )
        for node in sorted_nodes:
            for finding in (architecture.capabilities or {}).get(node["id"], ()):
                lines.append(
                    f'(capability {path_to_sid[node["id"]]} {finding.capability} :line {finding.line} '
                    f':evidence "{_sexp_escape(finding.evidence)}"{" :suspect" if finding.suspect else ""})'
                )
        for part, node_id, capability in architecture.capability_known:
            if node_id in path_to_sid:
                stated = terms.get(("baseline-capability", part, node_id, capability))
                lines.append(f'(capability-known "{_sexp_escape(part)}" {path_to_sid[node_id]} {capability} "{BASELINE_FILE}"'
                             f'{terms_sexp(stated) if stated else ""})')

    # -- Violations (Gap 2 fix) --
    if violations:
        lines.append("")
        lines.append("; --- violations ---")
        for v in violations:
            src = _pid(v["source"])
            tgt = _pid(v["target"])
            rule = v.get("rule", "unknown")
            detail = _sexp_escape(v.get("detail", ""))
            lines.append(f'(violation {rule} {src} {tgt} "{detail}")')

    return "\n".join(lines) + "\n"


USES_SCHEMA_VERSION = 1


def uses_path(map_path: str) -> str:
    """The use-position file that goes with a code graph: index.sexp -> index.uses.sexp."""
    return (map_path[: -len(".sexp")] if map_path.endswith(".sexp") else map_path) + ".uses.sexp"


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def symbol_index_key(results: Sequence[FileResult], fingerprint: str, mode: str) -> str:
    """Identity of every input of use resolution: each file and its content, the builder, the mode."""
    digest = hashlib.sha256(f"{fingerprint}|{mode}".encode("utf-8"))
    for result in sorted(results, key=lambda item: item.path):
        digest.update(f"\n{result.path}\0{result.content_hash}".encode("utf-8"))
    return digest.hexdigest()[:32]


def render_uses_sexp(nodes: list[dict], key: str, graph_text: str, positions: bool = False) -> str:
    """The use file of a code graph: what only entity, usages and entity --positions read.

    `var` facts define module-level variables (Python). Each `use` line names a file and a definition
    in another file it uses (the refs that reach a definition through a re-export are only here);
    with positions, every use of the file, also within itself, with the 1-based line:column of each
    site and a letter for its kind, and the unresolved method calls. A build writes the file without
    positions; the first query that needs them fills them in (write_use_positions). The module IDs
    are the short IDs of the graph whose digest the header carries.
    """
    sorted_nodes, path_to_sid = _path_ids(nodes)
    lines = _uses_header(key, graph_text, positions)
    for node in sorted_nodes:
        lines.extend(_use_block(node, path_to_sid, positions))
    return "\n".join(lines) + "\n"


def _uses_header(key: str, graph_text: str, positions: bool) -> list[str]:
    return [
        "; Use file of the code graph: (use <module> <defining-module>::<name> [<line>:<col>[<kind>] ...])",
        f"(code-graph-uses :schema {USES_SCHEMA_VERSION} :key {key} :positions {'yes' if positions else 'no'} "
        f":graph {text_digest(graph_text)})",
    ]


def _use_block(node: Mapping[str, Any], path_to_sid: Mapping[str, str], positions: bool) -> list[str]:
    """The lines of one file in the use file: its variables, its uses, its unresolved method calls."""
    sid = path_to_sid[node["id"]]
    lines = [
        f"(var {sid} {entity['name']} {entity.get('line', 0)})"
        for entity in node.get("entities", [])
        if entity["kind"] == "variable"
    ]
    sites: dict[tuple[str, str], list[str]] = {}
    for line, column, module, name, kind in node.get("uses", []):
        if positions:
            sites.setdefault((module, name), []).append(f"{line}:{column}{_USE_KIND_LETTERS.get(kind, '')}")
        elif module != node["id"] and "." not in name:
            sites.setdefault((module, name), [])
    lines.extend(
        f"(use {sid} {path_to_sid.get(module, module)}::{name}{''.join(' ' + p for p in found)})"
        for (module, name), found in sorted(sites.items())
    )
    unresolved = node.get("unresolved_method_calls")
    if positions and unresolved is not None:
        lines.append(f"(unresolved {sid} :method-calls {unresolved.count})")
        lines.extend(
            f"(unresolved-call {sid} {member} " + " ".join(f"{line}:{column}" for line, column in found) + ")"
            for member, found in sorted(unresolved.sites.items())
        )
    return lines


@dataclass(frozen=True)
class ReusedUses:
    """What a build without changed files takes from the existing use file instead of resolving:
    the (definition short ID, name) pairs each file short ID uses, and the file's lines."""

    targets: dict[str, list[tuple[str, str]]]
    lines: list[str]


def reusable_symbol_uses(path: str, key: str) -> Optional[ReusedUses]:
    """The use file at path when it was built from the same inputs (same key); None otherwise."""
    try:
        with open(path, "r", encoding="utf-8") as uses_file:
            lines = uses_file.read().splitlines()
    except FileNotFoundError:
        return None
    except OSError as error:
        print(f"[code-graph] WARNING: cannot read {path}: {error}; resolving uses again", file=sys.stderr)
        return None
    header = next((line for line in lines if line.startswith("(code-graph-uses ")), "")
    if f":key {key} " not in header:
        return None
    targets: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        if line.startswith("(use "):
            _use, source, target = line.rstrip(")").split(" ", 3)[:3]
            module, _, name = target.partition("::")
            if "." not in name:
                targets.setdefault(source, []).append((module, name))
    return ReusedUses(targets, lines)


def reused_uses_text(reused: ReusedUses, graph_text: str) -> str:
    """The reused use file with the digest of the graph it now goes with."""
    digest = text_digest(graph_text)
    return "\n".join(
        re.sub(r":graph \S+\)$", f":graph {digest})", line) if line.startswith("(code-graph-uses ") else line
        for line in reused.lines
    ) + "\n"


def _position_parser(language: str) -> Callable[[str, str], list]:
    """The full use walk of a language (python_use_positions, typescript_use_positions, ...)."""
    family = {"python": "python", "rust": "rust"}.get(language, "typescript")
    package = f"{__package__}.lang" if __package__ else "lang"
    module = importlib.import_module(f"{package}.{family}.parser")
    return getattr(module, f"{family}_use_positions")


def _position_uses(item: tuple[str, str]) -> tuple[str, list]:
    """Worker: every use site of one file (path, result language)."""
    path, language = item
    with open(path, "r", encoding=SOURCE_ENCODING, errors="replace") as source_file:
        content = source_file.read()
    return path, _position_parser(language)(path, content)


USES_CACHE_SCHEMA_VERSION = 4


def uses_cache_path(map_path: str) -> str:
    """The cache of the full use walk next to a code graph: index.sexp -> index.uses-cache.json."""
    return (map_path[: -len(".sexp")] if map_path.endswith(".sexp") else map_path) + ".uses-cache.json"


@dataclass(frozen=True)
class UsePositions:
    """A use file filled with positions: its path, and how many files were walked again, taken from the
    walk cache, and resolved again."""

    path: str
    walked: int
    reused: int
    resolved: int = 0


def _compact_use(use: Mapping[str, Any]) -> list:
    """A use site as a short JSON list: path, flat positions, kind, module as written, method."""
    return [use["path"], [number for pair in use["at"] for number in pair],
            use.get("kind", ""), use.get("module", ""), use.get("member", "")]


def _expanded_use(item: list) -> dict:
    path, flat, kind, module, member = item
    use: dict[str, Any] = {"path": path, "at": [[flat[i], flat[i + 1]] for i in range(0, len(flat), 2)]}
    if kind:
        use["kind"] = kind
    if module:
        use["module"] = module
    if member:
        use["member"] = member
    return use


@dataclass(frozen=True)
class WalkCache:
    """index.uses-cache.json: per file, the content hash, the full use walk and, valid while the
    surface of the repository (files, definitions, bindings) is the same, the resolved uses."""

    surface: str
    files: dict[str, list]  # path -> [content hash, compact uses as JSON text, rendered use lines, unused]
    imports: dict[str, Optional[str]]  # "language\0spec\0source path" -> module ID, while the surface is the same


def _load_walk_cache(path: str, fingerprint: str) -> WalkCache:
    """The walk cache written by the same builder; an empty one otherwise."""
    empty = WalkCache("", {}, {})
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except FileNotFoundError:
        return empty
    except (OSError, json.JSONDecodeError) as error:
        print(f"[code-graph] WARNING: cannot read {path}: {error}; walking every file", file=sys.stderr)
        return empty
    if not isinstance(cache, dict) or cache.get("schema") != USES_CACHE_SCHEMA_VERSION \
            or cache.get("builder_fingerprint") != fingerprint or not isinstance(cache.get("files"), dict):
        return empty
    files = {
        file_path: entry
        for file_path, entry in cache["files"].items()
        if isinstance(entry, list) and len(entry) == 4 and isinstance(entry[0], str) and isinstance(entry[1], str)
    }
    imports = cache.get("imports") if isinstance(cache.get("imports"), dict) else {}
    return WalkCache(str(cache.get("surface", "")), files, imports)


def _surface_digest(results: Sequence[FileResult]) -> str:
    """What resolving one file's uses reads of the others: every path, and the definitions and
    bindings of the files with a use index. While it is the same, a file whose content did not
    change resolves as before."""
    digest = hashlib.sha256()
    for result in sorted(results, key=lambda item: item.path):
        digest.update(result.path.encode("utf-8") + b"\0")
        if supports_symbols(result.language):
            digest.update(repr((result.entities, result.bindings)).encode("utf-8"))
    return digest.hexdigest()[:32]


class _LazyBoundLanguages(dict):
    """Bound languages created on first need: a query whose imports are all in the walk cache never
    reads tsconfig files, packages or ignore rules."""

    def __init__(self, root: str, path_to_id: Mapping[str, str]) -> None:
        super().__init__()
        self._root, self._path_to_id = root, path_to_id

    def __missing__(self, name: str) -> BoundLanguage:
        git_ignore_result = load_git_ignored_paths(self._root, frozenset(SKIP_DIRS))
        exclusions = DiscoveryExclusions(
            directory_names=frozenset(SKIP_DIRS),
            paths=frozenset() if isinstance(git_ignore_result, GitIgnoreUnavailable) else git_ignore_result.paths,
        )
        language = next(item for item in LANGUAGES if item.name == name)
        self[name] = language.bind(self._root, self._path_to_id, exclusions)
        return self[name]


def _remove_walk_cache(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError as error:
        print(f"[code-graph] WARNING: cannot remove {path}: {error}", file=sys.stderr)


# Up to this many files are walked in this process: starting worker processes costs more.
_WALK_IN_PROCESS = 8


def _modified_since(result: FileResult) -> bool:
    try:
        return os.path.getmtime(result.path) != result.mtime
    except OSError:
        return True


def write_use_positions(map_path: str, root: str) -> Result[UsePositions, str]:
    """Fill the use file of a built graph with every use position; Err with the reason when it cannot.

    The build parses only the uses that can name another file. The first query that needs positions
    (usages, entity --positions) calls this. It reads the build cache and the walk cache
    (.wingbox/index.uses-cache.json): a file whose content hash is in the walk cache is not walked again
    and, while the surface of the repository is the same, not resolved again either. The other files
    are walked in parallel and resolved; the use file is rewritten with positions and keeps its key,
    so the next build without changes keeps the positions.
    """
    root = os.path.realpath(root)  # the build keys modules by real paths
    path = uses_path(map_path)
    try:
        with open(path, "r", encoding="utf-8") as uses_file:
            header = next((line for line in uses_file if line.startswith("(code-graph-uses ")), "")
        with open(map_path, "r", encoding="utf-8") as graph_file:
            graph_text = graph_file.read()
        with open(cache_path(root), "r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (OSError, json.JSONDecodeError) as error:
        return Err(f"cannot read the graph, its use file or its cache: {error}")
    key_match = re.search(r":key (\S+)", header)
    if key_match is None or f":graph {text_digest(graph_text)})" not in header:
        return Err("the use file does not belong to this code graph")
    entries = cache.get("entries") if isinstance(cache, dict) else None
    if not isinstance(entries, dict):
        return Err("the build cache has no entries")
    results: list[FileResult] = []
    for entry in entries.values():
        decoded = decode_cache_entry(entry)
        if isinstance(decoded, Err):
            return Err(f"the build cache is invalid: {decoded.error}")
        results.append(decoded.value)
    mode_match = re.search(r"^; mode (.+)$", graph_text, re.M)
    fingerprint = str(cache.get("builder_fingerprint", ""))
    if mode_match is None or symbol_index_key(results, fingerprint, mode_match.group(1)) != key_match.group(1):
        return Err("the build cache does not match the code graph")
    indexed = [result for result in results if supports_symbols(result.language)]
    # A file whose modification time is the one the build saw is not read again to compare hashes.
    changed = [result.path for result in indexed if _modified_since(result) and file_hash(result.path) != result.content_hash]
    if changed:
        return Err(f"{len(changed)} files changed since the build, e.g. {changed[0]}")

    walk_cache_path = uses_cache_path(map_path)
    walk_cache = _load_walk_cache(walk_cache_path, fingerprint)
    surface = _surface_digest(results)
    same_surface = walk_cache.surface == surface
    entries_out: dict[str, list] = {}
    to_walk: list[tuple[str, str]] = []
    for result in indexed:
        cached = walk_cache.files.get(result.path)
        if cached is not None and cached[0] == result.content_hash:
            entries_out[result.path] = cached if same_surface else [cached[0], cached[1], None, None]
        else:
            to_walk.append((result.path, result.language))
    if to_walk:
        with contextlib.ExitStack() as stack:
            if len(to_walk) <= _WALK_IN_PROCESS:
                walked = map(_position_uses, to_walk)
            else:
                pool = stack.enter_context(ProcessPoolExecutor(max_workers=portable.worker_count(len(to_walk))))
                walked = pool.map(_position_uses, to_walk, chunksize=16)
            for file_path, uses in walked:
                entries_out[file_path] = ["", json.dumps([_compact_use(use) for use in uses], separators=(",", ":")),
                                          None, None]
        hashes = {result.path: result.content_hash for result in indexed}
        for file_path, _language in to_walk:
            entries_out[file_path][0] = hashes[file_path]

    to_resolve = {result.path for result in indexed if entries_out[result.path][2] is None}
    with_uses = [
        dataclasses.replace(result, uses=[_expanded_use(item) for item in json.loads(entries_out[result.path][1])])
        if result.path in to_resolve else result
        for result in results
    ]
    index_result = _build_node_indexes(with_uses, root)
    if isinstance(index_result, Err):
        return Err("module ID collision")
    path_to_id, id_to_result = index_result.value
    imports = dict(walk_cache.imports) if same_surface else {}
    sorted_nodes, path_to_sid = _path_ids(
        [{"id": node_id, "repo": result.repo} for node_id, result in id_to_result.items()])
    if to_resolve:
        bound = _LazyBoundLanguages(root, path_to_id)

        def resolve_import(language: str, spec: str, source: str) -> Optional[str]:
            key = f"{language}\0{spec}\0{source}"
            if key not in imports:
                imports[key] = bound[language_for_result(language).name].resolve_import(spec, source)
            return imports[key]

        id_of = {result.path: node_id for node_id, result in id_to_result.items()}
        resolved, unresolved = _resolve_symbol_uses(
            id_to_result, bound, resolve_import, frozenset(id_of[file_path] for file_path in to_resolve))
        for file_path in to_resolve:
            node_id = id_of[file_path]
            node: dict[str, Any] = {"id": node_id, "entities": id_to_result[node_id].entities,
                                    "uses": resolved.get(node_id, [])}
            if node_id in unresolved:
                node["unresolved_method_calls"] = unresolved[node_id]
            # The rendered lines stay valid while the surface (hence every short ID) is the same.
            entries_out[file_path][2] = "\n".join(_use_block(node, path_to_sid, positions=True))

    order = {node["id"]: index for index, node in enumerate(sorted_nodes)}
    texts = [
        entries_out[result.path][2]
        for node_id, result in sorted(id_to_result.items(), key=lambda item: order[item[0]])
        if result.path in entries_out and entries_out[result.path][2]
    ]
    _atomic_write_text(path, "\n".join([*_uses_header(key_match.group(1), graph_text, True), *texts]) + "\n")
    if to_walk or to_resolve or set(walk_cache.files) != set(entries_out):
        _atomic_write_text(walk_cache_path, json.dumps({
            "schema": USES_CACHE_SCHEMA_VERSION,
            "builder_fingerprint": fingerprint,
            "surface": surface,
            "files": entries_out,
            "imports": imports,
        }, separators=(",", ":")) + "\n")
    return Ok(UsePositions(path, walked=len(to_walk), reused=len(indexed) - len(to_walk), resolved=len(to_resolve)))
def _learned_from(rule: str) -> str:
    """` :learned-from <id>` for a label that rests on the decisions store."""
    marker = "(learned from "
    if marker not in rule:
        return ""
    return " :learned-from " + rule.split(marker, 1)[1].split(")", 1)[0]


def write_sexp(
    output_path: str,
    nodes: list[dict],
    edges: list[dict],
    root: str,
    mode: str,
    total_files: int,
    architecture: Optional[GraphArchitecture] = None,
) -> None:
    """Atomically publish a fully rendered code graph and its use positions."""
    content = render_sexp(nodes, edges, root, mode, total_files, architecture)
    _atomic_write_text(output_path, content)
    _atomic_write_text(uses_path(output_path), render_uses_sexp(nodes, "-", content, positions=True))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuilderOptions:
    root: str
    output_path: str
    full_rebuild: bool
    use_lsp: bool
    lsp_timeout: float
    skip_directories: frozenset[str]
    fragments_path: Optional[str] = None  # write the fragment store there (partial updates)
    snapshot_path: Optional[str] = None  # write the snapshot of the tree there (change detection)
    keep_published: bool = False  # an automatic refresh: never replace a graph with modules by an empty one


def _builder_usage() -> str:
    return (
        "usage: build_code_graph.py --root ABSOLUTE_PATH [--full] [--lsp] "
        "[--lsp-timeout SECONDS] [--exclude-dir NAME ...]\n"
        "Writes ROOT/.wingbox/index.sexp and the files that go with it."
    )


def parse_directory_basename(value: str) -> Result[str, str]:
    """Validate one portable directory basename from the public CLI."""
    if (
        not value
        or value in (".", "..")
        or "\0" in value
        or "/" in value
        or "\\" in value
        or bool(PureWindowsPath(value).drive)
    ):
        return Err(f"--exclude-dir must be a directory name, not a path: {value}")
    return Ok(value)


def parse_builder_options(args: list[str]) -> Result[BuilderOptions, str]:
    """Validate builder CLI inputs before filesystem work starts."""
    root_argument: Optional[str] = None
    full_rebuild = False
    use_lsp = False
    lsp_timeout = LSP_REQUEST_TIMEOUT
    excluded_directories: set[str] = set()

    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--output":
            return Err("--output is not supported: the graph is always ROOT/.wingbox/index.sexp")
        if argument in ("--root", "--lsp-timeout", "--exclude-dir"):
            if index + 1 >= len(args):
                return Err(f"{argument} requires a value")
            value = args[index + 1]
            if argument == "--root":
                root_argument = value
            elif argument == "--exclude-dir":
                parsed_basename = parse_directory_basename(value)
                if isinstance(parsed_basename, Err):
                    return parsed_basename
                excluded_directories.add(parsed_basename.value)
            else:
                try:
                    lsp_timeout = float(value)
                except ValueError:
                    return Err(f"invalid --lsp-timeout value: {value}")
            index += 2
            continue
        if argument == "--full":
            full_rebuild = True
            index += 1
            continue
        if argument == "--lsp":
            use_lsp = True
            index += 1
            continue
        return Err(f"unknown argument: {argument}")

    if root_argument is None:
        return Err("--root is required")
    if not os.path.isabs(root_argument):
        return Err("--root must be an absolute path")
    root = os.path.realpath(root_argument)
    if not os.path.isdir(root):
        return Err(f"{root} is not a directory")

    if not math.isfinite(lsp_timeout) or lsp_timeout <= 0:
        return Err("--lsp-timeout must be a positive finite number")

    return Ok(
        BuilderOptions(
            root=root,
            output_path=index_path(root),
            full_rebuild=full_rebuild,
            use_lsp=use_lsp,
            lsp_timeout=lsp_timeout,
            skip_directories=frozenset(SKIP_DIRS | excluded_directories),
        )
    )


def _cache_entry_result(path: str, entry: object) -> Result[FileResult, str]:
    if not is_cache_entry_current(path, entry):
        return Err("content hash changed or cache entry is invalid")
    return decode_cache_entry(entry)


def _serialize_file_result(result: FileResult) -> dict:
    return {
        "mtime": result.mtime,
        "result": {
            "path": result.path,
            "language": result.language,
            "repo": result.repo,
            "mtime": result.mtime,
            "content_hash": result.content_hash,
            "imports": result.imports,
            "exports_abstract": result.exports_abstract,
            "implements": result.implements,
            "extends": result.extends,
            "purpose": result.purpose,
            "naming_convention": result.naming_convention,
            "has_validation": result.has_validation,
            "entities": result.entities,
            "imported_names": result.imported_names,
            "exported_names": result.exported_names,
            "relations": result.relations,
            "import_forms": result.import_forms,
            "bindings": result.bindings,
            "uses": result.uses,
            "module_loads": result.module_loads,
            "registry_calls": result.registry_calls,
            "validation_evidence": result.validation_evidence,
        },
    }


def _run_build(options: BuilderOptions) -> int:
    root = options.root
    t0 = time.monotonic()
    mode = "LSP-enhanced" if options.use_lsp else "syntax"
    print(f"[code-graph] Scanning {root} ... (mode: {mode})")

    git_ignore_result = load_git_ignored_paths(root, options.skip_directories)
    if isinstance(git_ignore_result, GitIgnoreUnavailable):
        print(f"[code-graph] {_ignore_note(git_ignore_result.reason)}", file=sys.stderr)
        ignored_paths = frozenset()
    else:
        ignored_paths = git_ignore_result.paths
        if ignored_paths:
            print(f"[code-graph] Git ignore rules excluded {len(ignored_paths)} paths")
    exclusions = DiscoveryExclusions(
        directory_names=options.skip_directories,
        paths=ignored_paths,
    )

    prepare_wingbox_directory(root)
    snapshot = TreeSnapshot() if options.snapshot_path else None
    files = discover_files(root, exclusions, snapshot)
    language_counts = {
        language.name: sum(
            1 for _, language_name, _ in files if language_name == language.name
        )
        for language in LANGUAGES
    }
    count_summary = ", ".join(
        f"{language_counts[language.name]} {language.cli_label}"
        for language in LANGUAGES
    )
    extensions = ", ".join(
        extension for language in LANGUAGES for extension in language.source_extensions
    )
    print(f"[code-graph] Found {len(files)} source files ({count_summary})")
    print(f"[code-graph] Supported source: {extensions}")

    active_language_names = frozenset(language_name for _, language_name, _ in files)
    dependency_issues = _dependency_issues(active_language_names)
    skipped = tuple(issue for issue in dependency_issues if issue.language.name in OPTIONAL_LANGUAGE_EXTRAS)
    # Skip such a language only when other source files remain to map; a
    # project of that language alone stops with the install command below.
    skipped_names = frozenset(issue.language.name for issue in skipped)
    skipped_counts: list[tuple[str, int]] = []
    if skipped and any(item[1] not in skipped_names for item in files):
        _report_skipped_optional_languages(skipped, language_counts)
        skipped_counts = [(issue.language.name, language_counts[issue.language.name]) for issue in skipped]
        files = [item for item in files if item[1] not in skipped_names]
        active_language_names = active_language_names - skipped_names
        dependency_issues = tuple(issue for issue in dependency_issues if issue not in skipped)
    if dependency_issues:
        _report_dependency_issues(dependency_issues)
        return 2

    cache = (
        {}
        if options.full_rebuild
        else load_cache(root, active_language_names, exclusions)
    )
    to_parse: list[tuple[str, ...]] = []
    cached_results: list[FileResult] = []

    for path, lang, repo in files:
        entry = cache.get(path)
        if entry and not options.full_rebuild:
            cached_result = _cache_entry_result(path, entry)
            if isinstance(cached_result, Ok):
                cached_results.append(cached_result.value)
                continue
            print(
                f"[code-graph] WARNING: rebuilding invalid cache entry for {path}: {cached_result.error}",
                file=sys.stderr,
            )
        to_parse.append((path, lang, repo, root))

    print(
        f"[code-graph] Parsing {len(to_parse)} changed files ({len(cached_results)} cached)"
    )

    new_results: list[FileResult] = []
    if to_parse:
        new_results = parse_files(to_parse)

    all_results = sorted(
        cached_results + new_results,
        key=lambda result: os.path.relpath(result.path, root).replace(os.sep, "/"),
    )
    symbol_key = symbol_index_key(all_results, _builder_fingerprint(active_language_names, exclusions), mode)
    # A fragment store that does not match these inputs is rewritten, which needs the uses resolved.
    stale_store = bool(options.fragments_path) and fragments_key(options.fragments_path or "") != symbol_key
    reused = (
        None
        if options.full_rebuild or stale_store
        else reusable_symbol_uses(uses_path(options.output_path), symbol_key)
    )
    if options.full_rebuild:
        # Import lookups and resolved uses of the walk cache may depend on tsconfig, package or Cargo
        # settings that are not part of its key: a full build starts it afresh.
        _remove_walk_cache(uses_cache_path(options.output_path))
    graph_result = build_graph(all_results, root, exclusions, reused, jobs=portable.worker_count())
    if isinstance(graph_result, Err):
        for collision in graph_result.error.collisions:
            paths = ", ".join(collision.paths)
            print(
                f"[code-graph] ERROR: module ID collision {collision.node_id}: {paths}",
                file=sys.stderr,
            )
        print(
            "[code-graph] ERROR: no cache or map was published",
            file=sys.stderr,
        )
        return 1
    nodes = graph_result.value.nodes
    edges = graph_result.value.edges

    syntax_elapsed = time.monotonic() - t0
    print(
        f"[code-graph] Syntax pass: {syntax_elapsed:.2f}s -- {len(nodes)} nodes, {len(edges)} edges"
    )

    # LSP enrichment pass (if requested)
    if options.use_lsp:
        t_lsp_start = time.monotonic()
        print(
            f"[code-graph] Starting LSP enrichment (timeout={options.lsp_timeout}s per request) ..."
        )
        nodes, edges = enrich_with_lsp(
            root,
            all_results,
            nodes,
            edges,
            options.lsp_timeout,
            exclusions,
        )
        t_lsp = time.monotonic() - t_lsp_start
        print(f"[code-graph] LSP pass: {t_lsp:.2f}s")

    built = project_architecture(root, exclusions, all_results, nodes)
    if isinstance(built, Err):
        print(f"[code-graph] ERROR: {built.error}", file=sys.stderr)
        return 2
    architecture, plan = built.value
    print(
        "[code-graph] Architecture: .wingbox/config.yaml" if any(p.origin == "config" for p in plan.parts)
        else f"[code-graph] Architecture: detected ({len(plan.parts)} parts); `wingbox init` writes it to .wingbox/config.yaml"
    )
    new_cache = {result.path: _serialize_file_result(result) for result in all_results}
    if options.keep_published and not nodes and _published_modules(options.output_path):
        print(
            f"[code-graph] ERROR: no source files under {root}; the published graph is kept",
            file=sys.stderr,
        )
        return 1
    map_content = render_sexp(nodes, edges, root, mode, len(all_results), architecture)
    uses_content = (
        render_uses_sexp(nodes, symbol_key, map_content) if reused is None else reused_uses_text(reused, map_content)
    )
    save_cache(
        root,
        new_cache,
        active_language_names,
        exclusions,
    )
    _atomic_write_text(options.output_path, map_content)
    _atomic_write_text(uses_path(options.output_path), uses_content)
    publish_layer_guesses(root, nodes, again=options.full_rebuild)
    _publish_skipped(root, skipped_counts)
    if options.fragments_path and graph_result.value.fragments and not options.use_lsp:
        write_fragments(options.fragments_path, symbol_key, graph_result.value.fragments, root, exclusions)
    if options.snapshot_path and snapshot is not None:
        record_loader_configs(root, snapshot, all_results)
        os.makedirs(os.path.dirname(options.snapshot_path), exist_ok=True)
        _atomic_write_text(options.snapshot_path, snapshot_json(snapshot, options.skip_directories, root))

    elapsed = time.monotonic() - t0
    print(
        f"[code-graph] Done in {elapsed:.2f}s -- {len(nodes)} nodes, {len(edges)} edges"
    )
    print(f"[code-graph] Output: {options.output_path}")

    # Stats
    repos = sorted(set(n["repo"] for n in nodes))
    print(f"[code-graph] Repos: {', '.join(repos)}")

    from collections import Counter

    edge_types = Counter(e["type"] for e in edges)
    total_entities = sum(len(n.get("entities", [])) for n in nodes)
    lsp_verified = sum(1 for e in edges if e.get("lsp_verified"))
    print(f"[code-graph] Entities: {total_entities}")
    for etype, count in sorted(edge_types.items()):
        print(f"[code-graph]   {etype}: {count}")
    if lsp_verified:
        print(f"[code-graph]   LSP-verified: {lsp_verified}")
    return 0


def parse_files(to_parse: list[tuple[str, ...]]) -> list[FileResult]:
    """Parse files in worker processes (the start method of portable.start_method); in this process
    when a pool cannot start (a sandbox without semaphores, a system without processes)."""
    workers = portable.worker_count(len(to_parse))
    if workers > 1:
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context(portable.start_method())) as pool:
                futures = {pool.submit(parse_file, item): item for item in to_parse}
                return [future.result() for future in as_completed(futures)]
        except (OSError, BrokenProcessPool) as error:
            print(f"[code-graph] WARNING: parallel parsing failed ({error}); parsing in one process", file=sys.stderr)
    return [parse_file(item) for item in to_parse]


def _published_modules(path: str) -> bool:
    """Whether the graph at path has at least one module."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return any(line.startswith("(node ") for line in handle)
    except OSError:
        return False
def project_architecture(
    root: str,
    exclusions: DiscoveryExclusions,
    results: Sequence[FileResult],
    nodes: Sequence[dict],
) -> Result[tuple[GraphArchitecture, RepositoryPlan], str]:
    """The architecture facts of a graph (parts, layers, known violations) and the plan they come from.

    A build and a partial update both call it, so the published graph is the same either way.
    """
    plan_paths = list_files(root, exclusions.directory_names, exclusions.paths)
    plan = architecture_plan(root, exclusions.directory_names, exclusions.paths, plan_paths)
    if isinstance(plan, Err):
        return plan
    names_by_path = {
        os.path.relpath(result.path, root): frozenset(
            f"{item.get('module', '')}:{item.get('original', '')}"
            for item in result.imported_names if isinstance(item, dict)
        )
        for result in results
    }
    baseline_text = text_reader(root)(BASELINE_FILE) or ""
    architecture = graph_architecture(
        plan.value, load_library(), {node["id"]: node["path"] for node in nodes if node.get("path")},
        {node["id"]: names_by_path.get(node["path"], frozenset()) for node in nodes if node.get("path")},
        plan_paths,
        read_baseline_sexp(baseline_text),
        _node_capabilities(root, nodes, {os.path.relpath(r.path, root).replace(os.sep, "/"): r.content_hash for r in results}),
        read_capability_baseline(baseline_text),
        read_baseline_terms(baseline_text),
    )
    return Ok((architecture, plan.value))


CAPABILITIES_CACHE_FILE = "cache.capabilities.json"   # in .wingbox/, ignored with the other caches


def _node_capabilities(root: str, nodes: Sequence[dict], hashes: Mapping[str, str]) -> dict[str, tuple]:
    """What the file of each node does (SQL, database client, outbound HTTP, ...), found by syntax.

    Findings are cached by content hash in .wingbox/cache.capabilities.json, so a rebuild or a partial
    update reads again only the files that changed; a change of the detector empties the cache.
    """
    detector = _file_digest(detect_capabilities.__code__.co_filename)
    path = os.path.join(wingbox_directory(root), CAPABILITIES_CACHE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        cached = stored.get("files", {}) if isinstance(stored, dict) and stored.get("detector") == detector else {}
    except (OSError, ValueError):
        cached = {}
    found: dict[str, tuple] = {}
    entries: dict[str, list] = {}
    changed = False
    for node in nodes:
        relative = node.get("path")
        if not relative or not capability_language(relative):
            continue
        digest = hashes.get(relative, "")
        entry = cached.get(relative)
        if digest and isinstance(entry, list) and len(entry) == 2 and entry[0] == digest:
            rows = entry[1]
        else:
            try:
                with open(os.path.join(root, relative), "r", encoding=SOURCE_ENCODING, errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            rows = [[f.capability, f.line, f.evidence, f.suspect] for f in detect_capabilities(relative, text)]
            changed = True
        entries[relative] = [digest, rows]
        if rows:
            found[node["id"]] = tuple(_finding(*row) for row in rows)
    if changed or set(entries) != set(cached):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _atomic_write_text(path, json.dumps({"detector": detector, "files": entries}, separators=(",", ":")) + "\n")
        except OSError as error:
            print(f"[code-graph] WARNING: capability cache not written: {error}", file=sys.stderr)
    return found


def _file_digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:16]


LAYER_GUESS_FILE = layer_guess.FILE_NAME   # in .wingbox/, ignored with the other caches


def publish_layer_guesses(root: str, nodes: Sequence[Mapping[str, Any]], again: bool = False) -> None:
    """Store, with a graph that is being published, the layer each distinct purpose of its modules suggests
    (.wingbox/cache.layer-guess.json), so that a query reads the guess instead of matching words.

    The guess is a pure function of the purpose (layer_guess.classify) and the file carries the key of that
    function: what a file with the current key already holds is kept unless `again` is set (a full build),
    so a rebuild or a partial update matches only the purposes that are new. A query that does not find a
    purpose here works it out itself, so a file that could not be written costs time, never an answer.
    """
    path = os.path.join(wingbox_directory(root), LAYER_GUESS_FILE)
    try:
        current = portable.read_text(path)
    except (OSError, ValueError):   # unreadable, or bytes that are not UTF-8: written anew
        current = None
    known = layer_guess.parse(current) if current is not None and not again else {}
    content = layer_guess.serialise(layer_guess.guesses((node.get("purpose", "") for node in nodes), known))
    if content == current:
        return
    try:
        _atomic_write_text(path, content)
    except OSError as error:
        print(f"[code-graph] WARNING: layer guess cache not written: {error}", file=sys.stderr)


def architecture_plan(
    root: str, skip_directories: frozenset[str], ignored_paths: frozenset[str],
    listed: Optional[tuple[str, ...]] = None,
) -> Result[RepositoryPlan, str]:
    """The parts and styles of the repository: .wingbox/config.yaml when present, else detection."""
    library = load_library()
    paths = listed if listed is not None else list_files(root, skip_directories, ignored_paths)
    read = text_reader(root)
    config = read_config(read, library)
    if isinstance(config, ConfigErr):
        return Err(config.error)
    if config is not None and config.note:
        print(f"[wingbox] note: {config.note}", file=sys.stderr)
    learned = learned_layers(read_store(read(STORE_FILE) or ""))
    if config is None:
        plan = detect_plan(paths, read, library)
        return Ok(apply_learned(plan, learned))
    return Ok(apply_learned(plan_from_config(config, paths, read, library), learned, from_config=True))


def _ignore_note(reason: str) -> str:
    """One plain line when git gives no ignore rules (outside a git repository: expected, not a fault)."""
    if "not a git repository" in reason:
        return "not a git repository: .gitignore rules are not applied"
    return f"WARNING: repository ignore rules unavailable: {reason}"


def _lock_path(root: str) -> str:
    """The build lock of the project at root, in its .wingbox/ folder: Wingbox writes nowhere else in a project."""
    return os.path.join(wingbox_directory(root), "build.lock")


FRAGMENTS_FILE = "graph-fragments.json"
SNAPSHOT_FILE = "graph-snapshot.json"


def freshness_files(options: BuilderOptions) -> tuple[Optional[str], Optional[str]]:
    """(fragment store, snapshot) that `wingbox` keeps the graph fresh with.

    The fragment store lets a later change be patched in (scripts/graph_update.py); an LSP build has none.
    """
    folder = wingbox_directory(options.root)
    return (None if options.use_lsp else os.path.join(folder, FRAGMENTS_FILE)), os.path.join(folder, SNAPSHOT_FILE)


def main(args: Optional[list[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if args is None else args)
    if raw_args in (["-h"], ["--help"]):
        print(_builder_usage())
        return 0
    parsed_options = parse_builder_options(raw_args)
    if isinstance(parsed_options, Err):
        print(f"Error: {parsed_options.error}", file=sys.stderr)
        print(_builder_usage(), file=sys.stderr)
        return 2

    fragments_path, snapshot_path = freshness_files(parsed_options.value)
    options = dataclasses.replace(
        parsed_options.value, fragments_path=fragments_path, snapshot_path=snapshot_path
    )
    lock_path = _lock_path(options.root)
    try:
        prepare_wingbox_directory(options.root)
        lock_file = open(lock_path, "a+", encoding="utf-8")
    except OSError as error:
        print(
            f"[code-graph] ERROR: could not open build lock {lock_path}: {error}",
            file=sys.stderr,
        )
        return 1

    try:
        if not portable.lock(lock_file, blocking=False):
            print(
                f"[code-graph] ERROR: another build is active for {options.root}",
                file=sys.stderr,
            )
            return 1
        return _run_build(options)
    except OSError as error:
        print(f"[code-graph] ERROR: build failed: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            portable.unlock(lock_file)
        except OSError as error:
            print(
                f"[code-graph] ERROR: could not release build lock: {error}",
                file=sys.stderr,
            )
        lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
