#!/usr/bin/env python3
"""Dart/Flutter graph accuracy check against the import statements of the code.

Usage::

    python3 benchmarks/dart/check_accuracy.py /path/to/flutter/app
    python3 benchmarks/dart/check_accuracy.py /path/to/flutter/app --sample 40 --seed 20261117

The script builds the code graph of a real Dart or Flutter project with
``scripts/build_code_graph.py`` (in a subprocess, as a user runs it), draws a
seeded random sample of ``.dart`` files under ``lib/``, and compares, for each
sampled file, the files the graph puts in its reverse dependency set with the
files on disk that name it by an import specifier:

* ``import 'package:<name>/<path>'`` or ``export 'package:<name>/<path>'``,
  where ``<name>`` is the package name of the enclosing ``pubspec.yaml``. A
  project that hosts several packages resolves each name in its own directory.
* ``import '<relative path>'`` or ``export '<relative path>'``, resolved
  against the importing file's directory after ``./`` and ``../`` are dropped:
  a bare path is never taken from the project root, because in a package file
  that does not name ``lib/<path>``.
* ``part '<path>'`` of a library names that part and ``part of '<path>'`` of a
  part names its library: each is one dependent of the other.

Nothing is added by transitivity. A file that imports a part names the part and
is that part's dependent only; it is not counted as a dependent of the library
that holds the part, because it never names the library.

Ground truth is recomputed from the files on disk on every run; nothing is
hardcoded and nothing is committed.

The graph's reverse dependents of a file are its incoming ``imports`` edges,
the edges a query command (``wingbox impact``) follows. Two refinements keep
the comparison honest:

* A library and one of its parts name each other, one directive in each file,
  and the pair is one dependency: the link is counted whichever way the
  ``imports`` edge runs, and is never an extra dependent.
* A dependent whose edge no import statement backs -- a file that only
  extends a class of the target -- is listed as ``extra`` with its reason
  (``inheritance-only``) and is not counted as a wrong dependent of the import
  comparison. It is reported, not hidden.

The exit status is 1 when the aggregate recall falls below ``--min-recall``
(default 0.99), when the graph misses a single importer pair, or when the
build fails. Precision and recall are printed for every sampled file and in
aggregate: as text by default, with ``--sexp`` in the graph's own format and
with ``--json <path>`` for a machine.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_code_graph.py"
SEARCH_SCRIPT = REPO_ROOT / "scripts" / "code_graph_search.py"

DEFAULT_SAMPLE = 40
DEFAULT_SEED = 20261117
DEFAULT_MIN_RECALL = 0.99
BUILD_TIMEOUT_SECONDS = 900

# Edge kinds a query command treats as "depends on": imports, implements/extends an
# abstract type, and data flow. An inheritance edge that no import backs is classified
# as such, so that it is reported and not silently counted as an import.
DEPENDENT_EDGE_KINDS = ("imports", "calls-api", "data-flow")
GENERATED_DIRECTORIES = frozenset({".git", ".dart_tool", "build", "node_modules", ".venv"})
# Platform folders of a Flutter project hold native sources, never Dart files.
PLATFORM_DIRECTORIES = frozenset({"ios", "android", "macos", "windows", "linux"})

# import 'x'; export 'x'; part 'x'; and part of 'x' -- the `of` clause belongs to the part.
_DIRECTIVE = re.compile(r"\b(import|export|part)(?:\s+of)?\s+(['\"])([^'\"]+)\2")
_PART_OF_URI = re.compile(r"\bpart\s+of\s+(['\"])([^'\"]+)\1")
_PUBSPEC_NAME = re.compile(r"^name:\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*(?:#.*)?$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Package:
    """One pub package: the name of its pubspec.yaml and the directory that owns it."""

    name: str
    directory: str  # absolute, normalized


@dataclass(frozen=True)
class Specifier:
    """One import, export or part directive as written in a file."""

    kind: str  # import, export or part
    uri: str
    resolved: str | None  # absolute path of the named file, None when it names no project file


@dataclass
class FileScore:
    """The comparison of one sampled file."""

    file: str  # path relative to the project root, with forward slashes
    node: str  # module ID in the graph
    present: bool
    expected: set[str] = field(default_factory=set)  # counted importer pairs on disk
    found: set[str] = field(default_factory=set)  # counted dependent pairs in the graph
    extra: set[str] = field(default_factory=set)  # graph facts with no import statement
    missing: set[str] = field(default_factory=set)  # importers on disk that the graph lost
    extra_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        if not self.found:
            return 1.0
        return (len(self.found) - len(self.extra)) / len(self.found)

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return (len(self.expected) - len(self.missing)) / len(self.expected)


@dataclass
class Report:
    """The result of one run, for every sampled file and in aggregate."""

    root: str
    graph: str
    seed: int
    packages: list[str]
    lib_files: int
    modules: int
    build_seconds: float
    files: list[FileScore] = field(default_factory=list)

    @property
    def expected_pairs(self) -> int:
        return sum(len(score.expected) for score in self.files)

    @property
    def found_pairs(self) -> int:
        return sum(len(score.found) for score in self.files)

    @property
    def right_pairs(self) -> int:
        return sum(len(score.expected) - len(score.missing) for score in self.files)

    @property
    def missing_pairs(self) -> int:
        return sum(len(score.missing) for score in self.files)

    @property
    def extra_pairs(self) -> int:
        return sum(len(score.extra) for score in self.files)

    @property
    def recall(self) -> float:
        return 1.0 if self.expected_pairs == 0 else self.right_pairs / self.expected_pairs

    @property
    def precision(self) -> float:
        correct = self.found_pairs - self.extra_pairs
        return 1.0 if self.found_pairs == 0 else correct / self.found_pairs

    @property
    def absent_nodes(self) -> list[str]:
        return [score.file for score in self.files if not score.present]

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "graph": self.graph,
            "seed": self.seed,
            "build_seconds": round(self.build_seconds, 2),
            "packages": self.packages,
            "lib_files": self.lib_files,
            "modules": self.modules,
            "aggregate": {
                "sampled": len(self.files),
                "expected_pairs": self.expected_pairs,
                "found_pairs": self.found_pairs,
                "right_pairs": self.right_pairs,
                "missing_pairs": self.missing_pairs,
                "extra_pairs": self.extra_pairs,
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "inheritance_only_edges": sum(
                    1 for score in self.files for reason in score.extra_reasons.values()
                    if reason == "inheritance-only"
                ),
                "absent_nodes": self.absent_nodes,
            },
            "files": [
                {
                    "file": score.file,
                    "node": score.node,
                    "module_in_graph": score.present,
                    "expected_pairs": len(score.expected),
                    "found_pairs": len(score.found),
                    "missing": sorted(score.missing),
                    "extra": sorted(score.extra),
                    "extra_reasons": dict(sorted(score.extra_reasons.items())),
                    "precision": round(score.precision, 4),
                    "recall": round(score.recall, 4),
                }
                for score in self.files
            ],
        }


# ---------------------------------------------------------------------------
# Ground truth from the files on disk
# ---------------------------------------------------------------------------


def strip_dart_comments(text: str) -> str:
    """Remove comments, leaving string literals (and a ``//`` inside a URL) untouched."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in "'\"":
            quote = char * 3 if text.startswith(char * 3, index) else char
            end = index + len(quote)
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text.startswith(quote, end):
                    end += len(quote)
                    break
                end += 1
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def read_pubspec_name(pubspec: Path) -> str | None:
    """The ``name`` of a pubspec.yaml, read without a YAML parser."""
    try:
        text = pubspec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _PUBSPEC_NAME.search(text)
    return match.group(1) if match else None


def discover_packages(root: str) -> list[Package]:
    """Every pub package of the project; the longest directory owns a path."""
    packages: list[Package] = []
    for directory, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(name for name in directory_names if name not in GENERATED_DIRECTORIES)
        if "pubspec.yaml" not in file_names:
            continue
        name = read_pubspec_name(Path(directory) / "pubspec.yaml")
        if name is not None:
            packages.append(Package(name=name, directory=os.path.normpath(directory)))
    packages.sort(key=lambda package: (len(package.directory), package.directory))
    return packages


def project_dart_files(root: str) -> list[str]:
    """Every ``.dart`` file of the project, without generated output, sorted."""
    found: list[str] = []
    for directory, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in GENERATED_DIRECTORIES and name not in PLATFORM_DIRECTORIES
        )
        for file_name in sorted(file_names):
            if file_name.endswith(".dart"):
                found.append(os.path.normpath(os.path.join(directory, file_name)))
    return found


def lib_dart_files(files: Sequence[str], root: str) -> list[str]:
    """The files under a ``lib/`` directory of the project."""
    lib = os.path.normpath(os.path.join(root, "lib")) + os.sep
    return [path for path in files if path.startswith(lib)]


def resolve_specifier(
    uri: str,
    importer: str,
    packages: Sequence[Package],
    files: set[str],
) -> str | None:
    """The file a specifier names, or None for ``dart:``, an outside package or a missing file."""
    uri = uri.strip()
    if uri.startswith("dart:"):
        return None
    if uri.startswith("package:"):
        package_name, _, relative = uri[len("package:") :].partition("/")
        if not relative:
            return None
        owners = [package for package in packages if package.name == package_name]
        for package in reversed(owners):  # a nested package before the root one
            candidate = os.path.normpath(os.path.join(package.directory, "lib", relative))
            if candidate in files:
                return candidate
        return None
    if ":" in uri.split("/", 1)[0]:  # file:, http:, an asset: no project file
        return None
    relative = uri
    while relative.startswith("./"):
        relative = relative[2:]
    candidate = os.path.normpath(os.path.join(os.path.dirname(importer), relative))
    return candidate if candidate in files else None


def parse_specifiers(
    text: str,
    importer: str,
    packages: Sequence[Package],
    files: set[str],
) -> list[Specifier]:
    """Every import, export and part directive of one file, resolved."""
    return [
        Specifier(
            kind=match.group(1),
            uri=match.group(3),
            resolved=resolve_specifier(match.group(3), importer, packages, files),
        )
        for match in _DIRECTIVE.finditer(strip_dart_comments(text))
    ]


def part_of_uri(text: str) -> str | None:
    """The library named by ``part of 'lib.dart'``, or None when the file is no part."""
    match = _PART_OF_URI.search(strip_dart_comments(text))
    return match.group(2) if match else None


def read_project_exports(
    root: str,
    packages: Sequence[Package],
    files: Sequence[str],
) -> dict[str, str]:
    """The library file each part file belongs to, by absolute path.

    Both directions are read: a part file's ``part of`` directive and a
    library's ``part`` directives, so that a part file without the directive
    still groups with its library.
    """
    on_disk = set(files)
    belongs: dict[str, str] = {}
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        named = part_of_uri(text)
        if named is None:
            continue
        resolved = resolve_specifier(named, path, packages, on_disk)
        if resolved is not None:
            belongs[path] = resolved
        for directive in parse_specifiers(text, path, packages, on_disk):
            if directive.kind == "part" and directive.resolved is not None:
                belongs[directive.resolved] = path
    return belongs


def load_search_module() -> ModuleType:
    """Load scripts/code_graph_search.py by path: the scripts are called by path, not installed."""
    if not SEARCH_SCRIPT.is_file():
        raise SystemExit(f"missing {SEARCH_SCRIPT}: run from a Wingbox checkout")
    spec = importlib.util.spec_from_file_location("dart_accuracy_search", SEARCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {SEARCH_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class GraphFacts:
    """What the check needs from a built graph, by absolute file path."""

    modules: dict[str, str]  # file -> module ID
    dependents: dict[str, dict[str, str]]  # target -> source -> edge kind
    graph_path: str


def _serial_pool_fallback() -> str | None:
    """The path of a small runner that patches the builder's process pool, or None.

    The builder parses files in a pool of processes, which needs OS semaphores
    (POSIX named semaphores or Windows named objects). A container that mounts
    ``/dev/shm`` without write access, or a macOS process without the
    ``com.apple.security.posix-semaphores`` entitlement, refuses them: the
    builder exits 1 with ``[Errno 13] Permission denied`` before it parses a
    single file, on every language. A check that then reports "the adapter is
    missing" would accuse the wrong code, so it falls back to a pool that runs
    the same work in this process and prints that it did. On a normal host the
    build is the plain ``python scripts/build_code_graph.py``.
    """
    try:
        multiprocessing.get_context().Lock()
    except (OSError, ValueError):
        pass
    else:
        return None
    runner = """
import importlib.util, sys
from concurrent.futures import Future

builder_path = sys.argv[1]
builder_arguments = sys.argv[2:]
spec = importlib.util.spec_from_file_location("wingbox_builder_serial", builder_path)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


class SerialPool:
    def __init__(self, *args, **keywords):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def submit(self, function, *args, **keywords):
        future = Future()
        try:
            future.set_result(function(*args, **keywords))
        except BaseException as error:
            future.set_exception(error)
        return future


builder.ProcessPoolExecutor = SerialPool
print("(serial parse: no OS semaphores on this host)", file=sys.stderr)
sys.exit(builder.main(builder_arguments))
"""
    directory = tempfile.mkdtemp(prefix="wingbox-dart-serial-")
    path = os.path.join(directory, "serial_build.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(runner)
    return path


def run_builder(
    root: str,
    python: str | None = None,
    output: str | None = None,
    full: bool = False,
    allow_serial: bool = True,
) -> tuple[str, float]:
    """Build the graph in a subprocess, as a user does; returns its path and the wall time.

    ``allow_serial`` keeps the check usable on a host without OS semaphores (see
    ``_serial_pool_fallback``); pass False to require the unmodified builder.
    """
    root = os.path.realpath(root)
    graph_path = output or os.path.join(root, ".wingbox", "index.sexp")
    interpreter = python or sys.executable
    builder_arguments = ["--root", root, *(["--full"] if full else [])]
    attempts = [[interpreter, str(BUILD_SCRIPT), *builder_arguments]]
    if allow_serial:
        serial_runner = _serial_pool_fallback()
        if serial_runner is not None:
            # The runner takes the builder's own arguments after its two first ones.
            attempts.insert(0, [interpreter, serial_runner, str(BUILD_SCRIPT), *builder_arguments])
    started = time.monotonic()
    completed = None
    for command in attempts:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"error: the build did not finish in {BUILD_TIMEOUT_SECONDS} s")
        semaphores_refused = (
            "Permission denied" in completed.stderr
            and completed.returncode != 0
            and command[1] == str(BUILD_SCRIPT)
        )
        if not semaphores_refused:
            break
    seconds = time.monotonic() - started
    assert completed is not None
    if completed.returncode != 0:
        raise SystemExit(
            f"error: the build failed with exit status {completed.returncode}\n"
            f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}"
        )
    return graph_path, seconds


def _from_node_id(node_id: str, root: str) -> str:
    """The absolute file path a module ID names (a module ID is the path under the root)."""
    return os.path.normpath(os.path.join(root, node_id.split("::")[0]))


def read_graph(graph_path: str, root: str) -> GraphFacts:
    """The import-level facts of a built graph, by file path.

    A module ID is the path of its file without ``.dart``; a fact names the two
    sides by module, so each side is turned back into the file it names before
    anything is compared with the files on disk.
    """
    search = load_search_module()
    graph = search.CodeGraph()
    loaded = graph.load(graph_path, root)
    if not isinstance(loaded, search.MapLoadOk):
        raise SystemExit(f"error: cannot load {graph_path}: {loaded.error}")

    def file_of(node_id: str) -> str | None:
        path = _from_node_id(node_id, root)
        for candidate in (path + ".dart", os.path.join(path, "index.dart"), path):
            if os.path.isfile(candidate):
                return candidate
        return None  # not a file of the project: a module ID, a node the disk does not hold

    modules: dict[str, str] = {}
    for node_id in graph.nodes:
        path = file_of(node_id)
        if path is not None:
            modules[path] = node_id
    dependents: dict[str, dict[str, str]] = {}
    for edge in graph.all_edges:
        if edge.etype not in DEPENDENT_EDGE_KINDS:
            continue
        source, target = file_of(edge.source), file_of(edge.target)
        # Only a file can be an importer: a fact about a configuration file (a
        # pubspec.yaml, a package.json) has no import statement to compare, so it is
        # left out instead of being reported as an extra dependent.
        if source is None or target is None or source == target:
            continue
        kinds = dependents.setdefault(target, {})
        # An imports edge wins over another kind for the same pair.
        kinds[source] = edge.etype if edge.etype == "imports" or source not in kinds else kinds[source]
    return GraphFacts(modules=modules, dependents=dependents, graph_path=graph_path)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _same_file(path: str | None, others: set[str]) -> bool:
    """Whether a resolved path names one of the others, ignoring a symlinked directory.

    A directory under /tmp is often a symlink on a development machine; a graph
    built from the realpath and a walk of the symlinked path name the same file
    with different absolute paths.
    """
    if path is None:
        return False
    if path in others:
        return True
    real = os.path.realpath(path)
    return any(real == os.path.realpath(other) for other in others)


def part_link(source: str, target: str, belongs: dict[str, str]) -> bool:
    """Whether two files of the project are linked by a ``part`` directive, in either direction.

    A library names its parts (``part 'x.dart'``) and a part names its library
    (``part of 'lib.dart'``). The adapter records both directions as an imports
    edge, because a change to either file can affect the other; the check
    counts the pair once and accepts it whichever way the edge runs.
    """
    return belongs.get(source) == target or belongs.get(target) == source


def score_file(
    target: str,
    root: str,
    graph: GraphFacts,
    specifiers: dict[str, list[Specifier]],
    belongs: dict[str, str],
) -> FileScore:
    """Compare the reverse dependents of one file in the graph with the importers on disk.

    ``expected`` holds the files whose own directive names the target and
    ``found`` the graph's dependent edges. Nothing is closed over the ``part``
    relation: an importer of a part is a dependent of the part, not of the
    library. ``part`` links are legitimate edges in either direction and never
    extras; a dependent whose edge no statement backs is one, with its reason.
    """
    relative = _relative(target, root)
    node = graph.modules.get(target) or (relative[: -len(".dart")] if relative.endswith(".dart") else relative)
    score = FileScore(file=relative, node=node, present=target in graph.modules)
    def names(directive: Specifier, kinds: tuple[str, ...]) -> bool:
        return directive.kind in kinds and _same_file(directive.resolved, {target})

    for source, directives in specifiers.items():
        # The files whose own directive names this file: an import, an export, a part
        # directive of the library or the part of directive of the part.
        if source != target and any(names(item, ("import", "export", "part")) for item in directives):
            score.expected.add(_relative(source, root))

    def file_of(name: str) -> str:
        """Both sides by file path: a graph fact names a module ID, a disk reader a file."""
        path = os.path.join(root, name)
        if path in specifiers:
            return path
        for candidate in (path + ".dart", os.path.join(path, "index.dart")):
            if candidate in specifiers:
                return candidate
        return path

    for name, kind in sorted(graph.dependents.get(target, {}).items()):
        source_path = file_of(name)
        source = _relative(source_path, root)
        score.found.add(source)
        if source in score.expected:
            continue
        if part_link(source_path, target, belongs):
            continue  # one edge of a library and its part, named in either direction
        directives = specifiers.get(source_path, [])
        if any(names(item, ("import", "export", "part")) for item in directives):
            # An exotic spelling the disk reader did not resolve but the adapter did.
            continue
        score.extra.add(source)
        score.extra_reasons[source] = "imports-edge-without-specifier" if kind == "imports" else "inheritance-only"
    score.missing = set(score.expected) - set(score.found)
    return score


def sample_files(files: Sequence[str], count: int, seed: int) -> list[str]:
    """A seeded random sample of the given files, sorted so the report is stable."""
    ordered = sorted(files)
    if count >= len(ordered):
        return ordered
    return sorted(random.Random(seed).sample(ordered, max(1, count)))


def evaluate(
    root: str,
    sample: int,
    seed: int,
    graph_path: str | None = None,
    python: str | None = None,
    build: bool = True,
) -> Report:
    """Build (unless a graph is given), sample and score one project; importable."""
    root = os.path.realpath(root)
    if graph_path is None:
        graph_path, seconds = run_builder(root, python=python)
    else:
        graph_path, seconds = graph_path, 0.0
    facts = read_graph(graph_path, root)
    packages = discover_packages(root)
    # A directory under /tmp may be a symlink; the builder keys modules by realpath,
    # so the walk below uses the same, or every pair would be compared by another name.
    on_disk = project_dart_files(root)
    files_on_disk = set(on_disk)
    specifiers: dict[str, list[Specifier]] = {}
    for path in on_disk:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        specifiers[path] = parse_specifiers(text, path, packages, files_on_disk)
    belongs = read_project_exports(root, packages, on_disk)
    libs = lib_dart_files(on_disk, root)
    report = Report(
        root=root,
        graph=graph_path,
        seed=seed,
        packages=[package.name for package in packages],
        lib_files=len(libs),
        modules=len(facts.modules),
        build_seconds=seconds,
    )
    for target in sample_files(libs, sample, seed):
        report.files.append(score_file(target, root, facts, specifiers, belongs))
    return report


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def format_text(report: Report, min_recall: float) -> str:
    """The human report: one line per sampled file, then the aggregate."""
    lines = [
        f"Wingbox Dart accuracy: {report.root}",
        f"  packages: {', '.join(report.packages) or '(no pubspec.yaml found)'}",
        f"  lib files: {report.lib_files}; modules in the graph: {report.modules}",
        f"  build: {report.build_seconds:.1f} s; sample: {len(report.files)} of {report.lib_files} files "
        f"(seed {report.seed})",
        "",
        f"  {'file':<46} {'exp':>3} {'found':>5} {'miss':>4} {'extra':>5} {'right':>8}   P     R",
    ]
    for score in report.files:
        lines.append(
            f"  {score.file:<46} {len(score.expected):>3} {len(score.found):>5} "
            f"{len(score.missing):>4} {len(score.extra):>5}  "
            f"{len(score.expected) - len(score.missing):>3}/{len(score.expected):<4}"
            f"{score.precision:.3f} {score.recall:.3f}"
            + ("" if score.present else "  NOT A MODULE OF THE GRAPH")
        )
        for name in sorted(score.missing):
            lines.append(f"      missing importer: {name}")
        for name in sorted(score.extra):
            lines.append(f"      extra dependent:  {name} ({score.extra_reasons.get(name, '?')})")
    lines.extend(
        [
            "",
            f"  aggregate over {len(report.files)} sampled files: {report.right_pairs} of "
            f"{report.expected_pairs} importer pairs found; {report.extra_pairs} extra dependent(s)",
            f"  precision {report.precision:.4f}  recall {report.recall:.4f}  "
            f"(threshold {min_recall:.2f})",
            f"  {report.extra_pairs} dependent(s) with no import statement behind the edge "
            "(inheritance or another non-import fact) are reported above, not counted as misses",
        ]
    )
    if report.absent_nodes:
        lines.append(f"  files that are not modules of the graph: {len(report.absent_nodes)}")
        lines.extend(f"      not in graph: {name}" for name in report.absent_nodes)
    return "\n".join(lines)


def format_sexp(report: Report) -> str:
    """The result in the graph's own format, for an agent to read."""

    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        f"(dart-accuracy :seed {report.seed} :sampled {len(report.files)} "
        f":expected {report.expected_pairs} :right {report.right_pairs} "
        f":precision {report.precision:.4f} :recall {report.recall:.4f})"
    ]
    for score in report.files:
        lines.append(
            f"(file {quote(score.file)} :present {'true' if score.present else 'false'} "
            f":expected {len(score.expected)} :found {len(score.found)} "
            f":precision {score.precision:.3f} :recall {score.recall:.3f})"
        )
        for name in sorted(score.missing):
            lines.append(f"(missing {quote(score.file)} {quote(name)})")
        for name in sorted(score.extra):
            lines.append(
                f"(extra {quote(score.file)} {quote(name)} "
                f":reason {score.extra_reasons.get(name, 'unknown')})"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare the Dart imports of a project with the reverse dependents of its code graph.",
    )
    parser.add_argument("project", help="root of the Dart or Flutter project (its pubspec.yaml is there)")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help=f"files to sample (default {DEFAULT_SAMPLE})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"random seed (default {DEFAULT_SEED})")
    parser.add_argument(
        "--min-recall",
        type=float,
        default=DEFAULT_MIN_RECALL,
        help=f"exit 1 below this aggregate recall (default {DEFAULT_MIN_RECALL})",
    )
    parser.add_argument("--graph", help="read this built code graph instead of building one")
    parser.add_argument("--json", help="write the result as JSON to this path")
    parser.add_argument("--sexp", action="store_true", help="print in the graph format instead of text")
    parser.add_argument("--python", help="interpreter of the builder (default: the one running this script)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = os.path.realpath(args.project)
    if not os.path.isdir(root):
        print(f"error: {args.project} is not a directory", file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(root, "pubspec.yaml")):
        print(f"error: {root} has no pubspec.yaml: not a Dart or Flutter package", file=sys.stderr)
        return 2
    report = evaluate(root, args.sample, args.seed, graph_path=args.graph, python=args.python)
    print(format_sexp(report) if args.sexp else format_text(report, args.min_recall))
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failed = (
        report.recall < args.min_recall
        or report.missing_pairs > 0
        or bool(report.absent_nodes)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())