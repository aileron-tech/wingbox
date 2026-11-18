#!/usr/bin/env python3
"""
Code Graph Search -- query tool for S-expression code graphs.

Reads ``.wingbox/index.sexp`` and provides structured queries: module views,
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
    code_graph_search.py usages <symbol> [--sexp] [--all]
                                                Every use of one symbol (Name, <file>::Name,
                                                <file>::Class.method) by file, line:column and kind
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
import hashlib
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Optional

if __package__:
    from .architecture import advice as _advice
    from .architecture.detect import Part
    from .architecture.labels import label_in_part
    from .architecture.library import load_library
    from .architecture.plan import PartPlan, layer_folders
    from .architecture.words import DEFAULT_SETTINGS as _MATCH_SETTINGS
    from .architecture.words import levenshtein as _levenshtein
    from . import portable
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from architecture import advice as _advice
    from architecture.detect import Part
    from architecture.labels import label_in_part
    from architecture.library import load_library
    from architecture.plan import PartPlan, layer_folders
    from architecture.words import DEFAULT_SETTINGS as _MATCH_SETTINGS
    from architecture.words import levenshtein as _levenshtein
    import portable

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


# Graphs written up to 0.11.0 mark a data-flow fact with this flag from a text match on the imported
# file. It is read as no mark, so no answer states validation on that ground.
_RETIRED_VALIDATION_MARK = ":validated"
# Edge facts of the code graph; every kind except a pipeline data-flow is a dependency of its source.
_EDGE_KINDS = ("imports", "calls-api", "data-flow", "refs", "calls", "http-calls", "registry-wires")


@dataclass(frozen=True)
class CapabilityRuleFact:
    """Where a part allows a capability: `(arch-capability <part> <capability> :allowed (...) :forbidden (...))`."""

    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    fix: str

    def breaks(self, layer: str) -> bool:
        return bool(self.allowed and layer not in self.allowed) or layer in self.forbidden


@dataclass(frozen=True)
class CapabilityFact:
    """What a file does, found by syntax: `(capability <module> sql :line 42 :evidence "..." [:suspect])`."""

    capability: str
    line: int
    evidence: str
    suspect: bool


@dataclass(frozen=True)
class NameRoute:
    """Names of the source file that the builder followed to the target of an import edge, and how they arrive.

    way: use (an item of the module named in an import), reexport (an item a facade re-exports; through is
    the facade as written), glob (a name used after a glob import; through is the module of that import),
    member (a member an impl block of the target defines; through is the type as written), macro (an exported
    macro). names holds a few of them, count their number.
    """

    way: str
    through: str
    names: tuple[str, ...]
    count: int


@dataclass
class Edge:
    etype: str   # imports, calls-api, data-flow, refs, calls, registry-wires
    source: str
    target: str
    flags: list[str] = field(default_factory=list)
    names: tuple[NameRoute, ...] = ()   # imports: the names followed to the target, by the way they arrive
    evidence: str = ""   # data-flow: the text of :evidence, what makes the target a validation module


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

# One token of a fact line that holds no backslash: a quoted value (to its closing quote, or to the end of the
# line), a list with no quote and no list inside, a bare word, or a bracket that opens anything else.
_TOKEN = re.compile(r'"([^"]*)(?:"|$)|(\([^()"]*\))|([^ ()"][^ )]*)|(\()')


def _fact_body(line: str) -> Optional[str]:
    """What stands between the outer brackets of a fact line; None for a comment, an empty line, anything else."""
    line = line.strip()
    if not line or line.startswith(";") or not line.startswith("(") or not line.endswith(")"):
        return None
    return line[1:-1]


def tokenize(line: str) -> list[str]:
    """Tokenize a single-line S-expression into a flat list of tokens."""
    inner = _fact_body(line)
    if inner is None:
        return []
    if "\\" in inner:
        return _tokens_one_by_one(inner)
    tokens: list[str] = []
    for match in _TOKEN.finditer(inner):
        kind = match.lastindex
        if kind == 4:   # a list holding a quote or another list, or one that never closes
            return _tokens_one_by_one(inner)
        tokens.append(match.group(kind) if kind else "")
    return tokens


def tokenize_reference(line: str) -> list[str]:
    """The tokens of a line read one character at a time: what `tokenize` must return for every line."""
    inner = _fact_body(line)
    return [] if inner is None else _tokens_one_by_one(inner)


def _tokens_one_by_one(inner: str) -> list[str]:
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


_ROUTES_WITH_THROUGH = frozenset({"reexport", "glob", "member"})


def parse_name_route(token: str) -> Optional[NameRoute]:
    """A `(:use A B)` or `(:glob "super" A B C :of 5)` list of an import edge; None for any other token."""
    parts = tokenize(token) if token.startswith("(:") and token.endswith(")") else []
    if not parts:
        return None
    way, values = parts[0][1:], parts[1:]
    through = values.pop(0) if way in _ROUTES_WITH_THROUGH and values else ""
    count = len(values)
    if len(values) >= 2 and values[-2] == ":of" and values[-1].isdigit():
        count, values = int(values[-1]), values[:-2]
    return NameRoute(way, through, tuple(values), count)


def name_route_sexp(route: NameRoute) -> str:
    head = f'(:{route.way} "{route.through}"' if route.way in _ROUTES_WITH_THROUGH else f"(:{route.way}"
    more = f" :of {route.count}" if route.count > len(route.names) else ""
    return f"{head} {' '.join(route.names)}{more})"


def parse_sub_list(token: str) -> list[str]:
    """Parse a (:key a b c) token into ['a', 'b', 'c']; quoted values keep spaces and brackets."""
    if token.startswith("(") and token.endswith(")"):
        parts = tokenize(token)
        return parts[1:] if parts and parts[0].startswith(":") else parts
    return []


# ---------------------------------------------------------------------------
# S-expression output helpers
# ---------------------------------------------------------------------------

def _escaped(s: str) -> str:
    """The text of a quoted S-expression value: backslash and double quote escaped."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _q(s: str) -> str:
    """Quote a string for S-expression output if it contains spaces."""
    if " " in s or '"' in s:
        return '"' + _escaped(s) + '"'
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
_PHP_EXTENSIONS = (".blade.php", ".php")
_PASCAL_EXTENSIONS = (".pas", ".pp", ".lpr", ".inc")
_OTHER_EXTENSIONS = (".go", ".java", ".cs", ".dart")


def _module_id_to_file(module_id: str, root: str) -> str:
    """Convert a module ID back to a file path relative to root."""
    base = os.path.join(root, *module_id.split("/"))   # a module ID uses /, the file system its own separator
    if os.path.isfile(base):
        return base
    source_extensions = (
        _TS_EXTENSIONS
        + _PY_EXTENSIONS
        + _RUBY_EXTENSIONS
        + _RUST_EXTENSIONS
        + _PHP_EXTENSIONS
        + _PASCAL_EXTENSIONS
        + _OTHER_EXTENSIONS
    )
    for ext in source_extensions:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    for idx in ("index.ts", "index.tsx", "index.js", "__init__.py", "mod.rs", "index.blade.php"):
        candidate = os.path.join(base, idx)
        if os.path.isfile(candidate):
            return candidate
    return module_id


# ---------------------------------------------------------------------------
# Graph loader
# ---------------------------------------------------------------------------

# Facts kept unparsed at load and read when a query needs them.
_LAZY_FACTS = ("(use ", "(unresolved-call ", "(unresolved ", "(var ")


def _load_builder():
    """scripts/build_code_graph.py (it fills use positions), imported by name from this folder as
    `wingbox build` does, so that its worker processes can import it too."""
    import importlib

    directory = os.path.dirname(os.path.abspath(__file__))
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module("build_code_graph")


def _uses_path(map_path: str) -> str:
    """The use-position file next to a code graph (build_code_graph.uses_path)."""
    return (map_path[: -len(".sexp")] if map_path.endswith(".sexp") else map_path) + ".uses.sexp"
_USE_POSITION = re.compile(r"(\d+):(\d+)([a-z]?)")
_USE_KINDS = {"i": "import", "r": "re-export", "c": "call", "t": "type", "e": "extends", "": "reference"}
UseSite = tuple[str, int, int, str]  # module, line, column, kind of use
def _keywords(tokens: list[str]) -> dict[str, str]:
    """`:key value` pairs of a fact."""
    return {
        tokens[i][1:]: tokens[i + 1]
        for i in range(len(tokens) - 1)
        if tokens[i].startswith(":") and not tokens[i + 1].startswith(":")
    }


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
        self._uses_by_target: Optional[dict[str, list[UseSite]]] = None
        self._unresolved_by_member: Optional[dict[str, list[tuple[str, int, int]]]] = None
        self._unresolved_counts: Optional[dict[str, int]] = None
        self._variables: Optional[dict[str, list[EntityFact]]] = None
        self._uses_header = ""
        self._use_pairs: Optional[dict[str, set[str]]] = None
        self._uses_file_read = False
        self._map_path = ""
        self._module_files: dict[tuple[str, str], str] = {}  # (root, module) -> file, worked out once
        self.pmap: dict[str, str] = {}
        self.package_layers: dict[str, str] = {}  # Python package __init__ -> layer of its package
        self.arch_parts: dict[str, dict[str, str]] = {}   # part -> keyword values of its arch-part fact
        self.arch_denies: dict[str, dict[tuple[str, str], str]] = {}   # part -> (from, to) -> reason
        self.candidate_rules: set[tuple[str, str, str]] = set()   # (part, from, to) of rules that give candidates
        self.entry_roots: dict[str, bool] = {}   # module -> nothing imports it and it imports a composition root
        self.arch_fixes: dict[tuple[str, str, str], str] = {}   # (part, from, to) -> fix the style prescribes
        self.arch_ranks: dict[str, dict[str, int]] = {}         # part -> layer -> rank
        self.arch_targets: list[tuple[str, str, str, str, bool, str, str]] = []   # part, layer, block, folder, proposed, present, source
        self.advice_cache: Optional[tuple] = None
        self.labels: dict[str, "ModuleLabel"] = {}   # module -> layer label written by the build
        self.known: dict[tuple[str, str], str] = {}   # (source, target) -> baseline file of the project
        self.arch_capabilities: dict[str, dict[str, CapabilityRuleFact]] = {}   # part -> capability -> rule
        self.arch_patterns: dict[str, set[str]] = {}          # part -> pattern checks of its style
        self.capabilities: dict[str, list[CapabilityFact]] = {}   # module -> what its file does
        self.capability_known: dict[tuple[str, str], str] = {}    # (module, capability) -> baseline file
        self.declared_unread: dict[str, list[str]] = {}   # part -> its rule files that were not read
        self._root: str = ""

    def load(self, path: str, root: Optional[str] = None, focus: Optional[str] = None) -> MapLoadResult:
        """Read a code graph. With focus (a name), only the entity and edge facts that mention it are
        read, besides the paths and the nodes: what a question about that one name needs."""
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
        self._unresolved_by_member = None
        self._unresolved_counts = None
        self._variables = None
        self._uses_header = ""
        self._use_pairs = None
        self._uses_file_read = False
        self._map_path = path
        self._module_files = {}
        self.pmap = {}
        self.package_layers = {}
        self.arch_parts = {}
        self.arch_denies = {}
        self.candidate_rules = set()
        self.entry_roots = {}
        self.arch_fixes = {}
        self.arch_ranks = {}
        self.arch_targets = []
        self.advice_cache = None
        self.labels = {}
        self.known = {}
        self.arch_capabilities = {}
        self.arch_patterns = {}
        self.capabilities = {}
        self.capability_known = {}
        self.declared_unread = {}
        self._root = os.path.abspath(root if root is not None else project_root_of(path))
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
            if found_paths and not in_paths:
                break  # header, def lines and paths come first; nothing after them is needed here
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith(_LAZY_FACTS):
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
            if line.startswith(_LAZY_FACTS):
                self._symbol_lines.append(line)
                continue
            if focus is not None and focus not in line and not line.startswith(("(node ", "(code-graph")):
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

            elif kind in _EDGE_KINDS and len(tokens) >= 3:
                source = _resolve(tokens[1])
                target = _resolve(tokens[2])
                flags = [t for t in tokens[3:] if t.startswith(":")]
                routes = tuple(route for route in map(parse_name_route, tokens[3:]) if route is not None) \
                    if kind == "imports" else ()
                evidence = ""
                if kind == "data-flow":
                    flags = [flag for flag in flags if flag != _RETIRED_VALIDATION_MARK]
                    evidence = _keywords(tokens[3:]).get("evidence", "")
                edge = Edge(kind, source, target, flags, routes, evidence)
                self.all_edges.append(edge)
                if kind == "data-flow" and ":pipeline" in flags:
                    # The next step of a pipeline consumes the output of this one; it is
                    # not a dependency of this step, so it stays out of deps and impact.
                    continue
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

            elif kind == "arch-part" and len(tokens) >= 2:
                self.arch_parts[tokens[1]] = _keywords(tokens[2:])
            elif kind == "arch-deny" and len(tokens) >= 5:
                self.arch_denies.setdefault(tokens[1], {})[(tokens[2], tokens[3])] = tokens[4]
                values = _keywords(tokens[5:])
                if values.get("severity") == "candidate":
                    self.candidate_rules.add((tokens[1], tokens[2], tokens[3]))
                if values.get("fix"):
                    self.arch_fixes[(tokens[1], tokens[2], tokens[3])] = values["fix"]
            elif kind == "arch-rank" and len(tokens) >= 4 and tokens[3].lstrip("-").isdigit():
                self.arch_ranks.setdefault(tokens[1], {})[tokens[2]] = int(tokens[3])
            elif kind == "arch-target" and len(tokens) >= 4:
                values = _keywords(tokens[4:])
                self.arch_targets.append((
                    tokens[1], tokens[2], values.get("block", ""), tokens[3], ":proposed" in tokens,
                    values.get("present", ""), values.get("source", ""),
                ))
            elif kind == "arch-known" and len(tokens) >= 5:
                self.known[(_resolve(tokens[2]), _resolve(tokens[3]))] = tokens[4]
            elif kind == "arch-capability" and len(tokens) >= 3:
                values = _keywords(tokens[3:])
                self.arch_capabilities.setdefault(tokens[1], {})[tokens[2]] = CapabilityRuleFact(
                    tuple(values.get("allowed", "").strip("()").split()), tuple(values.get("forbidden", "").strip("()").split()),
                    values.get("fix", ""),
                )
            elif kind == "arch-pattern" and len(tokens) >= 3:
                self.arch_patterns.setdefault(tokens[1], set()).add(tokens[2])
            elif kind == "capability" and len(tokens) >= 3:
                values = _keywords(tokens[3:])
                self.capabilities.setdefault(_resolve(tokens[1]), []).append(CapabilityFact(
                    tokens[2], int(values.get("line", "0") or 0), values.get("evidence", ""), ":suspect" in tokens,
                ))
            elif kind == "arch-declared-unread" and len(tokens) >= 3:
                self.declared_unread.setdefault(tokens[1], []).append(tokens[2])
            elif kind == "capability-known" and len(tokens) >= 5:
                self.capability_known[(_resolve(tokens[2]), tokens[3])] = tokens[4]
            elif kind == "layer" and len(tokens) >= 3:
                values = _keywords(tokens[3:])  # :learned-from <id> is part of the rule text as well
                self.labels[_resolve(tokens[1])] = ModuleLabel(
                    tokens[2], values.get("part", "-"), values.get("style", "-"),
                    values.get("rule", ""), values.get("match", "none"), ":composition-root" in tokens,
                )

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

    def _use_lines(self) -> list[str]:
        """The use facts: those of the graph file and those of the use file next to it
        (.wingbox/index.uses.sexp), read on first need and only when it belongs to this graph."""
        if not self._uses_file_read:
            self._uses_file_read = True
            path = _uses_path(self._map_path)
            try:
                with open(path, "r", encoding="utf-8") as uses_file:
                    lines = uses_file.read().splitlines()
                with open(self._map_path, "r", encoding="utf-8") as graph_file:
                    digest = hashlib.sha256(graph_file.read().encode("utf-8")).hexdigest()[:16]
            except FileNotFoundError:
                return self._symbol_lines
            except OSError as error:
                print(f"(warning uses-unreadable {_q(path)} {_q(str(error))})", file=sys.stderr)
                return self._symbol_lines
            header = next((line for line in lines if line.startswith("(code-graph-uses ")), "")
            self._uses_header = header
            if f":graph {digest})" not in header:
                print(f"(warning uses-stale {_q(path)} :rerun \"wingbox build\")", file=sys.stderr)
                return self._symbol_lines
            self._symbol_lines.extend(line for line in lines if line.startswith(_LAZY_FACTS))
        return self._symbol_lines

    def _positions(self) -> list[str]:
        """The use facts with positions. A build writes the use file without them; the first query
        that needs them has the builder fill them in (build_code_graph.write_use_positions)."""
        lines = self._use_lines()
        header = self._uses_header
        if header and ":positions no" in header:
            builder = _load_builder()
            written = builder.write_use_positions(self._map_path, self._root)
            if isinstance(written, builder.Err):
                print(f"(warning positions-unavailable {_q(written.error)} :rerun \"wingbox build\")", file=sys.stderr)
                return lines
            self._symbol_lines = []
            self._uses_file_read = False
            lines = self._use_lines()
        return lines

    @property
    def use_pairs(self) -> dict[str, set[str]]:
        """module::name -> modules that use it, from the use lines (no positions needed)."""
        if self._use_pairs is None:
            found: dict[str, set[str]] = {}
            for line in self._use_lines():
                if line.startswith("(use "):
                    parts = line.rstrip(")").split(" ", 3)
                    module, _, name = parts[2].partition("::")
                    found.setdefault(f"{self.pmap.get(module, module)}::{name}", set()).add(
                        self.pmap.get(parts[1], parts[1]))
            self._use_pairs = found
        return self._use_pairs

    @property
    def variables_by_name(self) -> dict[str, list[EntityFact]]:
        """Module-level variables (Python), from the (var <module> <name> <line>) facts of the use file."""
        if self._variables is None:
            found: dict[str, list[EntityFact]] = {}
            for line in self._use_lines():
                if line.startswith("(var "):
                    parts = line.strip().strip("()").split()
                    if len(parts) == 4 and parts[3].isdigit():
                        module = self.pmap.get(parts[1], parts[1])
                        found.setdefault(parts[2], []).append(EntityFact(module, parts[2], "var", int(parts[3]), [], [], []))
            self._variables = found
        return self._variables

    def definitions_named(self, name: str) -> list[EntityFact]:
        """Entities of the graph and module-level variables named name."""
        return [*self.entity_by_name.get(name, []), *self.variables_by_name.get(name, [])]

    @property
    def unresolved_method_calls(self) -> dict[str, int]:
        """module -> method calls without a visible receiver type, from (unresolved <module> :method-calls N)."""
        if self._unresolved_counts is None:
            found: dict[str, int] = {}
            for line in self._positions():
                tokens = line.strip().strip("()").split()
                if len(tokens) == 4 and tokens[0] == "unresolved" and tokens[3].isdigit():
                    found[self.pmap.get(tokens[1], tokens[1])] = int(tokens[3])
            self._unresolved_counts = found
        return self._unresolved_counts

    def uses_named(self, name: str) -> dict[str, list[UseSite]]:
        """module::name (and module::name.method) -> its use sites, read only from the use lines
        that mention name (a query about one symbol does not parse the whole use file)."""
        marker = f"::{name}"
        found: dict[str, list[UseSite]] = {}
        for line in self._positions():
            if not line.startswith("(use ") or marker not in line:
                continue
            parts = line.rstrip(")").split(" ")
            module, _, target_name = parts[2].partition("::")
            if target_name != name and not target_name.startswith(name + "."):
                continue
            sites = found.setdefault(f"{self.pmap.get(module, module)}::{target_name}", [])
            source = self.pmap.get(parts[1], parts[1])
            for position in parts[3:]:
                match = _USE_POSITION.fullmatch(position)
                if match:
                    sites.append((source, int(match.group(1)), int(match.group(2)), _USE_KINDS.get(match.group(3), "reference")))
        return found

    @property
    def uses_by_target(self) -> dict[str, list[UseSite]]:
        """module::name -> (module, line, column, kind) of every use,
        from (use <module> <target> <line>:<col>[<kind letter>] ...)."""
        if self._uses_by_target is None:
            found: dict[str, list[UseSite]] = {}
            for line in self._positions():
                tokens = tokenize(line)
                if len(tokens) < 4 or tokens[0] != "use":
                    continue
                module, _, name = tokens[2].partition("::")
                sites = found.setdefault(f"{self.pmap.get(module, module)}::{name}", [])
                source = self.pmap.get(tokens[1], tokens[1])
                for position in tokens[3:]:
                    match = _USE_POSITION.fullmatch(position)
                    if match:
                        kind = _USE_KINDS.get(match.group(3), "reference")
                        sites.append((source, int(match.group(1)), int(match.group(2)), kind))
            self._uses_by_target = found
        return self._uses_by_target

    @property
    def unresolved_by_member(self) -> dict[str, list[tuple[str, int, int]]]:
        """method name -> (module, line, column) of the calls of it that no receiver type resolves."""
        if self._unresolved_by_member is None:
            found: dict[str, list[tuple[str, int, int]]] = {}
            for line in self._positions():
                tokens = tokenize(line)
                if len(tokens) < 4 or tokens[0] != "unresolved-call":
                    continue
                sites = found.setdefault(tokens[2], [])
                source = self.pmap.get(tokens[1], tokens[1])
                for position in tokens[3:]:
                    match = _USE_POSITION.fullmatch(position)
                    if match:
                        sites.append((source, int(match.group(1)), int(match.group(2))))
            self._unresolved_by_member = found
        return self._unresolved_by_member

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
    "application entry point": "config",
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


def _within(word: str, keyword: str, threshold: int) -> int:
    """The edit distance of two words, or threshold + 1 when their lengths alone put it above the threshold."""
    if abs(len(word) - len(keyword)) > threshold:
        return threshold + 1
    return _levenshtein(word, keyword)


@functools.lru_cache(maxsize=None)
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
                dist = _within(pw, kw, threshold)
                if dist <= threshold and dist < best_dist:
                    best_dist = dist
                    best_layer = layer
    return best_layer


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


def _edge_marks(e: Edge) -> str:
    """The marks of an edge as stored, each after a space; :evidence keeps its quoted text."""
    return "".join(
        f' {flag} "{_escaped(e.evidence)}"' if flag == ":evidence" and e.evidence else f" {flag}"
        for flag in e.flags
    )


def _edge_sexp(e: Edge) -> str:
    """Format an edge as an S-expression fact."""
    return f"({e.etype} {e.source} {e.target}{_edge_marks(e)})"


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
        + _PHP_EXTENSIONS
        + _PASCAL_EXTENSIONS
        + _OTHER_EXTENSIONS,
        key=len,
        reverse=True,
    )
)


def _target_to_module_pattern(target: str, root: str) -> str:
    """Turn a source file path (absolute, or relative to the graph root) into a module pattern."""
    path = portable.local_path(target)
    if root and os.path.isabs(path):
        relative = portable.relative_posix(path, root)
        if relative is not None:
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


@dataclass(frozen=True)
class ImpactRow:
    """One dependent of the impact root and why it is on the list.

    through is the module of the previous depth it is reached through (the root at depth 1), the first in
    the order of the list; more is the number of other modules of that depth it also depends on. edge_type
    and names are those of its edge to through.
    """

    module: str
    edge_type: str
    through: str
    more: int
    names: tuple[NameRoute, ...]


def _impact_rows(g: CodeGraph, start: str, max_depth: int) -> list[list[ImpactRow]]:
    """The levels of _impact_levels, each dependent with the module it is reached through and its edge to it."""
    levels = _impact_levels(g, start, max_depth)
    rows: list[list[ImpactRow]] = []
    previous = [start]
    for level in levels:
        reached: dict[str, list[tuple[str, str]]] = {}
        for through in previous:
            for source, edge_type in _collect_rdep_mods(g, through).items():
                reached.setdefault(source, []).append((through, edge_type))
        level_rows = []
        for module, _edge_type in level:
            through, edge_type = reached[module][0]
            names = next((edge.names for edge in g.edges_to.get(through, ())
                          if edge.etype == "imports" and edge.source.split("::")[0] == module and edge.names), ())
            level_rows.append(ImpactRow(module, edge_type, through, len(reached[module]) - 1, names if edge_type == "imports" else ()))
        rows.append(level_rows)
        previous = [module for module, _edge_type in level]
    return rows


_ROUTE_NAME_SEPARATOR = ", "


def _route_text(route: NameRoute) -> str:
    """One way names arrive, in plain words; "it" and "there" are the file the row depends on."""
    shown = [f"{route.through}::{name}" for name in route.names] if route.way == "member" else list(route.names)
    names = _ROUTE_NAME_SEPARATOR.join(shown)
    if route.count > len(route.names):
        names += f" and {route.count - len(route.names)} more"
    if route.way == "use":
        return f"imports {names} from it"
    if route.way == "reexport":
        return f"uses {names}, re-exported by {route.through}"
    if route.way == "glob":
        return f"uses {names} by name after `use {route.through}::*`"
    if route.way == "member":
        return f"uses {names}, defined in an impl block there"
    if route.way == "macro":
        return f"uses the macro {names}, exported at the crate root"
    return f"uses {names}"


def impact_reason(g: CodeGraph, row: ImpactRow, depth: int) -> str:
    """Why a file is on the impact list, as one line of text: the relation of its edge and, below depth 1,
    the file it is reached through."""
    if row.names:
        relation = "; ".join(_route_text(route) for route in row.names)
    else:
        relation = "imports it" if row.edge_type == "imports" else row.edge_type
    if depth == 1:
        return relation
    others = f" (or {row.more} other {'file' if row.more == 1 else 'files'})" if row.more else ""
    return f"through {_impact_file(g, row.through)}{others}: {relation}"


def impact_fact(g: CodeGraph, root_id: str, row: ImpactRow, depth: int) -> str:
    through = f" :through {row.through}" if depth > 1 else ""
    more = f" :more {row.more}" if depth > 1 and row.more else ""
    names = "".join(f" {name_route_sexp(route)}" for route in row.names)
    return f"(impact {root_id} {row.module} :lang {g.module_lang(row.module)} :via {row.edge_type} :depth {depth}{through}{more}{names})"


def _impact_file(g: CodeGraph, module_id: str) -> str:
    """File of a module relative to the graph root; the module ID when it is not on disk."""
    key = (g._root or "", module_id)
    known = g._module_files.get(key)
    if known is not None:
        return known
    path = g.module_to_file(module_id)
    if g._root and os.path.isabs(path):
        relative = portable.relative_posix(path, g._root)
        if relative is not None:
            path = relative
    g._module_files[key] = path
    return path


def _is_package_init(g: CodeGraph, module_id: str) -> bool:
    return module_id.rsplit("/", 1)[-1] == "__init__" and g.module_lang(module_id) == "py"


@dataclass(frozen=True)
class ModuleLabel:
    """Layer of a module, with the part, the style and the rule that set it."""

    layer: str
    part: str
    style: str
    rule: str
    match: str   # exact | spelling | config | file | declared | test | role | package | none
    root: bool = False   # a composition root: wires the layers, may import every one


_UNPLACED = ModuleLabel("other", "-", "-", "in no part", "none")
# Parts whose layers a guess from the role of a file may fill: the role words name layered layers.
_ROLE_GUESS_STYLES = ("layered",)


def _default_labels(g: CodeGraph) -> None:
    """Labels of a graph without architecture facts (written by hand): one part, the layered style."""
    library = load_library()
    style = library.style("layered")
    assert style is not None
    from_files = {module: _label_path(g, module) for module in g.nodes}
    files = tuple(sorted(set(from_files.values())))
    layers, routing = layer_folders(style, Part(".", (), files, files), _MATCH_SETTINGS)
    part = PartPlan(
        path=".", kind="folder", level="container", files=len(files), style="layered", state="assumed",
        origin="default", score=None, judgement=None, layers=layers, routing_root=routing, declared=None,
    )
    g.arch_parts["."] = {
        "style": "layered", "state": "assumed", "origin": "default", "level": "container", "files": str(len(files)),
    }
    g.arch_denies["."] = {(deny.source, deny.target): deny.reason for deny in style.denies}
    g.candidate_rules |= {(".", deny.source, deny.target) for deny in style.denies if deny.severity == "candidate"}
    g.arch_fixes.update({(".", deny.source, deny.target): deny.fix for deny in style.denies if deny.fix})
    g.arch_ranks["."] = {layer.id: layer.rank for layer in style.layers}
    g.arch_capabilities["."] = {
        rule.capability: CapabilityRuleFact(rule.allowed_in, rule.forbidden_in, rule.fix) for rule in style.capabilities
    }
    g.arch_patterns["."] = set(style.patterns)
    for module, path in from_files.items():
        label = label_in_part(path, part, library)
        g.labels[module] = ModuleLabel(label.layer, ".", label.style, label.rule, label.match, label.root)


_LANGUAGE_EXTENSIONS = {"py": ".py", "ts": ".ts", "tsx": ".tsx", "js": ".js", "rs": ".rs", "go": ".go",
                        "rb": ".rb", "cs": ".cs", "java": ".java", "pas": ".pas", "dart": ".dart", "php": ".php"}


def _label_path(g: CodeGraph, module_id: str) -> str:
    """File of a module relative to the root; for a module not on disk, its ID with its language's extension."""
    path = _impact_file(g, module_id)
    if path != module_id:
        return path
    return module_id + _LANGUAGE_EXTENSIONS.get(g.module_lang(module_id), "")


def _stored_label(g: CodeGraph, module_id: str) -> ModuleLabel:
    if not g.arch_parts:
        _default_labels(g)
    return g.labels.get(module_id, _UNPLACED)


def _package_label(g: CodeGraph, module_id: str, label: ModuleLabel) -> ModuleLabel:
    """Label of a Python package, for its __init__.py.

    A folder rule decides when one applies. Otherwise the package is where most
    of its modules are: its own modules, or all modules below it when it holds
    only sub-packages. What __init__.py re-exports says nothing.
    """
    if label.match not in ("none", "role"):
        return label
    if module_id not in g.package_layers:
        prefix = module_id[: -len("__init__")]
        below = [
            module
            for module in g.nodes
            if module.startswith(prefix) and module != module_id and not _is_package_init(g, module)
        ]
        own = [module for module in below if "/" not in module[len(prefix):]]
        counts = collections.Counter(module_label(g, module).layer for module in (own or below))
        ranked = counts.most_common(2)
        tied = len(ranked) == 2 and ranked[0][1] == ranked[1][1]
        g.package_layers[module_id] = ranked[0][0] if ranked and not tied else "other"
    layer = g.package_layers[module_id]
    if layer == "other":
        return replace(label, layer="other")
    return replace(label, layer=layer, rule=f"package: most of its modules are {layer}", match="package")


def module_label(g: CodeGraph, module_id: str) -> ModuleLabel:
    """Layer of a module of this graph, and where it comes from.

    The build writes the label of every module (folder rule of the part's
    style, file pattern, declared layer, test file). A module the rules leave
    in `other` takes a guess from the role of the file in a layered part; a
    Python __init__.py takes the layer of its package.
    """
    label = _stored_label(g, module_id)
    if _is_package_init(g, module_id):
        return _package_label(g, module_id, label)
    if label.layer == "other" and g.arch_parts.get(label.part, {}).get("style") in _ROLE_GUESS_STYLES:
        purpose = g.module_purpose(module_id)
        guess = _classify_layer_by_purpose(purpose) if purpose else "other"
        if g.module_lang(module_id) == "dart" and purpose.lower() == "state store":
            # A Dart state store (bloc, cubit, notifier) is application state; a JS store may be a UI concern.
            guess = "service"
        if guess != "other":
            return replace(label, layer=guess, rule=f"role {purpose} -> {guess} (guess)", match="role")
    return label


def _graph_layer(g: CodeGraph, module_id: str) -> str:
    return module_label(g, module_id).layer


def _layer_is_certain(g: CodeGraph, module_id: str) -> bool:
    """True when the layer comes from a rule (folder, file type, declared layer), not from a guess at the role."""
    label = module_label(g, module_id)
    return label.layer != "other" and label.match not in ("role", "package", "none")


# Public names used by the report and the map (src/wingbox/overview.py).

def module_layer(g: CodeGraph, module_id: str) -> str:
    """Architecture layer of a module: a layer of its part's style, test or other."""
    return _graph_layer(g, module_id)


def module_layer_basis(g: CodeGraph, module_id: str) -> str:
    """Where the layer of a module comes from: "path" (a rule: folder, file type, declared layer),
    "role" (a guess from what the file does) or "none" (no rule applies, the layer is "other")."""
    if _layer_is_certain(g, module_id):
        return "path"
    return "role" if module_label(g, module_id).layer != "other" else "none"


def module_file(g: CodeGraph, module_id: str) -> str:
    """File of a module relative to the graph root."""
    return _impact_file(g, module_id)


def module_dependencies(g: CodeGraph, module_id: str) -> dict[str, str]:
    """Modules that module_id depends on: module -> edge type."""
    return _collect_dep_mods(g, module_id)


def module_dependents(g: CodeGraph, module_id: str) -> dict[str, str]:
    """Modules that depend on module_id: module -> edge type."""
    return _collect_rdep_mods(g, module_id)


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
    target = portable.local_path(target)
    as_given = target.replace(os.sep, "/")
    if g._root and os.path.isabs(target):
        relative = portable.relative_posix(target, g._root)
        if relative is not None:
            as_given = relative
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
    levels = _impact_rows(g, root_id, max_depth)
    direct = len(levels[0]) if levels else 0
    total = sum(len(level) for level in levels)
    root_file = _impact_file(g, root_id)
    read_first = list(
        dict.fromkeys(
            [root_file] + [_impact_file(g, row.module) for row in (levels[0] if levels else [])]
        )
    )
    read_first_count = len(read_first)
    if not show_all:
        read_first = read_first[: _READ_FIRST_LIMIT + 1]
    # Nearest dependents first: the limit is spent on depth 1 before depth 2.
    budget = total if show_all else _ANSWER_FILE_LIMIT
    shown_levels: list[list[ImpactRow]] = []
    for level in levels:
        shown_levels.append(level[:budget])
        budget -= len(shown_levels[-1])
    hidden = total - sum(len(level) for level in shown_levels)

    if sexp:
        if others:
            print(f"(impact-ambiguous {_q(pattern)} :showing {root_id} :others {' '.join(others)})")
        print(f"(impact-root {root_id} :lang {g.module_lang(root_id)} :file {_q(root_file)})")
        for depth, level in enumerate(shown_levels, start=1):
            for row in level:
                print(impact_fact(g, root_id, row, depth))
        if hidden:
            print(f"(impact-more {root_id} :hidden {hidden} :rerun {_rerun(f'impact {target} {max_depth}')})")
        print(f"(impact-summary {root_id} :direct {direct} :total {total} :depth {max_depth})")
        for path in read_first:
            print(f"(read-first {_q(path)})")
        if read_first_count > len(read_first):
            print(f"(read-first-more :hidden {read_first_count - len(read_first)})")
        for item in module_advice(g, root_id):
            print(advice_sexp(item))
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
            _print_impact_block(f"Depth {depth}", [(_impact_file(g, row.module), impact_reason(g, row, depth)) for row in level])
            print()
    if hidden:
        print(f"  ... {hidden} more {'file' if hidden == 1 else 'files'}: rerun with --all")
        print()
    files = "file" if read_first_count == 1 else "files"
    _print_impact_block(f"Read first ({read_first_count} {files})", [(path, "") for path in read_first])
    if read_first_count > len(read_first):
        print(f"  ... {read_first_count - len(read_first)} more: the other files of Depth 1")
    related = module_advice(g, root_id)
    if related:
        print()
        print("Advice")
        for item in related:
            print(f"  {item.reason} [{item.style}: {item.rule}]")


def cmd_entity(g: CodeGraph, name: str, positions: bool = False) -> None:
    # Always try exact match first, then safe-pattern fallback.
    definitions: list[EntityFact] = g.definitions_named(name)
    if not definitions:
        parsed_pattern = parse_search_pattern(name)
        if isinstance(parsed_pattern, SearchPatternErr):
            _print_invalid_pattern(name, parsed_pattern.error)
            return
        for ename, ents in [*g.entity_by_name.items(), *g.variables_by_name.items()]:
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
    # Modules that reach a definition only through a re-export: in the use file, not in the graph.
    referencing = {(e.source.split("::")[0], e.target) for e in unique_refs}
    for defn in definitions:
        target = f"{defn.module}::{defn.name}"
        for source in g.use_pairs.get(target, set()):
            if source != defn.module and (source, target) not in referencing:
                referencing.add((source, target))
                unique_refs.append(Edge("refs", source, target, [":reexport"]))
    for e in sorted(unique_refs, key=lambda x: (x.etype, x.source)):
        print(_edge_sexp(e))
    if positions:
        # Every use site, as find usages lists it: file, line, column (1-based).
        # The definition itself and its methods (Class.method) where a call resolved to one.
        wanted = {f"{defn.module}::{defn.name}" for defn in definitions}
        sites = sorted(
            (_impact_file(g, source), line, column, source, target)
            for target, found in [item for defn_name in {d.name for d in definitions}
                                  for item in g.uses_named(defn_name).items()]
            if target in wanted or target.rsplit(".", 1)[0] in wanted
            for source, line, column, _kind in found
        )
        for file_path, line, column, source, target in sites:
            print(f"(use {source} {line} {column} {target} :file {_q(file_path)})")


_KIND_WORDS = {"cls": "class", "fn": "function", "ifc": "interface", "typ": "type", "enm": "enum", "mod": "module",
               "var": "variable"}
_USAGES_LIMIT = 200


@dataclass(frozen=True)
class Usage:
    """One place that uses the symbol; line 0 when its language has no use positions."""

    file: str
    module: str
    line: int
    column: int
    kind: str


def _usages_definitions(g: CodeGraph, target: str) -> tuple[str, list[EntityFact]]:
    """The symbol asked (Name, Class.method) and its definitions: of the named file, or of every file."""
    file_part, separator, symbol = target.rpartition("::")
    owner = symbol.split(".", 1)[0]
    if not separator:
        return symbol, g.definitions_named(owner)
    pattern = _target_to_module_pattern(file_part, g._root)
    modules = _impact_target_modules(g, file_part, pattern)[:1]
    found = [entity for module in modules for entity in g.definitions_named(owner) if entity.module == module]
    if found or not modules:
        return symbol, found
    # The file only re-exports the name (a package __init__.py, a barrel): follow the re-export.
    return symbol, [
        entity
        for target, sites in g.uses_named(owner).items()
        if target.endswith(f"::{owner}")
        and any(source == modules[0] and kind == "re-export" for source, _line, _column, kind in sites)
        for entity in g.definitions_named(owner)
        if entity.module == target.rsplit("::", 1)[0]
    ]


def _usages_of(g: CodeGraph, definition: EntityFact, symbol: str) -> tuple[list[Usage], int]:
    """Every use of symbol defined by definition, sorted by file and position; and how many are unresolved."""
    target = f"{definition.module}::{symbol}"
    found = [
        Usage(_impact_file(g, source), source, line, column, kind)
        for source, line, column, kind in g.uses_named(symbol.split(".", 1)[0]).get(target, [])
    ]
    unresolved = 0
    if "." in symbol:
        method = symbol.rsplit(".", 1)[1]
        for source, line, column in g.unresolved_by_member.get(method, []):
            found.append(Usage(_impact_file(g, source), source, line, column, "unresolved-call"))
            unresolved += 1
    with_positions = {usage.module for usage in found}
    # A file that references the symbol in a language without a use index: no positions.
    for edge in g.edges_by_entity_name.get(symbol, []):
        source = edge.source.split("::")[0]
        if edge.etype == "refs" and edge.target == target and source not in with_positions:
            with_positions.add(source)
            found.append(Usage(_impact_file(g, source), source, 0, 0, "reference"))
    return sorted(found, key=lambda usage: (usage.file, usage.line, usage.column, usage.kind)), unresolved


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def cmd_usages(g: CodeGraph, target: str, sexp: bool = False, show_all: bool = False) -> None:
    """Every place that uses one symbol, grouped by file, with line:column and the kind of use.

    target is Name, <file or module>::Name, or either with Class.method. A bare
    name defined in several files lists the candidates instead of mixing them.
    """
    symbol, definitions = _usages_definitions(g, target)
    if not definitions:
        _print_no_match(g, target)
        return
    if len(definitions) > 1:
        rows = sorted((_impact_file(g, d.module), d) for d in definitions)
        if sexp:
            print(f"(usages-ambiguous {symbol} :definitions {len(rows)})")
            for file_path, d in rows:
                print(f"(usages-candidate {d.module}::{symbol} :kind {d.kind} :file {_q(file_path)} :line {d.line})")
            return
        print(f"{len(rows)} definitions of {symbol}; ask for one of them:")
        width = max(len(file_path) + len(symbol) + 2 for file_path, _d in rows)
        for file_path, d in rows:
            name = f"{file_path}::{symbol}"
            print(f"  {name:<{width}}   {_KIND_WORDS.get(d.kind, d.kind)}, line {d.line}")
        return

    definition = definitions[0]
    usages, unresolved = _usages_of(g, definition, symbol)
    target_id = f"{definition.module}::{symbol}"
    definition_file = _impact_file(g, definition.module)
    shown = usages if show_all else usages[:_USAGES_LIMIT]
    hidden = len(usages) - len(shown)
    resolved_count = len(usages) - unresolved
    files = len({usage.file for usage in usages if usage.kind != "unresolved-call"})
    if sexp:
        print(f"(usages-target {target_id} :kind {definition.kind} :file {_q(definition_file)} :line {definition.line})")
        for usage in shown:
            if usage.line:
                print(f"(usage {usage.module} {usage.line} {usage.column} :kind {usage.kind} :file {_q(usage.file)})")
            else:
                print(f"(usage-file {usage.module} :kind {usage.kind} :file {_q(usage.file)})")
        if hidden:
            print(f"(usages-more {target_id} :hidden {hidden} :rerun {_rerun(f'usages {target}')})")
        print(f"(usages-summary {target_id} :uses {resolved_count} :files {files} :unresolved {unresolved})")
        return

    if "." in symbol:
        what = f"method of {symbol.split('.', 1)[0]}"
    else:
        what = _KIND_WORDS.get(definition.kind, definition.kind)
    print(f"Usages of {symbol} ({what}, {definition_file}:{definition.line})")
    summary = f"{_plural(resolved_count, 'use')} in {_plural(files, 'file')}"
    if unresolved:
        summary += f"; {_plural(unresolved, 'unresolved call')} (receiver type not in sight)"
    print(f"  {summary}")
    if not usages:
        return
    print()
    current = ""
    for usage in shown:
        if usage.file != current:
            current = usage.file
            print(current)
        where = f"{usage.line}:{usage.column}" if usage.line else "-"
        detail = usage.kind if usage.line else "reference (no use positions for this language)"
        print(f"  {where}   {detail}")
    if hidden:
        print(f"  ... {hidden} more {'use' if hidden == 1 else 'uses'}: rerun with --all")


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
    data_flow_targets: list[tuple[str, str]] = []   # target module, the marks of the stored fact
    call_targets: list[tuple[str, str]] = []
    for e in out_edges:
        tgt_mod = e.target.split("::")[0]
        if e.etype == "imports" and tgt_mod not in import_targets:
            import_targets.append(tgt_mod)
        elif e.etype == "refs":
            ref_targets.append((e.source, e.target))
        elif e.etype == "data-flow" and (tgt_mod, _edge_marks(e)) not in data_flow_targets:
            data_flow_targets.append((tgt_mod, _edge_marks(e)))
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
    data_flow_targets: list[tuple[str, str]], call_targets: list[tuple[str, str]],
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
    # Data flows: the marks are those of the stored fact, one line per different set of marks
    for t, marks in sorted(data_flow_targets):
        print(f"(analyze-dataflow {nid} {t} :lang {g.module_lang(t)}{marks})")

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
        label = module_label(g, t)
        print(f"(layer {nid} {t.split('/')[-1]} :layer {label.layer} :style {label.style} :rule {_q(label.rule)})")

    for item in module_advice(g, nid):
        print(advice_sexp(item))

    # Summary
    print(f"(analyze-summary {nid} :entities {len(ents)} :imports {len(import_targets)} :dataflows {len({t for t, _marks in data_flow_targets})} :refs {len(ref_targets)} :dependents {len(all_dependents)} :boundary-files {len(boundary_files)})")


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
    with open(sexp_path, "r", encoding="utf-8") as f:
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

    count = 0
    try:
        with open(sexp_path, "r", encoding="utf-8") as code_graph:
            for line in code_graph:
                if search_pattern_matches(parsed_pattern.value, line):
                    print(line.rstrip())
                    count += 1
    except OSError as error:
        print(f"(error search-read-failed {_q(str(error))})")
        return
    print(f"(result-count {count})")


@dataclass(frozen=True)
class BoundaryBreak:
    """A dependency that breaks a boundary rule of the style of its part."""

    source: str
    target: str
    source_layer: str
    target_layer: str
    kind: str          # imports | data-flow
    reason: str
    part: str
    style: str
    rule: str          # "<from> -> <to>": the deny rule of the style
    known: str         # baseline file of the project that already lists this pair, or ""
    severity: str = "violation"   # candidate: the rule itself gives candidates (a utility on infrastructure)


def _is_entry_root(g: CodeGraph, module_id: str) -> bool:
    """A composition root by its label, or an entry point: nothing imports it and it imports a composition root."""
    if module_label(g, module_id).root:
        return True
    if module_id not in g.entry_roots:
        g.entry_roots[module_id] = not _collect_rdep_mods(g, module_id) and any(
            module_label(g, target).root for target in _collect_dep_mods(g, module_id)
        )
    return g.entry_roots[module_id]


def boundary_rules(g: CodeGraph) -> list[tuple[str, str, str, str, str]]:
    """Every deny rule of every part: (part, style, from layer, to layer, reason)."""
    if not g.arch_parts:
        _default_labels(g)
    return [
        (part, g.arch_parts.get(part, {}).get("style", "-"), source, target, reason)
        for part, rules in sorted(g.arch_denies.items())
        for (source, target), reason in sorted(rules.items())
    ]


def boundary_violations(g: CodeGraph, scope_mods: set[str]) -> list[BoundaryBreak]:
    """Dependencies that break a deny rule of the style of their part.

    Rules apply between two modules of the same part only: a part is judged by
    its own style, and a dependency between parts is a dependency between
    packages, not between layers. A part without a determined style has no rules. A composition
    root (main.ts, app.module.ts, Program.cs) wires the layers and may import every one.
    """
    if not g.arch_parts:
        _default_labels(g)
    found: list[BoundaryBreak] = []
    for src_mod in sorted(scope_mods):
        source = module_label(g, src_mod)
        rules = g.arch_denies.get(source.part)
        if not rules or _is_entry_root(g, src_mod):
            continue
        for edge in g.edges_from.get(src_mod, []):
            if edge.etype not in ("imports", "data-flow"):
                continue
            tgt_mod = edge.target.split("::")[0]
            if tgt_mod == src_mod:
                continue
            target = module_label(g, tgt_mod)
            if target.part != source.part or target.layer == source.layer:
                continue
            reason = rules.get((source.layer, target.layer))
            if reason is None:
                continue
            found.append(BoundaryBreak(
                source=src_mod, target=tgt_mod, source_layer=source.layer, target_layer=target.layer,
                kind=edge.etype, reason=reason, part=source.part, style=source.style if source.style != "-" else
                g.arch_parts.get(source.part, {}).get("style", "-"),
                rule=f"{source.layer} -> {target.layer}", known=g.known.get((src_mod, tgt_mod), ""),
                severity="candidate" if (source.part, source.layer, target.layer) in g.candidate_rules else "violation",
            ))
    return found


@dataclass(frozen=True)
class CapabilityBreak:
    """A file that does something its layer may not do (SQL in a service, the framework in the domain)."""

    module: str
    capability: str
    layer: str
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    line: int
    evidence: str
    part: str
    style: str
    severity: str     # violation | candidate (a suspected finding, or a layer guessed from the role)
    known: str        # the baseline file that accepts it, or ""
    fix: str
    pattern: str = ""   # domain-imports-orm when the style checks that pattern


@dataclass(frozen=True)
class PatternBreak:
    """A pattern of the style that the code does not follow."""

    pattern: str
    source: str
    target: str       # the module the source reaches, or "" for a pattern within one file
    line: int
    evidence: str
    part: str
    style: str
    reason: str


_CONTROLLER_LAYERS = frozenset({"api", "controller", "interface", "driving-adapter"})
_SERVICE_LAYERS = frozenset({"service", "application"})
_REPOSITORY_LAYERS = frozenset({"infra", "infrastructure", "driven-adapter", "adapter", "model"})
_REPOSITORY_NAME = re.compile(r"repositor|(?:^|[/_.-])daos?(?:$|[/_.-])", re.IGNORECASE)
PATTERN_REASONS = {
    "controller-skips-service": "the controller calls a repository directly and skips the service layer",
    "service-builds-http-response": "the service builds an HTTP response; responses belong to the controller",
    "domain-imports-orm": "the domain imports the database client or ORM",
}


def _capability_rules(g: CodeGraph, part: str) -> dict[str, CapabilityRuleFact]:
    if not g.arch_parts:
        _default_labels(g)
    return g.arch_capabilities.get(part, {})


def capability_violations(g: CodeGraph, scope_mods: set[str]) -> list[CapabilityBreak]:
    """Files whose capabilities (SQL, database client, HTTP, framework, env, ...) their layer may not have.

    One item per file and capability, at its first line. A composition root may
    do everything; tests and files in no layer are not judged. A finding that is
    only suspected (a SQL literal not passed to a query call), or a layer only
    guessed from the role of the file, makes a candidate.
    """
    found: list[CapabilityBreak] = []
    for module in sorted(scope_mods):
        facts = g.capabilities.get(module)
        if not facts:
            continue
        label = module_label(g, module)
        rules = _capability_rules(g, label.part)
        if not rules or label.layer in ("other", "test") or _is_entry_root(g, module):
            continue
        certain = _layer_is_certain(g, module)
        style = g.arch_parts.get(label.part, {}).get("style", label.style)
        orm_pattern = "domain-imports-orm" in g.arch_patterns.get(label.part, set())
        by_capability: dict[str, list[CapabilityFact]] = {}
        for fact in facts:
            by_capability.setdefault(fact.capability, []).append(fact)
        for capability, items in sorted(by_capability.items()):
            rule = rules.get(capability)
            if rule is None or not rule.breaks(label.layer):
                continue
            proven = [item for item in items if not item.suspect]
            first = (proven or items)[0]
            severity = "violation" if proven and certain else "candidate"
            found.append(CapabilityBreak(
                module, capability, label.layer, rule.allowed, rule.forbidden, first.line, first.evidence,
                label.part, style, severity, g.capability_known.get((module, capability), ""), rule.fix,
                "domain-imports-orm" if orm_pattern and capability == "db-client" and label.layer == "domain" else "",
            ))
    return found


def pattern_violations(g: CodeGraph, scope_mods: set[str]) -> list[PatternBreak]:
    """The pattern checks of each part's style: a controller that calls a repository and skips the
    service, a service that builds an HTTP response. Both layers must come from rules."""
    if not g.arch_parts:
        _default_labels(g)
    found: list[PatternBreak] = []
    for module in sorted(scope_mods):
        label = module_label(g, module)
        patterns = g.arch_patterns.get(label.part, set())
        if not patterns or not _layer_is_certain(g, module) or _is_entry_root(g, module):
            continue
        style = g.arch_parts.get(label.part, {}).get("style", label.style)
        has_service = any(layer in _SERVICE_LAYERS for layer in g.arch_ranks.get(label.part, {}))
        if "controller-skips-service" in patterns and has_service and label.layer in _CONTROLLER_LAYERS:
            for target in sorted(_collect_dep_mods(g, module)):
                target_label = module_label(g, target)
                # A repository that reaches the database: a "logging repository" or a storage adapter is not one.
                data_access = any(fact.capability in ("sql", "db-client") for fact in g.capabilities.get(target, []))
                if (target_label.part == label.part and target_label.layer in _REPOSITORY_LAYERS
                        and _layer_is_certain(g, target) and _REPOSITORY_NAME.search(target) and data_access
                        and not is_type_only_import(g, module, target)):
                    found.append(PatternBreak("controller-skips-service", module, target, 0, "", label.part, style,
                                              PATTERN_REASONS["controller-skips-service"]))
        if "service-builds-http-response" in patterns and label.layer in _SERVICE_LAYERS:
            response = next((fact for fact in g.capabilities.get(module, []) if fact.capability == "http-response"), None)
            if response is not None:
                found.append(PatternBreak("service-builds-http-response", module, "", response.line, response.evidence,
                                          label.part, style, PATTERN_REASONS["service-builds-http-response"]))
    return found


def print_capability_findings(g: CodeGraph, scope_mods: set[str]) -> int:
    """The capability and pattern facts of `boundaries`, and their summary; nothing when there is none.
    Returns 1 when a capability violation or a pattern break was found, else 0."""
    breaks = capability_violations(g, scope_mods)
    patterns = pattern_violations(g, scope_mods)
    if not breaks and not patterns:
        return 0
    counts = {"capability-violation": 0, "capability-violation-known": 0, "capability-candidate": 0}
    for item in breaks:
        fact = "capability-candidate" if item.severity == "candidate" else (
            "capability-violation-known" if item.known else "capability-violation")
        counts[fact] += 1
        allowed = f" :allowed ({' '.join(item.allowed)})" if item.allowed else ""
        forbidden = f" :forbidden ({' '.join(item.forbidden)})" if item.forbidden else ""
        known = f" :known {_q(item.known)}" if item.known else ""
        pattern = f" :pattern {item.pattern}" if item.pattern else ""
        print(
            f"({fact} {_q(module_file(g, item.module))} :capability {item.capability} :layer {item.layer}{allowed}{forbidden} "
            f":line {item.line} :evidence {_q(item.evidence)} :part {_q(item.part)} :style {item.style}{known}{pattern}"
            f"{' :fix ' + _q(item.fix) if item.fix else ''})"
        )
    for item in patterns:
        target = f" :target {_q(module_file(g, item.target))}" if item.target else ""
        line = f" :line {item.line} :evidence {_q(item.evidence)}" if item.line else ""
        print(f"(pattern-violation {_q(module_file(g, item.source))} :pattern {item.pattern}{target}{line} "
              f":part {_q(item.part)} :style {item.style} {_q(item.reason)})")
    print(
        f"(capability-summary :violations {counts['capability-violation']} :known {counts['capability-violation-known']} "
        f":candidates {counts['capability-candidate']} :patterns {len(patterns)} :checked {len(scope_mods)})"
    )
    return 1 if counts["capability-violation"] or patterns else 0


def is_type_only_import(g: CodeGraph, src_mod: str, tgt_mod: str) -> bool:
    """True when src_mod imports tgt_mod for types only (the builder marks the edge :type-only)."""
    return any(
        edge.etype == "imports" and edge.target.split("::")[0] == tgt_mod and ":type-only" in edge.flags
        for edge in g.edges_from.get(src_mod, [])
    )


@dataclass(frozen=True)
class NotChecked:
    """A part with files that no rule checked: saying ok for it would be a false pass."""

    part: str
    reason: str               # declared-rules-not-read | style-undetermined | no-layer-style | too-few-files
    files: tuple[str, ...]    # the rule files that were not read
    count: int                # files (modules) of the part in the scope


_NOT_CHECKED_WORDS = {
    "declared-rules-not-read": "declared rules not read",
    "style-undetermined": "style not determined",
    "no-layer-style": "no layer style",
    "too-few-files": "too few files to judge a style",
}


def not_checked_text(item: NotChecked, code: str = "") -> str:
    """The reason in words, with the rule files when there are some: declared rules not read (.dependency-cruiser.js)."""
    files = ", ".join(f"{code}{name}{code}" for name in item.files)
    return _NOT_CHECKED_WORDS.get(item.reason, item.reason) + (f" ({files})" if files else "")


def not_checked_parts(g: CodeGraph, scope_mods: set[str]) -> list[NotChecked]:
    """The parts of the scope that hold files and have no rule that was read or determined."""
    if not g.arch_parts:
        _default_labels(g)
    counts: dict[str, int] = {}
    for module in scope_mods:
        part = module_label(g, module).part
        counts[part] = counts.get(part, 0) + 1
    found: list[NotChecked] = []
    for part, count in sorted(counts.items()):
        values = g.arch_parts.get(part)
        if values is None or g.arch_denies.get(part):
            continue
        style, state = values.get("style", "-"), values.get("state", "")
        if style == "declared":
            reason = "declared-rules-not-read"
        elif style == "undetermined":
            reason = "style-undetermined"
        elif state == "not-judged":
            reason = "too-few-files"
        else:
            reason = "no-layer-style"
        found.append(NotChecked(part, reason, tuple(sorted(g.declared_unread.get(part, []))), count))
    return found


def cmd_boundaries(g: CodeGraph, module_id: str) -> int:
    """Check the boundary rules of the style of each part (or of the rules the part declares).

    A pair is a violation when both layers come from rules (folders, file
    patterns, declared layers); when a layer is only guessed from the role of a
    file, the pair is a candidate. An import of types only is listed apart.
    Each line names the part, the style and the rule; `:known` names the
    project's own baseline when it already lists the pair.
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
    unchecked = not_checked_parts(g, scope_mods)
    for item in unchecked:
        files = f" :files ({' '.join(_q(name) for name in item.files)})" if item.files else ""
        print(f"(boundaries-not-checked :part {_q(item.part)} :reason {item.reason}{files} :files-in-part {item.count})")
    not_checked = sum(item.count for item in unchecked)
    coverage = (f"(boundaries-partial :checked {len(scope_mods) - not_checked} :not-checked {not_checked})"
                if unchecked else "")
    # Exit 2: a part whose own rules were not read was not checked; 1: a violation; 0 otherwise.
    status = 2 if any(item.reason == "declared-rules-not-read" for item in unchecked) else 0

    if not violations:
        print(coverage or f"(boundaries-ok :checked {len(scope_mods)})")
        return max(status, print_capability_findings(g, scope_mods))
    pairs: dict[str, set[tuple[str, str]]] = {
        "violation": set(), "violation-known": set(), "violation-type-only": set(), "violation-candidate": set(),
    }
    for item in sorted(violations, key=lambda v: (v.source, v.target, v.kind)):
        # The import decides: a pair imported for types only stays type-only whatever other edge joins it.
        if is_type_only_import(g, item.source, item.target):
            fact = "violation-type-only"
        elif item.severity != "candidate" and _layer_is_certain(g, item.source) and _layer_is_certain(g, item.target):
            fact = "violation-known" if item.known else "violation"
        else:
            fact = "violation-candidate"
        pairs[fact].add((item.source, item.target))
        known = f" :known {_q(item.known)}" if item.known else ""
        print(
            f"({fact} {item.source} {item.target} :src-layer {item.source_layer} :tgt-layer {item.target_layer} "
            f":via {item.kind} :lang {g.module_lang(item.source)} :part {_q(item.part)} :style {item.style} "
            f":rule {_q(item.rule)}{known} :src-file {_q(g.module_to_file(item.source))} "
            f":tgt-file {_q(g.module_to_file(item.target))} {_q(item.reason)})"
        )
    print(
        f"(boundaries-summary :violations {len(pairs['violation'])} :known {len(pairs['violation-known'])} "
        f":type-only {len(pairs['violation-type-only'])} "
        f":candidates {len(pairs['violation-candidate'])} :checked {len(scope_mods)})"
    )
    if coverage:
        print(coverage)
    capability_status = print_capability_findings(g, scope_mods)
    return status or (1 if pairs["violation"] else capability_status)


def cmd_layers(g: CodeGraph) -> None:
    """Show the layer of every module, grouped by layer, with the part, style and rule that set it."""
    by_layer: dict[str, list[str]] = {}
    for nid in sorted(g.nodes.keys()):
        by_layer.setdefault(_graph_layer(g, nid), []).append(nid)

    for layer_name in sorted(by_layer.keys()):
        mods = by_layer[layer_name]
        print(f"(layer-group {layer_name} :count {len(mods)})")
        for m in mods:
            label = module_label(g, m)
            print(
                f"(layer-member {layer_name} {m} :lang {g.module_lang(m)} :part {_q(label.part)} "
                f":style {label.style} :rule {_q(label.rule)} :match {label.match})"
            )


def architecture_inputs(g: CodeGraph) -> tuple:
    """(parts, modules, dependents, dependencies) of the graph as plain values for advice and align."""
    if not g.arch_parts:
        _default_labels(g)
    parts = tuple(
        _advice.PartRules(
            path=part, style=values.get("style", "-"), state=values.get("state", ""),
            ranks=g.arch_ranks.get(part, {}),
            denies={
                (source, target): (reason, "candidate" if (part, source, target) in g.candidate_rules else "violation",
                                   g.arch_fixes.get((part, source, target), ""))
                for (source, target), reason in g.arch_denies.get(part, {}).items()
            },
        )
        for part, values in sorted(g.arch_parts.items())
    )
    known_targets: dict[str, set[str]] = {}
    for source, target in g.known:
        known_targets.setdefault(source, set()).add(target)
    modules = {}
    for module in g.nodes:
        label = module_label(g, module)
        modules[module] = _advice.ModuleFact(
            module, _impact_file(g, module), label.part, label.layer,
            _layer_is_certain(g, module) and not _is_entry_root(g, module),
            frozenset(known_targets.get(module, ())),
        )
    dependents: dict[str, list[str]] = {}
    dependencies: dict[str, list[str]] = {}
    for source in g.nodes:
        for edge in g.edges_from.get(source, []):
            target = edge.target.split("::")[0]
            if edge.etype in ("imports", "data-flow") and target != source and target in g.nodes:
                if target not in dependencies.setdefault(source, []):
                    dependencies[source].append(target)
                    dependents.setdefault(target, []).append(source)
    return (
        parts, modules, {k: tuple(v) for k, v in dependents.items()}, {k: tuple(v) for k, v in dependencies.items()},
    )


def advice_items(g: CodeGraph) -> tuple[tuple, tuple]:
    """(advice, parts without advice) of the whole graph, computed once.

    Built from the architecture facts of the graph: the rules, ranks and fixes
    of each part, the labels, the import and data-flow edges inside a part, the
    violations (both layers set by rules; candidates and type-only imports are
    not advice) and the target layout.
    """
    if g.advice_cache is not None:
        return g.advice_cache
    parts, modules, dependents, dependencies = architecture_inputs(g)
    violations = tuple(
        _advice.Violation(item.source, item.target, item.source_layer, item.target_layer, item.part, item.style,
                          item.reason, bool(item.known))
        for item in boundary_violations(g, set(g.nodes))
        if item.severity != "candidate" and _layer_is_certain(g, item.source) and _layer_is_certain(g, item.target)
        and not is_type_only_import(g, item.source, item.target)
    )
    def in_repo(part: str, folder: str) -> str:
        return folder if part == "." else f"{part}/{folder}"

    targets = tuple(
        _advice.TargetFact(part, layer, block, in_repo(part, folder), proposed, source)
        for part, layer, block, folder, proposed, _present, source in g.arch_targets
    )
    layer_folders: dict[str, dict[str, str]] = {}
    for part, layer, block, folder, _proposed, present, _source in g.arch_targets:
        if not block:
            layer_folders.setdefault(part, {}).setdefault(layer, in_repo(part, present or folder))
    advice, quiet = _advice.advise(parts, modules, dependents, dependencies, violations, targets, layer_folders)
    # A part no rule checked says so in the same words as `boundaries`.
    unchecked = {item.part: item for item in not_checked_parts(g, set(g.nodes))}
    quiet = tuple((part, f"not checked: {not_checked_text(unchecked[part])}" if part in unchecked else why)
                  for part, why in quiet)
    g.advice_cache = (tuple(advice) + _capability_advice(g), quiet)
    return g.advice_cache


def _capability_advice(g: CodeGraph) -> tuple:
    """fix-capability and fix-pattern advice: what a file does that its layer may not, and how the style fixes it."""
    items = []
    scope = set(g.nodes)
    for item in capability_violations(g, scope):
        if item.severity != "violation" or item.known:
            continue
        target = module_file(g, item.module)
        rule = f"{item.capability} in {item.layer}"
        where = f"allowed in {', '.join(item.allowed)}" if item.allowed else f"not in {', '.join(item.forbidden)}"
        items.append(_advice.Advice(
            _advice.advice_id(item.part, "fix-capability", target, rule), item.part, "fix-capability", target,
            f"{item.fix or 'move this out of the ' + item.layer + ' layer'} ({item.capability} is {where})",
            item.style, rule, f"line {item.line}: {item.evidence}", (item.module,),
        ))
    for item in pattern_violations(g, scope):
        target = module_file(g, item.source)
        evidence = f"imports {module_file(g, item.target)}" if item.target else f"line {item.line}: {item.evidence}"
        items.append(_advice.Advice(
            _advice.advice_id(item.part, "fix-pattern", target, item.pattern), item.part, "fix-pattern", target,
            item.reason, item.style, item.pattern, evidence, tuple(m for m in (item.source, item.target) if m),
        ))
    return tuple(items)


def advice_sexp(item) -> str:
    """One advice item as an S-expression fact."""
    return _advice.to_sexp(item)


def module_advice(g: CodeGraph, module_id: str) -> list:
    """Advice items that concern one module (it is moved, or one of its imports breaks a rule)."""
    return [item for item in advice_items(g)[0] if module_id in item.modules]


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
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
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
            ["git", "-c", "core.quotepath=off", "diff", "--unified=0", ref],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, cwd=git_cwd,
            **portable.hidden_process_options(),
        )
        if result.returncode != 0:
            # Try as a commit ref
            result = subprocess.run(
                ["git", "-c", "core.quotepath=off", "diff", "--unified=0", ref, "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, cwd=git_cwd,
                **portable.hidden_process_options(),
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
; Scheme query language for .wingbox/index.sexp
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
;   (search "pattern")                 Grep .wingbox/index.sexp
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
            return {item.source for item in boundary_violations(g, scope)}

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

# The graph of a project is ROOT/.wingbox/index.sexp; the .wingbox/ folder marks the project root.
WINGBOX_DIRECTORY = ".wingbox"
INDEX_FILE = "index.sexp"
NO_PROJECT = "no .wingbox/ in this folder or its parents: run wingbox build"


def project_root_of(map_path: str) -> str:
    """The project a graph belongs to: the folder holding its .wingbox/ (else the graph's own folder)."""
    folder = os.path.dirname(os.path.abspath(map_path))
    return os.path.dirname(folder) if os.path.basename(folder) == WINGBOX_DIRECTORY else folder


def find_sexp_file(start: Optional[str] = None) -> str:
    """.wingbox/index.sexp of the nearest folder at or above start (default: the current folder).

    Without one, say so with the paths looked at and the --map and --root options, and exit 1.
    """
    current = os.path.abspath(start or os.getcwd())
    searched: list[str] = []
    while True:
        candidate = os.path.join(current, WINGBOX_DIRECTORY, INDEX_FILE)
        if os.path.isfile(candidate):
            return candidate
        searched.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    shown = searched[:6] + ([f"... {len(searched) - 6} more"] if len(searched) > 6 else [])
    print(
        f"{NO_PROJECT}\n"
        f"  looked for: {', '.join(shown)}\n"
        "  or point at a graph: --map /path/to/project/.wingbox/index.sexp --root /path/to/project",
        file=sys.stderr,
    )
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

    if args[0] == "usages":
        if not [argument for argument in args[1:] if argument != "--sexp"]:
            return "usages requires a symbol: Name, <file>::Name or <file>::Class.method"
        return None

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


def _load_focus(args: list[str]) -> Optional[str]:
    """The one name a command asks about, when it reads nothing else of the graph: usages, and
    entity with a literal name (a pattern may match any name, so it reads everything)."""
    if len(args) < 2 or args[0] not in ("usages", "entity"):
        return None
    target = [argument for argument in args[1:] if not argument.startswith("--")][:1]
    if not target or ".*" in target[0]:
        return None
    name = target[0].rpartition("::")[2].split(".", 1)[0]
    return name or None


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

    sexp_path = explicit_map or find_sexp_file(explicit_root)
    g = CodeGraph()
    load_result = g.load(sexp_path, explicit_root, focus=_load_focus(args))
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
    elif cmd == "usages" and len(args) >= 2:
        usages_args = [argument for argument in args[1:] if argument != "--sexp"]
        cmd_usages(g, usages_args[0], sexp="--sexp" in args, show_all=show_all)
    elif cmd == "entity" and len(args) >= 2:
        entity_args = [argument for argument in args[1:] if argument != "--positions"]
        cmd_entity(g, entity_args[0], positions="--positions" in args)
    elif cmd == "flow" and len(args) >= 3:
        cmd_flow(g, args[1], args[2])
    elif cmd == "search" and len(args) >= 2:
        cmd_search(g, args[1], sexp_path)
    elif cmd == "boundaries":
        target = args[1] if len(args) >= 2 else "all"
        return cmd_boundaries(g, target) or 0
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
