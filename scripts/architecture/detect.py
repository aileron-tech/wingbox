"""Split a repository into parts and judge the architecture style of each part.

Pure functions over a list of file paths (POSIX, relative to the repository
root) and a reader of small text files (manifests); no file system access.

Parts (index.toml [parts]): a part starts at a folder that holds a manifest
(package.json, pyproject.toml, Cargo.toml, go.mod, ...) and ends where a nested
part starts; the repository root is always a part. A pom.xml below another
pom.xml is a module of the same application, not a new part. Each file belongs
to the deepest part above it. A part with fewer than min_source_files source
files (ignored folders and tests left out) is listed but not judged.

Scoring (one style): each detection group whose folder names or file patterns
match at least `min` files adds its weight; a required group without a match
gives 0; each `against` pattern that is present subtracts its weight; the score
is the result over the sum of weights. Coverage is the share of the part's
source files that gave evidence. A style is a candidate at score >=
candidate_score and coverage >= min_coverage; a style drops out when a
candidate that wins over it scores at least as high; the part is determined
when one candidate is left or the best leads the next by `lead`.
"""
from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from .library import Library, Style
from .words import DEFAULT_SETTINGS, Dictionary, MatchSettings, WordMatch, make_dictionary, match_name, normalise

SOURCE_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rb", ".go", ".rs", ".cs", ".java", ".kt",
    ".php", ".vue", ".svelte", ".erb", ".cshtml", ".swift", ".scala", ".ex", ".exs", ".haml", ".slim",
    ".pas", ".pp", ".lpr", ".dpr",
})
EXTRA_TEST_FOLDERS = ("__tests__", "__mocks__", "e2e", "testdata", "integration_test")   # integration_test/: Flutter

ReadText = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class Evidence:
    group: str
    folder: str        # folder name as found ("routes/"), or the file pattern
    files: int
    match: str         # exact | spelling | file
    via: str           # the dictionary word that matched


@dataclass(frozen=True)
class StyleScore:
    style: str
    score: float
    coverage: float
    evidence: tuple[Evidence, ...]
    against: tuple[str, ...]
    missing: str       # the required group that is missing, or ""


@dataclass(frozen=True)
class Judgement:
    state: str                          # determined | undetermined | not-judged
    best: Optional[StyleScore]          # the determined style, or None
    candidates: tuple[StyleScore, ...]  # every candidate, best first
    near: tuple[StyleScore, ...]        # best non-candidates with a score above 0
    overlays: tuple[StyleScore, ...]


@dataclass(frozen=True)
class Part:
    path: str                   # "." for the repository root
    manifests: tuple[str, ...]  # manifest file names in the part folder
    files: tuple[str, ...]      # every source file of the part, relative to the part folder
    judged: tuple[str, ...]     # source files counted for the style: no tests, no ignored folders
    root_files: tuple[str, ...] = ()   # names of all files directly in the part folder


def is_test_file(relative: str, library: Library) -> bool:
    segments = relative.split("/")
    folders = {normalise(segment) for segment in segments[:-1]}
    tests = {normalise(name) for name in library.index.test_folders + EXTRA_TEST_FOLDERS}
    if folders & tests:
        return True
    # A folder named by a test word first holds tests: tests-integration/, test-utils/, e2e-cases/;
    # a feature named after tests (openappa-policy-tests/) does not.
    if any(re.split(r"[-_.]", segment.lower())[0] in {"test", "tests", "spec", "specs", "e2e"} for segment in segments[:-1]):
        return True
    base = segments[-1]
    if any(fnmatch.fnmatchcase(base.lower(), pattern.lower()) for pattern in library.index.test_files):
        return True
    stem = base.rsplit(".", 1)[0]
    # x.test-helpers.ts, x.test-data.ts: helpers that only tests import.
    return (
        stem.endswith((".test", ".spec", "_test", "_spec"))
        or ".test-" in stem or ".spec-" in stem
        or stem.startswith("test_")
    )


def _is_ignored(relative: str, library: Library) -> bool:
    ignored = {normalise(name) for name in library.index.ignored_folders}
    return any(segment.startswith(".") or normalise(segment) in ignored for segment in relative.split("/")[:-1])


def owner(path: str, roots: tuple[str, ...]) -> Optional[str]:
    """The part folder a path belongs to: the deepest root above it."""
    root_set = _root_set(roots)
    folder = path
    while folder:
        folder = folder.rsplit("/", 1)[0] if "/" in folder else ""
        if folder in root_set:
            return folder
    return None


@functools.lru_cache(maxsize=16)
def _root_set(roots: tuple[str, ...]) -> frozenset[str]:
    return frozenset(roots)


def is_source(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SOURCE_EXTENSIONS


def part_roots(paths: tuple[str, ...], library: Library) -> tuple[str, ...]:
    """Folders that start a part, deepest first; "" is the repository root."""
    manifests = library.index.manifests
    by_folder: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        folder, base = os.path.split(path)
        if any(segment.startswith(".") or segment in ("node_modules", "vendor") for segment in folder.split("/") if segment):
            continue
        if any(fnmatch.fnmatchcase(base, pattern) for pattern in manifests):
            by_folder[folder].add(base)
    poms = {folder for folder, names in by_folder.items() if "pom.xml" in names}
    roots = set(by_folder)
    for folder in poms:
        nested = any(other != folder and (other == "" or folder.startswith(other + "/")) for other in poms)
        if nested and by_folder[folder] == {"pom.xml"}:
            roots.discard(folder)
    roots.add("")
    return tuple(sorted(roots, key=lambda root: (-len(root), root)))


def split_parts(paths: tuple[str, ...], library: Library, roots: Optional[tuple[str, ...]] = None) -> tuple[Part, ...]:
    """Parts of a repository with their source files; parts without source files are left out.

    `roots` (deepest first) replaces the folders found by their manifests, e.g. the parts of a config file.
    """
    roots = roots if roots is not None else part_roots(paths, library)
    files: dict[str, list[str]] = defaultdict(list)
    manifests: dict[str, list[str]] = defaultdict(list)
    root_files: dict[str, list[str]] = defaultdict(list)
    for path in sorted(paths):
        root = owner(path, roots)
        if root is None:
            continue
        relative = path[len(root) + 1:] if root else path
        if "/" not in relative:
            root_files[root].append(relative)
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in library.index.manifests):
                manifests[root].append(relative)
        if is_source(path):
            files[root].append(relative)
    parts: list[Part] = []
    for root in sorted(files):
        judged = tuple(f for f in files[root] if not _is_ignored(f, library) and not is_test_file(f, library))
        parts.append(Part(
            path=root or ".", manifests=tuple(manifests.get(root, ())), files=tuple(files[root]), judged=judged,
            root_files=tuple(root_files.get(root, ())),
        ))
    return tuple(parts)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_DICTIONARIES: dict[tuple[str, str], Dictionary] = {}


def _group_dictionaries(style: Style) -> Dictionary:
    key = (style.id, "groups")
    if key not in _DICTIONARIES:
        _DICTIONARIES[key] = make_dictionary((name, group.id) for group in style.groups for name in group.names)
    return _DICTIONARIES[key]


def layer_dictionary(style: Style) -> Dictionary:
    key = (style.id, "layers")
    if key not in _DICTIONARIES:
        _DICTIONARIES[key] = make_dictionary((name, layer.id) for layer in style.layers for name in layer.folders)
    return _DICTIONARIES[key]


def _segment_matches(files: tuple[str, ...], dictionary: Dictionary, settings: MatchSettings) -> dict[str, WordMatch]:
    segments = {segment for f in files for segment in f.split("/")[:-1]}
    found: dict[str, WordMatch] = {}
    for segment in sorted(segments):
        match = match_name(segment, dictionary, settings)
        if match is not None:
            found[segment] = match
    return found


def _siblings_ok(style: Style, files: tuple[str, ...], matches: dict[str, WordMatch]) -> bool:
    required = [group.id for group in style.groups if group.required]
    children: dict[str, set[str]] = defaultdict(set)
    for f in files:
        segments = f.split("/")[:-1]
        for i, segment in enumerate(segments):
            if segment in matches:
                children["/".join(segments[:i])].add(matches[segment].role)
    return any(all(group in roles for group in required) for roles in children.values())


def score_style(style: Style, files: tuple[str, ...], settings: MatchSettings = DEFAULT_SETTINGS) -> StyleScore:
    """Score of one style for the judged files of a part (index.toml, style [detect])."""
    if not files:
        return StyleScore(style.id, 0.0, 0.0, (), (), "files")
    dictionary = _group_dictionaries(style)
    matches = _segment_matches(files, dictionary, settings)
    total = sum(group.weight for group in style.groups) or 1.0
    got = 0.0
    hits: set[int] = set()
    evidence: list[Evidence] = []
    for group in style.groups:
        group_hits: set[int] = set()
        folders: Counter[str] = Counter()
        file_hits: Counter[str] = Counter()
        patterns = [pattern.lower() for pattern in group.files]
        for i, f in enumerate(files):
            segments = f.split("/")
            matched = [s for s in segments[:-1] if s in matches and matches[s].role == group.id]
            if matched:
                group_hits.add(i)
                folders[matched[-1]] += 1
            elif patterns and len(segments) <= group.max_depth:
                pattern = next((p for p in patterns if fnmatch.fnmatchcase(segments[-1].lower(), p)), None)
                if pattern:
                    group_hits.add(i)
                    file_hits[pattern] += 1
        if len(group_hits) < group.min_files:
            group_hits = set()
        if group_hits:
            got += group.weight
            hits |= group_hits
            for folder, count in folders.most_common():
                match = matches[folder]
                evidence.append(Evidence(group.id, folder + "/", count, match.step, match.via))
            for pattern, count in file_hits.most_common():
                evidence.append(Evidence(group.id, pattern, count, "file", pattern))
        elif group.required:
            return StyleScore(style.id, 0.0, 0.0, (), (), group.id)
    if any(group.siblings for group in style.groups) and not _siblings_ok(style, files, matches):
        return StyleScore(style.id, 0.0, 0.0, (), (), "siblings")
    present = {normalise(s) for f in files for s in f.split("/")[:-1]}
    against: list[str] = []
    for item in style.against:
        if all(any(normalise(name) in present for name in names) for names in item.all_of):
            got -= item.weight
            against.append(" + ".join(names[0] for names in item.all_of))
    return StyleScore(
        style=style.id,
        score=round(max(got, 0.0) / total, 2),
        coverage=round(len(hits) / len(files), 2),
        evidence=tuple(evidence),
        against=tuple(against),
        missing="",
    )


def judge(part: Part, library: Library, settings: MatchSettings = DEFAULT_SETTINGS) -> Judgement:
    """Decide the style of a part (index.toml [decision])."""
    index = library.index
    if len(part.judged) < index.min_source_files:
        return Judgement("not-judged", None, (), (), ())
    scores = sorted(
        (score_style(style, part.judged, settings) for style in library.styles),
        key=lambda item: (-item.score, -item.coverage, item.style),
    )
    candidates = [s for s in scores if s.score >= index.candidate_score and s.coverage >= index.min_coverage]
    wins = {style.id: set(style.wins_over) for style in library.styles}
    candidates = [
        s for s in candidates
        if not any(s.style in wins[o.style] and o.score >= s.score for o in candidates if o is not s)
    ]
    decisive = [
        s for s in scores
        if any(fnmatch.fnmatchcase(name, pattern) for style in library.styles if style.id == s.style
               for pattern in style.decisive_files for name in part.root_files)
    ]
    if decisive:
        # A decisive file (nest-cli.json) settles the style; the others stay as candidates.
        best = decisive[0]
        others = [s for s in candidates if s.style != best.style]
        candidates = [best] + others
        state = "determined"
    elif not candidates:
        state, best = "undetermined", None
    elif len(candidates) == 1 or candidates[0].score - candidates[1].score >= index.lead:
        state, best = "determined", candidates[0]
    else:
        state, best = "undetermined", None
    overlays = tuple(
        o for o in (score_style(style, part.judged, settings) for style in library.overlays)
        if o.score >= index.candidate_score and o.coverage >= 0.03
    )
    near = tuple(s for s in scores if s not in candidates and s.score > 0)[:2]
    return Judgement(state, best, tuple(candidates), near, overlays)


# ---------------------------------------------------------------------------
# C4 level, kind and declared rules of a part
# ---------------------------------------------------------------------------


def _json(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _toml(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _join(part: str, name: str) -> str:
    return name if part == "." else f"{part}/{name}"


def c4_level(part: Part, paths: tuple[str, ...], library: Library, read_text: ReadText) -> str:
    """system (the workspace), container (runs by itself) or component (c4.toml)."""
    c4 = library.c4
    prefix = "" if part.path == "." else part.path + "/"
    here = {p[len(prefix):] for p in paths if p.startswith(prefix) and "/" not in p[len(prefix):]}
    package = _json(read_text(_join(part.path, "package.json"))) if "package.json" in here else {}
    cargo = _toml(read_text(_join(part.path, "Cargo.toml"))) if "Cargo.toml" in here else {}
    # turbo.json also configures a single package of a Turborepo: it marks a workspace only at its root.
    workspace_files = [name for name in c4.workspace_files if name != "turbo.json"]
    if any(name in here for name in workspace_files) or "workspaces" in package or (
        "workspace" in cargo and "package" not in cargo
    ):
        return "system"
    below = [p[len(prefix):] for p in paths if p.startswith(prefix)]
    for pattern in c4.container_files:
        if "/" in pattern:
            if any(fnmatch.fnmatchcase(p, pattern) for p in below):
                return "container"
        elif any(fnmatch.fnmatchcase(name, pattern) for name in here):
            return "container"
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    pyproject = _toml(read_text(_join(part.path, "pyproject.toml"))) if "pyproject.toml" in here else {}
    if "start" in scripts or "bin" in package or "scripts" in pyproject.get("project", {}) or "bin" in cargo:
        return "container"
    segments = [] if part.path == "." else part.path.split("/")
    if len(segments) >= 2 and normalise(segments[-2]) in {normalise(n) for n in c4.container_folders}:
        return "container"
    if len(segments) >= 2 and normalise(segments[-2]) in {normalise(n) for n in c4.component_folders}:
        return "component"
    return "container" if not segments else "component"


_KINDS = (
    ("Cargo.toml", "Rust crate"),
    ("go.mod", "Go module"),
    ("pyproject.toml", "Python project"),
    ("setup.py", "Python project"),
    ("pom.xml", "Java project"),
    ("settings.gradle", "Gradle project"),
    ("settings.gradle.kts", "Gradle project"),
    ("Gemfile", "Ruby project"),
    ("composer.json", "PHP project"),
    ("mix.exs", "Elixir project"),
    ("package.json", "npm package"),
)


def part_kind(part: Part) -> str:
    for manifest, kind in _KINDS:
        if manifest in part.manifests:
            return kind
    if any(name.endswith(".sln") for name in part.manifests):
        return ".NET solution"
    return "folder"


@dataclass(frozen=True)
class DeclaredFile:
    path: str      # relative to the repository root
    kind: str      # rules | exceptions | manifest-field


def declared_files(part: Part, paths: tuple[str, ...], all_roots: tuple[str, ...], library: Library, read_text: ReadText) -> tuple[DeclaredFile, ...]:
    """Files in which the part states its own architecture rules (index.toml [declared_rules])."""
    root = "" if part.path == "." else part.path
    prefix = root + "/" if root else ""
    found: list[DeclaredFile] = []
    for path in paths:
        if not path.startswith(prefix) or owner(path, all_roots) != root:
            continue
        relative = path[len(prefix):]
        if _is_ignored(relative, library) or relative.split("/")[0] in ("node_modules",):
            continue
        base = relative.split("/")[-1]
        if any(fnmatch.fnmatchcase(base, pattern) for pattern in library.index.declared_files):
            found.append(DeclaredFile(path, "rules"))
        elif any(fnmatch.fnmatchcase(base, pattern) for pattern in library.index.known_exceptions):
            found.append(DeclaredFile(path, "exceptions"))
        elif "/" not in relative and _names_a_manifest_field(base, library) and _declares_in_manifest(base, read_text(path), library):
            found.append(DeclaredFile(path, "manifest-field"))
    return tuple(found)


def _names_a_manifest_field(base: str, library: Library) -> bool:
    return any(fnmatch.fnmatchcase(base, field.partition(":")[0]) for field in library.index.declared_fields)


def _declares_in_manifest(base: str, text: Optional[str], library: Library) -> bool:
    if not text:
        return False
    for field in library.index.declared_fields:
        file_pattern, _, key = field.partition(":")
        if not fnmatch.fnmatchcase(base, file_pattern):
            continue
        if base.endswith(".toml"):
            value: object = _toml(text)
            for name in key.split("."):
                value = value.get(name) if isinstance(value, dict) else None
            if value is not None:
                return True
        elif re.search(rf"\b{re.escape(key)}\b", text):
            return True
    return False
