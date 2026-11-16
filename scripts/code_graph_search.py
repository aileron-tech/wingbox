#!/usr/bin/env python3
"""
Code Graph Search -- query tool for S-expression code graphs.

Reads ``.code_graph.sexp`` and provides structured queries: module views,
dependency trees, reverse dependency analysis, entity lookups, data flow
tracing, and boundary discovery for minimal context loading.

Output is S-expression facts (one per line, greppable).

Usage::

    code_graph_search.py analyze <id>           Full analysis in one step
    code_graph_search.py boundary <id>          File paths for change boundary
    code_graph_search.py quick <keyword>        Search + analyze in one step
    code_graph_search.py module <id>            Show module with entities and edges
    code_graph_search.py deps <id> [depth]      Outgoing dependency tree (default depth 2)
    code_graph_search.py rdeps <id> [depth]     Reverse dependency tree (default depth 2)
    code_graph_search.py impact <file|id> [depth] [--sexp]  Blast radius of a change: dependents by depth and files to read first
    code_graph_search.py entity <name> [--positions]
                                                Find entity: definition + the modules that use it;
                                                --positions: every use with its line and column
    code_graph_search.py flow <id> <entity>     Trace data/call flow from entity
    code_graph_search.py search <pattern>       Safe pattern search across all facts
    code_graph_search.py invariants [scope]     Check naming-convention violations (default: all)
    code_graph_search.py find_function <name>   Find function/method definitions by name
    code_graph_search.py find_type <name>       Find type/class/interface/enum definitions by name
    code_graph_search.py find_callchain <src_fn> <tgt_fn> [depth]  Trace call path between two functions
    code_graph_search.py find_callers <fn_name> [depth]  Who calls this function (incoming calls)
    code_graph_search.py find_calls <fn_name> [depth]    What does this function call (outgoing calls)
    code_graph_search.py grep_functions <pattern>         Search function bodies in source files
    code_graph_search.py diff_functions [ref]             Identify changed functions in git diff (default: HEAD)
    code_graph_search.py query <scheme-expr>    Evaluate a Scheme query expression
    code_graph_search.py repl                   Interactive Scheme REPL

Scheme query examples::

    code_graph_search.py query '(boundary "operatives.controller")'
    code_graph_search.py query '(files (intersect (deps "A" 2) (rdeps "B" 1)))'
    code_graph_search.py query '(filter (boundary "operatives.service") "dto")'
    code_graph_search.py query '(count (deps "operatives.service" 3))'

CLI examples::

    code_graph_search.py analyze drive/tools/drive_vsearch
    code_graph_search.py boundary operatives.service
    code_graph_search.py entity DriveDbCommitResponse
    code_graph_search.py deps drive/modules/drive_db/services/database_service 3
    code_graph_search.py find_function createOrder
    code_graph_search.py find_type DriveDbCommitResponse
    code_graph_search.py find_callchain createOrder processPayment
    code_graph_search.py find_callers handleRequest 3
    code_graph_search.py find_calls createOrder 2
    code_graph_search.py grep_functions "validate.*input"
    code_graph_search.py diff_functions HEAD~3

Patterns support literal text, ``.*`` wildcards, boundary ``^``/``$`` anchors,
and backslash escapes. Other regex operators are rejected.
"""

from __future__ import annotations

import collections
import functools
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

MAP_SCHEMA_VERSION = 1
MAX_SEARCH_PATTERN_LENGTH = 256
MAX_QUERY_DEPTH = 50


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchPattern:
    segments: tuple[str, ...]
    anchor_start: bool
    anchor_end: bool


@dataclass(frozen=True)
class SearchPatternOk:
    value: SearchPattern


@dataclass(frozen=True)
class SearchPatternErr:
    error: str


SearchPatternResult = SearchPatternOk | SearchPatternErr


@dataclass(frozen=True)
class MapLoadOk:
    pass


@dataclass(frozen=True)
class MapLoadErr:
    error: str


MapLoadResult = MapLoadOk | MapLoadErr


@dataclass(frozen=True)
class QueryError:
    kind: str
    message: str


@dataclass
class Node:
    nid: str
    lang: str
    purpose: str
    naming: str = ""   # snake_case, camelCase, PascalCase, kebab-case, mixed
    pkg: str = ""      # repository/package name


@dataclass
class EntityFact:
    module: str
    name: str
    kind: str  # fn, cls, ifc, typ, enm
    line: int
    bases: list[str] = field(default_factory=list)
    deco: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


@dataclass
class Edge:
    etype: str   # imports, calls-api, data-flow, refs, calls
    source: str
    target: str
    flags: list[str] = field(default_factory=list)


def parse_search_pattern(raw: str) -> SearchPatternResult:
    """Parse the supported linear pattern grammar without regex execution."""
    if len(raw) > MAX_SEARCH_PATTERN_LENGTH:
        return SearchPatternErr(
            f"pattern exceeds {MAX_SEARCH_PATTERN_LENGTH} characters"
        )

    anchor_start = raw.startswith("^")
    body_start = 1 if anchor_start else 0
    trailing_backslashes = len(raw) - len(raw.rstrip("\\"))
    anchor_end = raw.endswith("$") and trailing_backslashes % 2 == 0
    body_end = len(raw) - 1 if anchor_end else len(raw)
    body = raw[body_start:body_end]

    segments: list[str] = []
    literal: list[str] = []
    index = 0
    unsupported = set("[](){}+?|")
    while index < len(body):
        character = body[index]
        if character == "\\":
            if index + 1 >= len(body):
                return SearchPatternErr("pattern ends with an incomplete escape")
            literal.append(body[index + 1])
            index += 2
            continue
        if character == "." and index + 1 < len(body) and body[index + 1] == "*":
            segments.append("".join(literal).casefold())
            literal = []
            index += 2
            continue
        if character == "*" or character in unsupported:
            return SearchPatternErr(
                f"unsupported pattern operator: {character}"
            )
        if character in ("^", "$"):
            return SearchPatternErr(
                f"{character} is only supported at the pattern boundary"
            )
        literal.append(character)
        index += 1

    segments.append("".join(literal).casefold())
    return SearchPatternOk(SearchPattern(
        segments=tuple(segments),
        anchor_start=anchor_start,
        anchor_end=anchor_end,
    ))


def search_pattern_matches(pattern: SearchPattern, value: str) -> bool:
    """Match literal segments and ``.*`` wildcards in linear time."""
    candidate = value.casefold()
    position = 0
    final_index = len(pattern.segments) - 1
    for index, segment in enumerate(pattern.segments):
        if index == final_index and pattern.anchor_end:
            match_position = len(candidate) - len(segment)
            return (
                match_position >= position
                and candidate[match_position:] == segment
                and (not pattern.anchor_start or index > 0 or match_position == 0)
            )
        if index == 0 and pattern.anchor_start:
            if not candidate.startswith(segment):
                return False
            match_position = 0
        else:
            match_position = candidate.find(segment, position)
            if match_position < 0:
                return False
        position = match_position + len(segment)
    return True


def _parse_depth(raw: str) -> tuple[Optional[int], Optional[str]]:
    try:
        depth = int(raw)
    except ValueError:
        return None, f"depth must be an integer: {raw}"
    if depth < 0 or depth > MAX_QUERY_DEPTH:
        return None, f"depth must be between 0 and {MAX_QUERY_DEPTH}: {raw}"
    return depth, None


def _print_invalid_pattern(raw: str, error: str) -> None:
    print(f"(error invalid-pattern {_q(raw)} {_q(error)})")


# ---------------------------------------------------------------------------
# S-expression line parser
# ---------------------------------------------------------------------------

def tokenize(line: str) -> list[str]:
    """Tokenize a single-line S-expression into a flat list of tokens."""
    line = line.strip()
    if not line or line.startswith(";"):
        return []
    if not line.startswith("(") or not line.endswith(")"):
        return []
    inner = line[1:-1]
    tokens: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == " ":
            i += 1
        elif ch == '"':
            j = i + 1
            while j < len(inner):
                if inner[j] == "\\" and j + 1 < len(inner):
                    j += 2
                elif inner[j] == '"':
                    break
                else:
                    j += 1
            tokens.append(inner[i + 1 : j].replace('\\"', '"').replace("\\\\", "\\"))
            i = j + 1
        elif ch == "(":
            depth = 1
            j = i + 1
            while j < len(inner) and depth > 0:
                if inner[j] == '"':
                    # a quoted value may hold brackets: (:methods ")" "]")
                    j = _end_of_quoted(inner, j)
                elif inner[j] == "(":
                    depth += 1
                elif inner[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(inner[i:j])
            i = j
        elif ch == ")":
            i += 1  # a stray closing bracket is skipped; it must never stall the reader
        else:
            j = i
            while j < len(inner) and inner[j] not in (" ", ")"):
                j += 1
            tokens.append(inner[i:j])
            i = j
    return tokens


def _end_of_quoted(text: str, start: int) -> int:
    """Index of the quote that closes the quoted value opening at start (or the last index)."""
    j = start + 1
    while j < len(text):
        if text[j] == "\\" and j + 1 < len(text):
            j += 2
        elif text[j] == '"':
            return j
        else:
            j += 1
    return len(text) - 1


def parse_sub_list(token: str) -> list[str]:
    """Parse a (:key a b c) token into ['a', 'b', 'c']; quoted values keep spaces and brackets."""
    if token.startswith("(") and token.endswith(")"):
        parts = tokenize(token)
        return parts[1:] if parts and parts[0].startswith(":") else parts
    return []


# ---------------------------------------------------------------------------
# S-expression output helpers
# ---------------------------------------------------------------------------

def _q(s: str) -> str:
    """Quote a string for S-expression output if it contains spaces."""
    if " " in s or '"' in s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _sexp_list(tag: str, items: list[str]) -> str:
    """Format a tagged list: (:tag a b c)."""
    if not items:
        return ""
    return f"(:{tag} {' '.join(items)})"


# ---------------------------------------------------------------------------
# No-match error helper
# ---------------------------------------------------------------------------

def _print_no_match(g: CodeGraph, pattern: str) -> None:
    """Print a structured no-match error with fuzzy suggestions."""
    suggestions = CodeGraph._suggest_similar(pattern, g.nodes)
    if suggestions:
        sug_str = " ".join(suggestions)
        print(f"(error no-match {_q(pattern)} :suggestions {sug_str})")
    else:
        print(f"(error no-match {_q(pattern)})")


# ---------------------------------------------------------------------------
# File-path resolution helpers
# ---------------------------------------------------------------------------

_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_PY_EXTENSIONS = (".py",)
_RUBY_EXTENSIONS = (".rb", ".rake")
_RUST_EXTENSIONS = (".rs",)
_PASCAL_EXTENSIONS = (".pas", ".pp", ".lpr", ".inc")
_OTHER_EXTENSIONS = (".go", ".java", ".cs")


def _module_id_to_file(module_id: str, root: str) -> str:
    """Convert a module ID back to a file path relative to root."""
    base = os.path.join(root, module_id)
    if os.path.isfile(base):
        return base
    source_extensions = (
        _TS_EXTENSIONS
        + _PY_EXTENSIONS
        + _RUBY_EXTENSIONS
        + _RUST_EXTENSIONS
        + _PASCAL_EXTENSIONS
        + _OTHER_EXTENSIONS
    )
    for ext in source_extensions:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    for idx in ("index.ts", "index.tsx", "index.js", "__init__.py", "mod.rs"):
        candidate = os.path.join(base, idx)
        if os.path.isfile(candidate):
            return candidate
    return module_id


# ---------------------------------------------------------------------------
# Graph loader
# ---------------------------------------------------------------------------

class CodeGraph:
    """In-memory code graph built from S-expression facts."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.entities: dict[str, list[EntityFact]] = {}  # module -> entities
        self.entity_by_name: dict[str, list[EntityFact]] = {}  # name -> entities
        self.edges_from: dict[str, list[Edge]] = {}  # source module -> edges
        self.edges_to: dict[str, list[Edge]] = {}  # target module -> edges
        self.edges_from_entity: dict[str, list[Edge]] = {}  # entity-qualified source -> edges
        self.edges_by_entity_name: dict[str, list[Edge]] = {}  # entity name -> edges
        self.all_edges: list[Edge] = []
        self._symbol_lines: list[str] = []  # use facts, parsed on first access (most queries never need them)
        self._uses_by_target: Optional[dict[str, list[tuple[str, int, int]]]] = None
        self.pmap: dict[str, str] = {}
        self.package_layers: dict[str, str] = {}  # Python package __init__ -> layer of its package
        self._root: str = ""

    def load(self, path: str, root: Optional[str] = None) -> MapLoadResult:
        self.nodes = {}
        self.entities = {}
        self.entity_by_name = {}
        self.edges_from = {}
        self.edges_to = {}
        self.edges_from_entity = {}
        self.edges_by_entity_name = {}
        self.all_edges = []
        self._symbol_lines = []
        self._uses_by_target = None
        self.pmap = {}
        self.package_layers = {}
        self._root = os.path.abspath(
            root if root is not None else os.path.dirname(os.path.abspath(path))
        )
        try:
            with open(path, "r", encoding="utf-8") as code_graph:
                map_lines = code_graph.readlines()
        except OSError as error:
            return MapLoadErr(f"could not read {path}: {error}")

        pmap: dict[str, str] = {}
        declared_node_count: Optional[int] = None
        found_header = False
        found_paths = False

        in_paths = False
        path_stack: list[str] = []

        for line in map_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("(use "):
                continue

            header_tokens = tokenize(stripped)
            if header_tokens and header_tokens[0] == "code-graph":
                found_header = True
                try:
                    schema_index = header_tokens.index(":schema")
                    nodes_index = header_tokens.index(":nodes")
                except ValueError:
                    return MapLoadErr("code-graph header is missing schema or node count")
                if schema_index + 1 >= len(header_tokens):
                    return MapLoadErr("code-graph header has no schema value")
                if header_tokens[schema_index + 1] != str(MAP_SCHEMA_VERSION):
                    return MapLoadErr(
                        f"unsupported code-graph schema: "
                        f"{header_tokens[schema_index + 1]}"
                    )
                if (
                    nodes_index + 1 >= len(header_tokens)
                    or not header_tokens[nodes_index + 1].isdigit()
                ):
                    return MapLoadErr("code-graph header has an invalid node count")
                declared_node_count = int(header_tokens[nodes_index + 1])
                continue

            if stripped == "(paths":
                found_paths = True
                in_paths = True
                path_stack = []
                continue

            if in_paths:
                if stripped == ")":
                    if path_stack:
                        path_stack.pop()
                    else:
                        in_paths = False
                    continue

                tokens = tokenize(stripped)
                if not tokens:
                    inner = stripped.lstrip("(").rstrip()
                    if inner:
                        path_stack.append(inner)
                    continue

                if len(tokens) == 2 and not tokens[0].startswith(":"):
                    sid, leaf = tokens[0], tokens[1]
                    if leaf == ":self":
                        full = "/".join(path_stack) if path_stack else sid
                        pmap[sid] = full
                    else:
                        full = "/".join(path_stack + [leaf]) if path_stack else leaf
                        pmap[sid] = full
                elif len(tokens) == 1:
                    path_stack.append(tokens[0])
                continue

            tokens = tokenize(stripped)
            if tokens and tokens[0] == "def" and len(tokens) >= 3:
                pmap[tokens[1]] = tokens[2]

        if not found_header:
            return MapLoadErr("code-graph header is missing")
        if not found_paths or in_paths:
            return MapLoadErr("code-graph path section is missing or unbalanced")

        self.pmap = pmap

        def _resolve(sid: str) -> str:
            if "::" in sid:
                mod, ent = sid.split("::", 1)
                return f"{pmap.get(mod, mod)}::{ent}"
            return pmap.get(sid, sid)

        for line in map_lines:
            if line.startswith("(use "):
                self._symbol_lines.append(line)
                continue
            tokens = tokenize(line)
            if not tokens:
                continue
            kind = tokens[0]

            if kind in ("code-graph", "paths", "def", ")"):
                continue

            if kind == "node" and len(tokens) >= 4:
                nid = _resolve(tokens[1])
                lang, purpose = tokens[2], tokens[3]
                naming, pkg = "", ""
                rest = tokens[4:]
                for i, t in enumerate(rest):
                    if t == ":naming" and i + 1 < len(rest):
                        naming = rest[i + 1]
                    elif t == ":pkg" and i + 1 < len(rest):
                        pkg = rest[i + 1]
                self.nodes[nid] = Node(nid, lang, purpose, naming, pkg)

            elif kind in ("fn", "cls", "mod", "ifc", "typ", "enm") and len(tokens) >= 4:
                module = _resolve(tokens[1])
                name = tokens[2]
                line_num = int(tokens[3]) if tokens[3].isdigit() else 0
                bases, deco, methods = [], [], []
                for t in tokens[4:]:
                    if t.startswith("(:bases"):
                        bases = parse_sub_list(t)
                    elif t.startswith("(:deco"):
                        deco = parse_sub_list(t)
                    elif t.startswith("(:methods"):
                        methods = parse_sub_list(t)
                ent = EntityFact(module, name, kind, line_num, bases, deco, methods)
                self.entities.setdefault(module, []).append(ent)
                self.entity_by_name.setdefault(name, []).append(ent)

            elif kind in ("imports", "calls-api", "data-flow", "refs", "calls", "http-calls") and len(tokens) >= 3:
                source = _resolve(tokens[1])
                target = _resolve(tokens[2])
                flags = [t for t in tokens[3:] if t.startswith(":")]
                edge = Edge(kind, source, target, flags)
                self.all_edges.append(edge)
                src_mod = source.split("::")[0]
                tgt_mod = target.split("::")[0]
                # A use reached through a re-export is entity-level only: module
                # queries (impact, deps, rdeps, analyze) follow the imports.
                if ":reexport" not in flags:
                    self.edges_from.setdefault(src_mod, []).append(edge)
                    self.edges_to.setdefault(tgt_mod, []).append(edge)
                self.edges_from_entity.setdefault(source, []).append(edge)
                for part in (source, target):
                    if "::" in part:
                        ent_name = part.split("::", 1)[1]
                        self.edges_by_entity_name.setdefault(ent_name, []).append(edge)

            elif kind == "violation" and len(tokens) >= 5:
                source = _resolve(tokens[2])
                target = _resolve(tokens[3])
                edge = Edge("violation", source, target, [f":rule={tokens[1]}"])
                self.all_edges.append(edge)

        if declared_node_count != len(self.nodes):
            return MapLoadErr(
                f"code-graph node count mismatch: expected "
                f"{declared_node_count}, loaded {len(self.nodes)}"
            )
        return MapLoadOk()

    @property
    def uses_by_target(self) -> dict[str, list[tuple[str, int, int]]]:
        """module::name -> (module, line, column) of every use, from (use <module> <target> <line>:<col> ...)."""
        if self._uses_by_target is None:
            found: dict[str, list[tuple[str, int, int]]] = {}
            for line in self._symbol_lines:
                tokens = tokenize(line)
                if len(tokens) < 4:
                    continue
                module, _, name = tokens[2].partition("::")
                sites = found.setdefault(f"{self.pmap.get(module, module)}::{name}", [])
                source = self.pmap.get(tokens[1], tokens[1])
                for position in tokens[3:]:
                    line_text, _, column_text = position.partition(":")
                    if line_text.isdigit() and column_text.isdigit():
                        sites.append((source, int(line_text), int(column_text)))
            self._uses_by_target = found
        return self._uses_by_target

    def resolve_alias(self, module_id: str) -> str:
        """Resolve an internal path-map alias to a module ID when possible."""
        if "::" in module_id:
            mod, ent = module_id.split("::", 1)
            return f"{self.pmap.get(mod, mod)}::{ent}"
        return self.pmap.get(module_id, module_id)

    def resolve_module(self, pattern: str) -> list[str]:
        """Find module IDs: exact > suffix > substring > case-insensitive substring.

        Returns an empty list when nothing matches (caller should use
        _print_no_match to report the failure with suggestions).
        """
        resolved_pattern = self.resolve_alias(pattern)
        if resolved_pattern in self.nodes:
            return [resolved_pattern]
        if pattern in self.nodes:
            return [pattern]
        suffix_matches = [nid for nid in self.nodes if nid.endswith("/" + pattern) or nid.endswith(pattern)]
        if suffix_matches:
            return sorted(suffix_matches)
        sub_matches = [nid for nid in self.nodes if pattern in nid]
        if sub_matches:
            if len(sub_matches) > 10:
                print(f"; warning: '{pattern}' matches {len(sub_matches)} modules, showing first 10", file=sys.stderr)
                return sorted(sub_matches)[:10]
            return sorted(sub_matches)
        # Case-insensitive substring fallback
        lp = pattern.lower()
        ci_matches = [nid for nid in self.nodes if lp in nid.lower()]
        if ci_matches:
            if len(ci_matches) > 10:
                print(f"; warning: '{pattern}' (case-insensitive) matches {len(ci_matches)} modules, showing first 10", file=sys.stderr)
                return sorted(ci_matches)[:10]
            return sorted(ci_matches)
        return []

    def search_modules(self, pattern: SearchPattern) -> list[str]:
        """Find module IDs whose path or basename matches a safe pattern."""
        matches = [
            nid for nid in self.nodes
            if search_pattern_matches(pattern, nid)
            or search_pattern_matches(pattern, nid.rsplit("/", 1)[-1])
        ]
        return sorted(matches)

    @staticmethod
    def _suggest_similar(pattern: str, nodes: "dict[str, Node]", max_results: int = 5) -> list[str]:
        """Return up to max_results node IDs whose path segments overlap with pattern.

        Splits pattern by '/', '.', '_', '-' and counts how many parts appear
        (case-insensitively) in each candidate node ID.  Returns the top N
        nodes with at least one matching part, sorted by match count descending.
        """
        import re as _re
        parts = [p for p in _re.split(r"[/._-]", pattern.lower()) if p]
        if not parts:
            return []
        scored: list[tuple[int, str]] = []
        for nid in nodes:
            nid_lower = nid.lower()
            count = sum(1 for p in parts if p in nid_lower)
            if count > 0:
                scored.append((count, nid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [nid for _, nid in scored[:max_results]]

    def module_to_file(self, module_id: str) -> str:
        """Resolve a module ID to a file path on disk."""
        return _module_id_to_file(module_id, self._root)

    def module_purpose(self, module_id: str) -> str:
        """Return the purpose string of a module. Empty if unknown."""
        node = self.nodes.get(module_id)
        return node.purpose if node else ""

    def module_lang(self, module_id: str) -> str:
        """Return the language of a module ('py', 'ts', 'tsx'). Empty if unknown."""
        node = self.nodes.get(module_id)
        if not node:
            return ""
        # Normalize legacy 'ty' -> 'ts', 'tx' -> 'tsx'
        return {"ty": "ts", "tx": "tsx"}.get(node.lang, node.lang)


# ---------------------------------------------------------------------------
# Shared tree collectors
# ---------------------------------------------------------------------------

def _collect_dep_mods(g: CodeGraph, nid: str) -> dict[str, str]:
    """Outgoing dependency modules (deduped): module -> edge_type.

    An import is the relation reported for a pair that has one; another
    relation (data flow, inheritance) is named only when there is no import.
    """
    seen: dict[str, str] = {}
    for e in g.edges_from.get(nid, []):
        tgt_mod = e.target.split("::")[0]
        if tgt_mod != nid and (tgt_mod not in seen or e.etype == "imports"):
            seen[tgt_mod] = e.etype
    return seen


def _collect_rdep_mods(g: CodeGraph, nid: str) -> dict[str, str]:
    """Incoming dependency modules (deduped): module -> edge_type."""
    seen: dict[str, str] = {}
    for e in g.edges_to.get(nid, []):
        src_mod = e.source.split("::")[0]
        if src_mod != nid and (src_mod not in seen or e.etype == "imports"):
            seen[src_mod] = e.etype
    return seen


def _walk_deps(
    g: CodeGraph, nid: str, visited: set[str], max_depth: int, depth: int,
    emit: Callable[[int, str, str, bool], None],
) -> None:
    if depth > max_depth:
        return
    for tgt, etype in sorted(_collect_dep_mods(g, nid).items()):
        cycle = tgt in visited
        emit(depth, etype, tgt, cycle)
        if not cycle:
            visited.add(tgt)
            _walk_deps(g, tgt, visited, max_depth, depth + 1, emit)


def _walk_rdeps(
    g: CodeGraph, nid: str, visited: set[str], max_depth: int, depth: int,
    emit: Callable[[int, str, str, bool], None],
) -> None:
    if depth > max_depth:
        return
    for src, etype in sorted(_collect_rdep_mods(g, nid).items()):
        cycle = src in visited
        emit(depth, etype, src, cycle)
        if not cycle:
            visited.add(src)
            _walk_rdeps(g, src, visited, max_depth, depth + 1, emit)


# ---------------------------------------------------------------------------
# Layer classifier + boundary rules
# ---------------------------------------------------------------------------

_TEST_FOLDERS = ("/tests/", "/test/", "/__tests__/", "/e2e/", "/testdata/")
_TEST_NAME_ENDINGS = (".test", ".spec", "_test", "_spec", ".test-d")


def _is_test_path(p: str) -> bool:
    """True for a module in a test folder or named like a test (module IDs carry no extension)."""
    name = p.rsplit("/", 1)[-1]
    return (
        any(folder in p for folder in _TEST_FOLDERS)
        or name.startswith("test_")
        or name.endswith(_TEST_NAME_ENDINGS)
        or ".test." in name
        or ".spec." in name
    )


def _classify_layer_fallback(p: str) -> str:
    """Classify a module into an architecture layer from its path.

    Order matters: test/config checked first (override other patterns).
    NestJS conventions: guards/controllers are api, .module files are config,
    /interface/ folders are shared contracts (not api).
    """
    # High-priority overrides (tests, config)
    if _is_test_path(p):
        return "test"
    if "/config/" in p or "/env/" in p:
        return "config"
    # NestJS .module files are wiring/config, not service
    if ".module" in p and "/modules/" in p:
        return "config"
    # Interface folders are shared contracts, not api
    if "/interface/" in p or "/interfaces/" in p:
        return "shared"
    # Migrations and seeds change the database: infrastructure, even under a schema folder
    if "/migrations/" in p or "/seeds/" in p or "/seeders/" in p:
        return "infra"
    # Architecture layers
    if "/api/" in p or "/routes/" in p or "/controllers/" in p:
        return "api"
    if "/services/" in p or "/application/" in p:
        return "service"
    if "/domain/" in p or "/entities/" in p or "/schema" in p or "_schema" in p:
        return "domain"
    if "/repositories/" in p or "/db/" in p or "/infra/" in p or "/infrastructure/" in p or "rabbitmq" in p or "s3" in p:
        return "infra"
    if "/commons/" in p or "/common/" in p or "/utils/" in p or "/result" in p or "/types/" in p:
        return "shared"
    if "/components/" in p or "/pages/" in p or "/ui/" in p or "/hooks/" in p:
        return "ui"
    # NestJS / backend patterns
    if "/dto/" in p or "/dtos/" in p:
        return "domain"
    if "/guards/" in p or "/guard." in p:
        return "api"
    if "/middleware/" in p or "/middlewares/" in p or "/interceptors/" in p or "/pipes/" in p:
        return "api"
    if "/adapters/" in p or "/adapter." in p:
        return "infra"
    if "/gateways/" in p or "/gateway." in p:
        return "infra"
    # Domain patterns
    if "/models/" in p or "/schemas/" in p or "/events/" in p or "/commands/" in p or "/queries/" in p:
        return "domain"
    if "/handlers/" in p:
        return "service"
    # Shared patterns
    if "/lib/" in p or "/helpers/" in p or "/constants/" in p or "/decorators/" in p or "/mappers/" in p:
        return "shared"
    # UI patterns (state management tied to UI)
    if ("/stores/" in p or "/store/" in p or "/contexts/" in p or "/providers/" in p) and ("/ui/" in p or "/web/" in p):
        return "ui"
    # Test patterns
    if "/fixtures/" in p or "/mocks/" in p or "/__mocks__/" in p or "/factories/" in p:
        return "test"
    # Config / tooling patterns
    if "/scripts/" in p:
        return "config"
    return "other"


_PURPOSE_TO_LAYER: dict[str, str] = {
    # Domain layer
    "domain logic": "domain",
    "domain entity": "domain",
    "data model": "domain",
    "data transfer object": "domain",
    "schema": "domain",
    "enum definitions": "domain",
    "policy": "domain",
    "value object": "domain",
    "aggregate root": "domain",
    "domain event": "domain",
    "Specification pattern": "domain",
    # Infrastructure layer
    "infrastructure adapter": "infra",
    "database migration": "infra",
    "database seed": "infra",
    "repository": "infra",
    "adapter": "infra",
    "gateway": "infra",
    "client": "infra",
    "connector": "infra",
    "scheduler": "infra",
    "worker": "infra",
    "Unit of Work": "infra",
    "Identity Map": "infra",
    "Proxy pattern": "infra",
    "SDK": "infra",
    "API client": "infra",
    # API layer
    "API layer": "api",
    "API controller": "api",
    "router": "api",
    "access guard": "api",
    "middleware": "api",
    "interceptor": "api",
    "validation pipe": "api",
    "resolver": "api",
    # Service layer
    "service": "service",
    "application service": "service",
    "mailer": "service",
    "event handler": "service",
    "handler": "service",
    "event listener": "service",
    "processor": "service",
    "provider": "service",
    "registry": "service",
    "agent": "service",
    "manager": "service",
    "subscriber": "service",
    "dispatcher": "service",
    "emitter": "service",
    "Facade pattern": "service",
    "Mediator pattern": "service",
    "Observer pattern": "service",
    "Command pattern": "service",
    "Chain of Responsibility": "service",
    "Memento pattern": "service",
    # Config layer
    "NestJS wiring module": "config",
    "NestJS injectable": "config",
    "configuration": "config",
    "application bootstrap": "config",
    "module entry point": "config",
    # UI layer
    "UI component": "ui",
    "page/route": "ui",
    "React hook": "ui",
    "state store": "other",
    # Test layer
    "test": "test",
    "test fixture": "test",
    "test mock": "test",
    # Shared layer
    "shared utilities": "shared",
    "utility helpers": "shared",
    "constants": "shared",
    "type definitions": "shared",
    "interface definitions": "shared",
    "decorator": "shared",
    "data mapper": "shared",
    "barrel re-exports": "shared",
    "plugin": "shared",
    "Factory pattern": "shared",
    "Builder pattern": "shared",
    "Strategy pattern": "shared",
    "Singleton pattern": "shared",
    "Composite pattern": "shared",
    "Visitor pattern": "shared",
    "Iterator pattern": "shared",
    "Decorator pattern": "shared",
}


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _classify_layer_by_purpose(purpose: str) -> str:
    """Classify layer from the module's purpose string using fuzzy matching.

    Tries exact match, then substring, then Levenshtein distance on
    individual words from the purpose against the keyword map.
    """
    if not purpose or purpose == "general module":
        return "other"
    pl = purpose.lower()
    # 1. Exact match
    for key, layer in _PURPOSE_TO_LAYER.items():
        if key.lower() == pl:
            return layer
    # 2. Substring match
    for key, layer in _PURPOSE_TO_LAYER.items():
        if key.lower() in pl:
            return layer
    # 3. Fuzzy word match: split purpose into words, compare each word
    #    against keywords from the map using Levenshtein distance.
    #    Threshold: distance <= 2 for words >= 5 chars, <= 1 for shorter.
    purpose_words = re.split(r"[\s;,/]+", pl)
    best_layer = "other"
    best_dist = 999
    for key, layer in _PURPOSE_TO_LAYER.items():
        key_words = key.lower().split()
        for kw in key_words:
            if len(kw) < 4:
                continue  # skip short keywords (api, ui, dto) -- too ambiguous for fuzzy
            threshold = 2 if len(kw) >= 5 else 1
            for pw in purpose_words:
                if len(pw) < 4:
                    continue
                dist = _levenshtein(pw, kw)
                if dist <= threshold and dist < best_dist:
                    best_dist = dist
                    best_layer = layer
    return best_layer


@functools.lru_cache(maxsize=None)
def _classify_layer(module_path: str, purpose: str = "") -> str:
    """Classify using path patterns first, fall back to purpose string.

    Cached: the role guess compares words by edit distance, and the boundary
    check asks for the layer of both ends of every edge.
    """
    result = _classify_layer_by_path(module_path)
    if result == "other" and purpose:
        result = _classify_layer_by_purpose(purpose)
    return result


def _classify_layer_by_path(module_path: str) -> str:
    """Layer from the folders of a module. The leading slash lets "/tests/" match a top-level folder.

    A file named like a layer folder (types.ts, models.py) and the index file of
    such a folder (module ID = the folder) belong to that layer.
    """
    path = "/" + module_path.lower().lstrip("/")
    layer = _classify_layer_fallback(path)
    return layer if layer != "other" else _classify_layer_fallback(path + "/")


# ---------------------------------------------------------------------------
# Commands -- S-expression output (default)
# ---------------------------------------------------------------------------

def _entity_sexp(e: EntityFact, lang: str = "") -> str:
    """Format an entity as an S-expression fact with :lang."""
    parts = [e.kind, e.module, e.name, str(e.line)]
    if lang:
        parts.append(f":lang {lang}")
    if e.bases:
        parts.append(_sexp_list("bases", e.bases))
    if e.deco:
        parts.append(_sexp_list("deco", e.deco))
    if e.methods:
        parts.append(_sexp_list("methods", e.methods))
    return f"({' '.join(parts)})"


def _edge_sexp(e: Edge) -> str:
    """Format an edge as an S-expression fact."""
    flags = " " + " ".join(e.flags) if e.flags else ""
    return f"({e.etype} {e.source} {e.target}{flags})"


def cmd_module(g: CodeGraph, module_id: str) -> None:
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    for nid in matches:
        node = g.nodes[nid]
        fpath = g.module_to_file(nid)
        mod_lang = g.module_lang(nid)
        print(f"(node {nid} :lang {mod_lang} {_q(node.purpose)} :file {_q(fpath)})")
        for e in sorted(g.entities.get(nid, []), key=lambda x: x.line):
            print(_entity_sexp(e, mod_lang))
        for e in sorted(g.edges_from.get(nid, []), key=lambda x: (x.etype, x.target)):
            print(_edge_sexp(e))
        for e in sorted(g.edges_to.get(nid, []), key=lambda x: (x.etype, x.source)):
            if e.source.split("::")[0] != nid:
                print(_edge_sexp(e))


def cmd_deps(g: CodeGraph, module_id: str, max_depth: int) -> None:
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    for start in matches:
        visited: set[str] = {start}
        print(f"(deps-root {start} :lang {g.module_lang(start)})")
        _walk_deps(g, start, visited, max_depth, 1,
                   lambda d, et, tgt, cyc: print(f"(dep {start} {tgt} :lang {g.module_lang(tgt)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))


def cmd_rdeps(g: CodeGraph, module_id: str, max_depth: int) -> None:
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    for start in matches:
        visited: set[str] = {start}
        print(f"(rdeps-root {start} :lang {g.module_lang(start)})")
        _walk_rdeps(g, start, visited, max_depth, 1,
                    lambda d, et, src, cyc: print(f"(rdep {start} {src} :lang {g.module_lang(src)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))


# ---------------------------------------------------------------------------
# Impact: blast radius of changing one file or module
# ---------------------------------------------------------------------------

_IMPACT_SOURCE_EXTENSIONS = tuple(
    sorted(
        _TS_EXTENSIONS
        + _PY_EXTENSIONS
        + _RUBY_EXTENSIONS
        + _RUST_EXTENSIONS
        + _PASCAL_EXTENSIONS
        + _OTHER_EXTENSIONS,
        key=len,
        reverse=True,
    )
)


def _target_to_module_pattern(target: str, root: str) -> str:
    """Turn a source file path (absolute, or relative to the graph root) into a module pattern."""
    path = target
    if root and os.path.isabs(path):
        relative = os.path.relpath(os.path.realpath(path), root)
        if not relative.startswith(".."):
            path = relative
    path = path.replace(os.sep, "/")
    if path.startswith("./"):
        path = path[2:]
    for extension in _IMPACT_SOURCE_EXTENSIONS:
        if path.endswith(extension):
            return path[: -len(extension)]
    return path


def _impact_levels(g: CodeGraph, start: str, max_depth: int) -> list[list[tuple[str, str]]]:
    """Modules that depend on start, grouped by shortest hop distance.

    Each level is a sorted list of (module ID, edge type); a module appears once,
    at the first depth that reaches it.
    """
    seen: set[str] = {start}
    frontier: list[str] = [start]
    levels: list[list[tuple[str, str]]] = []
    for _depth in range(max_depth):
        found: dict[str, str] = {}
        for module in frontier:
            for source, edge_type in sorted(_collect_rdep_mods(g, module).items()):
                if source not in seen and source not in found:
                    found[source] = edge_type
        if not found:
            break
        seen.update(found)
        level = sorted(found.items())
        levels.append(level)
        frontier = [module for module, _edge_type in level]
    return levels


def _impact_file(g: CodeGraph, module_id: str) -> str:
    """File of a module relative to the graph root; the module ID when it is not on disk."""
    path = g.module_to_file(module_id)
    if g._root and os.path.isabs(path):
        relative = os.path.relpath(path, g._root)
        if not relative.startswith(".."):
            return relative.replace(os.sep, "/")
    return path


def _is_package_init(g: CodeGraph, module_id: str) -> bool:
    return module_id.rsplit("/", 1)[-1] == "__init__" and g.module_lang(module_id) == "py"


def _package_layer(g: CodeGraph, module_id: str) -> str:
    """Layer of a Python package, for its __init__.py.

    The folder decides when a folder rule applies. Otherwise the package is
    where most of its modules are: its own modules, or all modules below it
    when it holds only sub-packages. What __init__.py re-exports says nothing.
    """
    by_path = _classify_layer_by_path(module_id)
    if by_path != "other":
        return by_path
    if module_id not in g.package_layers:
        prefix = module_id[: -len("__init__")]
        below = [
            module
            for module in g.nodes
            if module.startswith(prefix) and module != module_id and not _is_package_init(g, module)
        ]
        own = [module for module in below if "/" not in module[len(prefix):]]
        counts = collections.Counter(_graph_layer(g, module) for module in (own or below))
        ranked = counts.most_common(2)
        tied = len(ranked) == 2 and ranked[0][1] == ranked[1][1]
        g.package_layers[module_id] = ranked[0][0] if ranked and not tied else "other"
    return g.package_layers[module_id]


def _graph_layer(g: CodeGraph, module_id: str) -> str:
    """Layer of a module of this graph.

    A JSX component (.tsx) is UI whatever its folder is called: a Next.js route
    folder named `models` holds pages, not the domain model. A Python
    __init__.py is its package and takes the layer of the package.
    """
    if _is_package_init(g, module_id):
        return _package_layer(g, module_id)
    layer = _classify_layer(module_id, g.module_purpose(module_id))
    if layer != "test" and g.module_lang(module_id) == "tsx":
        return "ui"
    return layer


def _layer_is_certain(g: CodeGraph, module_id: str) -> bool:
    """True when the layer comes from the folders or the file type, not from a guess at the role."""
    if _classify_layer_by_path(module_id) != "other":
        return True
    return g.module_lang(module_id) == "tsx"


# Public names used by the report and the map (src/wingbox/overview.py).

def module_layer(g: CodeGraph, module_id: str) -> str:
    """Architecture layer of a module: ui, api, service, domain, infra, shared, config, test or other."""
    return _graph_layer(g, module_id)


def module_layer_basis(g: CodeGraph, module_id: str) -> str:
    """Where the layer of a module comes from: "path" (its folders), "role" (a guess from what the
    file does) or "none" (no rule applies, the layer is "other")."""
    if _layer_is_certain(g, module_id):
        return "path"
    return "role" if module_layer(g, module_id) != "other" else "none"


def module_file(g: CodeGraph, module_id: str) -> str:
    """File of a module relative to the graph root."""
    return _impact_file(g, module_id)


def module_dependencies(g: CodeGraph, module_id: str) -> dict[str, str]:
    """Modules that module_id depends on: module -> edge type."""
    return _collect_dep_mods(g, module_id)


def module_dependents(g: CodeGraph, module_id: str) -> dict[str, str]:
    """Modules that depend on module_id: module -> edge type."""
    return _collect_rdep_mods(g, module_id)


def impact_levels(g: CodeGraph, module_id: str, max_depth: int) -> list[list[tuple[str, str]]]:
    """Dependents of module_id grouped by shortest hop distance."""
    return _impact_levels(g, module_id, max_depth)


# By default an answer names at most this many dependent files; --all prints every one.
_ANSWER_FILE_LIMIT = 200
_READ_FIRST_LIMIT = 10
_KEY_ENTITY_REF_LIMIT = 20


def _rerun(command: str) -> str:
    return '"' + command.replace('"', '\\"') + ' --all"'


def _print_impact_block(title: str, rows: list[tuple[str, str]]) -> None:
    width = max(len(name) for name, _detail in rows)
    print(title)
    for name, detail in rows:
        print(f"  {name:<{width}}   {detail}" if detail else f"  {name}")


_FOLDER_MODULE_FILES = ("/index", "/__init__", "/mod")


def _impact_target_modules(g: CodeGraph, target: str, pattern: str) -> list[str]:
    """Modules named by an impact target.

    A file that shares its name with another one is a module named by its full
    path (src/tool.py next to src/tool.ts), so the path as given is tried first.
    An index file is the module of its folder (src/lib/index.ts -> src/lib).
    """
    as_given = target.replace(os.sep, "/")
    if g._root and os.path.isabs(target):
        relative = os.path.relpath(os.path.realpath(target), g._root)
        if not relative.startswith(".."):
            as_given = relative.replace(os.sep, "/")
    if as_given.startswith("./"):
        as_given = as_given[2:]
    if as_given in g.nodes:
        return [as_given]
    if pattern in g.nodes:
        return [pattern]
    for suffix in _FOLDER_MODULE_FILES:
        if pattern.endswith(suffix) and pattern[: -len(suffix)] in g.nodes:
            return [pattern[: -len(suffix)]]
    return g.resolve_module(pattern)


def cmd_impact(
    g: CodeGraph, target: str, max_depth: int = 2, sexp: bool = False, show_all: bool = False
) -> None:
    """Show what depends on a file or module: the blast radius of changing it.

    Prints readable text for people by default. With sexp=True it prints the
    same facts in the compact S-expression form used by agents. The counts are
    always complete; the lists stop at the file limit unless show_all is set.
    """
    pattern = _target_to_module_pattern(target, g._root)
    matches = _impact_target_modules(g, target, pattern)
    if not matches:
        _print_no_match(g, pattern)
        return

    root_id = matches[0]
    others = matches[1:]
    levels = _impact_levels(g, root_id, max_depth)
    direct = len(levels[0]) if levels else 0
    total = sum(len(level) for level in levels)
    root_file = _impact_file(g, root_id)
    read_first = list(
        dict.fromkeys(
            [root_file] + [_impact_file(g, module) for module, _edge_type in (levels[0] if levels else [])]
        )
    )
    read_first_count = len(read_first)
    if not show_all:
        read_first = read_first[: _READ_FIRST_LIMIT + 1]
    # Nearest dependents first: the limit is spent on depth 1 before depth 2.
    budget = total if show_all else _ANSWER_FILE_LIMIT
    shown_levels: list[list[tuple[str, str]]] = []
    for level in levels:
        shown_levels.append(level[:budget])
        budget -= len(shown_levels[-1])
    hidden = total - sum(len(level) for level in shown_levels)

    if sexp:
        if others:
            print(f"(impact-ambiguous {_q(pattern)} :showing {root_id} :others {' '.join(others)})")
        print(f"(impact-root {root_id} :lang {g.module_lang(root_id)} :file {_q(root_file)})")
        for depth, level in enumerate(shown_levels, start=1):
            for module, edge_type in level:
                print(f"(impact {root_id} {module} :lang {g.module_lang(module)} :via {edge_type} :depth {depth})")
        if hidden:
            print(f"(impact-more {root_id} :hidden {hidden} :rerun {_rerun(f'impact {target} {max_depth}')})")
        print(f"(impact-summary {root_id} :direct {direct} :total {total} :depth {max_depth})")
        for path in read_first:
            print(f"(read-first {_q(path)})")
        if read_first_count > len(read_first):
            print(f"(read-first-more :hidden {read_first_count - len(read_first)})")
        return

    if others:
        print(f"Note: {len(matches)} modules match {pattern}; showing {root_id}. Others: {', '.join(others)}")
        print()
    hops = "hop" if max_depth == 1 else "hops"
    print(f"Impact of changing {root_file}")
    print(f"  {'direct dependents':<20}{direct}")
    print(f"  {f'within {max_depth} {hops}':<20}{total}")
    print()
    if not levels:
        print("No other module depends on it.")
        return
    for depth, level in enumerate(shown_levels, start=1):
        if level:
            _print_impact_block(f"Depth {depth}", [(_impact_file(g, module), edge_type) for module, edge_type in level])
            print()
    if hidden:
        print(f"  ... {hidden} more {'file' if hidden == 1 else 'files'}: rerun with --all")
        print()
    files = "file" if read_first_count == 1 else "files"
    _print_impact_block(f"Read first ({read_first_count} {files})", [(path, "") for path in read_first])
    if read_first_count > len(read_first):
        print(f"  ... {read_first_count - len(read_first)} more: the other files of Depth 1")


def cmd_entity(g: CodeGraph, name: str, positions: bool = False) -> None:
    # Always try exact match first, then safe-pattern fallback.
    definitions: list[EntityFact] = []
    if name in g.entity_by_name:
        definitions = g.entity_by_name[name]
    else:
        parsed_pattern = parse_search_pattern(name)
        if isinstance(parsed_pattern, SearchPatternErr):
            _print_invalid_pattern(name, parsed_pattern.error)
            return
        for ename, ents in g.entity_by_name.items():
            if search_pattern_matches(parsed_pattern.value, ename):
                definitions.extend(ents)

    if not definitions:
        _print_no_match(g, name)
        return

    # Collect refs via index
    matched_names: set[str] = {d.name for d in definitions}
    seen_edge_ids: set[int] = set()
    unique_refs: list[Edge] = []
    for ent_name in matched_names:
        for e in g.edges_by_entity_name.get(ent_name, []):
            eid = id(e)
            if eid not in seen_edge_ids:
                seen_edge_ids.add(eid)
                unique_refs.append(e)

    for defn in definitions:
        print(f"(entity-def {defn.kind} {defn.module} {defn.name} {defn.line} :lang {g.module_lang(defn.module)} :file {_q(g.module_to_file(defn.module))})")
    for e in sorted(unique_refs, key=lambda x: (x.etype, x.source)):
        print(_edge_sexp(e))
    if positions:
        # Every use site, as find usages lists it: file, line, column (1-based).
        sites = sorted(
            (_impact_file(g, source), line, column, source, target)
            for target in sorted({f"{defn.module}::{defn.name}" for defn in definitions})
            for source, line, column in g.uses_by_target.get(target, [])
        )
        for file_path, line, column, source, target in sites:
            print(f"(use {source} {line} {column} {target} :file {_q(file_path)})")


def cmd_flow(g: CodeGraph, module_id: str, entity_name: str) -> None:
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    start_mod = matches[0]
    full_id = f"{start_mod}::{entity_name}"

    visited: set[str] = set()
    _trace_flow(g, full_id, visited, 0)


def _trace_flow(
    g: CodeGraph, entity_id: str, visited: set[str], depth: int,
) -> None:
    if entity_id in visited or depth > 5:
        return
    visited.add(entity_id)

    # Entity-level edges via index
    for edge in g.edges_from_entity.get(entity_id, []):
        tgt_lang = g.module_lang(edge.target.split("::")[0])
        print(f"(flow {entity_id} {edge.target} :lang {tgt_lang} :via {edge.etype} :depth {depth})")
        _trace_flow(g, edge.target, visited, depth + 1)

    # Module-level edges for cross-module flow
    mod = entity_id.split("::")[0] if "::" in entity_id else entity_id
    if "::" in entity_id:
        for edge in g.edges_from.get(mod, []):
            if "::" in edge.source:
                continue
            tgt_mod = edge.target.split("::")[0]
            if tgt_mod not in visited:
                print(f"(flow {entity_id} {edge.target} :lang {g.module_lang(tgt_mod)} :via {edge.etype} :depth {depth} :module-level)")
                _trace_flow(g, edge.target, visited, depth + 1)


def cmd_boundary(g: CodeGraph, module_id: str, files_only: bool = False) -> None:
    """Output the minimal set of files for a change boundary.

    When files_only is True, emit one bare file path per line instead of sexp.
    """
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    nid = matches[0]
    node = g.nodes.get(nid)
    if not node:
        print(f"(error no-node {_q(nid)})")
        return

    target_file = g.module_to_file(nid)
    import_mods = _collect_dep_mods(g, nid)
    dependent_mods = _collect_rdep_mods(g, nid)
    ents = g.entities.get(nid, [])

    if files_only:
        # Compact output: one file path per line, no sexp wrapping.
        seen: set[str] = set()
        for path in [target_file] + [g.module_to_file(m) for m in sorted(import_mods)] + [g.module_to_file(m) for m in sorted(dependent_mods)]:
            if path not in seen:
                seen.add(path)
                print(path)
        return

    lang = g.module_lang(nid)
    print(f"(boundary {nid} :lang {lang} :file {_q(target_file)} :purpose {_q(node.purpose)})")
    for e in sorted(ents, key=lambda x: x.line):
        print(_entity_sexp(e, lang))
    for mod, etype in sorted(import_mods.items()):
        print(f"(boundary-dep {nid} {mod} :lang {g.module_lang(mod)} :file {_q(g.module_to_file(mod))} :relation {etype})")
    for mod, etype in sorted(dependent_mods.items()):
        print(f"(boundary-rdep {nid} {mod} :lang {g.module_lang(mod)} :file {_q(g.module_to_file(mod))} :relation {etype})")
    print(f"(boundary-summary {nid} :total {1 + len(import_mods) + len(dependent_mods)} :deps {len(import_mods)} :rdeps {len(dependent_mods)})")


def cmd_analyze(g: CodeGraph, module_id: str, show_all: bool = False) -> None:
    """Full architectural analysis of a module."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return

    nid = matches[0]
    node = g.nodes.get(nid)
    if not node:
        print(f"(error no-node {_q(nid)})")
        return

    target_file = _impact_file(g, nid)
    ents = g.entities.get(nid, [])
    out_edges = g.edges_from.get(nid, [])
    in_edges = g.edges_to.get(nid, [])

    # Classify outgoing edges
    import_targets: list[str] = []
    ref_targets: list[tuple[str, str]] = []
    data_flow_targets: list[str] = []
    call_targets: list[tuple[str, str]] = []
    for e in out_edges:
        tgt_mod = e.target.split("::")[0]
        if e.etype == "imports" and tgt_mod not in import_targets:
            import_targets.append(tgt_mod)
        elif e.etype == "refs":
            ref_targets.append((e.source, e.target))
        elif e.etype == "data-flow" and tgt_mod not in data_flow_targets:
            data_flow_targets.append(tgt_mod)
        elif e.etype == "calls":
            call_targets.append((e.source, e.target))

    # Classify incoming edges
    importers: set[str] = set()
    referencers: set[str] = set()
    for e in in_edges:
        src_mod = e.source.split("::")[0]
        if src_mod == nid:
            continue
        if e.etype == "imports":
            importers.add(src_mod)
        else:
            referencers.add(src_mod)

    # Key entities cross-repo usage (indexed)
    exported_types = [
        entity
        for entity in ents
        if entity.kind in ("cls", "mod", "ifc", "typ")
        and not entity.name.startswith("_")
    ]
    key_entity_usage: list[tuple[EntityFact, int, set[str]]] = []
    for et in exported_types:
        edges_for_name = g.edges_by_entity_name.get(et.name, [])
        ref_mods: set[str] = set()
        ref_count = 0
        for e in edges_for_name:
            src_mod = e.source.split("::")[0]
            if src_mod != nid:
                ref_count += 1
                ref_mods.add(src_mod)
        if ref_count > 0:
            key_entity_usage.append((et, ref_count, ref_mods))

    # Boundary files: the module and what it imports. The file of a dependent
    # is on its analyze-dependent line, so each file is named once.
    boundary_files: list[str] = [target_file]
    for t in sorted(import_targets):
        f = _impact_file(g, t)
        if f not in boundary_files:
            boundary_files.append(f)

    _analyze_sexp(g, nid, node, target_file, ents, import_targets,
                  ref_targets, data_flow_targets, call_targets,
                  importers, referencers, key_entity_usage,
                  boundary_files, show_all)


def _analyze_sexp(
    g: CodeGraph, nid: str, node: Node, target_file: str,
    ents: list[EntityFact],
    import_targets: list[str], ref_targets: list[tuple[str, str]],
    data_flow_targets: list[str], call_targets: list[tuple[str, str]],
    importers: set[str], referencers: set[str],
    key_entity_usage: list[tuple[EntityFact, int, set[str]]],
    boundary_files: list[str],
    show_all: bool = False,
) -> None:
    rerun = _rerun(f"analyze {nid}")
    # Module header
    lang = g.module_lang(nid)
    print(f"(analyze {nid} :lang {lang} {_q(node.purpose)} :file {_q(target_file)})")

    # Entities
    for e in sorted(ents, key=lambda x: x.line):
        print(_entity_sexp(e, lang))

    # Imports
    for t in sorted(import_targets):
        print(f"(analyze-import {nid} {t} :lang {g.module_lang(t)} :file {_q(_impact_file(g, t))})")

    # Data flows
    for t in sorted(data_flow_targets):
        print(f"(analyze-dataflow {nid} {t} :lang {g.module_lang(t)} :validated)")

    # Entity refs
    for src, tgt in sorted(ref_targets):
        print(f"(refs {src} {tgt})")

    # Call edges
    for src, tgt in sorted(call_targets):
        print(f"(calls {src} {tgt})")

    # Dependents
    all_dependents = importers | referencers
    shown_dependents = sorted(all_dependents) if show_all else sorted(all_dependents)[:_ANSWER_FILE_LIMIT]
    for src in shown_dependents:
        kinds = []
        if src in importers:
            kinds.append("imports")
        if src in referencers:
            kinds.append("refs")
        print(f"(analyze-dependent {nid} {src} :lang {g.module_lang(src)} :via {' '.join(kinds)} :file {_q(_impact_file(g, src))})")
    if len(all_dependents) > len(shown_dependents):
        print(f"(analyze-more {nid} :dependents {len(all_dependents) - len(shown_dependents)} :rerun {rerun})")

    # Dependency tree (depth 2)
    visited: set[str] = {nid}
    _walk_deps(g, nid, visited, 2, 1,
               lambda d, et, tgt, cyc: print(f"(dep {nid} {tgt} :lang {g.module_lang(tgt)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))

    # Key entities: the count is complete, the modules are a sample unless show_all
    for et, ref_count, ref_mods in key_entity_usage:
        print(f"(key-entity {nid} {et.name} :lang {lang} :kind {et.kind} :line {et.line} :refs {ref_count} :modules {len(ref_mods)})")
        shown_refs = sorted(ref_mods) if show_all else sorted(ref_mods)[:_KEY_ENTITY_REF_LIMIT]
        for rm in shown_refs:
            print(f"(key-entity-ref {et.name} {rm} :lang {g.module_lang(rm)})")
        if len(ref_mods) > len(shown_refs):
            print(f"(key-entity-more {nid} {et.name} :modules {len(ref_mods) - len(shown_refs)} :rerun {rerun})")

    # Boundary files
    for f in boundary_files:
        print(f"(boundary-file {nid} {_q(f)})")

    # Architecture layers
    for t in sorted(import_targets):
        layer = _graph_layer(g, t)
        print(f"(layer {nid} {t.split('/')[-1]} :layer {layer})")

    # Summary
    print(f"(analyze-summary {nid} :entities {len(ents)} :imports {len(import_targets)} :dataflows {len(data_flow_targets)} :refs {len(ref_targets)} :dependents {len(all_dependents)} :boundary-files {len(boundary_files)})")


def cmd_quick(g: CodeGraph, keyword: str, sexp_path: str, show_all: bool = False) -> None:
    """Search for keyword then run analyze for the first match.

    Resolution order:
    1. resolve_module (exact/suffix/substring/case-insensitive)
    2. Literal search across the raw sexp file, extract module IDs from matches

    Runs cmd_analyze on the first unique module found.
    """
    matches = g.resolve_module(keyword)
    if matches:
        cmd_analyze(g, matches[0], show_all)
        return

    # Fallback: grep the sexp file for the keyword and extract module IDs from lines
    regex = re.compile(re.escape(keyword), re.IGNORECASE)
    seen_modules: list[str] = []
    seen_set: set[str] = set()
    with open(sexp_path, "r") as f:
        for line in f:
            if not regex.search(line):
                continue
            tokens = tokenize(line)
            if not tokens:
                continue
            # Heuristically grab module-like tokens and resolve legacy path-map aliases.
            for tok in tokens[1:3]:
                resolved = g.resolve_alias(tok)
                if resolved in g.nodes and resolved not in seen_set:
                    seen_set.add(resolved)
                    seen_modules.append(resolved)
                    break

    if not seen_modules:
        _print_no_match(g, keyword)
        return

    cmd_analyze(g, seen_modules[0], show_all)


def cmd_search(_g: CodeGraph, pattern: str, sexp_path: str) -> None:
    """Search module IDs first, then fall back to raw fact grep."""
    parsed_pattern = parse_search_pattern(pattern)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(pattern, parsed_pattern.error)
        return
    module_matches = _g.search_modules(parsed_pattern.value)
    if module_matches:
        for nid in module_matches:
            print(f"(result {nid})")
        print(f"(result-count {len(module_matches)})")
        return

    try:
        with open(sexp_path, "r", encoding="utf-8") as code_graph:
            for line in code_graph:
                if search_pattern_matches(parsed_pattern.value, line):
                    print(line.rstrip())
    except OSError as error:
        print(f"(error search-read-failed {_q(str(error))})")


# Well-known deny rules (source layer, target layer, reason).
BOUNDARY_DENY_RULES: tuple[tuple[str, str, str], ...] = (
    ("domain", "infra",   "domain imports infrastructure directly"),
    ("domain", "api",     "domain imports API layer"),
    ("domain", "ui",      "domain imports UI layer"),
    ("ui",     "infra",   "UI imports infrastructure directly"),
    ("service","api",     "service imports API layer (inversion)"),
    ("infra",  "api",     "infrastructure imports API layer"),
)


def boundary_violations(
    g: CodeGraph, scope_mods: set[str]
) -> list[tuple[str, str, str, str, str, str]]:
    """Dependencies that break a layer rule.

    Each item is (source module, target module, source layer, target layer,
    edge type, reason), for the import and data-flow edges of scope_mods.
    """
    violations: list[tuple[str, str, str, str, str, str]] = []
    for src_mod in sorted(scope_mods):
        src_layer = _graph_layer(g, src_mod)
        for edge in g.edges_from.get(src_mod, []):
            if edge.etype not in ("imports", "data-flow"):
                continue
            tgt_mod = edge.target.split("::")[0]
            if tgt_mod == src_mod:
                continue
            tgt_layer = _graph_layer(g, tgt_mod)
            if src_layer == tgt_layer:
                continue
            for deny_src, deny_tgt, reason in BOUNDARY_DENY_RULES:
                if src_layer == deny_src and tgt_layer == deny_tgt:
                    violations.append((src_mod, tgt_mod, src_layer, tgt_layer, edge.etype, reason))
                    break
    return violations


def is_type_only_import(g: CodeGraph, src_mod: str, tgt_mod: str) -> bool:
    """True when src_mod imports tgt_mod for types only (the builder marks the edge :type-only)."""
    return any(
        edge.etype == "imports" and edge.target.split("::")[0] == tgt_mod and ":type-only" in edge.flags
        for edge in g.edges_from.get(src_mod, [])
    )


def cmd_boundaries(g: CodeGraph, module_id: str) -> None:
    """Check boundary violations inferred from architecture layer patterns.

    Uses path-based layer classification and well-known architecture rules:
    - domain must not import infra/api/ui
    - ui must not import infra
    - service must not import api
    - infra must not import api
    """
    if module_id == "all":
        scope_mods = set(g.nodes.keys())
    else:
        matches = g.resolve_module(module_id)
        if not matches:
            _print_no_match(g, module_id)
            return
        scope_mods = set(matches)

    violations = boundary_violations(g, scope_mods)

    if not violations:
        print(f"(boundaries-ok :checked {len(scope_mods)})")
        return
    # A pair is a violation only when both layers are certain (folders or file type).
    # A layer guessed from the role of a file makes the pair a candidate to read.
    # An import of types only is erased at build time: reported apart, not a violation.
    # The summary counts pairs of files; a pair is printed once per relation.
    pairs: dict[str, set[tuple[str, str]]] = {"violation": set(), "violation-type-only": set(), "violation-candidate": set()}
    for src, tgt, sl, tl, etype, reason in sorted(violations):
        src_file = g.module_to_file(src)
        tgt_file = g.module_to_file(tgt)
        if etype == "imports" and is_type_only_import(g, src, tgt):
            fact = "violation-type-only"
        elif _layer_is_certain(g, src) and _layer_is_certain(g, tgt):
            fact = "violation"
        else:
            fact = "violation-candidate"
        pairs[fact].add((src, tgt))
        print(f"({fact} {src} {tgt} :src-layer {sl} :tgt-layer {tl} :via {etype} :lang {g.module_lang(src)} :src-file {_q(src_file)} :tgt-file {_q(tgt_file)} {_q(reason)})")
    print(
        f"(boundaries-summary :violations {len(pairs['violation'])} :type-only {len(pairs['violation-type-only'])} "
        f":candidates {len(pairs['violation-candidate'])} :checked {len(scope_mods)})"
    )


def cmd_layers(g: CodeGraph) -> None:
    """Show layer classification for all modules, grouped by layer."""
    by_layer: dict[str, list[str]] = {}
    for nid in sorted(g.nodes.keys()):
        layer = _graph_layer(g, nid)
        by_layer.setdefault(layer, []).append(nid)

    for layer_name in sorted(by_layer.keys()):
        mods = by_layer[layer_name]
        print(f"(layer-group {layer_name} :count {len(mods)})")
        for m in mods:
            print(f"(layer-member {layer_name} {m} :lang {g.module_lang(m)})")


# ---------------------------------------------------------------------------
# Naming convention invariant checker
# ---------------------------------------------------------------------------

# Allowed naming conventions per language.  A module whose :naming value is
# NOT in the allowed set is a violation.  Languages absent from this map have
# no strict rule and are always considered conforming.
_NAMING_RULES: dict[str, set[str]] = {
    "py":  {"snake_case", "mixed", "kebab-case"},
    "rb":  {"snake_case", "mixed"},
    "tsx": {"PascalCase", "camelCase", "kebab-case", "mixed"},
    "ts":  {"camelCase", "PascalCase", "kebab-case", "mixed", "snake_case"},
}


def cmd_invariants(g: CodeGraph, scope: str) -> None:
    """Check naming-convention violations across modules.

    For each module that has a :naming annotation, validate the value against
    the allowed set for its language.  Modules without a :naming annotation
    are silently skipped (no data, no opinion).

    Output format::

        (invariant-violation <mod> :expected snake_case :actual PascalCase :lang py :pkg <pkg>)
        (invariants-summary :violations 12 :checked 3587)
        (invariants-ok :checked 3587)
    """
    if scope == "all":
        candidates = list(g.nodes.values())
    else:
        matches = g.resolve_module(scope)
        if not matches:
            _print_no_match(g, scope)
            return
        candidates = [g.nodes[nid] for nid in matches if nid in g.nodes]

    checked = 0
    violations: list[tuple[str, str, str, str, str]] = []  # (nid, expected, actual, lang, pkg)

    for node in candidates:
        if not node.naming:
            continue
        checked += 1
        lang = {"ty": "ts", "tx": "tsx"}.get(node.lang, node.lang)
        allowed = _NAMING_RULES.get(lang)
        if allowed is None:
            # No rule defined for this language -- always conforming.
            continue
        if node.naming not in allowed:
            expected_repr = " ".join(sorted(allowed))
            violations.append((node.nid, expected_repr, node.naming, lang, node.pkg))

    if not violations:
        print(f"(invariants-ok :checked {checked})")
        return

    for nid, expected, actual, lang, pkg in sorted(violations):
        pkg_part = f" :pkg {_q(pkg)}" if pkg else ""
        print(
            f"(invariant-violation {nid}"
            f" :expected {_q(expected)}"
            f" :actual {actual}"
            f" :lang {lang}"
            f"{pkg_part})"
        )
    print(f"(invariants-summary :violations {len(violations)} :checked {checked})")


# ---------------------------------------------------------------------------
# Function / type search and call graph commands
# ---------------------------------------------------------------------------

def _short_function_name(entity_name: str) -> str:
    separator_index = max(entity_name.rfind("#"), entity_name.rfind("."))
    if separator_index < 0:
        return entity_name
    return entity_name[separator_index + 1 :]


def cmd_find_function(g: CodeGraph, name: str) -> None:
    """Find function/method definitions matching a name pattern.

    Searches fn entities and cls methods. Exact match first, then safe pattern.
    """
    parsed_pattern = parse_search_pattern(name)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(name, parsed_pattern.error)
        return
    results: list[tuple[EntityFact, str]] = []
    precise_methods: set[tuple[str, str]] = set()

    for ent_name, ent_list in g.entity_by_name.items():
        for ent in ent_list:
            if ent.kind == "fn":
                short_name = _short_function_name(ent_name)
                precise_methods.add((ent.module, short_name))
                if (
                    ent_name == name
                    or short_name == name
                    or search_pattern_matches(parsed_pattern.value, ent_name)
                    or search_pattern_matches(parsed_pattern.value, short_name)
                ):
                    results.append((ent, ""))

    # Also search methods inside classes
    for _mod, ent_list in g.entities.items():
        for ent in ent_list:
            if ent.kind in ("cls", "mod") and ent.methods:
                for method in ent.methods:
                    if (ent.module, method) in precise_methods:
                        continue
                    if (
                        method == name
                        or search_pattern_matches(parsed_pattern.value, method)
                    ):
                        results.append((ent, method))

    if not results:
        _print_no_match(g, name)
        return

    seen: set[str] = set()
    for ent, method in sorted(results, key=lambda x: (x[0].module, x[0].line)):
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        if method:
            key = f"{ent.module}::{ent.name}.{method}"
            if key not in seen:
                seen.add(key)
                print(f"(fn-def {ent.module}::{ent.name}.{method} :line {ent.line} :lang {lang} :file {_q(fpath)} :kind method)")
        else:
            key = f"{ent.module}::{ent.name}"
            if key not in seen:
                seen.add(key)
                function_kind = (
                    "method"
                    if _short_function_name(ent.name) != ent.name
                    else "function"
                )
                print(f"(fn-def {ent.module}::{ent.name} :line {ent.line} :lang {lang} :file {_q(fpath)} :kind {function_kind})")


def cmd_find_type(g: CodeGraph, name: str) -> None:
    """Find type/class/interface/enum definitions matching a name pattern.

    Searches cls, mod, ifc, typ, enm entities.
    """
    parsed_pattern = parse_search_pattern(name)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(name, parsed_pattern.error)
        return
    results: list[EntityFact] = []

    for ent_name, ent_list in g.entity_by_name.items():
        for ent in ent_list:
            if ent.kind in ("cls", "mod", "ifc", "typ", "enm"):
                if (
                    ent_name == name
                    or search_pattern_matches(parsed_pattern.value, ent_name)
                ):
                    results.append(ent)

    if not results:
        _print_no_match(g, name)
        return

    for ent in sorted(results, key=lambda x: (x.module, x.line)):
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        extras = ""
        if ent.bases:
            extras += " " + _sexp_list("bases", ent.bases)
        if ent.methods:
            extras += " " + _sexp_list("methods", ent.methods)
        print(f"(type-def {ent.kind} {ent.module}::{ent.name} :line {ent.line} :lang {lang} :file {_q(fpath)}{extras})")


def _resolve_fn_entity(g: CodeGraph, fn_name: str) -> list[tuple[str, EntityFact]]:
    """Resolve a function name to (qualified_id, entity) pairs.

    Returns entity-qualified IDs like 'module::fnName'. Searches both
    standalone functions and class methods.
    """
    pattern = re.compile(re.escape(fn_name), re.IGNORECASE)
    results: list[tuple[str, EntityFact]] = []

    precise_methods: set[tuple[str, str]] = set()
    for ent_name, ent_list in g.entity_by_name.items():
        for ent in ent_list:
            if ent.kind != "fn":
                continue
            short_name = _short_function_name(ent_name)
            precise_methods.add((ent.module, short_name))
            if (
                ent_name == fn_name
                or short_name == fn_name
                or pattern.search(ent_name)
                or pattern.search(short_name)
            ):
                results.append((f"{ent.module}::{ent_name}", ent))

    # Also search class and module methods
    for _mod, ent_list in g.entities.items():
        for ent in ent_list:
            if ent.kind in ("cls", "mod") and ent.methods:
                for method in ent.methods:
                    if (ent.module, method) in precise_methods:
                        continue
                    if method == fn_name or pattern.search(method):
                        results.append((f"{ent.module}::{method}", ent))

    return results


def cmd_find_callers(g: CodeGraph, fn_name: str, max_depth: int) -> None:
    """Find all callers of a function (incoming call graph).

    Uses 'calls' edges from the code graph. Falls back to refs edges
    when calls edges are unavailable.
    """
    targets = _resolve_fn_entity(g, fn_name)
    if not targets:
        _print_no_match(g, fn_name)
        return

    target_mods: set[str] = set()
    target_qualified: set[str] = set()
    for qid, ent in targets:
        target_mods.add(ent.module)
        target_qualified.add(qid)
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        print(f"(callers-target {qid} :line {ent.line} :lang {lang} :file {_q(fpath)})")

    # Collect direct callers via calls edges
    direct_callers: list[Edge] = []
    for e in g.all_edges:
        if e.etype == "calls":
            tgt_fn = e.target.split("::")[-1] if "::" in e.target else ""
            if e.target in target_qualified or tgt_fn == fn_name:
                direct_callers.append(e)

    if direct_callers:
        for e in sorted(direct_callers, key=lambda x: (x.source, x.target)):
            src_mod = e.source.split("::")[0]
            print(f"(caller {e.source} -> {e.target} :lang {g.module_lang(src_mod)} :file {_q(g.module_to_file(src_mod))} {' '.join(e.flags)})")
    else:
        # Fallback: use module-level edges (imports/refs) pointing at target modules
        print("; no calls edges found, showing module-level callers via imports/refs")

    # Walk reverse deps to given depth
    for tgt_mod in target_mods:
        visited: set[str] = {tgt_mod}
        _walk_rdeps(g, tgt_mod, visited, max_depth, 1,
                    lambda d, et, src, cyc: print(
                        f"(caller-module {src} -> {tgt_mod} :via {et} :depth {d} "
                        f":lang {g.module_lang(src)} :file {_q(g.module_to_file(src))}"
                        f"{' :cycle' if cyc else ''})"))


def cmd_find_calls(g: CodeGraph, fn_name: str, max_depth: int) -> None:
    """Find all functions called by a given function (outgoing call graph).

    Uses 'calls' edges from the code graph.
    """
    sources = _resolve_fn_entity(g, fn_name)
    if not sources:
        _print_no_match(g, fn_name)
        return

    source_mods: set[str] = set()
    source_qualified: set[str] = set()
    for qid, ent in sources:
        source_mods.add(ent.module)
        source_qualified.add(qid)
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        print(f"(calls-source {qid} :line {ent.line} :lang {lang} :file {_q(fpath)})")

    # Collect direct calls via calls edges
    direct_calls: list[Edge] = []
    for e in g.all_edges:
        if e.etype == "calls":
            src_fn = e.source.split("::")[-1] if "::" in e.source else ""
            if e.source in source_qualified or src_fn == fn_name:
                direct_calls.append(e)

    if direct_calls:
        for e in sorted(direct_calls, key=lambda x: (x.source, x.target)):
            tgt_mod = e.target.split("::")[0]
            print(f"(calls-target {e.source} -> {e.target} :lang {g.module_lang(tgt_mod)} :file {_q(g.module_to_file(tgt_mod))} {' '.join(e.flags)})")
    else:
        print("; no calls edges found, showing module-level deps via imports")

    # Walk forward deps to given depth
    for src_mod in source_mods:
        visited: set[str] = {src_mod}
        _walk_deps(g, src_mod, visited, max_depth, 1,
                   lambda d, et, tgt, cyc: print(
                       f"(calls-module {src_mod} -> {tgt} :via {et} :depth {d} "
                       f":lang {g.module_lang(tgt)} :file {_q(g.module_to_file(tgt))}"
                       f"{' :cycle' if cyc else ''})"))


def cmd_find_callchain(g: CodeGraph, src_fn: str, tgt_fn: str, max_depth: int) -> None:
    """Trace a call path between two functions via BFS on calls edges.

    Falls back to module-level path if no function-level calls edges exist.
    """
    src_entities = _resolve_fn_entity(g, src_fn)
    tgt_entities = _resolve_fn_entity(g, tgt_fn)

    if not src_entities:
        print(f"(error no-match-source {_q(src_fn)})")
        return
    if not tgt_entities:
        print(f"(error no-match-target {_q(tgt_fn)})")
        return

    src_ids = {qid for qid, _ in src_entities}
    tgt_ids = {qid for qid, _ in tgt_entities}
    tgt_fn_names = {qid.split("::")[-1] for qid in tgt_ids}

    # Build adjacency from calls edges
    call_adj: dict[str, list[str]] = {}
    for e in g.all_edges:
        if e.etype == "calls":
            call_adj.setdefault(e.source, []).append(e.target)

    # BFS from source to target
    from collections import deque

    found_path: list[str] = []
    for start in src_ids:
        queue: deque[list[str]] = deque([[start]])
        visited: set[str] = {start}
        while queue:
            path = queue.popleft()
            if len(path) - 1 > max_depth:
                break
            current = path[-1]
            cur_fn = current.split("::")[-1] if "::" in current else ""
            if current in tgt_ids or cur_fn in tgt_fn_names:
                found_path = path
                break
            for nxt in call_adj.get(current, []):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])
        if found_path:
            break

    if found_path:
        for i, node in enumerate(found_path):
            mod = node.split("::")[0]
            lang = g.module_lang(mod)
            fpath = g.module_to_file(mod)
            print(f"(callchain-step {i} {node} :lang {lang} :file {_q(fpath)})")
        print(f"(callchain-summary :length {len(found_path)} :hops {len(found_path) - 1})")
        return

    # Fallback: module-level BFS using both forward deps AND reverse deps
    src_mods = {ent.module for _, ent in src_entities}
    tgt_mods = {ent.module for _, ent in tgt_entities}

    print("; no function-level call path found, trying module-level path")

    # Build module adjacency (forward deps + shared entity refs for better connectivity)
    mod_adj: dict[str, set[str]] = {}
    for mod in g.nodes:
        neighbors = set(_collect_dep_mods(g, mod).keys())
        # Also add reverse deps (bidirectional search for reachability)
        neighbors.update(_collect_rdep_mods(g, mod).keys())
        mod_adj[mod] = neighbors

    for start_mod in src_mods:
        queue_m: deque[list[str]] = deque([[start_mod]])
        visited_m: set[str] = {start_mod}
        mod_path: list[str] = []
        while queue_m:
            path_m = queue_m.popleft()
            if len(path_m) - 1 > max_depth:
                break
            current_m = path_m[-1]
            if current_m in tgt_mods:
                mod_path = path_m
                break
            for nxt_m in sorted(mod_adj.get(current_m, set())):
                if nxt_m not in visited_m:
                    visited_m.add(nxt_m)
                    queue_m.append(path_m + [nxt_m])
        if mod_path:
            for i, mod in enumerate(mod_path):
                lang = g.module_lang(mod)
                fpath = g.module_to_file(mod)
                print(f"(callchain-module-step {i} {mod} :lang {lang} :file {_q(fpath)})")
            print(f"(callchain-module-summary :length {len(mod_path)} :hops {len(mod_path) - 1})")
            return

    print(f"(callchain-none {_q(src_fn)} -> {_q(tgt_fn)} :max-depth {max_depth})")


def cmd_grep_functions(g: CodeGraph, pattern: str) -> None:
    """Search function bodies using the supported linear pattern grammar.

    For each function in the code graph, reads the source file and searches
    the function body (from definition line to next entity or EOF).
    """
    parsed_pattern = parse_search_pattern(pattern)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(pattern, parsed_pattern.error)
        return
    results: list[tuple[str, str, int, str]] = []  # (module::fn, file, match_line, match_text)

    # Group entities by file for efficient reading
    file_entities: dict[str, list[tuple[str, EntityFact]]] = {}
    for mod, ent_list in g.entities.items():
        fpath = g.module_to_file(mod)
        if fpath == mod:
            continue  # file not found on disk
        for ent in ent_list:
            if ent.kind == "fn":
                file_entities.setdefault(fpath, []).append((mod, ent))

    for fpath, fn_list in sorted(file_entities.items()):
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", errors="replace") as f:
                lines = f.readlines()
        except OSError as error:
            print(
                f"(error source-read-failed {_q(fpath)} {_q(str(error))})"
            )
            continue

        # Sort functions by line number to determine body ranges
        sorted_fns = sorted(fn_list, key=lambda x: x[1].line)

        for idx, (mod, ent) in enumerate(sorted_fns):
            start = ent.line - 1  # 0-indexed
            if start < 0 or start >= len(lines):
                continue
            # End at next entity's line or +100 lines, whichever is smaller
            if idx + 1 < len(sorted_fns):
                end = min(sorted_fns[idx + 1][1].line - 1, start + 100, len(lines))
            else:
                end = min(start + 100, len(lines))

            for line_idx in range(start, end):
                line = lines[line_idx]
                if search_pattern_matches(parsed_pattern.value, line):
                    results.append((
                        f"{mod}::{ent.name}",
                        fpath,
                        line_idx + 1,
                        line.rstrip(),
                    ))

    if not results:
        print(f"(grep-functions-empty {_q(pattern)})")
        return

    for qid, fpath, line_num, text in results:
        lang = g.module_lang(qid.split("::")[0])
        print(f"(grep-fn {qid} :line {line_num} :lang {lang} :file {_q(fpath)} :match {_q(text)})")
    print(f"(grep-functions-summary :matches {len(results)} :pattern {_q(pattern)})")


def cmd_diff_functions(g: CodeGraph, ref: str) -> None:
    """Identify which functions were changed in a git diff.

    Parses 'git diff <ref>' output to find changed line ranges,
    then maps those ranges to function definitions in the code graph.
    """
    import subprocess

    # Use the code graph root as git working directory (not cwd)
    git_cwd = g._root or None

    try:
        result = subprocess.run(
            ["git", "diff", "--unified=0", ref],
            capture_output=True, text=True, timeout=30, cwd=git_cwd,
        )
        if result.returncode != 0:
            # Try as a commit ref
            result = subprocess.run(
                ["git", "diff", "--unified=0", ref, "HEAD"],
                capture_output=True, text=True, timeout=30, cwd=git_cwd,
            )
            if result.returncode != 0:
                print(f"(error git-diff-failed {_q(result.stderr.strip())})")
                return
    except FileNotFoundError:
        print("(error git-not-found)")
        return
    except subprocess.TimeoutExpired:
        print("(error git-diff-timeout)")
        return

    # Parse diff output to extract file -> changed line ranges
    changed_ranges: dict[str, list[tuple[int, int]]] = {}  # file -> [(start, end)]
    current_file = ""

    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@ ") and current_file:
            # Parse hunk header: @@ -old,count +new,count @@
            parts = line.split(" ")
            for part in parts:
                if part.startswith("+") and "," in part:
                    nums = part[1:].split(",")
                    start = int(nums[0])
                    count = int(nums[1]) if len(nums) > 1 else 1
                    if count > 0:
                        changed_ranges.setdefault(current_file, []).append(
                            (start, start + count - 1)
                        )
                elif part.startswith("+") and part[1:].isdigit():
                    start = int(part[1:])
                    changed_ranges.setdefault(current_file, []).append(
                        (start, start)
                    )

    if not changed_ranges:
        print(f"(diff-functions-empty {_q(ref)})")
        return

    # Map changed lines to functions
    # Build file -> sorted entities index
    file_entities: dict[str, list[tuple[str, EntityFact]]] = {}
    for mod, ent_list in g.entities.items():
        fpath = g.module_to_file(mod)
        for ent in ent_list:
            file_entities.setdefault(fpath, []).append((mod, ent))

    # Normalize paths for matching (diff paths are relative to repo root)
    repo_root = g._root
    results: list[tuple[str, EntityFact, str]] = []  # (qualified_id, entity, file)

    for diff_file, ranges in changed_ranges.items():
        # Try matching against code graph files
        abs_path = os.path.join(repo_root, diff_file)
        # Also try relative path
        candidates = [abs_path, diff_file]

        matched_ents: list[tuple[str, EntityFact]] = []
        for candidate in candidates:
            if candidate in file_entities:
                matched_ents = file_entities[candidate]
                break

        if not matched_ents:
            # Try suffix matching
            for fpath, ent_list in file_entities.items():
                if fpath.endswith(diff_file) or diff_file.endswith(fpath.lstrip("./")):
                    matched_ents = ent_list
                    break

        if not matched_ents:
            continue

        # Sort entities by line to determine body ranges
        sorted_ents = sorted(matched_ents, key=lambda x: x[1].line)

        for start, end in ranges:
            for idx, (mod, ent) in enumerate(sorted_ents):
                ent_start = ent.line
                if idx + 1 < len(sorted_ents):
                    ent_end = sorted_ents[idx + 1][1].line - 1
                else:
                    ent_end = ent_start + 200  # approximate

                # Check overlap
                if start <= ent_end and end >= ent_start:
                    qid = f"{mod}::{ent.name}"
                    if not any(r[0] == qid for r in results):
                        results.append((qid, ent, diff_file))

    if not results:
        # Report changed files even if no function mapping
        for diff_file in sorted(changed_ranges.keys()):
            print(f"(diff-file {_q(diff_file)} :hunks {len(changed_ranges[diff_file])})")
        print(f"(diff-functions-no-match :files {len(changed_ranges)} :ref {_q(ref)})")
        return

    for qid, ent, diff_file in sorted(results, key=lambda x: (x[2], x[1].line)):
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        print(f"(diff-fn {qid} :kind {ent.kind} :line {ent.line} :lang {lang} :file {_q(fpath)})")
    print(f"(diff-functions-summary :changed {len(results)} :files {len(changed_ranges)} :ref {_q(ref)})")


# ---------------------------------------------------------------------------
# Scheme query REPL -- composable S-expression queries on the graph
# ---------------------------------------------------------------------------
#
# A tiny Scheme-dialect evaluator that exposes the code graph as queryable
# data. Every value is either a string, int, bool, list, or a "module-set"
# (set of module IDs). Functions compose naturally:
#
#   (deps "operatives.controller" 2)        -> module-set of dependencies
#   (rdeps "operatives.controller" 1)       -> module-set of reverse deps
#   (boundary "operatives.controller")      -> module-set (target + deps + rdeps)
#   (entity "OperativesService")            -> list of module IDs defining it
#   (imports "operatives.controller")       -> module-set of direct imports
#   (dependents "operatives.controller")    -> module-set of direct dependents
#   (intersect (deps "A" 2) (rdeps "B" 1)) -> set intersection
#   (union A B)                             -> set union
#   (diff A B)                              -> set difference
#   (filter SET "pattern")                  -> grep within set
#   (files SET)                             -> resolve module IDs to file paths
#   (count SET)                             -> cardinality
#   (analyze "id")                          -> full analysis (prints sexp)
#   (search "pattern")                      -> raw grep on sexp file

_QUERY_HELP = """\
; Scheme query language for .code_graph.sexp
;
; Primitives (return module-set or list):
;   (resolve "pattern")                Find module IDs matching pattern
;   (deps "id" depth)                  Outgoing dependency set (default depth 2)
;   (rdeps "id" depth)                 Reverse dependency set (default depth 1)
;   (imports "id")                     Direct import targets
;   (dependents "id")                  Direct dependents (who imports this)
;   (boundary "id")                    Target + deps + rdeps (change boundary)
;   (entity "Name")                    Modules defining entity
;   (refs-to "Name")                   Modules referencing entity
;   (search "pattern")                 Grep .code_graph.sexp
;
; Function & type search (return module-set):
;   (find-function "name")             Modules containing function definitions
;   (find-type "name")                 Modules containing type/class/ifc/enum defs
;   (find-callers "fn" depth)          Modules that call a function (default depth 2)
;   (find-calls "fn" depth)            Modules that a function calls (default depth 2)
;   (diff-functions "ref")             Print changed functions (side-effect, returns empty)
;
; Set operations:
;   (intersect A B)                    Set intersection
;   (union A B ...)                    Set union
;   (diff A B)                         A minus B
;   (filter SET "pattern")             Keep members matching safe pattern
;
; Output:
;   (files SET)                        Resolve to file paths
;   (count SET)                        Count members
;   (analyze "id")                     Full analysis output
;
; Composition:
;   (files (intersect (deps "A" 2) (rdeps "B" 1)))
;   (filter (boundary "X") "dto")
;   (count (deps "operatives.service" 3))
;   (files (find-callers "createOrder" 3))
;   (intersect (find-function "handle") (deps "api" 2))
"""


class _SchemeEval:
    """Tiny Scheme evaluator for code graph queries."""

    def __init__(self, g: CodeGraph, sexp_path: str) -> None:
        self._g = g
        self._sexp_path = sexp_path

    def eval_str(self, expr_str: str) -> object:
        tokens = self._tokenize(expr_str)
        ast = self._parse(tokens)
        return self._eval(ast)

    # -- Tokenizer --

    @staticmethod
    def _tokenize(s: str) -> list[str]:
        tokens: list[str] = []
        i = 0
        while i < len(s):
            ch = s[i]
            if ch in (" ", "\t", "\n", "\r"):
                i += 1
            elif ch == ";":
                while i < len(s) and s[i] != "\n":
                    i += 1
            elif ch in ("(", ")"):
                tokens.append(ch)
                i += 1
            elif ch == '"':
                j = i + 1
                while j < len(s) and s[j] != '"':
                    if s[j] == "\\":
                        j += 1
                    j += 1
                tokens.append(s[i + 1 : j])
                i = j + 1
            else:
                j = i
                while j < len(s) and s[j] not in (" ", "\t", "\n", "\r", "(", ")", '"', ";"):
                    j += 1
                tokens.append(s[i:j])
                i = j
        return tokens

    # -- Parser: tokens -> nested lists --

    @staticmethod
    def _parse(tokens: list[str]) -> object:
        if not tokens:
            return []
        pos = [0]

        def _read() -> object:
            if pos[0] >= len(tokens):
                return None
            tok = tokens[pos[0]]
            if tok == "(":
                pos[0] += 1
                lst: list[object] = []
                while pos[0] < len(tokens) and tokens[pos[0]] != ")":
                    lst.append(_read())
                pos[0] += 1  # skip )
                return lst
            if tok == ")":
                pos[0] += 1
                return None
            pos[0] += 1
            # Try int
            try:
                return int(tok)
            except ValueError:
                return tok

        result = _read()
        return result

    # -- Evaluator --

    def _eval(self, ast: object) -> object:
        if isinstance(ast, (int, float)):
            return ast
        if isinstance(ast, str):
            return ast
        if not isinstance(ast, list) or len(ast) == 0:
            return set()

        head = ast[0]
        args = ast[1:]

        # Evaluate args (eager)
        evaled = [self._eval(a) for a in args]
        for value in evaled:
            if isinstance(value, QueryError):
                return value

        return self._apply(head, evaled)

    def _apply(self, fn: str, args: list[object]) -> object:
        g = self._g

        if fn == "resolve":
            pattern = str(args[0]) if args else ""
            return set(g.resolve_module(pattern))

        if fn == "deps":
            pattern = str(args[0]) if args else ""
            depth, depth_error = (
                _parse_depth(str(args[1])) if len(args) > 1 else (2, None)
            )
            if depth_error is not None or depth is None:
                return QueryError("invalid-depth", depth_error or "invalid depth")
            matches = g.resolve_module(pattern)
            result: set[str] = set()
            for m in matches:
                visited: set[str] = {m}
                self._collect_deps(m, visited, depth, 1, result)
            return result

        if fn == "rdeps":
            pattern = str(args[0]) if args else ""
            depth, depth_error = (
                _parse_depth(str(args[1])) if len(args) > 1 else (1, None)
            )
            if depth_error is not None or depth is None:
                return QueryError("invalid-depth", depth_error or "invalid depth")
            matches = g.resolve_module(pattern)
            result = set()
            for m in matches:
                visited = {m}
                self._collect_rdeps(m, visited, depth, 1, result)
            return result

        if fn == "imports":
            pattern = str(args[0]) if args else ""
            matches = g.resolve_module(pattern)
            result = set()
            for m in matches:
                result.update(_collect_dep_mods(g, m).keys())
            return result

        if fn == "dependents":
            pattern = str(args[0]) if args else ""
            matches = g.resolve_module(pattern)
            result = set()
            for m in matches:
                result.update(_collect_rdep_mods(g, m).keys())
            return result

        if fn == "boundary":
            pattern = str(args[0]) if args else ""
            matches = g.resolve_module(pattern)
            result = set()
            for m in matches:
                result.add(m)
                result.update(_collect_dep_mods(g, m).keys())
                result.update(_collect_rdep_mods(g, m).keys())
            return result

        if fn == "entity":
            name = str(args[0]) if args else ""
            defs = g.entity_by_name.get(name, [])
            if not defs:
                parsed_pattern = parse_search_pattern(name)
                if isinstance(parsed_pattern, SearchPatternErr):
                    return QueryError("invalid-pattern", parsed_pattern.error)
                for ename, ents in g.entity_by_name.items():
                    if search_pattern_matches(parsed_pattern.value, ename):
                        defs.extend(ents)
            return set(d.module for d in defs)

        if fn == "refs-to":
            name = str(args[0]) if args else ""
            edges = g.edges_by_entity_name.get(name, [])
            return set(e.source.split("::")[0] for e in edges)

        if fn == "intersect":
            a = self._to_set(args[0])
            b = self._to_set(args[1]) if len(args) > 1 else set()
            return a & b

        if fn == "union":
            result = set()
            for a in args:
                result |= self._to_set(a)
            return result

        if fn == "diff":
            a = self._to_set(args[0])
            b = self._to_set(args[1]) if len(args) > 1 else set()
            return a - b

        if fn == "filter":
            s = self._to_set(args[0])
            pattern = str(args[1]) if len(args) > 1 else ""
            parsed_pattern = parse_search_pattern(pattern)
            if isinstance(parsed_pattern, SearchPatternErr):
                return QueryError("invalid-pattern", parsed_pattern.error)
            return {
                m
                for m in s
                if search_pattern_matches(parsed_pattern.value, m)
            }

        if fn == "files":
            s = self._to_set(args[0])
            return set(g.module_to_file(m) for m in s)

        if fn == "count":
            s = self._to_set(args[0])
            return len(s)

        if fn == "analyze":
            pattern = str(args[0]) if args else ""
            cmd_analyze(g, pattern)
            return set()

        if fn == "search":
            pattern = str(args[0]) if args else ""
            cmd_search(g, pattern, self._sexp_path)
            return set()

        if fn == "layer":
            pattern = str(args[0]) if args else ""
            matches = g.resolve_module(pattern)
            if matches:
                return _graph_layer(g, matches[0])
            return "unknown"

        if fn == "violations":
            pattern = str(args[0]) if args else "all"
            scope = set(g.nodes.keys()) if pattern == "all" else set(g.resolve_module(pattern))
            deny_rules = [
                ("domain", "infra"), ("domain", "api"), ("domain", "ui"),
                ("ui", "infra"), ("service", "api"), ("infra", "api"),
            ]
            result: set[str] = set()
            for src_mod in scope:
                sl = _graph_layer(g, src_mod)
                for edge in g.edges_from.get(src_mod, []):
                    if edge.etype not in ("imports", "data-flow"):
                        continue
                    tgt_mod = edge.target.split("::")[0]
                    tl = _graph_layer(g, tgt_mod)
                    if sl != tl and (sl, tl) in deny_rules:
                        result.add(src_mod)
            return result

        if fn == "layer-of":
            s = self._to_set(args[0])
            target_layer = str(args[1]) if len(args) > 1 else ""
            return {m for m in s if _graph_layer(g, m) == target_layer}

        if fn == "find-function" or fn == "find_function":
            name = str(args[0]) if args else ""
            parsed_pattern = parse_search_pattern(name)
            if isinstance(parsed_pattern, SearchPatternErr):
                return QueryError("invalid-pattern", parsed_pattern.error)
            result: set[str] = set()
            for ent_name, ent_list in g.entity_by_name.items():
                for ent in ent_list:
                    short_name = _short_function_name(ent_name)
                    if ent.kind == "fn" and (
                        ent_name == name
                        or short_name == name
                        or search_pattern_matches(parsed_pattern.value, ent_name)
                        or search_pattern_matches(parsed_pattern.value, short_name)
                    ):
                        result.add(ent.module)
            return result

        if fn == "find-type" or fn == "find_type":
            name = str(args[0]) if args else ""
            parsed_pattern = parse_search_pattern(name)
            if isinstance(parsed_pattern, SearchPatternErr):
                return QueryError("invalid-pattern", parsed_pattern.error)
            result = set()
            for ent_name, ent_list in g.entity_by_name.items():
                for ent in ent_list:
                    if ent.kind in ("cls", "mod", "ifc", "typ", "enm") and (
                        ent_name == name
                        or search_pattern_matches(parsed_pattern.value, ent_name)
                    ):
                        result.add(ent.module)
            return result

        if fn == "find-callers" or fn == "find_callers":
            fn_name = str(args[0]) if args else ""
            depth, depth_error = (
                _parse_depth(str(args[1])) if len(args) > 1 else (2, None)
            )
            if depth_error is not None or depth is None:
                return QueryError("invalid-depth", depth_error or "invalid depth")
            targets = _resolve_fn_entity(g, fn_name)
            result = set()
            for _qid, ent in targets:
                result.add(ent.module)
            # Collect callers via calls edges
            tgt_qualified = {qid for qid, _ in targets}
            for e in g.all_edges:
                if e.etype == "calls" and e.target in tgt_qualified:
                    result.add(e.source.split("::")[0])
            # Also walk reverse deps
            for _qid, ent in targets:
                visited: set[str] = {ent.module}
                self._collect_rdeps(ent.module, visited, depth, 1, result)
            return result

        if fn == "find-calls" or fn == "find_calls":
            fn_name = str(args[0]) if args else ""
            depth, depth_error = (
                _parse_depth(str(args[1])) if len(args) > 1 else (2, None)
            )
            if depth_error is not None or depth is None:
                return QueryError("invalid-depth", depth_error or "invalid depth")
            sources = _resolve_fn_entity(g, fn_name)
            result = set()
            src_qualified = {qid for qid, _ in sources}
            for e in g.all_edges:
                if e.etype == "calls" and e.source in src_qualified:
                    result.add(e.target.split("::")[0])
            for _qid, ent in sources:
                visited = {ent.module}
                self._collect_deps(ent.module, visited, depth, 1, result)
            return result

        if fn == "diff-functions" or fn == "diff_functions":
            ref = str(args[0]) if args else "HEAD"
            cmd_diff_functions(g, ref)
            return set()

        print(f"; unknown function: {fn}")
        return set()

    def _collect_deps(
        self, nid: str, visited: set[str], max_depth: int, depth: int, result: set[str],
    ) -> None:
        if depth > max_depth:
            return
        for tgt in _collect_dep_mods(self._g, nid):
            result.add(tgt)
            if tgt not in visited:
                visited.add(tgt)
                self._collect_deps(tgt, visited, max_depth, depth + 1, result)

    def _collect_rdeps(
        self, nid: str, visited: set[str], max_depth: int, depth: int, result: set[str],
    ) -> None:
        if depth > max_depth:
            return
        for src in _collect_rdep_mods(self._g, nid):
            result.add(src)
            if src not in visited:
                visited.add(src)
                self._collect_rdeps(src, visited, max_depth, depth + 1, result)

    @staticmethod
    def _to_set(val: object) -> set[str]:
        if isinstance(val, set):
            return val
        if isinstance(val, str):
            return {val}
        if isinstance(val, (list, tuple)):
            return set(str(v) for v in val)
        return set()


def _format_result(val: object) -> None:
    """Print a Scheme query result as S-expression facts."""
    if isinstance(val, QueryError):
        print(f"(error {val.kind} {_q(val.message)})")
    elif isinstance(val, set):
        for item in sorted(val):
            print(f"(result {_q(item)})")
        if not val:
            print("(result-empty)")
    elif isinstance(val, int):
        print(f"(result-count {val})")
    elif isinstance(val, str):
        print(f"(result {_q(val)})")
    elif isinstance(val, list):
        for item in val:
            print(f"(result {_q(str(item))})")
    else:
        print(f"(result {_q(str(val))})")


def cmd_query(g: CodeGraph, expr: str, sexp_path: str) -> None:
    """Evaluate a single Scheme query expression."""
    evaluator = _SchemeEval(g, sexp_path)
    result = evaluator.eval_str(expr)
    _format_result(result)


def cmd_repl(g: CodeGraph, sexp_path: str) -> None:
    """Interactive Scheme REPL for code graph queries."""
    evaluator = _SchemeEval(g, sexp_path)
    print("; code-graph query REPL")
    print("; type (help) for available functions, (quit) to exit")
    print()

    while True:
        try:
            expr = input("bm> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not expr or expr.startswith(";"):
            continue
        if expr in ("(quit)", "(exit)", "quit", "exit"):
            break
        if expr in ("(help)", "help"):
            print(_QUERY_HELP)
            continue

        result = evaluator.eval_str(expr)
        _format_result(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_sexp_file() -> str:
    """Find code_graph.sexp in current or parent dirs.

    Searches for (in priority order per directory):
      .code_graph.sexp, code_graph.sexp

    If only a JSON code graph is found, prints a migration hint.
    """
    _SEXP_NAMES = (".code_graph.sexp", "code_graph.sexp")
    _JSON_NAMES = (".code_graph.json", "code_graph.json")
    cur = os.getcwd()
    json_found = ""
    while True:
        for name in _SEXP_NAMES:
            candidate = os.path.join(cur, name)
            if os.path.isfile(candidate):
                return candidate
        if not json_found:
            for name in _JSON_NAMES:
                candidate = os.path.join(cur, name)
                if os.path.isfile(candidate):
                    json_found = candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if json_found:
        print(f"Error: found JSON code graph at {json_found} but this script requires sexp format.", file=sys.stderr)
        print("Rebuild with the sexp builder:", file=sys.stderr)
        print(
            "  python3 /absolute/path/to/wingbox/scripts/"
            "build_code_graph.py --root /absolute/path/to/project",
            file=sys.stderr,
        )
    else:
        print("Error: .code_graph.sexp not found. Run build_code_graph.py first.", file=sys.stderr)
    sys.exit(1)


def _extract_query_paths(
    args: list[str],
) -> tuple[list[str], Optional[str], Optional[str], Optional[str]]:
    command_args: list[str] = []
    map_path: Optional[str] = None
    root: Optional[str] = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in ("--map", "--root"):
            if index + 1 >= len(args):
                return [], None, None, f"{argument} requires a value"
            value = args[index + 1]
            if not os.path.isabs(value):
                return [], None, None, f"{argument} must be an absolute path"
            if argument == "--map":
                map_path = os.path.realpath(value)
            else:
                root = os.path.realpath(value)
            index += 2
            continue
        command_args.append(argument)
        index += 1
    return command_args, map_path, root, None


def _validate_cli_command(args: list[str]) -> Optional[str]:
    if not args:
        return None
    pattern_commands = {
        "entity",
        "search",
        "find_function",
        "find_type",
        "grep_functions",
    }
    if args[0] in pattern_commands and len(args) >= 2:
        pattern_result = parse_search_pattern(args[1])
        if isinstance(pattern_result, SearchPatternErr):
            return f"invalid pattern {args[1]}: {pattern_result.error}"

    if args[0] == "impact":
        positional = [argument for argument in args[1:] if argument != "--sexp"]
        if not positional:
            return "impact requires a file path or module ID"
        if len(positional) >= 2:
            _impact_depth, impact_depth_error = _parse_depth(positional[1])
            return impact_depth_error
        return None

    depth_positions = {
        "deps": 2,
        "rdeps": 2,
        "find_callchain": 3,
        "find_callers": 2,
        "find_calls": 2,
    }
    depth_position = depth_positions.get(args[0])
    if depth_position is not None and len(args) > depth_position:
        _depth, depth_error = _parse_depth(args[depth_position])
        return depth_error
    return None


def main(args: Optional[list[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if args is None else args)
    args, explicit_map, explicit_root, option_error = _extract_query_paths(raw_args)
    if option_error is not None:
        print(f"(error invalid-argument {_q(option_error)})")
        return 2
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    show_all = "--all" in args
    args = [argument for argument in args if argument != "--all"]

    command_error = _validate_cli_command(args)
    if command_error is not None:
        print(f"(error invalid-argument {_q(command_error)})")
        return 2

    sexp_path = explicit_map or find_sexp_file()
    g = CodeGraph()
    load_result = g.load(sexp_path, explicit_root)
    if isinstance(load_result, MapLoadErr):
        print(f"(error invalid-map {_q(load_result.error)})")
        return 1

    cmd = args[0]
    if cmd == "analyze" and len(args) >= 2:
        cmd_analyze(g, args[1], show_all=show_all)
    elif cmd == "boundary" and len(args) >= 2:
        files_only = "--files-only" in args
        module_arg = [a for a in args[1:] if a != "--files-only"][0]
        cmd_boundary(g, module_arg, files_only=files_only)
    elif cmd == "quick" and len(args) >= 2:
        cmd_quick(g, args[1], sexp_path, show_all=show_all)
    elif cmd == "module" and len(args) >= 2:
        cmd_module(g, args[1])
    elif cmd == "deps" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        cmd_deps(g, args[1], depth)
    elif cmd == "rdeps" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        cmd_rdeps(g, args[1], depth)
    elif cmd == "impact" and len(args) >= 2:
        impact_args = [argument for argument in args[1:] if argument != "--sexp"]
        impact_depth = int(impact_args[1]) if len(impact_args) >= 2 else 2
        cmd_impact(g, impact_args[0], impact_depth, sexp="--sexp" in args, show_all=show_all)
    elif cmd == "entity" and len(args) >= 2:
        entity_args = [argument for argument in args[1:] if argument != "--positions"]
        cmd_entity(g, entity_args[0], positions="--positions" in args)
    elif cmd == "flow" and len(args) >= 3:
        cmd_flow(g, args[1], args[2])
    elif cmd == "search" and len(args) >= 2:
        cmd_search(g, args[1], sexp_path)
    elif cmd == "boundaries":
        target = args[1] if len(args) >= 2 else "all"
        cmd_boundaries(g, target)
    elif cmd == "layers":
        cmd_layers(g)
    elif cmd == "invariants":
        target = args[1] if len(args) >= 2 else "all"
        cmd_invariants(g, target)
    elif cmd == "find_function" and len(args) >= 2:
        cmd_find_function(g, args[1])
    elif cmd == "find_type" and len(args) >= 2:
        cmd_find_type(g, args[1])
    elif cmd == "find_callchain" and len(args) >= 3:
        depth = int(args[3]) if len(args) >= 4 else 5
        cmd_find_callchain(g, args[1], args[2], depth)
    elif cmd == "find_callers" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        cmd_find_callers(g, args[1], depth)
    elif cmd == "find_calls" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        cmd_find_calls(g, args[1], depth)
    elif cmd == "grep_functions" and len(args) >= 2:
        cmd_grep_functions(g, args[1])
    elif cmd == "diff_functions":
        ref = args[1] if len(args) >= 2 else "HEAD"
        cmd_diff_functions(g, ref)
    elif cmd == "query" and len(args) >= 2:
        cmd_query(g, " ".join(args[1:]), sexp_path)
    elif cmd == "repl":
        cmd_repl(g, sexp_path)
    else:
        print(f"(error unknown-command {_q(cmd if args else 'none')})")
        print("; Commands: analyze, boundary, quick, boundaries, layers, invariants, module, deps, rdeps, impact, entity, flow, search,")
        print(";          find_function, find_type, find_callchain, find_callers, find_calls, grep_functions, diff_functions,")
        print(";          query, repl")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
