#!/usr/bin/env python3
"""
Code Graph Search -- query tool for S-expression code graphs.

Reads ``.wingbox/index.sexp`` and provides structured queries: module views,
dependency trees, reverse dependency analysis, entity lookups, data flow
tracing, and boundary discovery for minimal context loading.

Output is S-expressions by default (the form agents read): fact lines, one per
line and greppable, or for ``impact`` and ``usages`` one compact form per
answer. The default form is complete. Every command takes one form option:

    --human   the text for people (``impact`` and ``usages``; the fact lines of the
              other commands), with its list limits; ``--all`` lifts them
    --json    the same answer as one JSON object, complete
    --sexp    what versions up to 0.13 printed with it: for ``impact`` and
              ``usages`` one flat fact per line with module IDs and the list
              limits (``--all`` lifts them); scripts written for those versions
              read these lines

Usage::

    code_graph_search.py analyze <id>           Full analysis in one step
    code_graph_search.py boundary <id>          File paths for change boundary
    code_graph_search.py quick <keyword>        Search + analyze in one step
    code_graph_search.py module <id> [--lines]  Show module with entities and edges; --lines: imports and
                                                refs name the lines of the statements they are read from
                                                (search <pattern> --lines: the facts as stored, with
                                                their lines and the built-from fact)
    code_graph_search.py deps <id> [depth]      Outgoing dependency tree (default depth 2)
    code_graph_search.py rdeps <id> [depth]     Reverse dependency tree (default depth 2)
    code_graph_search.py impact <file|id> [<file|id> ...] [depth]
                                                Blast radius of a change: dependents by depth, each with
                                                the reason it is listed, and the files to read first;
                                                one answer per file, in the order given, after a
                                                (graph :built .. :files .. :stale ..) fact when several
                                                files are asked
    code_graph_search.py usages <symbol>        Every use of one symbol (Name, <file>::Name,
                                                <file>::Class.method) by file, line:column and kind
    code_graph_search.py entity <name> [--positions]
                                                Find entity: definition + the modules that use it;
                                                --positions: every use with its line and column
    code_graph_search.py flow <id> <entity>     Trace data/call flow from entity
    code_graph_search.py search <pattern>       Safe pattern search across all facts
    code_graph_search.py boundaries [scope] [--strict]
                                                Check the layer rules of each part (default: all);
                                                --strict: a boundaries-result line and exit 0 pass,
                                                1 violation, 2 not fully checked, 3 no readable graph
    code_graph_search.py invariants [scope]     Check naming-convention violations (default: all)
    code_graph_search.py cycles [folder]        Files that depend on each other, hubs, and the order of the folders
    code_graph_search.py find_function <name>   Find function/method definitions by name
    code_graph_search.py find_type <name>       Find type/class/interface/enum definitions by name
    code_graph_search.py find_callchain <src_fn> <tgt_fn> [depth]  Trace call path between two functions
    code_graph_search.py find_callers <fn_name> [depth]  Who calls this function (incoming calls)
    code_graph_search.py find_calls <fn_name> [depth]    What does this function call (outgoing calls)
    code_graph_search.py grep_functions <pattern>         Search function bodies in source files
    code_graph_search.py diff_functions [ref]             Identify changed functions in git diff (default: HEAD)
    code_graph_search.py files [pattern]                  The source files of the graph: path, module, language, layer
    code_graph_search.py source <file|Name|file::Name> [first] [last]
                                                          The lines of a source file of the graph or of one definition
    code_graph_search.py grep <text> [path-pattern] [--first N]
                                                          The lines of the source files of the graph that hold a text
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
import contextlib
import datetime
import functools
import hashlib
import io
import os
import re
import shlex
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Optional, Union

if __package__:
    from .architecture import advice as _advice
    from .architecture import baseline as _baseline
    from .architecture.detect import Part
    from .architecture.labels import label_in_part
    from .architecture.library import load_library
    from .architecture.plan import PartPlan, layer_folders
    from .architecture.words import DEFAULT_SETTINGS as _MATCH_SETTINGS
    from .architecture.words import levenshtein as _levenshtein
    from . import fact_json, impact_forms, source_kinds, usages_forms
    from . import layer_guess as _layer_guess
    from . import portable
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from architecture import advice as _advice
    from architecture import baseline as _baseline
    from architecture.detect import Part
    from architecture.labels import label_in_part
    from architecture.library import load_library
    from architecture.plan import PartPlan, layer_folders
    from architecture.words import DEFAULT_SETTINGS as _MATCH_SETTINGS
    from architecture.words import levenshtein as _levenshtein
    import fact_json
    import impact_forms
    import layer_guess as _layer_guess
    import portable
    import source_kinds
    import usages_forms

MAP_SCHEMA_VERSION = 1
MAX_SEARCH_PATTERN_LENGTH = 256
MAX_QUERY_DEPTH = 50
MAX_QUERY_NESTING = 100   # levels of parentheses in one query expression; it is read and evaluated by recursion


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
_EDGE_KINDS = ("imports", "calls-api", "data-flow", "refs", "calls", "http-calls", "registry-wires", "generated", "runs")
_EDGE_FACTS = tuple(f"({kind} " for kind in _EDGE_KINDS)


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
    # imports, refs: the lines of the statements the fact is read from (:line, :lines); () when no single
    # statement states it, and in a graph built before the lines were stored.
    lines: tuple[int, ...] = ()


def parse_search_pattern(raw: str, text: bool = False) -> SearchPatternResult:
    """Parse the supported linear pattern grammar without regex execution.

    With text=True the pattern is text to find in source lines: every character stands for itself, the
    operators the grammar refuses elsewhere included; special are only `.*`, `^` first, `$` last and the
    backslash that makes the next character literal."""
    if len(raw) > MAX_SEARCH_PATTERN_LENGTH:
        return SearchPatternErr(
            f"pattern exceeds {MAX_SEARCH_PATTERN_LENGTH} characters"
        )

    anchor_start = raw.startswith("^")
    body_start = 1 if anchor_start else 0
    # A last `$` after an odd number of backslashes is escaped: it is text, not the end of the line.
    before_last = raw[:-1]
    escaping = len(before_last) - len(before_last.rstrip("\\"))
    anchor_end = raw.endswith("$") and escaping % 2 == 0
    body_end = len(raw) - 1 if anchor_end else len(raw)
    body = raw[body_start:body_end]

    segments: list[str] = []
    literal: list[str] = []
    index = 0
    unsupported = set() if text else set("[](){}+?|")
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
        if not text and (character == "*" or character in unsupported):
            return SearchPatternErr(
                f"unsupported pattern operator: {character}"
            )
        if not text and character in ("^", "$"):
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
    if len(values) >= 2 and values[-2] == ":of" and values[-1].isdecimal():
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
    """One atom of a fact line: bare when it is a plain symbol, else a quoted string. Empty text, a leading
    colon (it would read as a keyword), white space, a quote, a parenthesis and a semicolon (the comment
    sign) make it a quoted string, so a module or a file with such a name (`app/(group)/page`, `my dir/a`)
    stays one atom for every reader. A backslash alone does not: a Windows path prints as it did."""
    if s and not s.startswith(":") and not any(character in s for character in ' \t\n\r"();'):
        return s
    return '"' + _escaped(s) + '"'


def _q013(s: str) -> str:
    """An atom as versions up to 0.13 wrote it, quoted only for a space or a quote: the flat facts of
    `impact --sexp` and `usages --sexp` keep it, byte for byte, with the names they print bare."""
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

def _no_match_fact(pattern: str, suggestions: Sequence[str], flat: bool = False) -> str:
    """The no-match error; flat: as versions up to 0.13 wrote its atoms."""
    if flat:
        tail = f" :suggestions {' '.join(suggestions)}" if suggestions else ""
        return f"(error no-match {_q013(pattern)}{tail})"
    tail = f" :suggestions {' '.join(map(_q, suggestions))}" if suggestions else ""
    return f"(error no-match {_q(pattern)}{tail})"


def _print_no_match(g: CodeGraph, pattern: str, flat: bool = False) -> None:
    """Print a structured no-match error with fuzzy suggestions; for a file of the repository that is not in
    the graph, why it is not (flat: the 0.13 form, always no-match)."""
    outside = None if flat else outside_graph(g, pattern)
    if outside is not None:
        print(impact_forms.missing_fact(impact_forms.ImpactMissing(outside.file, (), "", outside.reason, outside.language)))
        return
    print(_no_match_fact(pattern, CodeGraph._suggest_similar(pattern, g.nodes), flat))


@dataclass(frozen=True)
class OutsideGraph:
    """A file of the repository that is not in the graph, and why."""

    file: str       # relative to the graph root
    reason: str     # not-parsed-language, skipped-language, excluded, not-source, or why the build left this
                    # one file out (a key of source_kinds.UNPARSED_FILE_REASONS: parser-failed, binary, ...)
    language: str   # the display name of its language; "" for not-source

    @property
    def text(self) -> str:
        if self.reason == "not-parsed-language":
            why = f"Wingbox has no parser for {self.language}"
        elif self.reason == "skipped-language":
            why = f"the {self.language} parser is not installed"
        elif self.reason == "excluded":
            why = f"the build left this {self.language} file out (a skipped folder, a path Git ignores, a minified file)"
        elif self.reason in source_kinds.UNPARSED_FILE_REASONS:
            why = source_kinds.UNPARSED_FILE_REASONS[self.reason].format(language=self.language)
        else:
            why = "it is not a source file"
        return (f"{self.file} is not in the graph: {why}. Its dependents are not known here: "
                f"search the text for its name (wingbox grep, rg).")


def _state_text(g: CodeGraph, name: str) -> str:
    """The text of a record the build wrote beside the graph; "" when there is none."""
    if not g._map_path:
        return ""
    try:
        with open(os.path.join(os.path.dirname(g._map_path), name), "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, ValueError):   # ValueError: bytes that are not UTF-8
        return ""


def outside_graph(g: CodeGraph, target: str) -> Optional[OutsideGraph]:
    """Why a file of the repository is not in the graph; None when target names no existing file under the root."""
    if not g._root or not target or "::" in target:
        return None
    path = portable.local_path(target)
    absolute = path if os.path.isabs(path) else os.path.join(g._root, path)
    if not os.path.isfile(absolute):
        absolute = os.path.join(os.getcwd(), path)   # as a file argument is read: from the root, then from here
        if not os.path.isfile(absolute):
            return None
    relative = portable.relative_posix(absolute, g._root)
    if relative is None:
        return None
    if _impact_target_modules(g, relative, _target_to_module_pattern(relative, g._root)):
        # The file is a module of the graph under its module ID: a command that takes IDs did not find
        # it by its path. That is a name that matches nothing, not a file outside the graph.
        return None
    name = os.path.basename(absolute)
    parsed = source_kinds.parsed_language(name)
    if parsed is not None:
        left_out = source_kinds.read_unparsed_files(_state_text(g, "graph-unparsed.sexp")).get(relative)
        if left_out is not None:
            return OutsideGraph(relative, left_out, parsed)
        skipped = f"(skipped-language {source_kinds.LANGUAGE_NAMES[parsed]} " in _state_text(g, "graph-skipped.sexp")
        return OutsideGraph(relative, "skipped-language" if skipped else "excluded", parsed)
    other = source_kinds.not_parsed_language(name)
    if other is not None:
        return OutsideGraph(relative, "not-parsed-language", other)
    return OutsideGraph(relative, "not-source", "")


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
    # In the order the builder gives a folder its module ID; a Python index.py is the module of its folder too.
    for idx in ("index.ts", "index.tsx", "index.js", "index.py", "__init__.py", "mod.rs", "index.blade.php"):
        candidate = os.path.join(base, idx)
        if os.path.isfile(candidate):
            return candidate
    return module_id


# ---------------------------------------------------------------------------
# Graph loader
# ---------------------------------------------------------------------------

# Facts kept unparsed at load and read when a query needs them.
_LAZY_FACTS = ("(use ", "(unresolved-call ", "(unresolved ", "(var ")


# Up to this many files the first question that needs use positions fills the whole use file at once,
# as it always did; above it the question reads the files that hold its name and the fill runs apart.
# WINGBOX_POSITIONS_FILL=1 takes the second way whatever the size, =0 the second way without the fill.
_FILL_AT_ONCE_FILES = 300


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
        # What the graph was built from (built-from fact): tree, commit, wingbox; {} in a graph without it.
        self.built_from: dict[str, str] = {}
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
        self._names_used: Optional[dict[tuple[str, str], tuple[str, ...]]] = None
        self._unloaded_imports: list[str] = []
        self._includes: Optional[dict[str, set[str]]] = None
        self._uses_file_read = False
        # Why the use facts are not those of the use file ("" when they are): no-use-file, stale-use-file,
        # unreadable-use-file. And whether the positions last asked for (_positions_of) were read in the
        # files that hold the name only.
        self._uses_gap = ""
        self._positions_pending = False
        self._map_path = ""
        self._module_files: dict[tuple[str, str], str] = {}  # (root, module) -> file, worked out once
        self.pmap: dict[str, str] = {}
        self.package_layers: dict[str, str] = {}  # Python package __init__ -> layer of its package
        self.arch_parts: dict[str, dict[str, str]] = {}   # part -> keyword values of its arch-part fact
        # A file of the repository outside the graph -> the generated modules that depend on it, with the marks
        # of the fact (:by or :from, :declared or :inferred); and the paths a generation link names in vain.
        self.generated_from_files: dict[str, list[tuple[str, list[str]]]] = {}
        self.generation_unresolved: list[tuple[str, dict[str, str], list[str]]] = []
        # Build targets (name -> the modules among its sources) and tests (name -> the target it runs, the
        # modules its command names), as the CMake files state them.
        self.build_targets: dict[str, tuple[str, ...]] = {}
        self.build_tests: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.unread_targets: set[str] = set()   # targets marked `:sources unread`: their source list was not all read
        self._name_positions: dict[str, list[str]] = {}   # name -> its use lines read before the use file has positions
        self.arch_denies: dict[str, dict[tuple[str, str], str]] = {}   # part -> (from, to) -> reason
        self.candidate_rules: set[tuple[str, str, str]] = set()   # (part, from, to) of rules that give candidates
        self.entry_roots: dict[str, bool] = {}   # module -> nothing imports it and it imports a composition root
        self.gate = GateFacts()   # what `boundaries` reads besides the labels and the rules
        self.arch_fixes: dict[tuple[str, str, str], str] = {}   # (part, from, to) -> fix the style prescribes
        self.arch_ranks: dict[str, dict[str, int]] = {}         # part -> layer -> rank
        self.arch_targets: list[tuple[str, str, str, str, bool, str, str]] = []   # part, layer, block, folder, proposed, present, source
        self.advice_cache: Optional[tuple] = None
        self._layer_guesses: Optional[dict[str, str]] = None   # purpose -> layer the build stored, read on first need
        self.labels: dict[str, "ModuleLabel"] = {}   # module -> layer label written by the build
        self.known: dict[tuple[str, str], str] = {}   # (source, target) -> baseline file of the project
        # The owner, reason and end date a baseline entry states; an entry past its end date is not in
        # `known` (nor in `capability_known`): its finding is a violation again.
        self.known_terms: dict[tuple[str, str], _baseline.Acceptance] = {}
        self.known_expired: dict[tuple[str, str], _baseline.Acceptance] = {}
        self.capability_terms: dict[tuple[str, str], _baseline.Acceptance] = {}
        self.capability_expired: dict[tuple[str, str], _baseline.Acceptance] = {}
        self.arch_capabilities: dict[str, dict[str, CapabilityRuleFact]] = {}   # part -> capability -> rule
        self.arch_patterns: dict[str, set[str]] = {}          # part -> pattern checks of its style
        self.capabilities: dict[str, list[CapabilityFact]] = {}   # module -> what its file does
        self.capability_known: dict[tuple[str, str], str] = {}    # (module, capability) -> baseline file
        self.declared_unread: dict[str, list[str]] = {}   # part -> its rule files that were not read
        self._root: str = ""

    def load(self, path: str, root: Optional[str] = None, focus: Optional[str] = None) -> MapLoadResult:
        """Read a code graph. With focus (a name), only the entity and edge facts that mention it are
        read, besides the paths and the nodes: what a question about that one name needs."""
        self.built_from = {}
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
        self._names_used = None
        self._unloaded_imports = []
        self._includes = None
        self._name_positions = {}
        self._uses_file_read = False
        self._uses_gap = ""
        self._positions_pending = False
        self._map_path = path
        self._module_files = {}
        self.pmap = {}
        self.package_layers = {}
        self.arch_parts = {}
        self.generated_from_files = {}
        self.generation_unresolved = []
        self.build_targets = {}
        self.build_tests = {}
        self.unread_targets = set()
        self.arch_denies = {}
        self.candidate_rules = set()
        self.entry_roots = {}
        self.gate = GateFacts()
        self.arch_fixes = {}
        self.arch_ranks = {}
        self.arch_targets = []
        self.advice_cache = None
        self._layer_guesses = None
        self.labels = {}
        self.known = {}
        self.known_terms = {}
        self.known_expired = {}
        self.capability_terms = {}
        self.capability_expired = {}
        today = datetime.date.today().isoformat()
        self.arch_capabilities = {}
        self.arch_patterns = {}
        self.capabilities = {}
        self.capability_known = {}
        self.declared_unread = {}
        self._root = os.path.abspath(root if root is not None else project_root_of(path))
        try:
            with open(path, "r", encoding="utf-8") as code_graph:
                map_lines = code_graph.readlines()
        except (OSError, ValueError) as error:   # ValueError: bytes that are not UTF-8
            return MapLoadErr(f"could not read {path}: {error}")

        pmap: dict[str, str] = {}
        declared_node_count: Optional[int] = None
        declared_edge_count: Optional[int] = None   # None: a header that states no edge count
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
                    or not header_tokens[nodes_index + 1].isdecimal()   # isdigit takes a superscript two, int() does not
                ):
                    return MapLoadErr("code-graph header has an invalid node count")
                declared_node_count = int(header_tokens[nodes_index + 1])
                if ":edges" in header_tokens:
                    stated_edges = header_tokens[header_tokens.index(":edges") + 1:][:1]
                    if not stated_edges or not stated_edges[0].isdecimal():
                        return MapLoadErr("code-graph header has an invalid edge count")
                    declared_edge_count = int(stated_edges[0])
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

        edge_facts = 0   # the edge facts of the file, read or not: the header declares their number
        for line in map_lines:
            if line.startswith(_LAZY_FACTS):
                self._symbol_lines.append(line)
                continue
            if focus is not None and focus not in line and not line.startswith(("(node ", "(code-graph")):
                if line.startswith("(imports "):
                    self._unloaded_imports.append(line)   # read on need: who includes a header (includes_of)
                if line.startswith(_EDGE_FACTS) or (line[:1].isspace() and line.lstrip().startswith(_EDGE_FACTS)):
                    edge_facts += 1
                continue
            tokens = tokenize(line)
            if not tokens:
                continue
            kind = tokens[0]

            if kind in ("code-graph", "paths", "def", ")"):
                continue

            if kind == "built-from":
                self.built_from = _keywords(tokens[1:])
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
                line_num = int(tokens[3]) if tokens[3].isdecimal() else 0
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
                edge_facts += 1
                source = _resolve(tokens[1])
                target = _resolve(tokens[2])
                flags = [t for t in tokens[3:] if t.startswith(":")]
                routes = tuple(route for route in map(parse_name_route, tokens[3:]) if route is not None) \
                    if kind == "imports" else ()
                evidence = ""
                stated_lines: tuple[int, ...] = ()
                if kind == "data-flow":
                    flags = [flag for flag in flags if flag != _RETIRED_VALIDATION_MARK]
                    evidence = _keywords(tokens[3:]).get("evidence", "")
                elif kind in ("imports", "refs") and (":line" in flags or ":lines" in flags):
                    # The lines are not a mark of the edge: every output prints the marks as it did before.
                    stated_lines = _read_edge_lines(tokens[3:])
                    flags = [flag for flag in flags if flag not in (":line", ":lines")]
                edge = Edge(kind, source, target, flags, routes, evidence, stated_lines)
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

            elif kind == "generated-file" and len(tokens) >= 3:
                # A generated file depends on a file of the repository that is not in the graph (a schema, a template).
                flags = [token for token in tokens[3:] if token.startswith(":")]
                self.generated_from_files.setdefault(tokens[2], []).append((_resolve(tokens[1]), flags))
            elif kind in ("target", "ctest") and len(tokens) >= 2:
                values = _keywords(tokens[2:])
                taken = {":runs"} | {token for key in ("kind", "in", "target", "sources") if key in values for token in (f":{key}", values[key])}
                modules = tuple(_resolve(token) for token in tokens[2:] if token not in taken and token.strip('"') not in taken)
                if kind == "target":
                    self.build_targets[tokens[1]] = modules
                    if values.get("sources") == "unread":
                        self.unread_targets.add(tokens[1])
                else:
                    self.build_tests[tokens[1]] = (values.get("target", ""), modules)
            elif kind == "generation-unresolved" and len(tokens) >= 2:
                self.generation_unresolved.append((tokens[1], _keywords(tokens[2:]), [t for t in tokens[2:] if t in (":declared", ":inferred")]))
            elif kind == "arch-part" and len(tokens) >= 2:
                self.arch_parts[tokens[1]] = _keywords(tokens[2:])
            elif kind == "arch-deny" and len(tokens) >= 5:
                self.arch_denies.setdefault(tokens[1], {})[(tokens[2], tokens[3])] = tokens[4]
                values = _keywords(tokens[5:])
                if values.get("severity") == "candidate":
                    self.candidate_rules.add((tokens[1], tokens[2], tokens[3]))
                if values.get("fix"):
                    self.arch_fixes[(tokens[1], tokens[2], tokens[3])] = values["fix"]
            elif kind == "arch-rank" and len(tokens) >= 4 and _WHOLE_NUMBER.match(tokens[3]):
                self.arch_ranks.setdefault(tokens[1], {})[tokens[2]] = int(tokens[3])
            elif kind == "arch-target" and len(tokens) >= 4:
                values = _keywords(tokens[4:])
                self.arch_targets.append((
                    tokens[1], tokens[2], values.get("block", ""), tokens[3], ":proposed" in tokens,
                    values.get("present", ""), values.get("source", ""),
                ))
            elif kind == "arch-known" and len(tokens) >= 5:
                pair = (_resolve(tokens[2]), _resolve(tokens[3]))
                entries = self.gate.known_entries.setdefault(pair, [])
                entries.append((tokens[4], tokens[1], _baseline.terms_of(tokens[5:])))
                self.gate.known_files[pair] = _settle_entries(
                    pair, [(file, terms) for file, _part, terms in entries], today,
                    self.known, self.known_terms, self.known_expired)
            elif kind == "arch-known-rule" and len(tokens) >= 5:
                self.gate.known_rules.setdefault((tokens[1], tokens[2], tokens[3]), set()).add(tokens[4])
            elif kind in ("arch-not-parsed", "arch-capability-unread") and len(tokens) >= 3:
                files = _keywords(tokens[3:]).get("files", "")
                table = self.gate.not_parsed if kind == "arch-not-parsed" else self.gate.capability_unread
                table.setdefault(tokens[1], {})[tokens[2]] = int(files) if files.isdigit() else 0
            elif kind == "arch-rule-files":
                self.gate.rule_digests = _keywords(tokens[1:])
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
                stated_line = values.get("line", "")
                self.capabilities.setdefault(_resolve(tokens[1]), []).append(CapabilityFact(
                    tokens[2], int(stated_line) if stated_line.isdecimal() else 0, values.get("evidence", ""), ":suspect" in tokens,
                ))
            elif kind == "arch-declared-unread" and len(tokens) >= 3:
                self.declared_unread.setdefault(tokens[1], []).append(tokens[2])
            elif kind == "capability-known" and len(tokens) >= 5:
                key = (_resolve(tokens[2]), tokens[3])
                entries = self.gate.capability_entries.setdefault(key, [])
                entries.append((tokens[4], _baseline.terms_of(tokens[5:])))
                _settle_entries(key, entries, today, self.capability_known, self.capability_terms, self.capability_expired)
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
        if declared_edge_count is not None and edge_facts < declared_edge_count:
            # The builder writes one fact per edge it counts: fewer is a file cut short, and its answers
            # would name fewer dependents than there are. More is a graph written by hand: nothing is lost.
            return MapLoadErr(
                f"code-graph edge count mismatch: expected {declared_edge_count}, loaded {edge_facts} "
                "(the file is cut short: run wingbox build)"
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
                if not self._symbol_lines:
                    # A build writes the use file; only a graph written before there was one holds its use
                    # facts itself. Without either, who uses a name is not known, and the answer says so.
                    self._uses_gap = "no-use-file"
                    print(f"(warning uses-missing {_q(path)} :rerun \"wingbox build\")", file=sys.stderr)
                return self._symbol_lines
            except (OSError, ValueError) as error:   # ValueError: bytes that are not UTF-8
                self._uses_gap = "unreadable-use-file"
                print(f"(warning uses-unreadable {_q(path)} {_q(str(error))})", file=sys.stderr)
                return self._symbol_lines
            header = next((line for line in lines if line.startswith("(code-graph-uses ")), "")
            self._uses_header = header
            if f":graph {digest})" not in header:
                self._uses_gap = "stale-use-file"
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

    def _positions_of(self, name: str) -> list[str]:
        """The use facts with positions that a question about one name needs. While the use file has no
        positions, the builder reads only the files that hold the name (use_position_lines), which
        takes seconds where filling the whole file takes as long as a build, and starts that fill in
        a process of its own for the questions that follow."""
        self._positions_pending = False
        lines = self._use_lines()
        header = self._uses_header
        if not header or ":positions no" not in header:
            return lines
        mode = os.environ.get("WINGBOX_POSITIONS_FILL", "")
        if mode not in ("0", "1") and len(self.nodes) <= _FILL_AT_ONCE_FILES:
            # A small repository: filling the whole use file takes less than the question is worth splitting.
            return self._positions()
        if name not in self._name_positions:
            builder = _load_builder()
            found = builder.use_position_lines(self._map_path, self._root, name)
            if isinstance(found, builder.Err):
                return self._positions()   # the whole fill says why positions are not to be had
            self._name_positions[name] = found.value
            builder.start_position_fill(self._map_path, self._root)
        # A file that uses the name under another one (an alias, a re-export) is not among the files read.
        self._positions_pending = True
        return self._name_positions[name]

    def unresolved_calls_of(self, member: str) -> list[tuple[str, int, int]]:
        """(module, line, column) of the calls of a method name that no receiver type resolves."""
        found: list[tuple[str, int, int]] = []
        for line in self._positions_of(member):
            tokens = tokenize(line)
            if len(tokens) < 4 or tokens[0] != "unresolved-call" or tokens[2] != member:
                continue
            source = self.pmap.get(tokens[1], tokens[1])
            for position in tokens[3:]:
                match = _USE_POSITION.fullmatch(position)
                if match:
                    found.append((source, int(match.group(1)), int(match.group(2))))
        return found

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

    def includes_of(self, module: str) -> set[str]:
        """The modules a module imports or includes, also when the graph was read for one name only."""
        if self._includes is None:
            found: dict[str, set[str]] = {}
            for source, edges in self.edges_from.items():
                for edge in edges:
                    if edge.etype == "imports":
                        found.setdefault(source, set()).add(edge.target.split("::")[0])
            for line in self._unloaded_imports:
                parts = line.split(" ", 3)
                if len(parts) >= 3:
                    source, target = parts[1], parts[2].rstrip(")")
                    found.setdefault(self.pmap.get(source, source), set()).add(self.pmap.get(target, target))
            self._includes = found
        return self._includes.get(module, set())

    def names_used(self, owner: str, user: str) -> tuple[str, ...]:
        """The names of the module owner that the module user uses, by the use lines; sorted."""
        if self._names_used is None:
            found: dict[tuple[str, str], list[str]] = {}
            for key, users in self.use_pairs.items():
                module, _, name = key.partition("::")
                for item in users:
                    found.setdefault((module, item), []).append(name)
            self._names_used = {pair: tuple(sorted(set(names))) for pair, names in found.items()}
        return self._names_used.get((owner, user), ())

    @property
    def variables_by_name(self) -> dict[str, list[EntityFact]]:
        """Module-level variables (Python), from the (var <module> <name> <line>) facts of the use file."""
        if self._variables is None:
            found: dict[str, list[EntityFact]] = {}
            for line in self._use_lines():
                if line.startswith("(var "):
                    parts = line.strip().strip("()").split()
                    if len(parts) == 4 and parts[3].isdecimal():
                        module = self.pmap.get(parts[1], parts[1])
                        found.setdefault(parts[2], []).append(EntityFact(module, parts[2], "var", int(parts[3]), [], [], []))
            self._variables = found
        return self._variables

    def classes_with_method(self, name: str) -> list[EntityFact]:
        """The C and C++ classes that declare a method of this name, by file and line."""
        found = [
            entity for entities in self.entity_by_name.values() for entity in entities
            if entity.kind == "cls" and name in entity.methods and self.module_lang(entity.module) in ("cpp", "c")
        ]
        return sorted(found, key=lambda entity: (entity.module, entity.line, entity.name))

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
                if len(tokens) == 4 and tokens[0] == "unresolved" and tokens[3].isdecimal():
                    found[self.pmap.get(tokens[1], tokens[1])] = int(tokens[3])
            self._unresolved_counts = found
        return self._unresolved_counts

    def uses_named(self, name: str) -> dict[str, list[UseSite]]:
        """module::name (and module::name.method) -> its use sites, read only from the use lines
        that mention name (a query about one symbol does not parse the whole use file)."""
        marker = f"::{name}"
        found: dict[str, list[UseSite]] = {}
        for line in self._positions_of(name):
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

        An argument that names a file (see _names_file) is the module whose file it is, or nothing: only a
        module pattern is matched by its parts. A suffix match starts at a path or name boundary (`core`
        is app/core, not app/hardcore). An empty pattern matches nothing.

        Returns an empty list when nothing matches (caller should use
        _print_no_match to report the failure with suggestions).
        """
        if not pattern.strip():
            return []
        exact = _exact_module(self, pattern)
        if exact is not None:
            return [exact]
        if _names_file(self, pattern):
            return []
        resolved_pattern = self.resolve_alias(pattern)
        if resolved_pattern in self.nodes:
            return [resolved_pattern]
        if pattern in self.nodes:
            return [pattern]
        suffix_matches = [
            nid for nid in self.nodes
            if nid.endswith(pattern) and (len(nid) == len(pattern) or not (nid[-len(pattern) - 1] + pattern[0]).isalnum())
        ]
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

    def layer_guess(self, purpose: str) -> str:
        """The layer the purpose of a module suggests. The build stored it for every purpose of the graph
        (.wingbox/cache.layer-guess.json, read once, at the first guess asked for); a purpose that file does
        not hold is matched here, with the same result."""
        if self._layer_guesses is None:
            self._layer_guesses = _stored_layer_guesses(self._root)
        stored = self._layer_guesses.get(purpose)
        return stored if stored is not None else _classify_layer_by_purpose(purpose)

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


def _shortest_depths(start: str, hops: int, neighbours: Callable[[str], dict[str, str]]) -> dict[str, int]:
    """module -> the fewest hops from start to it, for start (0) and every module within hops of it."""
    depths = {start: 0}
    frontier = [start]
    for depth in range(1, hops + 1):
        found: list[str] = []
        for module in frontier:
            for other in neighbours(module):
                if other not in depths:
                    depths[other] = depth
                    found.append(other)
        if not found:
            break
        frontier = found
    return depths


def _walk_tree(
    start: str, visited: set[str], max_depth: int, first_depth: int,
    emit: Callable[[int, str, str, bool], None], neighbours: Callable[[str], dict[str, str]],
) -> None:
    """Emit the tree below start, depth first, as `deps` and `rdeps` print it: every module within the depth
    on one line at its shortest depth, where it is followed further. A module that is already listed and
    comes up again is emitted with the cycle mark and not followed, as versions up to 0.13 printed it. A
    module that a longer path reaches before its shortest one is not emitted there: followed at the longer
    depth (or not at all, at the last depth) it hid the modules behind it."""
    shortest = _shortest_depths(start, max_depth - first_depth + 1, neighbours)

    def below(module: str, depth: int) -> None:
        if depth > max_depth:
            return
        for other, edge_type in sorted(neighbours(module).items()):
            listed = other in visited
            if not listed and first_depth - 1 + shortest[other] < depth:
                continue
            emit(depth, edge_type, other, listed)
            if not listed:
                visited.add(other)
                below(other, depth + 1)

    below(start, first_depth)


def _walk_deps(
    g: CodeGraph, nid: str, visited: set[str], max_depth: int, depth: int,
    emit: Callable[[int, str, str, bool], None],
) -> None:
    _walk_tree(nid, visited, max_depth, depth, emit, functools.partial(_collect_dep_mods, g))


def _walk_rdeps(
    g: CodeGraph, nid: str, visited: set[str], max_depth: int, depth: int,
    emit: Callable[[int, str, str, bool], None],
) -> None:
    _walk_tree(nid, visited, max_depth, depth, emit, functools.partial(_collect_rdep_mods, g))


# ---------------------------------------------------------------------------
# Layer classifier + boundary rules
# ---------------------------------------------------------------------------

# The keyword table and the matching live in layer_guess.py, which the builder shares: it stores the guess of
# every purpose of a graph next to it (CodeGraph.layer_guess reads that file). The names below stay: the
# matching runs here with this module's _within, so its comparisons can be counted where they are asked for.
_PURPOSE_TO_LAYER: dict[str, str] = _layer_guess.PURPOSE_TO_LAYER


def _within(word: str, keyword: str, threshold: int) -> int:
    """The edit distance of two words, or threshold + 1 when their lengths alone put it above the threshold."""
    if abs(len(word) - len(keyword)) > threshold:
        return threshold + 1
    return _levenshtein(word, keyword)


@functools.lru_cache(maxsize=None)
def _classify_layer_by_purpose(purpose: str) -> str:
    """Classify layer from the module's purpose string using fuzzy matching (layer_guess.classify):
    exact match, then substring, then Levenshtein distance on individual words. What a query runs
    for a purpose the stored guesses of its graph do not hold."""
    return _layer_guess.classify(purpose, _within)


def _stored_layer_guesses(root: str) -> dict[str, str]:
    """purpose -> layer from .wingbox/cache.layer-guess.json of the project; {} when the file is missing,
    unreadable, not such a file or written for another keyword table or matching. Never an error: the
    file is a cache of _classify_layer_by_purpose, and a query without it gives the same answer."""
    if not root:
        return {}
    try:
        with open(os.path.join(root, WINGBOX_DIRECTORY, _layer_guess.FILE_NAME), "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError):   # ValueError: bytes that are not UTF-8
        return {}
    return _layer_guess.parse(text)


# ---------------------------------------------------------------------------
# Commands -- S-expression output (default)
# ---------------------------------------------------------------------------

def _entity_sexp(e: EntityFact, lang: str = "") -> str:
    """Format an entity as an S-expression fact with :lang."""
    parts = [e.kind, _q(e.module), _q(e.name), str(e.line)]
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


def _edge_sexp(e: Edge, lines: bool = False) -> str:
    """Format an edge as an S-expression fact; with `lines`, an imports or refs fact names its source lines."""
    return f"({e.etype} {_q(e.source)} {_q(e.target)}{_edge_marks(e)}{_edge_lines(e) if lines else ''})"


def _edge_lines(e: Edge) -> str:
    """` :line 7` or ` :lines 3 9` as the graph stores them; "" for an edge without stored lines."""
    if not e.lines:
        return ""
    return f" :line {e.lines[0]}" if len(e.lines) == 1 else f" :lines {' '.join(str(line) for line in e.lines)}"


_LINE_MARK = re.compile(r" :lines?(?: \d+)+")
_LINED_FACTS = ("(imports ", "(refs ")
_SNAPSHOT_FACT = "(built-from "


def _fact_without_lines(line: str) -> Optional[str]:
    """A line of the graph file as it read before edges carried lines and the header named the snapshot:
    imports and refs without their line mark; None for the built-from fact."""
    if line.startswith(_LINED_FACTS):
        return _LINE_MARK.sub("", line, count=1)
    return None if line.startswith(_SNAPSHOT_FACT) else line


def _read_edge_lines(tokens: list[str]) -> tuple[int, ...]:
    """The numbers after :line or :lines among the tokens of an edge fact."""
    for index, token in enumerate(tokens):
        if token in (":line", ":lines"):
            found: list[int] = []
            for value in tokens[index + 1:]:
                if not value.isdecimal():
                    break
                found.append(int(value))
            return tuple(found)
    return ()


def cmd_module(g: CodeGraph, module_id: str, lines: bool = False) -> int:
    """Print the module with its entities and edges; returns 1 when the name is not in the graph."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    for nid in matches:
        node = g.nodes[nid]
        fpath = g.module_to_file(nid)
        mod_lang = g.module_lang(nid)
        print(f"(node {_q(nid)} :lang {mod_lang} {_q(node.purpose)} :file {_q(fpath)})")
        for e in sorted(g.entities.get(nid, []), key=lambda x: x.line):
            print(_entity_sexp(e, mod_lang))
        for e in sorted(g.edges_from.get(nid, []), key=lambda x: (x.etype, x.target)):
            print(_edge_sexp(e, lines))
        for e in sorted(g.edges_to.get(nid, []), key=lambda x: (x.etype, x.source)):
            if e.source.split("::")[0] != nid:
                print(_edge_sexp(e, lines))
    return 0


def cmd_deps(g: CodeGraph, module_id: str, max_depth: int) -> int:
    """Print the dependency tree of a module; returns 1 when the name is not in the graph."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    for start in matches:
        visited: set[str] = {start}
        print(f"(deps-root {_q(start)} :lang {g.module_lang(start)})")
        _walk_deps(g, start, visited, max_depth, 1,
                   lambda d, et, tgt, cyc: print(f"(dep {_q(start)} {_q(tgt)} :lang {g.module_lang(tgt)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))
    return 0


def cmd_rdeps(g: CodeGraph, module_id: str, max_depth: int) -> int:
    """Print the tree of the modules that depend on a module; returns 1 when the name is not in the graph."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    for start in matches:
        visited: set[str] = {start}
        print(f"(rdeps-root {_q(start)} :lang {g.module_lang(start)})")
        _walk_rdeps(g, start, visited, max_depth, 1,
                    lambda d, et, src, cyc: print(f"(rdep {_q(start)} {_q(src)} :lang {g.module_lang(src)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))
    return 0


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


_C_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx")
_C_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")


def paired_headers(g: CodeGraph, module: str) -> list[str]:
    """The headers of a C or C++ source file: a header of the same stem that it includes (also from an
    include folder elsewhere), then one of the same stem in its folder. Nothing includes a source file, so
    a change of it reaches other files through what its header declares; [] for any other module."""
    if g.module_lang(module) not in ("cpp", "c") or not module.casefold().endswith(_C_SOURCE_SUFFIXES):
        return []
    folder, _, name = module.rpartition("/")
    stem = os.path.splitext(name)[0]
    found: list[str] = []
    for edge in g.edges_from.get(module, ()):
        target = edge.target.split("::")[0]
        if edge.etype == "imports" and target not in found and target.casefold().endswith(_C_HEADER_SUFFIXES) \
                and os.path.splitext(target.rpartition("/")[2])[0] == stem:
            found.append(target)
    for suffix in _C_HEADER_SUFFIXES:
        candidate = f"{folder}/{stem}{suffix}" if folder else stem + suffix
        if candidate in g.nodes and candidate not in found:
            found.append(candidate)
    return found


def _impact_levels(g: CodeGraph, start: str, max_depth: int) -> list[list[tuple[str, str]]]:
    """Modules that depend on start, grouped by shortest hop distance.

    Each level is a sorted list of (module ID, edge type); a module appears once,
    at the first depth that reaches it. A C or C++ source file starts from its headers
    as well (paired_headers): their includers are at depth 1.
    """
    paired = paired_headers(g, start)
    seen: set[str] = {start, *paired}
    frontier: list[str] = [start, *paired]
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
    basis: str = ""   # declared or inferred; "" for an edge the source text states


_IMPACT_INCLUDE_NAMES = 3   # as many names as an import edge carries


def _impact_rows(g: CodeGraph, start: str, max_depth: int) -> list[list[ImpactRow]]:
    """The levels of _impact_levels, each dependent with the module it is reached through and its edge to it."""
    levels = _impact_levels(g, start, max_depth)
    rows: list[list[ImpactRow]] = []
    paired = paired_headers(g, start)
    previous = [start, *paired]
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
            if not names and edge_type == "imports" and g.module_lang(through) in ("cpp", "c"):
                # An include states no names: the names of the header the file uses come from the use lines.
                used = g.names_used(through, module)
                if used:
                    names = (NameRoute("use", "", used[:_IMPACT_INCLUDE_NAMES], len(used)),)
            # A file that includes the header of the asked source file, not the file itself.
            basis = "paired-header" if not rows and through in paired else _edge_basis(g, module, through, edge_type)
            level_rows.append(ImpactRow(
                module, edge_type, through, len(reached[module]) - 1, names if edge_type == "imports" else (), basis,
            ))
        rows.append(level_rows)
        previous = [module for module, _edge_type in level]
    return rows


def _flags_basis(flags: Sequence[str]) -> str:
    return "declared" if ":declared" in flags else "inferred" if ":inferred" in flags else ""


def _edge_basis(g: CodeGraph, module: str, through: str, edge_type: str) -> str:
    """What the edge of module to through rests on: `declared` (the config file), `inferred` (a marker in a
    generated file, a build file, a launch call, an include found without the compiler's folders), "" when
    the source text states it."""
    for edge in g.edges_to.get(through, ()):
        if edge.etype == edge_type and edge.source.split("::")[0] == module:
            return _flags_basis(edge.flags)
    return ""


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


_RELATION_WORDS = {"imports": "imports it", "generated": "is generated by it or from it", "runs": "runs it"}
ONLY_KINDS = ("tests",)


def impact_reason(g: CodeGraph, row: ImpactRow, depth: int) -> str:
    """Why a file is on the impact list, as one line of text: the relation of its edge and, below depth 1,
    the file it is reached through."""
    if row.names:
        relation = "; ".join(_route_text(route) for route in row.names)
    else:
        relation = _RELATION_WORDS.get(row.edge_type, row.edge_type)
    if row.basis == "paired-header":
        relation = f"includes its header {_impact_file(g, row.through)}"
    elif row.basis:
        relation += f" ({row.basis})"
    if depth == 1:
        return relation
    others = f" (or {row.more} other {'file' if row.more == 1 else 'files'})" if row.more else ""
    return f"through {_impact_file(g, row.through)}{others}: {relation}"


def impact_fact(g: CodeGraph, root_id: str, row: ImpactRow, depth: int) -> str:
    through = f" :through {row.through}" if depth > 1 else ""
    more = f" :more {row.more}" if depth > 1 and row.more else ""
    names = "".join(f" {name_route_sexp(route)}" for route in row.names)
    basis = f" :basis {row.basis}" if row.basis else ""
    return f"(impact {root_id} {row.module} :lang {g.module_lang(row.module)} :via {row.edge_type} :depth {depth}{through}{more}{basis}{names})"


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
    """Labels of a graph without architecture facts (written by hand): one part, the layered style.

    Only in a project without .wingbox/config.yaml. Where a config states the parts and the graph still
    has none, the parts of the config hold no file: the files are in no part and no style is assumed."""
    if g.gate.no_default is None:
        g.gate.no_default = bool(g._root) and os.path.isfile(os.path.join(g._root, ".wingbox", "config.yaml"))
    if g.gate.no_default:
        return
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
        guess = g.layer_guess(purpose) if purpose else "other"
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


# The text for people and the flat facts name at most this many dependent files; --all prints every one.
_ANSWER_FILE_LIMIT = impact_forms.ANSWER_FILE_LIMIT
_READ_FIRST_LIMIT = impact_forms.READ_FIRST_LIMIT
_KEY_ENTITY_REF_LIMIT = 20


def _rerun(command: str) -> str:
    return '"' + command.replace('"', '\\"') + ' --all"'


_FOLDER_MODULE_FILES = ("/index", "/__init__", "/mod")


def _names_file(g: CodeGraph, target: str) -> bool:
    """Whether an argument names a file and not a module pattern: its name has the extension of a programming
    language, parsed or not (`operatives.service` has none), or a file of that path is on disk under the
    graph root or the current folder (a document, a data file)."""
    path = portable.local_path(target)
    name = os.path.basename(path)
    if source_kinds.parsed_language(name) is not None or source_kinds.not_parsed_language(name) is not None:
        return True
    return any(os.path.isfile(os.path.join(base, path)) for base in (g._root, os.getcwd()) if base)


def _root_relative(g: CodeGraph, target: str, from_cwd: bool = False) -> Optional[str]:
    """A path as given, written relative to the graph root with /: an absolute path, a path relative to the
    root or, with from_cwd, a path relative to the current folder. None for a path outside the root."""
    path = portable.local_path(target)
    if from_cwd and not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    if os.path.isabs(path):
        return portable.relative_posix(path, g._root) if g._root else None
    relative = path.replace(os.sep, "/")
    return relative[2:] if relative.startswith("./") else relative


def _module_of_file(g: CodeGraph, relative: str) -> Optional[str]:
    """The module whose module ID, or whose file, is the path given (relative to the graph root).

    A file that shares its name with another one is a module named by its full path (src/tool.py next to
    src/tool.ts), so the path as given is tried first. An index file is the module of its folder
    (src/lib/index.ts -> src/lib). A module found by the path without its extension is taken only when its
    file is that path (app/tool.cs is not the module of app/tool.rs) or is not on disk to compare."""
    if relative in g.nodes:
        return relative
    stem = _target_to_module_pattern(relative, "")
    if stem == relative:
        return None
    for module in (stem, *(stem[: -len(suffix)] for suffix in _FOLDER_MODULE_FILES if stem.endswith(suffix))):
        if module not in g.nodes:
            continue
        file = _impact_file(g, module)
        # The same file also when the path given reaches it through a link.
        if file in (relative, module) or (g._root and file == portable.relative_posix(os.path.join(g._root, relative), g._root)):
            return module
    return None


def _exact_module(g: CodeGraph, target: str) -> Optional[str]:
    """The module an argument names exactly: by its module ID or by the path of its file, absolute or relative
    to the graph root; the path of a file is then read relative to the current folder. None for anything
    else: a file is never answered for another file whose name is like it."""
    relative = _root_relative(g, target)
    module = _module_of_file(g, relative) if relative else None
    if module is None and _names_file(g, target):
        from_cwd = _root_relative(g, target, from_cwd=True)
        if from_cwd and from_cwd != relative:
            module = _module_of_file(g, from_cwd)
    return module


def _impact_target_modules(g: CodeGraph, target: str, pattern: str) -> list[str]:
    """Modules named by an impact target: the module a file path or a module ID names exactly (see
    _exact_module); for an argument that names no file, the modules its pattern matches."""
    exact = _exact_module(g, target)
    if exact is not None:
        return [exact]
    if not target.strip() or _names_file(g, target):
        return []
    return g.resolve_module(pattern)


def _only_rows(g: CodeGraph, levels: list[list[ImpactRow]], only: str) -> list[list[ImpactRow]]:
    """The rows of each depth that `--only` keeps: with "tests" the test files (the layer `test`)."""
    if only != "tests":
        return levels
    return [[row for row in level if module_layer(g, row.module) == "test"] for level in levels]


def _source_file_note(g: CodeGraph, root_id: str, root_file: str, has_rows: bool) -> tuple[str, str]:
    """(fact, text) said with the impact of a C or C++ source file: which header's includers the list
    holds, or, without a header and without rows, that the include graph cannot show its callers."""
    headers = [_impact_file(g, header) for header in paired_headers(g, root_id)]
    if headers:
        return (
            f"(impact-paired-header {_q(root_file)} {' '.join(_q(header) for header in headers)})",
            f"Note: nothing includes a source file itself. The list holds the files that include its header "
            f"{', '.join(headers)}: a change of {root_file} reaches them through what the header declares.",
        )
    if not has_rows and g.module_lang(root_id) in ("cpp", "c") and root_id.casefold().endswith(_C_SOURCE_SUFFIXES):
        return (
            f"(impact-no-includers {_q(root_file)})",
            f"Note: nothing includes {root_file} and it has no header of its name, so the include graph does "
            f"not show who calls what it defines. Ask for a function it defines: wingbox usages {root_file}::<name>.",
        )
    return "", ""


def _affected_build(g: CodeGraph, modules: set[str]) -> Optional[impact_forms.Affected]:
    """The build targets that hold one of the modules among their sources, and the tests that run one of
    those targets or one of the modules; None in a graph without build targets and tests."""
    if not g.build_targets and not g.build_tests:
        return None
    targets = sorted(name for name, sources in g.build_targets.items() if modules.intersection(sources))
    reached = set(targets)
    tests = sorted(name for name, (target, runs) in g.build_tests.items() if target in reached or modules.intersection(runs))
    # The targets whose source list was not all read may hold the file too: they are named, never left out silently.
    return impact_forms.Affected(tuple(targets), tuple(tests), tuple(sorted(g.unread_targets - reached)))


_GENERATION_RELATIONS = frozenset({"generated", "runs", "reads"})


def _dependent_kind(g: CodeGraph, row: ImpactRow) -> str:
    """The kind `impact --summary` counts a dependent under."""
    if row.edge_type in _GENERATION_RELATIONS:
        return "generated"
    if module_layer(g, row.module) == "test":
        return "test"
    return "other" if _collect_rdep_mods(g, row.module) else "entry-point"


def impact_answer(
    g: CodeGraph, target: str, max_depth: int, advice: Optional[Sequence] = None, only: str = "", kinds: bool = False,
) -> impact_forms.ImpactAnswer:
    """What depends on a file or module, complete: every dependent by depth with the reason it is listed, the
    files to read first and the advice that concerns the file; or that the file is not in the graph.

    advice is every advice item of the graph when the caller already has them (several files in one call).
    only "tests" keeps the test files of the list."""
    pattern = _target_to_module_pattern(target, g._root)
    matches = _impact_target_modules(g, target, pattern)
    if not matches:
        outside = outside_graph(g, target)
        if outside is not None and outside.file in g.generated_from_files:
            return _generation_input_answer(g, outside.file, max_depth)
        if outside is not None:
            return impact_forms.ImpactMissing(outside.file, (), outside.text, outside.reason, outside.language)
        suggestions = CodeGraph._suggest_similar(pattern, g.nodes)
        return impact_forms.ImpactMissing(target, tuple(_impact_file(g, module) for module in suggestions), _no_match_fact(pattern, suggestions))
    root_id, others = matches[0], matches[1:]
    root_file = _impact_file(g, root_id)
    whole = _impact_rows(g, root_id, max_depth)
    affected = _affected_build(g, {root_id, *(row.module for level in whole for row in level)}) if only == "tests" else None
    levels = tuple(
        tuple(
            impact_forms.ImpactLine(
                _impact_file(g, row.module),
                row.edge_type,
                _impact_file(g, row.through) if depth > 1 else "",
                row.more if depth > 1 else 0,
                tuple(name_route_sexp(route) for route in row.names),
                impact_reason(g, row, depth),
                row.basis,
                _dependent_kind(g, row) if kinds else "",
            )
            for row in level
        )
        for depth, level in enumerate(_only_rows(g, whole, only), start=1)
    )
    while levels and not levels[-1]:
        levels = levels[:-1]   # the depths below the last kept row
    headers = [_impact_file(g, header) for header in paired_headers(g, root_id)]
    read_first = tuple(dict.fromkeys([root_file, *headers] + [line.file for line in (levels[0] if levels else ())]))
    related = module_advice(g, root_id) if advice is None else [item for item in advice if root_id in item.modules]
    said = tuple(impact_forms.Advice(advice_sexp(item), f"  {item.reason} [{item.style}: {item.rule}]") for item in related)
    if not others:
        note_fact, note_text = _source_file_note(g, root_id, root_file, bool(levels))
        return impact_forms.ImpactFound(root_file, max_depth, levels, read_first, note_fact, note_text, said, only, affected)
    return impact_forms.ImpactFound(
        root_file, max_depth, levels, read_first,
        f"(impact-ambiguous {_q(pattern)} :showing {_q(root_id)} :others {' '.join(map(_q, others))})",
        f"Note: {len(matches)} modules match {pattern}; showing {root_id}. Others: {', '.join(others)}",
        said, only, affected,
    )


def _generation_input_answer(g: CodeGraph, file: str, max_depth: int) -> impact_forms.ImpactFound:
    """The impact of a file that is not in the graph and that generated files are made from (a schema, a
    template): at depth 1 the generated files and the generators that read it, below them what depends on those."""
    first: dict[str, impact_forms.ImpactLine] = {}
    for module, flags in sorted(g.generated_from_files[file]):
        basis = _flags_basis(flags)
        first.setdefault(module, impact_forms.ImpactLine(
            _impact_file(g, module), "generated", "", 0, (), f"is generated from it ({basis})" if basis else "is generated from it", basis))
        for edge in g.edges_from.get(module, ()):
            if edge.etype == "generated" and ":by" in edge.flags:
                generator = edge.target.split("::")[0]
                first.setdefault(generator, impact_forms.ImpactLine(
                    _impact_file(g, generator), "reads", "", 0, (), f"generates {_impact_file(g, module)} from it ({basis})", basis))
    levels: list[list[impact_forms.ImpactLine]] = [list(first.values())]
    seen = set(first)
    frontier = list(first)
    for depth in range(2, max_depth + 1):
        level: list[impact_forms.ImpactLine] = []
        for through in frontier:
            for row in (_impact_rows(g, through, 1) or [[]])[0]:
                if row.module not in seen:
                    seen.add(row.module)
                    level.append(impact_forms.ImpactLine(
                        _impact_file(g, row.module), row.edge_type, _impact_file(g, through), 0,
                        tuple(name_route_sexp(route) for route in row.names), impact_reason(g, row, depth), row.basis))
        if not level:
            break
        levels.append(level)
        frontier = [module for module in seen if _impact_file(g, module) in {line.file for line in level}]
    read_first = tuple(dict.fromkeys([file] + [line.file for line in levels[0]]))
    return impact_forms.ImpactFound(file, max_depth, tuple(tuple(level) for level in levels), read_first)


def graph_header(g: CodeGraph, stale: Optional[int]) -> impact_forms.GraphHeader:
    """When the graph file was written, how many files it holds and how many changed files it is known to miss
    (None: nothing checked it)."""
    try:
        written = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(g._map_path)))
    except OSError:
        written = "unknown"
    return impact_forms.GraphHeader(written, len(g.nodes), stale, source_kinds.read_not_parsed(_state_text(g, "graph-unparsed.sexp")))


def _print_impact_flat(g: CodeGraph, target: str, max_depth: int, show_all: bool, only: str = "") -> None:
    """The flat facts of one file as versions up to 0.13 printed them with --sexp: `impact-root`, one
    `(impact <root> <dependent> ...)` line per dependent with module IDs, `impact-summary`, `read-first`.
    The counts are complete; the lists stop at the file limit unless show_all."""
    pattern = _target_to_module_pattern(target, g._root)
    matches = _impact_target_modules(g, target, pattern)
    if not matches:
        _print_no_match(g, pattern, flat=True)
        return
    root_id = matches[0]
    others = matches[1:]
    levels = _only_rows(g, _impact_rows(g, root_id, max_depth), only)
    direct = len(levels[0]) if levels else 0
    total = sum(len(level) for level in levels)
    root_file = _impact_file(g, root_id)
    read_first = list(
        dict.fromkeys(
            [root_file] + [_impact_file(g, header) for header in paired_headers(g, root_id)]
            + [_impact_file(g, row.module) for row in (levels[0] if levels else [])]
        )
    )
    note_fact, _note_text = _source_file_note(g, root_id, root_file, total > 0)
    if note_fact:
        print(note_fact)
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
    if others:
        print(f"(impact-ambiguous {_q013(pattern)} :showing {root_id} :others {' '.join(others)})")
    print(f"(impact-root {root_id} :lang {g.module_lang(root_id)} :file {_q013(root_file)})")
    for depth, level in enumerate(shown_levels, start=1):
        for row in level:
            print(impact_fact(g, root_id, row, depth))
    if hidden:
        print(f"(impact-more {root_id} :hidden {hidden} :rerun {_rerun(f'impact {target} {max_depth}')})")
    print(f"(impact-summary {root_id} :direct {direct} :total {total} :depth {max_depth})")
    for path in read_first:
        print(f"(read-first {_q013(path)})")
    if read_first_count > len(read_first):
        print(f"(read-first-more :hidden {read_first_count - len(read_first)})")
    for item in module_advice(g, root_id):
        print(advice_sexp(item))


def impact_document(
    g: CodeGraph, targets: Sequence[str], max_depth: int = 2, stale: Optional[int] = None, only: str = "",
    changed: Optional[Sequence[impact_forms.Changed]] = None,
) -> tuple[int, dict]:
    """(exit status, the JSON document) of `impact` for the files asked."""
    answers = _impact_answers(g, targets, max_depth, only)
    return _impact_status(answers), impact_forms.impact_json(answers, graph_header(g, stale), changed)


def _impact_answers(
    g: CodeGraph, targets: Sequence[str], max_depth: int, only: str = "", kinds: bool = False,
) -> list[impact_forms.ImpactAnswer]:
    # The advice of the graph is worked out once for a call with several files.
    advice = advice_items(g)[0] if len(targets) > 1 else None
    return [impact_answer(g, target, max_depth, advice, only, kinds) for target in targets]


def cmd_impact_summary(
    g: CodeGraph, targets: Sequence[str], max_depth: int = 2, form: str = "sexp", stale: Optional[int] = None,
    only: str = "", changed: Optional[Sequence[impact_forms.Changed]] = None,
) -> tuple[int, Optional[dict]]:
    """`impact --summary`: per file the counts of its dependents and the few to look at first, in place of
    the complete list. Prints the compact form or the text; returns (exit status, the JSON document or None)."""
    answers = _impact_answers(g, targets, max_depth, only, kinds=True)
    header = graph_header(g, stale)
    if form == "json":
        return _impact_status(answers), impact_forms.summary_json(answers, header, changed)
    if form == "human":
        sys.stdout.write(impact_forms.summary_text(answers))
        for item in changed or ():
            print(f"  changed {item.symbol} (line {item.line}): used by {len(item.used_by)} "
                  f"{'test files' if only else 'files'}{impact_forms.other_users_text(item)}"
                  + (f"; tests to run: {len(item.affected.tests)}" if item.affected is not None else ""))
    else:
        sys.stdout.write(impact_forms.summary_sexp(answers, header if len(answers) > 1 or header.not_parsed else None))
        for item in changed or ():
            print(impact_forms.changed_summary_fact(item))
    return _impact_status(answers), None


def impact_options(args: Sequence[str]) -> Union[str, tuple[list[str], str, Optional[str], bool]]:
    """(the other arguments, the value of --only, the ref of --diff or None, whether --summary was given) of
    an `impact` call; a string when an option is written wrongly. `--only tests`; `--diff [ref]`, the ref
    HEAD when the word after it is another option, a whole number (the depth) or missing."""
    rest: list[str] = []
    only = ""
    diff: Optional[str] = None
    summary = False
    index = 0
    while index < len(args):
        argument = args[index]
        index += 1
        if argument == "--only":
            value = args[index] if index < len(args) else ""
            if value not in ONLY_KINDS:
                return f"--only takes {' or '.join(ONLY_KINDS)}"
            only = value
            index += 1
        elif argument == "--diff":
            value = args[index] if index < len(args) else ""
            if value and not value.startswith("--") and not _WHOLE_NUMBER.match(value):
                diff = value
                index += 1
            else:
                diff = "HEAD"
        elif argument == "--summary":
            summary = True
        else:
            rest.append(argument)
    return rest, only, diff, summary


def diff_changes(
    g: CodeGraph, ref: str, only: str = "",
) -> Union[str, tuple[list[str], list[impact_forms.Changed], list[impact_forms.ChangedFile]]]:
    """(the files a diff against ref changes that are files now, the functions it changes, the paths it
    removes: a deleted file, the path a renamed file had) for `impact --diff`; an error fact when ref is no
    ref or Git cannot give the diff. The files that use a changed function are read from the use lines of
    the graph, also those that use it through the declaration in its header."""
    diff = _diff_files(g, ref)
    if isinstance(diff, str):
        return diff

    def on_disk(path: str) -> bool:
        return os.path.isfile(os.path.join(g._root or "", os.path.normpath(path)))

    # A path Git reports as deleted that is still a file (taken out of the index only) is answered as a file.
    current = [item for item in diff if item.status != "deleted" or on_disk(item.path)]
    files = sorted(item.path for item in current)
    gone = sorted(
        [impact_forms.ChangedFile(item.path, "deleted") for item in diff if item.status == "deleted" and not on_disk(item.path)]
        + [impact_forms.ChangedFile(item.old_path, "renamed", item.path)
           for item in diff if item.status == "renamed" and not on_disk(item.old_path)],
        key=lambda item: item.file,
    )
    ranges = {item.path: item.ranges for item in current}
    changed: list[impact_forms.Changed] = []
    for qualified, entity, _diff_file in sorted(_diff_functions(g, ranges), key=lambda item: (item[2], item[1].line)):
        module, _, name = qualified.partition("::")
        users: set[str] = set()
        for owner in (module, *paired_headers(g, module)):
            users |= g.use_pairs.get(f"{owner}::{name}", set())
        users -= {module, *paired_headers(g, module)}
        # The targets and tests of the file and of the files that use the function: narrower than those of
        # every dependent of the file.
        affected = _affected_build(g, users | {module}) if only == "tests" else None
        other_users = 0
        if only == "tests":
            tests = {user for user in users if module_layer(g, user) == "test"}
            other_users = len(users) - len(tests)
            users = tests
        changed.append(impact_forms.Changed(
            f"{_impact_file(g, module)}::{name}", entity.line, tuple(sorted(_impact_file(g, user) for user in users)), affected,
            other_users,
        ))
    return files, changed, gone


def _impact_status(answers: Sequence[impact_forms.ImpactAnswer]) -> int:
    return 1 if any(isinstance(answer, impact_forms.ImpactMissing) for answer in answers) else 0


def cmd_impact(
    g: CodeGraph, targets: Sequence[str], max_depth: int = 2, form: str = "sexp", show_all: bool = False,
    stale: Optional[int] = None, only: str = "", changed: Optional[Sequence[impact_forms.Changed]] = None,
) -> int:
    """Print what depends on each file or module: the blast radius of changing it, one answer per target.

    form is "sexp" (the compact form agents read, complete; several files open with the graph fact), "json"
    (the same content), "human" (text, whose lists stop at the file limit unless show_all) or "flat" (the
    facts of versions up to 0.13, with the same limit). Returns 1 when a target is not in the graph, but for
    "flat", which answers 0 as those versions did.
    """
    if isinstance(targets, str):
        targets = [targets]
    if form == "flat":
        for target in targets:
            _print_impact_flat(g, target, max_depth, show_all, only)
        for item in changed or ():
            print(impact_forms.changed_fact(item))
        return 0
    if form == "json":
        status, document = impact_document(g, targets, max_depth, stale)
        print(fact_json.render(document))
        return status
    answers = _impact_answers(g, targets, max_depth, only)
    if form == "human":
        sys.stdout.write(impact_forms.impact_text(answers, show_all))
        if changed:
            width = max(len(item.symbol) for item in changed)
            print("\nChanged functions and the files that use them")
            for item in changed:
                used = ", ".join(item.used_by) if item.used_by else "no other file"
                if impact_forms.other_users_text(item):
                    used = "no test file" + impact_forms.other_users_text(item)
                if item.affected is not None:
                    used += f"; tests to run: {', '.join(item.affected.tests) if item.affected.tests else 'none'}"
                print(f"  {item.symbol:<{width}}   line {item.line}: {used}")
    else:
        # The graph fact stands before the answers of several files, and before one answer when files of
        # the repository are in languages without a parser: its list of dependents cannot name those.
        header = graph_header(g, stale)
        sys.stdout.write(impact_forms.impact_sexp(answers, header if len(answers) > 1 or header.not_parsed else None))
        for item in changed or ():
            print(impact_forms.changed_fact(item))
    return _impact_status(answers)


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
        print(f"(entity-def {defn.kind} {_q(defn.module)} {_q(defn.name)} {defn.line} :lang {g.module_lang(defn.module)} :file {_q(g.module_to_file(defn.module))})")
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
            print(f"(use {_q(source)} {line} {column} {_q(target)} :file {_q(file_path)})")


_KIND_WORDS = {"cls": "class", "fn": "function", "ifc": "interface", "typ": "type", "enm": "enum", "mod": "module",
               "var": "variable"}
_USAGES_LIMIT = usages_forms.USAGES_LIMIT


@dataclass(frozen=True)
class Usage:
    """One place that uses the symbol; line 0 when its language has no use positions, or when the positions
    of its file are not read yet (pending)."""

    file: str
    module: str
    line: int
    column: int
    kind: str
    pending: bool = False


def _usages_definitions(g: CodeGraph, target: str) -> tuple[str, list[EntityFact]]:
    """The symbol asked (Name, Class.method) and its definitions: of the named file, or of every file."""
    file_part, separator, symbol = target.rpartition("::")
    owner = symbol.split(".", 1)[0]
    if not separator:
        return symbol, g.definitions_named(owner)
    # A file path names one module; a module pattern may match several, and the definitions of all of them
    # are found: the caller lists them as candidates instead of answering for the first.
    modules = _usages_modules(g, file_part)
    found = [entity for module in modules for entity in g.definitions_named(owner) if entity.module == module]
    if found or not modules:
        return symbol, found
    # The file only re-exports the name (a package __init__.py, a barrel): follow the re-export.
    return symbol, [
        entity
        for target, sites in g.uses_named(owner).items()
        if target.endswith(f"::{owner}")
        and any(source in modules and kind == "re-export" for source, _line, _column, kind in sites)
        for entity in g.definitions_named(owner)
        if entity.module == target.rsplit("::", 1)[0]
    ]


def _usages_modules(g: CodeGraph, file_part: str) -> list[str]:
    """The modules the part before `::` of a usages target names."""
    return _impact_target_modules(g, file_part, _target_to_module_pattern(file_part, g._root))


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
        # A call on an object of unknown type can be a call of this method only in a file of its language
        # (C and C++ count as one).
        family = {"c": "cpp"}.get(g.module_lang(definition.module), g.module_lang(definition.module))
        for source, line, column in g.unresolved_calls_of(method):
            if {"c": "cpp"}.get(g.module_lang(source), g.module_lang(source)) != family:
                continue
            found.append(Usage(_impact_file(g, source), source, line, column, "unresolved-call"))
            unresolved += 1
    with_positions = {usage.module for usage in found}
    # A file that references the symbol in a language without a use index: no positions.
    for edge in g.edges_by_entity_name.get(symbol, []):
        source = edge.source.split("::")[0]
        if edge.etype == "refs" and edge.target == target and source not in with_positions:
            with_positions.add(source)
            found.append(Usage(_impact_file(g, source), source, 0, 0, "reference"))
    if g._positions_pending:
        # Positions were read only in the files that hold the name as text: a file the use lines name
        # besides them (it uses the symbol under another name) is listed without a position, not left out,
        # and the answer says that the positions of such a file are still to be read.
        users = g.use_pairs.get(target, set())
        for source in sorted(users - with_positions):
            found.append(Usage(_impact_file(g, source), source, 0, 0, "reference"))
        found = [replace(usage, pending=True) if not usage.line and usage.module in users else usage for usage in found]
    return sorted(found, key=lambda usage: (usage.file, usage.line, usage.column, usage.kind)), unresolved


def _one_function(g: CodeGraph, definitions: list[EntityFact]) -> Optional[EntityFact]:
    """The entity to answer for when several definitions of a name are one C or C++ function, else None.

    One function: its prototypes in headers together with the definitions in source files that include one
    of those headers (directly or through one other header); or several entities of one file (a prototype
    and the definition below it). Functions of one name in source files that share no declaring header
    are different functions (static helpers of two files). The answer is given at the definition in a
    source file when there is one, else at the first declaration."""
    if len(definitions) < 2 or any(d.kind != "fn" or g.module_lang(d.module) not in ("cpp", "c") for d in definitions):
        return None
    headers = sorted({d.module for d in definitions if d.module.casefold().endswith(_C_HEADER_SUFFIXES)})
    sources = sorted({d.module for d in definitions if not d.module.casefold().endswith(_C_HEADER_SUFFIXES)})
    if not headers and len(sources) != 1:
        return None

    for source in sources if headers else ():
        direct = g.includes_of(source)
        reached = direct | {target for through in direct for target in g.includes_of(through)}
        if not reached.intersection(headers):
            return None
    chosen = sources[0] if sources else headers[0]
    return max((d for d in definitions if d.module == chosen), key=lambda d: d.line)


def _function_parts(g: CodeGraph, definition: EntityFact, symbol: str) -> list[EntityFact]:
    """The prototypes and definitions that are one C or C++ function with definition, when the question
    names one of its files (`src/timer.c::uv_timer_start`, or the header): the prototypes in headers and
    the definitions in source files that include one of them. A function of the same name in a source
    file that includes none of those headers is another function and is left out; [definition] when the
    file asked is such a file, or the symbol is no C or C++ function."""
    if "." in symbol or definition.kind != "fn" or g.module_lang(definition.module) not in ("cpp", "c"):
        return [definition]
    named = [d for d in g.definitions_named(symbol) if d.kind == "fn" and g.module_lang(d.module) in ("cpp", "c")]
    headers = sorted({d.module for d in named if d.module.casefold().endswith(_C_HEADER_SUFFIXES)})
    if not headers:
        return [definition]
    related = set(headers)
    for source in {d.module for d in named} - related:
        direct = g.includes_of(source)
        if (direct | {target for through in direct for target in g.includes_of(through)}).intersection(headers):
            related.add(source)
    if definition.module not in related:
        return [definition]
    return [d for d in named if d.module in related]


def _usages_together(g: CodeGraph, definitions: list[EntityFact], symbol: str) -> tuple[list[Usage], int]:
    """The uses of the definitions of one function together, each place once."""
    found: dict[tuple[str, int, int, str], Usage] = {}
    unresolved = 0
    for module in dict.fromkeys(d.module for d in definitions):
        usages, missing = _usages_of(g, next(d for d in definitions if d.module == module), symbol)
        unresolved = max(unresolved, missing)
        for usage in usages:
            found.setdefault((usage.file, usage.line, usage.column, usage.kind), usage)
    return [found[key] for key in sorted(found)], unresolved


def _candidate_rows(g: CodeGraph, definitions: list[EntityFact]) -> list[tuple[str, EntityFact]]:
    return sorted(((_impact_file(g, d.module), d) for d in definitions), key=lambda row: (row[0], row[1].line, row[1].kind))


def usages_answer(g: CodeGraph, target: str) -> usages_forms.UsagesAnswer:
    """Every place that uses one symbol, complete; the candidates when a bare name has several definitions.

    target is Name, <file or module>::Name, or either with Class.method.
    """
    symbol, definitions = _usages_definitions(g, target)
    # Two entities of one name in one file are one thing to ask about (a prototype and its definition, two
    # local classes of two functions): the later one stands for it.
    definitions = list({(d.module, d.name, d.kind): d for d in definitions}.values())
    if "." in symbol and any(d.kind != "fn" for d in definitions):
        # `Class.method`: the owner is the class, not a function of the same name (its constructor), and of
        # two types of that name (an alias in one preprocessor branch, the class in the other) the one
        # that declares the method.
        definitions = [d for d in definitions if d.kind != "fn"]
        declaring = [d for d in definitions if symbol.split(".", 1)[1] in d.methods]
        definitions = declaring or definitions
    # A bare name that C or C++ classes declare as a method: each class is a candidate, `Class.name`.
    classes = g.classes_with_method(symbol) if "::" not in target and "." not in symbol else []
    if not definitions and len(classes) == 1:
        return usages_answer(g, f"{_impact_file(g, classes[0].module)}::{classes[0].name}.{symbol}")
    if not definitions and not classes:
        suggestions = CodeGraph._suggest_similar(target, g.nodes)
        return usages_forms.UsagesMissing(target, _no_match_fact(target, suggestions), tuple(suggestions))
    one = _one_function(g, definitions)
    if classes or (len(definitions) > 1 and one is None):
        rows = _candidate_rows(g, definitions) if one is None else [(_impact_file(g, one.module), one)]
        return usages_forms.UsagesAmbiguous(symbol, tuple(
            usages_forms.Candidate(file_path, d.kind, _KIND_WORDS.get(d.kind, d.kind), d.line) for file_path, d in rows
        ) + tuple(
            usages_forms.Candidate(_impact_file(g, c.module), "method", f"method of {c.name}", c.line, f"{c.name}.{symbol}")
            for c in classes
        ))
    definition = one or definitions[0]
    together = definitions if one is not None else _function_parts(g, definition, symbol)
    usages, unresolved = _usages_together(g, together, symbol) if len(together) > 1 else _usages_of(g, definition, symbol)
    method = "." in symbol
    return usages_forms.UsagesFound(
        symbol,
        "method" if method else definition.kind,
        f"method of {symbol.split('.', 1)[0]}" if method else _KIND_WORDS.get(definition.kind, definition.kind),
        _impact_file(g, definition.module),
        definition.line,
        tuple(usages_forms.Site(usage.file, usage.line, usage.column, usage.kind) for usage in usages),
        unresolved,
        g._uses_gap or ("positions-pending" if any(usage.pending for usage in usages) else ""),
    )


def _print_usages_flat(g: CodeGraph, target: str, show_all: bool) -> None:
    """The flat facts of one symbol as versions up to 0.13 printed them with --sexp: `usages-target`, one
    `usage` (or `usage-file`) line per use with the module ID, `usages-summary`; at most 200 uses unless
    show_all."""
    symbol, definitions = _usages_definitions(g, target)
    if not definitions:
        _print_no_match(g, target)
        return
    one = _one_function(g, definitions)
    if len(definitions) > 1 and one is None:
        rows = _candidate_rows(g, definitions)
        print(f"(usages-ambiguous {symbol} :definitions {len(rows)})")
        for file_path, d in rows:
            print(f"(usages-candidate {d.module}::{symbol} :kind {d.kind} :file {_q013(file_path)} :line {d.line})")
        return
    definition = one or definitions[0]
    usages, unresolved = _usages_together(g, definitions, symbol) if one is not None else _usages_of(g, definition, symbol)
    target_id = f"{definition.module}::{symbol}"
    definition_file = _impact_file(g, definition.module)
    shown = usages if show_all else usages[:_USAGES_LIMIT]
    hidden = len(usages) - len(shown)
    resolved_count = len(usages) - unresolved
    files = len({usage.file for usage in usages if usage.kind != "unresolved-call"})
    print(f"(usages-target {target_id} :kind {definition.kind} :file {_q013(definition_file)} :line {definition.line})")
    for usage in shown:
        if usage.line:
            print(f"(usage {usage.module} {usage.line} {usage.column} :kind {usage.kind} :file {_q013(usage.file)})")
        else:
            print(f"(usage-file {usage.module} :kind {usage.kind} :file {_q013(usage.file)})")
    if hidden:
        print(f"(usages-more {target_id} :hidden {hidden} :rerun {_rerun(f'usages {target}')})")
    print(f"(usages-summary {target_id} :uses {resolved_count} :files {files} :unresolved {unresolved})")


def _usages_status(g: CodeGraph, target: str, answer: usages_forms.UsagesAnswer) -> int:
    """1 when the file a usages target names (`<file>::Name`) is not in the graph, else 0: an unknown bare
    name is an answer, a file that is not there is not."""
    file_part, separator, _symbol = target.rpartition("::")
    missing = isinstance(answer, usages_forms.UsagesMissing) and bool(separator) and not _usages_modules(g, file_part)
    return 1 if missing else 0


def cmd_usages(g: CodeGraph, target: str, form: str = "sexp", show_all: bool = False) -> int:
    """Print every place that uses one symbol, by file, with line:column and the kind of use.

    form is "sexp" (the compact form agents read, complete), "json" (the same content), "human" (text, at
    most 200 uses unless show_all) or "flat" (the facts of versions up to 0.13, with the same limit). A bare
    name defined in several files lists the candidates instead of mixing them. Returns 1 when the file the
    target names is not in the graph, but for "flat", which answers 0 as those versions did.
    """
    if form == "flat":
        _print_usages_flat(g, target, show_all)
        return 0
    answer = usages_answer(g, target)
    if form == "json":
        print(fact_json.render(usages_forms.usages_json(answer)))
    elif form == "human":
        sys.stdout.write(usages_forms.usages_text(answer, show_all))
    else:
        sys.stdout.write(usages_forms.usages_sexp(answer))
    return _usages_status(g, target, answer)


def cmd_flow(g: CodeGraph, module_id: str, entity_name: str) -> int:
    """Print the flow from an entity of a module; returns 1 when the module is not in the graph."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    start_mod = matches[0]
    full_id = f"{start_mod}::{entity_name}"

    visited: set[str] = set()
    _trace_flow(g, full_id, visited, 0)
    return 0


def _trace_flow(
    g: CodeGraph, entity_id: str, visited: set[str], depth: int,
) -> None:
    if entity_id in visited or depth > 5:
        return
    visited.add(entity_id)

    # Entity-level edges via index
    for edge in g.edges_from_entity.get(entity_id, []):
        tgt_lang = g.module_lang(edge.target.split("::")[0])
        print(f"(flow {_q(entity_id)} {_q(edge.target)} :lang {tgt_lang} :via {edge.etype} :depth {depth})")
        _trace_flow(g, edge.target, visited, depth + 1)

    # Module-level edges for cross-module flow
    mod = entity_id.split("::")[0] if "::" in entity_id else entity_id
    if "::" in entity_id:
        for edge in g.edges_from.get(mod, []):
            if "::" in edge.source:
                continue
            tgt_mod = edge.target.split("::")[0]
            if tgt_mod not in visited:
                print(f"(flow {_q(entity_id)} {_q(edge.target)} :lang {g.module_lang(tgt_mod)} :via {edge.etype} :depth {depth} :module-level)")
                _trace_flow(g, edge.target, visited, depth + 1)


def cmd_boundary(g: CodeGraph, module_id: str, files_only: bool = False) -> int:
    """Output the minimal set of files for a change boundary; returns 1 when the name is not in the graph.

    When files_only is True, emit one bare file path per line instead of sexp.
    """
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    nid = matches[0]
    node = g.nodes.get(nid)
    if not node:
        print(f"(error no-node {_q(nid)})")
        return 1

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
        return 0

    lang = g.module_lang(nid)
    print(f"(boundary {_q(nid)} :lang {lang} :file {_q(target_file)} :purpose {_q(node.purpose)})")
    for e in sorted(ents, key=lambda x: x.line):
        print(_entity_sexp(e, lang))
    for mod, etype in sorted(import_mods.items()):
        print(f"(boundary-dep {_q(nid)} {_q(mod)} :lang {g.module_lang(mod)} :file {_q(g.module_to_file(mod))} :relation {etype})")
    for mod, etype in sorted(dependent_mods.items()):
        print(f"(boundary-rdep {_q(nid)} {_q(mod)} :lang {g.module_lang(mod)} :file {_q(g.module_to_file(mod))} :relation {etype})")
    print(f"(boundary-summary {_q(nid)} :total {1 + len(import_mods) + len(dependent_mods)} :deps {len(import_mods)} :rdeps {len(dependent_mods)})")
    return 0


def cmd_analyze(g: CodeGraph, module_id: str, show_all: bool = False) -> int:
    """Full architectural analysis of a module; returns 1 when the name is not in the graph."""
    matches = g.resolve_module(module_id)
    if not matches:
        _print_no_match(g, module_id)
        return 1

    nid = matches[0]
    node = g.nodes.get(nid)
    if not node:
        print(f"(error no-node {_q(nid)})")
        return 1

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
    return 0


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
    shown = _q(nid)   # the module as one atom of a fact line
    # Module header
    lang = g.module_lang(nid)
    print(f"(analyze {shown} :lang {lang} {_q(node.purpose)} :file {_q(target_file)})")

    # Entities
    for e in sorted(ents, key=lambda x: x.line):
        print(_entity_sexp(e, lang))

    # Imports
    for t in sorted(import_targets):
        print(f"(analyze-import {shown} {_q(t)} :lang {g.module_lang(t)} :file {_q(_impact_file(g, t))})")

    # Data flows
    # Data flows: the marks are those of the stored fact, one line per different set of marks
    for t, marks in sorted(data_flow_targets):
        print(f"(analyze-dataflow {shown} {_q(t)} :lang {g.module_lang(t)}{marks})")

    # Entity refs
    for src, tgt in sorted(ref_targets):
        print(f"(refs {_q(src)} {_q(tgt)})")

    # Call edges
    for src, tgt in sorted(call_targets):
        print(f"(calls {_q(src)} {_q(tgt)})")

    # Dependents
    all_dependents = importers | referencers
    shown_dependents = sorted(all_dependents) if show_all else sorted(all_dependents)[:_ANSWER_FILE_LIMIT]
    for src in shown_dependents:
        kinds = []
        if src in importers:
            kinds.append("imports")
        if src in referencers:
            kinds.append("refs")
        print(f"(analyze-dependent {shown} {_q(src)} :lang {g.module_lang(src)} :via {' '.join(kinds)} :file {_q(_impact_file(g, src))})")
    if len(all_dependents) > len(shown_dependents):
        print(f"(analyze-more {shown} :dependents {len(all_dependents) - len(shown_dependents)} :rerun {rerun})")

    # Dependency tree (depth 2)
    visited: set[str] = {nid}
    _walk_deps(g, nid, visited, 2, 1,
               lambda d, et, tgt, cyc: print(f"(dep {shown} {_q(tgt)} :lang {g.module_lang(tgt)} :via {et} :depth {d}{' :cycle' if cyc else ''})"))

    # Key entities: the count is complete, the modules are a sample unless show_all
    for et, ref_count, ref_mods in key_entity_usage:
        print(f"(key-entity {shown} {_q(et.name)} :lang {lang} :kind {et.kind} :line {et.line} :refs {ref_count} :modules {len(ref_mods)})")
        shown_refs = sorted(ref_mods) if show_all else sorted(ref_mods)[:_KEY_ENTITY_REF_LIMIT]
        for rm in shown_refs:
            print(f"(key-entity-ref {_q(et.name)} {_q(rm)} :lang {g.module_lang(rm)})")
        if len(ref_mods) > len(shown_refs):
            print(f"(key-entity-more {shown} {_q(et.name)} :modules {len(ref_mods) - len(shown_refs)} :rerun {rerun})")

    # Boundary files
    for f in boundary_files:
        print(f"(boundary-file {shown} {_q(f)})")

    # Architecture layers
    for t in sorted(import_targets):
        label = module_label(g, t)
        print(f"(layer {shown} {_q(t.split('/')[-1])} :layer {label.layer} :style {label.style} :rule {_q(label.rule)})")

    for item in module_advice(g, nid):
        print(advice_sexp(item))

    # Summary
    print(f"(analyze-summary {shown} :entities {len(ents)} :imports {len(import_targets)} :dataflows {len({t for t, _marks in data_flow_targets})} :refs {len(ref_targets)} :dependents {len(all_dependents)} :boundary-files {len(boundary_files)})")


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
        for stored in f:
            line = _fact_without_lines(stored)
            if line is None or not regex.search(line):
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


def cmd_search(_g: CodeGraph, pattern: str, sexp_path: str, lines: bool = False) -> None:
    """Search module IDs first, then fall back to raw fact grep.

    The facts print as they did before edges carried lines; with `lines` they print as stored: imports and
    refs with :line or :lines, and the built-from fact of the header."""
    parsed_pattern = parse_search_pattern(pattern)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(pattern, parsed_pattern.error)
        return
    module_matches = _g.search_modules(parsed_pattern.value)
    if module_matches:
        for nid in module_matches:
            print(f"(result {_q(nid)})")
        print(f"(result-count {len(module_matches)})")
        return

    count = 0
    try:
        with open(sexp_path, "r", encoding="utf-8") as code_graph:
            for stored in code_graph:
                line = stored if lines else _fact_without_lines(stored)
                if line is not None and search_pattern_matches(parsed_pattern.value, line):
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


@dataclass
class GateFacts:
    """What `boundaries` reads of a graph besides the labels and the rules, and what it works out once."""

    stale_files: Optional[int] = None   # changed files the graph is known to miss; None when nobody checked
    rule_digests: dict[str, str] = field(default_factory=dict)   # arch-rule-files: what the build read of each rule file
    not_parsed: dict[str, dict[str, int]] = field(default_factory=dict)   # part -> language no parser reads -> source files
    capability_unread: dict[str, dict[str, int]] = field(default_factory=dict)   # part -> language the detector skips -> files
    # (source, target) -> (baseline file, part, terms) of each entry; the files that accept the pair today;
    # (part, source, target) -> the rules the entries of .wingbox/baseline.sexp state for it.
    known_entries: dict[tuple[str, str], list[tuple[str, str, _baseline.Acceptance]]] = field(default_factory=dict)
    known_files: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    known_rules: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)
    capability_entries: dict[tuple[str, str], list[tuple[str, _baseline.Acceptance]]] = field(default_factory=dict)
    judged: Optional[dict[str, ModuleLabel]] = None   # _judged_names, once per graph
    no_default: Optional[bool] = None   # the project has a config: a graph without parts gets no default style


def _settle_entries(
    key: tuple[str, str], entries: Sequence[tuple[str, _baseline.Acceptance]], today: str,
    known: dict[tuple[str, str], str], stated: dict[tuple[str, str], _baseline.Acceptance],
    expired: dict[tuple[str, str], _baseline.Acceptance],
) -> tuple[str, ...]:
    """Set what the baseline entries of one finding come to, and return the files that accept it.

    A baseline file accepts the finding when none of its entries for it is past its end date: another entry
    of the same file (written for another part, or without terms) does not undo an end date. The project's
    own baseline and .wingbox/baseline.sexp are two files: each accepts on its own.
    """
    by_file: dict[str, list[_baseline.Acceptance]] = {}
    for file, terms in entries:
        by_file.setdefault(file, []).append(terms)
    live = sorted(file for file, items in by_file.items() if not any(terms.expired(today) for terms in items))
    for table in (known, stated, expired):
        table.pop(key, None)
    if live:
        known[key] = live[-1]
        with_terms = [terms for terms in by_file[live[-1]] if terms.stated()]
        if with_terms:
            stated[key] = with_terms[-1]
    else:
        expired[key] = next(terms for items in by_file.values() for terms in items if terms.expired(today))
    return tuple(live)


def _accepting_file(g: CodeGraph, pair: tuple[str, str], rule: str) -> str:
    """The baseline file that accepts a break of `rule` between two modules, or "".

    An entry of .wingbox/baseline.sexp that states a rule accepts a break of that rule only: the same two
    files breaking another rule (their layers changed) are a new finding. An entry that states no rule,
    and the project's own baseline, accept the pair whatever the rule.
    """
    file = g.known.get(pair, "")
    for candidate in reversed(g.gate.known_files.get(pair, ())):
        if candidate != _baseline.BASELINE_FILE:
            return candidate
        parts = {part for entry_file, part, _terms in g.gate.known_entries[pair] if entry_file == candidate}
        stated = [g.gate.known_rules.get((part, *pair)) for part in parts]
        if any(rules is None or rule in rules for rules in stated):
            return candidate
        file = ""
    return file


def _label_beside(g: CodeGraph, module_id: str, label: ModuleLabel, by_folder: dict[str, list[str]]) -> Optional[ModuleLabel]:
    """The label of a test file judged like the files of its folder: the layer most files of a layer have
    in its folder, else in the nearest folder above it; None when no folder above it holds such a file."""
    folder = _label_path(g, module_id)
    while "/" in folder:
        folder = folder.rpartition("/")[0]
        layers = collections.Counter(
            beside.layer for beside in (module_label(g, other) for other in by_folder.get(folder, ()))
            if beside.part == label.part and beside.layer not in ("other", "test")
            and beside.match not in ("role", "package", "none")
        )
        if layers:
            layer = min(layers, key=lambda name: (-layers[name], name))
            return replace(label, layer=layer, rule=f"test file that a file of a layer imports: judged as {folder}/ ({layer})")
    return None


def _judged_names(g: CodeGraph) -> dict[str, ModuleLabel]:
    """The files that a name exempts from the rules and that a file of a layer imports, each with the
    label it is judged by.

    A composition root (main.ts, container.ts, ...) wires the layers and a test may import anything: no
    rule is stated for them. A file of a layer that imports one of them would reach every layer through
    it, so such a file is judged like any file of its folder instead: a composition root by the layer of
    its own label, a test by the layer of the files beside it. A file that only composition roots and
    tests (and files in no layer) import keeps the exemption. Worked out once per graph, to a fixed
    point: a file that a judged file imports is judged too.
    """
    if g.gate.judged is not None:
        return g.gate.judged
    labels = {module: module_label(g, module) for module in g.nodes}
    exempt = {module: label for module, label in labels.items() if label.root or label.layer == "test"}
    by_folder: dict[str, list[str]] = {}   # folder -> its modules, filled when the first test is judged
    importers = {module: [other for other in _collect_rdep_mods(g, module) if other in labels] for module in exempt}
    judged: dict[str, ModuleLabel] = {}
    unplaced: set[str] = set()   # tests with no file of a layer beside them: nothing to judge them by
    changed = True
    while changed:
        changed = False
        for module, label in exempt.items():
            if module in judged or module in unplaced:
                continue
            if not any(other in judged or (other not in exempt and _layer_is_certain(g, other))
                       for other in importers[module]):
                continue
            if label.layer != "test":
                as_label: Optional[ModuleLabel] = replace(label, root=False)
            else:
                if not by_folder:
                    for other in g.nodes:
                        by_folder.setdefault(_label_path(g, other).rpartition("/")[0], []).append(other)
                as_label = _label_beside(g, module, label, by_folder)
            if as_label is None:
                unplaced.add(module)
            else:
                judged[module] = as_label
                changed = True
    g.gate.judged = judged
    return judged


def _judged_label(g: CodeGraph, module_id: str) -> ModuleLabel:
    """The label the rules judge a module by: its own, or the one of `_judged_names`."""
    return _judged_names(g).get(module_id) or module_label(g, module_id)


def _is_entry_root(g: CodeGraph, module_id: str) -> bool:
    """A composition root by its label that no file of a layer imports (`_judged_names`), or an entry point:
    nothing imports it and it imports such a composition root. A file whose layer a rule sets is never
    an entry point this way: the root it imports is judged, because a file of a layer imports it."""
    if module_id not in g.entry_roots:
        judged = _judged_names(g)

        def is_root(module: str) -> bool:
            return module_label(g, module).root and module not in judged

        g.entry_roots[module_id] = is_root(module_id) or (
            not _collect_rdep_mods(g, module_id) and any(is_root(target) for target in _collect_dep_mods(g, module_id))
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
    root (main.ts, app.module.ts, Program.cs) wires the layers and may import every one, and so may a
    test; one that a file of a layer imports is judged like the files of its folder (`_judged_names`).
    """
    if not g.arch_parts:
        _default_labels(g)
    found: list[BoundaryBreak] = []
    for src_mod in sorted(scope_mods):
        source = _judged_label(g, src_mod)
        rules = g.arch_denies.get(source.part)
        if not rules or _is_entry_root(g, src_mod):
            continue
        for edge in g.edges_from.get(src_mod, []):
            if edge.etype not in ("imports", "data-flow"):
                continue
            tgt_mod = edge.target.split("::")[0]
            if tgt_mod == src_mod:
                continue
            target = _judged_label(g, tgt_mod)
            if target.part != source.part or target.layer == source.layer:
                continue
            reason = rules.get((source.layer, target.layer))
            if reason is None:
                continue
            rule = f"{source.layer} -> {target.layer}"
            found.append(BoundaryBreak(
                source=src_mod, target=tgt_mod, source_layer=source.layer, target_layer=target.layer,
                kind=edge.etype, reason=reason, part=source.part, style=source.style if source.style != "-" else
                g.arch_parts.get(source.part, {}).get("style", "-"),
                rule=rule, known=_accepting_file(g, (src_mod, tgt_mod), rule),
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
        label = _judged_label(g, module)
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
        label = _judged_label(g, module)
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
    violations, _known = _print_capability_findings(g, scope_mods)
    return 1 if violations else 0


def _print_capability_findings(g: CodeGraph, scope_mods: set[str]) -> tuple[int, int]:
    """Print the capability and pattern facts; (capability violations and pattern breaks, known ones)."""
    lines, violations, known = _capability_findings(g, scope_mods, boundaries_coverage(g, scope_mods).checked)
    for line in lines:
        print(line)
    return violations, known


def _capability_findings(g: CodeGraph, scope_mods: set[str], checked: int) -> tuple[list[str], int, int]:
    """(the capability and pattern facts, capability violations and pattern breaks, known ones).

    First, for each part of the scope that has capability rules, the files in a language the capability
    detector does not read: `(capability-not-read :part P :language L :files N)`. `checked` is the number
    of files the rules place, as `boundaries` counts them."""
    lines: list[str] = []
    for part in sorted({module_label(g, module).part for module in scope_mods} & set(g.gate.capability_unread)):
        unread = g.gate.capability_unread[part]
        lines.extend(f"(capability-not-read :part {_q(part)} :language {_q(language)} :files {unread[language]})"
                     for language in sorted(unread, key=lambda name: (-unread[name], name)))
    breaks = capability_violations(g, scope_mods)
    patterns = pattern_violations(g, scope_mods)
    if not breaks and not patterns:
        return lines, 0, 0
    counts = {"capability-violation": 0, "capability-violation-known": 0, "capability-candidate": 0}
    for item in breaks:
        fact = "capability-candidate" if item.severity == "candidate" else (
            "capability-violation-known" if item.known else "capability-violation")
        counts[fact] += 1
        allowed = f" :allowed ({' '.join(item.allowed)})" if item.allowed else ""
        forbidden = f" :forbidden ({' '.join(item.forbidden)})" if item.forbidden else ""
        known = f" :known {_q(item.known)}{_known_terms(g.capability_terms, (item.module, item.capability))}" if item.known else ""
        if fact == "capability-violation":
            known = _expired_terms(g.capability_expired, (item.module, item.capability))
        pattern = f" :pattern {item.pattern}" if item.pattern else ""
        lines.append(
            f"({fact} {_q(module_file(g, item.module))} :capability {item.capability} :layer {item.layer}{allowed}{forbidden} "
            f":line {item.line} :evidence {_q(item.evidence)} :part {_q(item.part)} :style {item.style}{known}{pattern}"
            f"{' :fix ' + _q(item.fix) if item.fix else ''})"
        )
    for item in patterns:
        target = f" :target {_q(module_file(g, item.target))}" if item.target else ""
        line = f" :line {item.line} :evidence {_q(item.evidence)}" if item.line else ""
        lines.append(f"(pattern-violation {_q(module_file(g, item.source))} :pattern {item.pattern}{target}{line} "
                     f":part {_q(item.part)} :style {item.style} {_q(item.reason)})")
    lines.append(
        f"(capability-summary :violations {counts['capability-violation']} :known {counts['capability-violation-known']} "
        f":candidates {counts['capability-candidate']} :patterns {len(patterns)} :checked {checked})"
    )
    return lines, counts["capability-violation"] + len(patterns), counts["capability-violation-known"]


def _known_terms(terms: dict[tuple[str, str], _baseline.Acceptance], key: tuple[str, str]) -> str:
    """` :owner "<who>" :reason "<why>" :expires <date>` of an accepted finding, with the fields its entry states."""
    stated = terms.get(key)
    return _baseline.terms_sexp(stated) if stated is not None else ""


def _expired_terms(expired: dict[tuple[str, str], _baseline.Acceptance], key: tuple[str, str]) -> str:
    """` :expired <date> [:owner "<who>"]` of a finding whose baseline entry is past its end date."""
    stated = expired.get(key)
    return _baseline.expired_sexp(stated) if stated is not None else ""


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
    reason: str               # declared-rules-not-read | style-undetermined | no-layer-style | too-few-files;
    #                           from boundaries_coverage also in-no-part | no-file-in-a-layer | not-parsed-language
    files: tuple[str, ...]    # the rule files that were not read
    count: int                # files (modules) of the part in the scope; not-parsed-language: files in no module
    languages: tuple[str, ...] = ()   # not-parsed-language: the languages, most files first


_NOT_CHECKED_WORDS = {
    "declared-rules-not-read": "declared rules not read",
    "style-undetermined": "style not determined",
    "no-layer-style": "no layer style",
    "too-few-files": "too few files to judge a style",
    "in-no-part": "in no part",
    "no-file-in-a-layer": "no file in a layer",
    "not-parsed-language": "a language no parser reads",
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


@dataclass(frozen=True)
class Coverage:
    """What `boundaries` judged of a scope, in files.

    `checked` counts the files a rule places (in a layer, or as a test) in a part that has rules. The
    other files are not checked, each group with its reason (`unchecked`), or belong to a checked part and
    to no layer of it (`no_layer`): the rules say nothing about those, and they are counted apart.
    """

    unchecked: tuple[NotChecked, ...]
    parts: tuple[tuple[str, str, int], ...]   # (part, style, modules of the scope) of each checked part
    no_layer: tuple[tuple[str, int], ...]     # (checked part, its files that no rule places)
    checked: int

    @property
    def not_checked(self) -> int:
        return sum(item.count for item in self.unchecked)

    @property
    def unplaced(self) -> int:
        return sum(count for _part, count in self.no_layer)


_NOTHING_CHECKED = Coverage((), (), (), 0)


def boundaries_coverage(g: CodeGraph, scope_mods: set[str], whole: bool = False) -> Coverage:
    """Sort the files of a scope into checked, not checked and in no layer.

    Not checked, besides the parts of `not_checked_parts`: the files in no part of the plan, a part with
    rules in which no file is in a layer, and the source files of a part in a language no parser reads
    (they are in no module; `whole` counts them for every part, a narrower scope for the parts it touches).
    """
    unchecked = not_checked_parts(g, scope_mods)
    left_out = {item.part for item in unchecked}
    by_part: dict[str, list[str]] = {}
    for module in scope_mods:
        by_part.setdefault(module_label(g, module).part, []).append(module)
    parts: list[tuple[str, str, int]] = []
    no_layer: list[tuple[str, int]] = []
    checked = 0
    for part, modules in sorted(by_part.items()):
        if part in left_out:
            continue
        if part not in g.arch_parts:
            unchecked.append(NotChecked(part, "in-no-part", (), len(modules)))
            continue
        placed = sum(1 for module in modules if _layer_is_certain(g, module))
        if not placed:
            unchecked.append(NotChecked(part, "no-file-in-a-layer", (), len(modules)))
            continue
        checked += placed
        parts.append((part, g.arch_parts[part].get("style", "-"), len(modules)))
        if placed < len(modules):
            no_layer.append((part, len(modules) - placed))
    for part, counts in sorted(g.gate.not_parsed.items()):
        if whole or part in by_part:
            languages = tuple(sorted(counts, key=lambda name: (-counts[name], name)))
            unchecked.append(NotChecked(part, "not-parsed-language", (), sum(counts.values()), languages))
    return Coverage(tuple(unchecked), tuple(parts), tuple(no_layer), checked)


def _not_checked_fact(item: NotChecked) -> str:
    files = f" :files ({' '.join(_q(name) for name in item.files)})" if item.files else ""
    languages = f" :languages ({' '.join(_q(name) for name in item.languages)})" if item.languages else ""
    return f"(boundaries-not-checked :part {_q(item.part)} :reason {item.reason}{files}{languages} :files-in-part {item.count})"


def _rule_reader(root: str) -> Callable[[str], Optional[str]]:
    """The reader of repository files the build uses: the digests are compared on the same text."""
    if __package__:
        from .architecture.repository import text_reader
    else:
        from architecture.repository import text_reader
    return text_reader(root)


def changed_rule_files(g: CodeGraph) -> tuple[str, ...]:
    """The rule files (.wingbox/config.yaml, baseline.sexp, the learned layers of decisions.sexp) that were
    edited, added or deleted since the build recorded them: the facts of the graph are older than the
    rules. None for a graph that holds no record."""
    if not g.gate.rule_digests or not g._root:
        return ()
    return _baseline.changed_rule_files(g.gate.rule_digests, _baseline.rule_file_digests(_rule_reader(g._root)))


STRICT_EXIT = {"pass": 0, "fail": 1, "incomplete": 2, "error": 3}


def boundaries_result_line(status: str, checked: int = 0, not_checked: int = 0, violations: int = 0, known: int = 0,
                           reason: str = "", no_layer: int = 0) -> str:
    """The one line a gate reads in strict mode; the counts are files (modules), violations and known breaks.
    `:no-layer` (files of the checked parts that no rule places) is written only when it is not zero."""
    return (f"(boundaries-result :status {status} :checked {checked} :not-checked {not_checked} "
            f":violations {violations} :known {known}{f' :no-layer {no_layer}' if no_layer else ''}"
            f"{' :reason ' + reason if reason else ''})")


def _strict_error(reason: str) -> int:
    """Strict mode could not run the check (no graph, a graph that cannot be read): say so and exit 3."""
    print(boundaries_result_line("error", reason=reason))
    return STRICT_EXIT["error"]


def _strict_status(g: CodeGraph, cover: Coverage, violations: int, changed: Sequence[str]) -> tuple[str, str]:
    """(status, reason) of the strict result.

    pass: every part with files was checked, at least one file was checked, the facts are current and
    nothing breaks a rule. fail: a violation. incomplete, first that applies: the graph is known to miss
    changed files; a rule file changed since the build; a part or the files in no part were not checked;
    source files were not read (no parser); no file is in a layer of a part, or no file was checked.
    """
    reasons = {item.reason for item in cover.unchecked}
    if violations:
        return "fail", ""
    if g.gate.stale_files:
        return "incomplete", "graph-stale"
    if changed:
        return "incomplete", "rules-changed"
    if reasons - {"not-parsed-language", "no-file-in-a-layer"}:
        return "incomplete", "part-not-checked"
    if "not-parsed-language" in reasons:
        return "incomplete", "not-parsed-language"
    if reasons or cover.checked == 0:
        return "incomplete", "no-files-checked"
    return "pass", ""


def _strict_result(g: CodeGraph, cover: Coverage, violations: int, known: int, changed: Sequence[str]) -> int:
    """The strict lines after the plain output: a fact per checked part, then the result; returns the exit status."""
    for part, style, modules in cover.parts:
        print(f"(boundaries-checked :part {_q(part)} :style {style} :modules {modules})")
    status, reason = _strict_status(g, cover, violations, changed)
    print(boundaries_result_line(status, cover.checked, cover.not_checked, violations, known, reason, cover.unplaced))
    return STRICT_EXIT[status]


def cmd_boundaries(g: CodeGraph, module_id: str, strict: bool = False) -> int:
    """Check the boundary rules of the style of each part (or of the rules the part declares).

    A pair is a violation when both layers come from rules (folders, file
    patterns, declared layers); when a layer is only guessed from the role of a
    file, the pair is a candidate. An import of types only is listed apart.
    Each line names the part, the style and the rule; `:known` names the
    project's own baseline when it already lists the pair.

    What was not checked comes first (`boundaries-not-checked`, with
    `boundaries-partial` in place of ok), then the files of a checked part that
    no rule places (`boundaries-no-layer`); `:checked` counts the files a rule
    places. `boundaries-ok` is a pass: it is not printed beside a violation of
    any kind, nor when a rule file changed since the build
    (`boundaries-rules-changed`). Exit status: 2 when a part's declared rules
    were not read or the scope matches nothing, 1 for a violation, else 0.

    With `strict` the same lines are followed by a `boundaries-checked` fact
    per checked part and one `boundaries-result` line, and the exit status is
    the one of that line (STRICT_EXIT): what was not checked or not read, and
    facts known to be older than the files or the rules, are never a pass.
    """
    if module_id == "all":
        scope_mods = set(g.nodes.keys())
    else:
        matches = g.resolve_module(module_id)
        if not matches:
            _print_no_match(g, module_id)
            return _strict_result(g, _NOTHING_CHECKED, 0, 0, ()) if strict else 2
        scope_mods = set(matches)

    violations = boundary_violations(g, scope_mods)
    cover = boundaries_coverage(g, scope_mods, whole=module_id == "all")
    changed = changed_rule_files(g)
    if changed:
        print(f"(boundaries-rules-changed :files ({' '.join(_q(name) for name in changed)}) "
              f":hint {_q('rebuild: wingbox build')})")
    for item in cover.unchecked:
        print(_not_checked_fact(item))
    for part, count in cover.no_layer:
        print(f"(boundaries-no-layer :part {_q(part)} :files-in-part {count})")
    no_layer = f" :no-layer {cover.unplaced}" if cover.unplaced else ""
    coverage = (f"(boundaries-partial :checked {cover.checked} :not-checked {cover.not_checked}{no_layer})"
                if cover.unchecked else "")
    # Exit 2: a part whose own rules were not read was not checked; 1: a violation; 0 otherwise.
    status = 2 if any(item.reason == "declared-rules-not-read" for item in cover.unchecked) else 0
    capability_lines, broken, accepted = _capability_findings(g, scope_mods, cover.checked)

    if not violations:
        if coverage:
            print(coverage)
        elif not broken and not changed and (not strict or _strict_status(g, cover, 0, changed)[0] == "pass"):
            print(f"(boundaries-ok :checked {cover.checked}{no_layer})")
        for line in capability_lines:
            print(line)
        if strict:
            return _strict_result(g, cover, broken, accepted, changed)
        return max(status, 1 if broken else 0)
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
        known = f" :known {_q(item.known)}{_known_terms(g.known_terms, (item.source, item.target))}" if item.known else ""
        if fact == "violation":
            known = _expired_terms(g.known_expired, (item.source, item.target))
        print(
            f"({fact} {item.source} {item.target} :src-layer {item.source_layer} :tgt-layer {item.target_layer} "
            f":via {item.kind} :lang {g.module_lang(item.source)} :part {_q(item.part)} :style {item.style} "
            f":rule {_q(item.rule)}{known} :src-file {_q(g.module_to_file(item.source))} "
            f":tgt-file {_q(g.module_to_file(item.target))} {_q(item.reason)})"
        )
    print(
        f"(boundaries-summary :violations {len(pairs['violation'])} :known {len(pairs['violation-known'])} "
        f":type-only {len(pairs['violation-type-only'])} "
        f":candidates {len(pairs['violation-candidate'])} :checked {cover.checked}{no_layer})"
    )
    if coverage:
        print(coverage)
    for line in capability_lines:
        print(line)
    if strict:
        return _strict_result(g, cover, len(pairs["violation"]) + broken, len(pairs["violation-known"]) + accepted, changed)
    return status or (1 if pairs["violation"] or broken else 0)


# ---------------------------------------------------------------------------
# Cycles: what the dependency graph says without a layer style
# ---------------------------------------------------------------------------

_HUB_DEGREE = 10       # a hub has at least this many dependents and this many dependencies
_HUBS_SHOWN = 20
_CYCLE_FILES_SHOWN = 30


def _strong_groups(nodes: Sequence[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """The groups of nodes that reach each other (strongly connected components), in the order in which a
    group stands before every group it depends on. Tarjan's algorithm without recursion."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    groups: list[list[str]] = []
    counter = 0
    for start in nodes:
        if start in index:
            continue
        work: list[tuple[str, list[str]]] = [(start, sorted(edges.get(start, ())))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, rest = work[-1]
            if rest:
                target = rest.pop()
                if target not in index:
                    index[target] = low[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, sorted(edges.get(target, ()))))
                elif target in on_stack:
                    low[node] = min(low[node], index[target])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                groups.append(sorted(group))
    return groups


def _import_edges(g: CodeGraph, prefix: str) -> tuple[dict[str, str], set[str], dict[str, set[str]]]:
    """(the file of every module, the modules below the folder prefix, their import edges among themselves)."""
    files = {module: _impact_file(g, module) for module in g.nodes}
    inside = {module for module, path in files.items() if not prefix or path == prefix or path.startswith(prefix + "/")}
    edges: dict[str, set[str]] = {}
    for edge in g.all_edges:
        # An import that the compiler erases (`import type`) makes no cycle at run time.
        if edge.etype != "imports" or ":type-only" in edge.flags:
            continue
        source, target = edge.source.split("::")[0], edge.target.split("::")[0]
        if source != target and source in inside and target in inside:
            edges.setdefault(source, set()).add(target)
    return files, inside, edges


@dataclass(frozen=True)
class FolderLayers:
    """The folders of a part as layers, lowest first, and the imports that go upward under that order."""

    part: str                                   # the folder asked, "." for the repository
    folders: tuple[str, ...]                    # lowest first; "." stands for the files directly in the part
    files: int                                  # source files counted (test files are in no layer)
    upward: tuple[tuple[str, str, int], ...]    # (from folder, to folder, file pairs), from below to above


def folder_layers(g: CodeGraph, part: str) -> Union[str, FolderLayers]:
    """One layer per folder of a part, lowest first: a folder stands below the folders that depend on it;
    folders that depend on each other are ordered so that few imports go upward (of the folders left, the
    one that the others depend on most and that depends on them least is placed lowest). A string says
    why there is nothing to order."""
    prefix = "" if part.strip("/") in ("", ".") else part.strip("/").replace(os.sep, "/")
    files, inside, edges = _import_edges(g, prefix)
    inside = {module for module in inside if module_layer(g, module) != "test"}
    if not inside:
        return f"no source file of the graph is below {part}"

    def folder_of(module: str) -> str:
        rest = files[module][len(prefix) + 1:] if prefix else files[module]
        return rest.split("/", 1)[0] if "/" in rest else "."

    folders = sorted({folder_of(module) for module in inside})
    if len(folders) < 2:
        return f"{part} has fewer than two folders with source files: there is nothing to order"
    between: dict[tuple[str, str], int] = {}
    for source, targets in edges.items():
        for target in targets:
            if source in inside and target in inside and folder_of(source) != folder_of(target):
                pair = (folder_of(source), folder_of(target))
                between[pair] = between.get(pair, 0) + 1
    depends: dict[str, set[str]] = {}
    for a, b in between:
        depends.setdefault(a, set()).add(b)
    groups = _strong_groups(folders, depends)
    group_of = {folder: number for number, group in enumerate(groups) for folder in group}
    # Lowest first: a group is placed when every group it depends on is placed; ties by name.
    waiting = {number: {group_of[b] for a in group for b in depends.get(a, ()) if group_of[b] != number}
               for number, group in enumerate(groups)}
    placed: list[int] = []
    while len(placed) < len(groups):
        ready = sorted((number for number in waiting if number not in placed and waiting[number] <= set(placed)),
                       key=lambda number: groups[number])
        placed.append(ready[0])
    ordered: list[str] = []
    for number in placed:
        left = set(groups[number])
        while left:
            def score(folder: str) -> int:
                used_by = sum(between.get((other, folder), 0) for other in left)
                uses = sum(between.get((folder, other), 0) for other in left)
                return used_by - uses
            lowest = sorted(left, key=lambda folder: (-score(folder), folder))[0]
            ordered.append(lowest)
            left.discard(lowest)
    rank = {folder: index for index, folder in enumerate(ordered)}
    upward = tuple(sorted((a, b, count) for (a, b), count in between.items() if rank[a] < rank[b]))
    return FolderLayers(part.strip("/") or ".", tuple(ordered), len(inside), upward)


def cmd_cycles(g: CodeGraph, scope: str = "") -> None:
    """What the import graph says where no layer style applies: the groups of files that depend on each
    other, the files many depend on that depend on many, and how the folders depend on each other.

    scope: a folder; the answer is then about the files below it, with its subfolders as the folders."""
    prefix = scope.strip("/").replace(os.sep, "/")
    files, inside, edges = _import_edges(g, prefix)
    if prefix and not inside:
        _print_no_match(g, scope)
        return
    dependents: dict[str, int] = {}
    for targets in edges.values():
        for target in targets:
            dependents[target] = dependents.get(target, 0) + 1

    cycles = sorted(
        (sorted(files[module] for module in group) for group in _strong_groups(sorted(inside), edges) if len(group) > 1),
        key=lambda group: (-len(group), group),
    )
    for number, group in enumerate(cycles, start=1):
        shown = group[:_CYCLE_FILES_SHOWN]
        more = f" :more {len(group) - len(shown)}" if len(group) > len(shown) else ""
        print(f"(cycle {number} :files {len(group)} {' '.join(_q(path) for path in shown)}{more})")

    hubs = sorted(
        ((dependents.get(module, 0), len(edges.get(module, ())), files[module]) for module in inside
         if dependents.get(module, 0) >= _HUB_DEGREE and len(edges.get(module, ())) >= _HUB_DEGREE),
        key=lambda item: (-item[0] * item[1], item[2]),
    )
    for used_by, uses, path in hubs[:_HUBS_SHOWN]:
        print(f"(hub {_q(path)} :dependents {used_by} :dependencies {uses})")

    def folder_of(module: str) -> str:
        rest = files[module][len(prefix) + 1:] if prefix else files[module]
        return rest.split("/", 1)[0] if "/" in rest else "."

    between: dict[tuple[str, str], int] = {}
    for source, targets in edges.items():
        for target in targets:
            pair = (folder_of(source), folder_of(target))
            if pair[0] != pair[1]:
                between[pair] = between.get(pair, 0) + 1
    folder_cycles = sorted((a, b) for (a, b) in between if a < b and (b, a) in between)
    for a, b in folder_cycles:
        print(f"(folder-cycle {_q(a)} {_q(b)} :forward {between[(a, b)]} :back {between[(b, a)]})")
    folders = sorted({folder_of(module) for module in inside})
    folder_edges: dict[str, set[str]] = {}
    for a, b in between:
        folder_edges.setdefault(a, set()).add(b)
    if len(folders) > 1:
        # From the folders that depend on others to the folders the others depend on: a group is ready when
        # nothing left depends on it; of two ready groups the one whose first folder sorts first comes first.
        groups = _strong_groups(folders, folder_edges)
        group_of = {folder: number for number, group in enumerate(groups) for folder in group}
        waiting = {number: 0 for number in range(len(groups))}
        targets_of: dict[int, set[int]] = {number: set() for number in range(len(groups))}
        for a, b in between:
            if group_of[a] != group_of[b] and group_of[b] not in targets_of[group_of[a]]:
                targets_of[group_of[a]].add(group_of[b])
                waiting[group_of[b]] += 1
        ordered: list[list[str]] = []
        ready = sorted((number for number, count in waiting.items() if count == 0), key=lambda number: groups[number])
        while ready:
            number = ready.pop(0)
            ordered.append(groups[number])
            for target in targets_of[number]:
                waiting[target] -= 1
                if waiting[target] == 0:
                    ready.append(target)
            ready.sort(key=lambda item: groups[item])
        print("(folder-order " + " ".join("(" + " ".join(_q(folder) for folder in group) + ")" for group in ordered) + ")")
    print(
        f"(cycles-summary :cycles {len(cycles)} :files-in-cycles {sum(len(group) for group in cycles)} "
        f":hubs {len(hubs)} :folder-cycles {len(folder_cycles)} :files {len(inside)})"
    )


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
                print(f"(fn-def {_q(key)} :line {ent.line} :lang {lang} :file {_q(fpath)} :kind method)")
        else:
            key = f"{ent.module}::{ent.name}"
            if key not in seen:
                seen.add(key)
                function_kind = (
                    "method"
                    if _short_function_name(ent.name) != ent.name
                    else "function"
                )
                print(f"(fn-def {_q(key)} :line {ent.line} :lang {lang} :file {_q(fpath)} :kind {function_kind})")


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
        print(f"(type-def {ent.kind} {_q(f'{ent.module}::{ent.name}')} :line {ent.line} :lang {lang} :file {_q(fpath)}{extras})")


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


def _calls_hint(text: str) -> None:
    """Say on standard error why an answer holds no calls edge and where calls are listed. Standard output
    keeps the lines versions up to 0.13 printed; the MCP server adds standard error to its answer."""
    print(f"; the graph holds calls edges only after a build with --lsp (wingbox build . --lsp). {text}", file=sys.stderr)


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
        print(f"(callers-target {_q(qid)} :line {ent.line} :lang {lang} :file {_q(fpath)})")

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
            print(f"(caller {_q(e.source)} -> {_q(e.target)} :lang {g.module_lang(src_mod)} :file {_q(g.module_to_file(src_mod))} {' '.join(e.flags)})")
    else:
        # Fallback: use module-level edges (imports/refs) pointing at target modules
        print("; no calls edges found, showing module-level callers via imports/refs")
        _calls_hint(f"The call sites the build resolved are listed by: wingbox usages {shlex.quote(fn_name)}")

    # Walk reverse deps to given depth
    for tgt_mod in target_mods:
        visited: set[str] = {tgt_mod}
        _walk_rdeps(g, tgt_mod, visited, max_depth, 1,
                    lambda d, et, src, cyc: print(
                        f"(caller-module {_q(src)} -> {_q(tgt_mod)} :via {et} :depth {d} "
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
        print(f"(calls-source {_q(qid)} :line {ent.line} :lang {lang} :file {_q(fpath)})")

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
            print(f"(calls-target {_q(e.source)} -> {_q(e.target)} :lang {g.module_lang(tgt_mod)} :file {_q(g.module_to_file(tgt_mod))} {' '.join(e.flags)})")
    else:
        print("; no calls edges found, showing module-level deps via imports")
        _calls_hint(f"What a function calls is in its text (wingbox source {shlex.quote(fn_name)}); who calls a function "
                    "is listed by: wingbox usages <name>")

    # Walk forward deps to given depth
    for src_mod in source_mods:
        visited: set[str] = {src_mod}
        _walk_deps(g, src_mod, visited, max_depth, 1,
                   lambda d, et, tgt, cyc: print(
                       f"(calls-module {_q(src_mod)} -> {_q(tgt)} :via {et} :depth {d} "
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
            print(f"(callchain-step {i} {_q(node)} :lang {lang} :file {_q(fpath)})")
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
                print(f"(callchain-module-step {i} {_q(mod)} :lang {lang} :file {_q(fpath)})")
            print(f"(callchain-module-summary :length {len(mod_path)} :hops {len(mod_path) - 1})")
            return

    print(f"(callchain-none {_q(src_fn)} -> {_q(tgt_fn)} :max-depth {max_depth})")


_BRACKET_LINE = ("(", ")", "[", "]", "{", "}")


def _indentation(line: str) -> int:
    return len(line) - len(line.lstrip())


def _method_at(lines: list[str], entity: EntityFact, last: int, index: int) -> Optional[str]:
    """Which definition of a class holds the line at index (0-based), read from the text of the class, lines
    entity.line to last: `Class.method` for a line of a method, `Class` for a line of the class that is in no
    method, None for a line that is not in the class (its own line, its closing bracket, code after it).

    The graph names the methods of a class without their lines, so the lines are found here: the members of
    a class are the lines at the smallest indentation below that of its first line, and a method starts at
    the member line that holds its name. A member line that is only a bracket (the end of a signature over
    several lines, a brace on a line of its own) belongs to what is above it."""
    first = entity.line - 1
    own = _indentation(lines[first])
    deeper = [_indentation(line) for line in lines[first + 1:last] if line.strip() and _indentation(line) > own]
    if deeper and _indentation(lines[index]) <= own:
        return None
    member = min(deeper) if deeper else own
    for above in range(index, first, -1):
        line = lines[above]
        if not line.strip() or _indentation(line) != member:
            continue
        for method in entity.methods:
            if re.search(rf"(?<![\w$]){re.escape(method)}(?![\w$])", line):
                return f"{entity.name}.{method}"
        if not line.lstrip().startswith(_BRACKET_LINE):
            break
    return entity.name


def cmd_grep_functions(g: CodeGraph, pattern: str) -> None:
    """Search function bodies using the supported linear pattern grammar.

    For each function and each class with methods of the code graph, reads the source file and searches the
    body (from the definition line to the next definition of the file, or to its end). A line of a method is
    named `<module>::<Class>.<method>` (see _method_at). Only files of the graph are read (see _source_file).
    """
    parsed_pattern = parse_search_pattern(pattern)
    if isinstance(parsed_pattern, SearchPatternErr):
        _print_invalid_pattern(pattern, parsed_pattern.error)
        return
    results: list[tuple[str, str, int, str]] = []  # (module::fn, file, match_line, match_text)

    def searched(entity: EntityFact) -> bool:
        return entity.kind == "fn" or (entity.kind in ("cls", "mod") and bool(entity.methods))

    files = sorted((g.module_to_file(mod), mod) for mod, ent_list in g.entities.items() if any(map(searched, ent_list)))
    for fpath, mod in files:
        located = _source_file(g, mod)
        if located is None:
            continue  # not on disk, or not a file that may be read
        try:
            lines = _read_source_lines(located[0])
        except OSError as error:
            print(
                f"(error source-read-failed {_q(fpath)} {_q(str(error))})"
            )
            continue

        # Sort the definitions by line number to determine body ranges
        ordered = sorted(g.entities[mod], key=lambda entity: entity.line)
        for idx, ent in enumerate(ordered):
            start = ent.line - 1  # 0-indexed
            if not searched(ent) or start < 0 or start >= len(lines):
                continue
            # End at the next definition of the file, or at its end
            end = min(ordered[idx + 1].line - 1, len(lines)) if idx + 1 < len(ordered) else len(lines)
            for line_idx in range(start, end):
                line = lines[line_idx]
                if not search_pattern_matches(parsed_pattern.value, line):
                    continue
                holder = ent.name if ent.kind == "fn" else _method_at(lines, ent, end, line_idx)
                if holder is not None:
                    results.append((f"{mod}::{holder}", fpath, line_idx + 1, _one_line(line.rstrip())))

    if not results:
        print(f"(grep-functions-empty {_q(pattern)})")
        return

    for qid, fpath, line_num, text in results:
        lang = g.module_lang(qid.split("::")[0])
        print(f"(grep-fn {_q(qid)} :line {line_num} :lang {lang} :file {_q(fpath)} :match {_q(text)})")
    print(f"(grep-functions-summary :matches {len(results)} :pattern {_q(pattern)})")


# ---------------------------------------------------------------------------
# files, source: what the graph holds on disk
# ---------------------------------------------------------------------------
SOURCE_LINE_LIMIT = 400   # lines one `source` call prints; the first line says where to go on
SOURCE_LINE_CHARS = 2000  # characters of one line; a longer line is cut and says its length (:cut N)
SOURCE_CHAR_LIMIT = 100_000   # characters of text in one answer; it stops before the line that would pass it


def cmd_files(g: CodeGraph, pattern: Optional[str] = None) -> None:
    """The source files of the graph, sorted by path, with the module, the language and the layer of each;
    with a pattern, the files whose path matches it."""
    parsed = None
    if pattern is not None:
        parsed = parse_search_pattern(pattern)
        if isinstance(parsed, SearchPatternErr):
            _print_invalid_pattern(pattern, parsed.error)
            return
    rows = sorted((_impact_file(g, module), module) for module in g.nodes)
    kept = [(path, module) for path, module in rows if parsed is None or search_pattern_matches(parsed.value, path)]
    if not kept:
        print(f"(files-empty {_q(pattern or '')})")
        return
    for path, module in kept:
        print(f"(file {_q(path)} :module {_q(module)} :lang {g.module_lang(module)} :layer {_graph_layer(g, module)})")
    print(f"(files-summary :count {len(kept)} :of {len(rows)}" + (f" :pattern {_q(pattern)})" if pattern is not None else ")"))


def _source_module(g: CodeGraph, target: str) -> Optional[str]:
    """The module of the graph a file path or module ID names exactly; None for anything else, so that
    `source` never guesses which file to print."""
    path = portable.local_path(target).replace(os.sep, "/")
    if g._root and os.path.isabs(portable.local_path(target)):
        relative = portable.relative_posix(portable.local_path(target), g._root)
        if relative is None:
            return None
        path = relative
    if path.startswith("./"):
        path = path[2:]
    if ".." in path.split("/"):
        return None
    for module in g.nodes:
        if module == path or _impact_file(g, module) == path:
            return module
    return None


def _source_target(g: CodeGraph, target: str) -> Union[tuple[str, Optional["EntityFact"]], str]:
    """(module, entity or None) for a file, a module or one definition; or the error line."""
    if "::" in target:
        file_part, name = target.split("::", 1)
        module = _source_module(g, file_part)
        found = [e for e in g.entities.get(module, [])] if module is not None else []
        found = [e for e in found if e.name == name]
        return (module, found[0]) if module is not None and found else _no_match_fact(target, [])
    module = _source_module(g, target)
    if module is not None:
        return module, None
    found = g.entity_by_name.get(target, [])
    if len(found) == 1:
        return found[0].module, found[0]
    if found:
        candidates = " ".join(_q(f"{_impact_file(g, e.module)}::{e.name}") for e in sorted(found, key=lambda e: (e.module, e.line)))
        return f"(error ambiguous {_q(target)} :candidates {candidates})"
    return _no_match_fact(target, [])


def _source_file(g: CodeGraph, module: str) -> Optional[tuple[str, str]]:
    """(path to read, path to show) of a module of the graph, or None when it must not be read.

    The file is read only when its real path is the root's real path joined with the path the graph holds:
    no component below the root may be a symbolic link, so a file replaced by a link after the build is
    refused, wherever the link points. Without a root nothing is read: the check cannot be made."""
    if not g._root:
        return None
    named = g.module_to_file(module)
    if not os.path.isabs(named):
        return None
    relative = os.path.relpath(named, g._root)
    if relative.startswith(os.pardir) or os.path.isabs(relative):
        return None
    expected = os.path.join(os.path.realpath(g._root), relative)
    real = os.path.realpath(named)
    if os.path.normcase(real) != os.path.normcase(expected) or not os.path.isfile(real):
        return None
    return real, relative.replace(os.sep, "/")


# The characters besides the line feed at which str.splitlines ends a line: a carriage return, a vertical
# tab, a form feed, the file, group and record separators, NEL, the line and the paragraph separator.
_LINE_LIKE = str.maketrans({character: " " for character in "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"})


def _read_source_lines(path: str) -> list[str]:
    """The lines of a source file as the graph numbers them: a line ends at a line feed, and a carriage
    return before it belongs to the line end. Nothing else ends a line: the parsers count line feeds, so a
    form feed inside a line must not shift the numbers of the lines below it."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        lines = handle.read().split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _one_line(text: str) -> str:
    """The text of a source line as an answer prints it: a character at which a reader of lines would end the
    line (see _LINE_LIKE) is a space, so the answer keeps one fact a line."""
    return text.translate(_LINE_LIKE)


def cmd_source(g: CodeGraph, target: str, first: Optional[int] = None, last: Optional[int] = None) -> None:
    """The lines of one source file of the graph, or of one definition in it, numbered and quoted.

    Only files of the graph are read (see _source_file). One answer holds at most SOURCE_LINE_LIMIT lines and
    SOURCE_CHAR_LIMIT characters of text, and a line longer than SOURCE_LINE_CHARS is cut."""
    if (first is not None and first < 1) or (last is not None and last < 1):
        print(f"(error invalid-argument {_q('source: first and last are line numbers from 1')})")
        return
    if first is not None and last is not None and last < first:
        print(f"(error invalid-argument {_q('source: last is before first')})")
        return
    resolved = _source_target(g, target)
    if isinstance(resolved, str):
        print(resolved)
        return
    module, entity = resolved
    located = _source_file(g, module)
    if located is None:
        print(_no_match_fact(target, []))
        return
    real, shown_path = located
    try:
        text_lines = _read_source_lines(real)
    except OSError as error:
        print(f"(error source-read-failed {_q(shown_path)} {_q(error.strerror or type(error).__name__)})")
        return
    total = len(text_lines)
    start, end = 1, total
    if entity is not None:
        later = sorted(e.line for e in g.entities.get(module, []) if e.line > entity.line)
        start, end = entity.line, (later[0] - 1 if later else total)
    if first is not None:
        start = first
    if last is not None:
        end = min(last, total)
    if start > max(total, 1) or end < start:
        print(f"(error invalid-argument {_q(f'lines {start} to {end}: the file has {total} lines')})")
        return
    printed: list[str] = []
    characters = 0
    for number in range(start, min(end, start + SOURCE_LINE_LIMIT - 1) + 1):
        text = text_lines[number - 1]
        cut = f" :cut {len(text)}" if len(text) > SOURCE_LINE_CHARS else ""
        line = f'(line {number} "{_escaped(_one_line(text[:SOURCE_LINE_CHARS]))}"{cut})'
        if printed and characters + len(line) > SOURCE_CHAR_LIMIT:
            break
        printed.append(line)
        characters += len(line)
    shown_end = start + len(printed) - 1
    head = f"(source {_q(shown_path)} :first {start} :last {shown_end} :of {total}"
    if entity is not None:
        head += f" :entity {_q(f'{module}::{entity.name}')}"
    if shown_end < end:
        head += f" :more {end - shown_end} :next {shown_end + 1}"
    print(head + ")")
    for line in printed:
        print(line)


GREP_LINE_CHARS = 300      # characters of one matching line `grep` prints; a longer line is cut (:cut N)
GREP_MATCH_LIMIT = 200     # matches one `grep` answer lists; the summary gives the total and where to go on
GREP_CHAR_LIMIT = 100_000  # characters of match lines in one answer; it stops before the line that would pass it


def _grep_arguments(words: list[str]) -> Union[tuple[str, Optional[str], int], str]:
    """(text, pattern of file paths or None, first match) from the words after `grep`, or what is wrong with
    them. The first word is always the text, whatever it starts with."""
    if not words:
        return "grep requires the text to search for: literal text with optional .* wildcards"
    path_pattern: Optional[str] = None
    first = 1
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--first":
            value = words[index + 1] if index + 1 < len(words) else ""
            if not (value.isdecimal() and int(value) >= 1):
                return "grep: --first takes the number of a match, from 1"
            first = int(value)
            index += 2
            continue
        if word.startswith("--"):
            return f"unknown option {word}: grep takes --first N"
        if path_pattern is not None:
            return "grep takes the text and at most one pattern of file paths; quote a text that holds spaces"
        path_pattern = word
        index += 1
    return words[0], path_pattern, first


def cmd_grep(g: CodeGraph, pattern: str, path_pattern: Optional[str] = None, first: int = 1) -> None:
    """The lines of the source files of the graph that hold a text, with their file and line number.

    The text is the linear pattern grammar in its text form (every character literal but `.*`, `^` first, `$`
    last and the backslash), matched inside one line with the letter case ignored; path_pattern narrows the
    files as `files` does. Only files of the graph are read (see _source_file); a file that must not or
    cannot be read is counted, never printed. The lines are numbered as `source` numbers them. One answer
    lists at most GREP_MATCH_LIMIT matches and GREP_CHAR_LIMIT characters, from match `first` on; the summary
    gives the total and the match to go on from."""
    if first < 1:
        print(f"(error invalid-argument {_q('grep: first is the number of a match, from 1')})")
        return
    parsed = parse_search_pattern(pattern, text=True)
    if isinstance(parsed, SearchPatternErr):
        _print_invalid_pattern(pattern, parsed.error)
        return
    if not any(parsed.value.segments):
        _print_invalid_pattern(pattern, "the pattern holds no text to search for")
        return
    parsed_path = None
    if path_pattern is not None:
        parsed_path = parse_search_pattern(path_pattern)
        if isinstance(parsed_path, SearchPatternErr):
            _print_invalid_pattern(path_pattern, parsed_path.error)
            return
    rows = sorted({(_impact_file(g, module), module) for module in g.nodes})
    kept = [(path, module) for path, module in rows if parsed_path is None or search_pattern_matches(parsed_path.value, path)]
    matches, files_matched, unread, characters = 0, 0, 0, 0
    printed: list[str] = []
    full = False   # the answer holds what one answer may hold; later matches are only counted
    read: set[str] = set()
    for _path, module in kept:
        located = _source_file(g, module)
        if located is None:
            unread += 1
            continue
        real, shown_path = located
        if real in read:
            continue
        read.add(real)
        try:
            text_lines = _read_source_lines(real)
        except OSError:
            unread += 1
            continue
        before = matches
        for number, text in enumerate(text_lines, 1):
            if not search_pattern_matches(parsed.value, text):
                continue
            matches += 1
            if matches < first or full:
                continue
            cut = f" :cut {len(text)}" if len(text) > GREP_LINE_CHARS else ""
            line = f'(match {_q(shown_path)} {number} "{_escaped(_one_line(text[:GREP_LINE_CHARS]))}"{cut})'
            if len(printed) == GREP_MATCH_LIMIT or (printed and characters + len(line) > GREP_CHAR_LIMIT):
                full = True
                continue
            printed.append(line)
            characters += len(line)
        files_matched += matches > before
    tail = f" :searched {len(kept)}"
    scope = (f" :path {_q(path_pattern)}" if path_pattern is not None else "") + (f" :unread {unread}" if unread else "")
    if not matches:
        print(f"(matches-empty {_q(pattern)}{tail}{scope})")
        return
    if first > matches:
        print(f"(error invalid-argument {_q(f'grep: first is {first}, there are {matches} matches')})")
        return
    for line in printed:
        print(line)
    part = ""
    if len(printed) < matches:
        last = first + len(printed) - 1
        part = f" :shown {len(printed)} :first {first}" + (f" :next {last + 1}" if last < matches else "")
    print(f"(matches-summary :matches {matches}{part} :files {files_matched}{tail} :pattern {_q(pattern)}{scope})")


@dataclass(frozen=True)
class DiffFile:
    """One file a diff changes, as Git names it."""

    path: str            # relative to the graph root, with /: where the file is now; of a deleted file, where it was
    status: str          # "modified", "added", "deleted" or "renamed"
    old_path: str = ""   # of a renamed file: the path it had
    hunks: int = 0
    # The line ranges that changed, as lines of the file now. Removed lines count for the line above them.
    ranges: tuple[tuple[int, int], ...] = ()


@dataclass
class _DiffSection:
    """What the header lines and the hunks of one `diff --git` section of a patch say."""

    header: str                  # its `diff --git` line
    old: Optional[str] = None    # the path before, from `--- ` or `rename from`
    new: Optional[str] = None    # the path now, from `+++ `, `rename to` or `copy to`
    deleted: bool = False
    added: bool = False
    renamed: bool = False
    hunks: int = 0
    ranges: list[tuple[int, int]] = field(default_factory=list)


# The look of the patch is fixed here, whatever the Git configuration of the repository or of the user says
# (diff.noprefix, diff.mnemonicPrefix, color.diff, diff.renames, diff.relative, diff.interHunkContext): no
# colour, the prefixes a/ and b/, hunks without context, renames found, and paths relative to the folder Git
# runs in, which is the graph root. Only what changed below that folder is listed.
_DIFF_FORMAT = ("--no-color", "--src-prefix=a/", "--dst-prefix=b/", "--unified=0", "--inter-hunk-context=0",
                "--find-renames", "--relative")
_DIFF_TIMEOUT_SECONDS = 30
_HUNK_HEADER = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _git_safe():
    """scripts/git_safe.py: every Git call goes through it."""
    try:
        from . import git_safe
    except ImportError:  # loaded as a top-level script module
        import git_safe
    return git_safe


def _diff_files(g: CodeGraph, ref: str) -> Union[str, list[DiffFile]]:
    """The files `git diff <ref>` changes below the graph root (the files on disk against ref), in path
    order; the error fact when ref is no ref or Git cannot give the diff."""
    import subprocess

    git_safe = _git_safe()
    problem = git_safe.ref_problem(ref)
    if problem is not None:
        return f"(error invalid-argument {fact_json.atom(problem)})"

    def diff(*revisions: str) -> "subprocess.CompletedProcess[str]":
        # After --end-of-options a word is a revision and never an option; before the last -- it is never a path.
        return git_safe.run(
            g._root or None, "diff", (*_DIFF_FORMAT, "--end-of-options", *revisions, "--"), config=("core.quotepath=off",),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_DIFF_TIMEOUT_SECONDS,
        )

    try:
        result = diff(ref)
        if result.returncode != 0:
            first, result = result, diff(ref, "HEAD")   # no work tree to compare: ref against the commit checked out
            if result.returncode != 0:
                # What Git said to the first call, without the usage text it can add; atom makes it one line.
                message = first.stderr.split("\nusage:", 1)[0].strip()
                return f"(error git-diff-failed {fact_json.atom(message)})"
    except FileNotFoundError:
        return "(error git-not-found)"
    except subprocess.TimeoutExpired:
        return "(error git-diff-timeout)"
    except OSError as error:   # git_safe refuses the call, or Git cannot be started
        return f"(error git-diff-failed {fact_json.atom(str(error))})"
    files = _parse_diff(result.stdout)
    if isinstance(files, str):
        return f"(error git-diff-failed {fact_json.atom('cannot read the diff header: ' + files)})"
    return files


def _diff_error_status(fact: str) -> int:
    """The exit status of an error fact of _diff_files: 2 for a ref that is refused, as every invalid
    argument, 1 when Git cannot give the diff."""
    return 2 if fact.startswith("(error invalid-argument ") else 1


def _parse_diff(text: str) -> Union[str, list[DiffFile]]:
    """The files of a patch that `git diff` wrote with _DIFF_FORMAT, in path order; the `diff --git` line of
    a section that names no file, when there is one. The lines of a hunk are counted by its header, so a line
    of a file is never read as a header of the patch."""
    sections: list[_DiffSection] = []
    old_left = new_left = 0
    for line in text.split("\n"):   # not splitlines(): a line of a file may hold a form feed or a carriage return
        if old_left > 0 or new_left > 0:
            if line.startswith("-"):
                old_left -= 1
            elif line.startswith("+"):
                new_left -= 1
            elif not line.startswith("\\"):   # a context line; "\ No newline at end of file" is no line
                old_left -= 1
                new_left -= 1
            continue
        if line.startswith("diff --git "):
            sections.append(_DiffSection(line))
            continue
        if not sections:
            continue
        section = sections[-1]
        hunk = _HUNK_HEADER.match(line)
        if hunk is not None:
            old_left = 1 if hunk.group(1) is None else int(hunk.group(1))
            new_start = int(hunk.group(2))
            new_left = 1 if hunk.group(3) is None else int(hunk.group(3))
            section.hunks += 1
            if new_left:
                section.ranges.append((new_start, new_start + new_left - 1))
            elif new_start:   # lines were only removed, below line new_start
                section.ranges.append((new_start, new_start))
        elif line.startswith("--- "):
            section.old = _diff_side(line[4:], "a/")
        elif line.startswith("+++ "):
            section.new = _diff_side(line[4:], "b/")
        elif line.startswith("rename from "):
            section.old, section.renamed = _c_unquoted(line[len("rename from "):]), True
        elif line.startswith("rename to "):
            section.new = _c_unquoted(line[len("rename to "):])
        elif line.startswith("copy to "):
            section.new = _c_unquoted(line[len("copy to "):])
        elif line.startswith("deleted file mode "):
            section.deleted = True
        elif line.startswith("new file mode "):
            section.added = True
    files: dict[str, DiffFile] = {}
    for section in sections:
        item = _diff_file(section)
        if item is None:
            return section.header
        known = files.get(item.path)
        if known is not None:   # removed and created again in one diff (its type changed): one changed file
            item = DiffFile(item.path, "modified", "", known.hunks + item.hunks, known.ranges + item.ranges)
        files[item.path] = item
    return [files[path] for path in sorted(files)]


def _diff_file(section: _DiffSection) -> Optional[DiffFile]:
    ranges = tuple(section.ranges)
    if section.renamed and section.old and section.new:
        return DiffFile(section.new, "renamed", section.old, section.hunks, ranges)
    # A section without a text patch (a mode change, a binary file, an empty file) has no --- and +++ lines.
    path = (section.old if section.deleted else section.new) or _diff_header_path(section.header)
    if path is None:
        return None
    return DiffFile(path, "deleted" if section.deleted else "added" if section.added else "modified", "", section.hunks, ranges)


def _diff_side(text: str, prefix: str) -> Optional[str]:
    """The path of a `--- ` or `+++ ` line, None for /dev/null. Git ends the line with a tab when the name
    holds a space: that tab is no part of the name."""
    name = _c_unquoted(text[:-1] if text.endswith("\t") else text)
    if name == "/dev/null":
        return None
    return name[len(prefix):] if name.startswith(prefix) else name


def _diff_header_path(header: str) -> Optional[str]:
    """The path of a `diff --git a/<path> b/<path>` line that names one path twice; None when the two differ."""
    rest = header[len("diff --git "):]
    if rest.startswith('"'):
        end = 1
        while end < len(rest) and rest[end] != '"':
            end += 2 if rest[end] == "\\" else 1
        first, second = _c_unquoted(rest[:end + 1]), _c_unquoted(rest[end + 2:])
    else:
        half = (len(rest) - 1) // 2
        first, second = rest[:half], rest[half + 1:]
    if first.startswith("a/") and second.startswith("b/") and first[2:] == second[2:]:
        return first[2:]
    return None


def _c_unquoted(text: str) -> str:
    """A path as Git writes it in a patch: as it is, or between double quotes with the escapes of C when it
    holds a quote, a backslash or a control character."""
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    out: list[str] = []
    index, end = 1, len(text) - 1
    while index < end:
        if text[index] != "\\" or index + 1 >= end:
            out.append(text[index])
            index += 1
            continue
        octal = text[index + 1:index + 4]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            out.append(chr(int(octal, 8)))
            index += 4
        else:
            out.append(_C_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
    return "".join(out)


def _diff_functions(
    g: CodeGraph, changed_ranges: dict[str, Sequence[tuple[int, int]]],
) -> list[tuple[str, EntityFact, str]]:
    """(module::name, the entity, the file) of every definition a changed range touches. A file of the diff
    is the file of the graph with that very path, relative to the graph root. A definition runs to the line
    before the next one of its file, the last one to the end of the file."""
    file_entities: dict[str, list[tuple[str, EntityFact]]] = {}
    for mod, ent_list in g.entities.items():
        if ent_list:
            file_entities.setdefault(_impact_file(g, mod), []).extend((mod, ent) for ent in ent_list)

    results: list[tuple[str, EntityFact, str]] = []  # (qualified_id, entity, file)
    named: set[str] = set()
    for diff_file, ranges in changed_ranges.items():
        sorted_ents = sorted(file_entities.get(diff_file, ()), key=lambda x: x[1].line)
        for start, end in ranges:
            for idx, (mod, ent) in enumerate(sorted_ents):
                last = idx + 1 == len(sorted_ents)
                if end >= ent.line and (last or start < sorted_ents[idx + 1][1].line):
                    qid = f"{mod}::{ent.name}"
                    if qid not in named:
                        named.add(qid)
                        results.append((qid, ent, diff_file))
    return results


def _diff_file_fact(item: DiffFile) -> str:
    if item.status == "renamed":
        return f"(diff-file {_q(item.old_path)} :hunks {item.hunks} :status renamed :to {_q(item.path)})"
    return f"(diff-file {_q(item.path)} :hunks {item.hunks}{' :status deleted' if item.status == 'deleted' else ''})"


def cmd_diff_functions(g: CodeGraph, ref: str) -> int:
    """Print the definitions a git diff changes: the changed line ranges of `git diff <ref>`, mapped to the
    definitions of the graph. A file the diff removes or renames is named with `:status`. Returns the exit
    status: 0, 2 for a ref that is refused, 1 when Git cannot give the diff."""
    files = _diff_files(g, ref)
    if isinstance(files, str):
        print(files)
        return _diff_error_status(files)
    if not files:
        print(f"(diff-functions-empty {_q(ref)})")
        return 0

    results = _diff_functions(g, {item.path: item.ranges for item in files if item.status != "deleted"})
    listed = sorted(files, key=lambda item: item.old_path or item.path)
    if not results:
        # Report changed files even if no function mapping
        for item in listed:
            print(_diff_file_fact(item))
        print(f"(diff-functions-no-match :files {len(files)} :ref {_q(ref)})")
        return 0

    for qid, ent, _diff_path in sorted(results, key=lambda x: (x[2], x[1].line)):
        lang = g.module_lang(ent.module)
        fpath = g.module_to_file(ent.module)
        print(f"(diff-fn {_q(qid)} :kind {ent.kind} :line {ent.line} :lang {lang} :file {_q(fpath)})")
    for item in listed:
        if item.status in ("deleted", "renamed"):   # no file of the graph now: no definition can name it
            print(_diff_file_fact(item))
    print(f"(diff-functions-summary :changed {len(results)} :files {len(files)} :ref {_q(ref)})")
    return 0


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


def _query_problem(expr: str) -> Optional[str]:
    """What makes the text of a query unreadable, or None. It reads strings and comments as the tokenizer of
    _SchemeEval does, so a parenthesis inside a string or after a comment sign is text."""
    depth, deepest, index, holds_something = 0, 0, 0, False
    while index < len(expr):
        character = expr[index]
        if character == ";":
            while index < len(expr) and expr[index] != "\n":
                index += 1
            continue
        if character == '"':
            end = index + 1
            while end < len(expr) and expr[end] != '"':
                end += 2 if expr[end] == "\\" else 1
            if end >= len(expr):
                return "a string is not closed"
            holds_something = True
            index = end + 1
            continue
        if character == "(":
            depth += 1
            deepest = max(deepest, depth)
            holds_something = True
        elif character == ")":
            depth -= 1
            if depth < 0:
                return "a closing parenthesis has no opening one"
        elif not character.isspace():
            holds_something = True
        index += 1
    if not holds_something:
        return "the expression is empty"
    if depth:
        return "a parenthesis is not closed"
    if deepest > MAX_QUERY_NESTING:
        return f"the expression is nested deeper than {MAX_QUERY_NESTING} levels"
    return None


# The functions of the query language that work on a set given as their first argument.
_SET_FUNCTIONS = frozenset({"intersect", "diff", "filter", "files", "count", "layer-of"})


class _SchemeEval:
    """Tiny Scheme evaluator for code graph queries."""

    def __init__(self, g: CodeGraph, sexp_path: str) -> None:
        self._g = g
        self._sexp_path = sexp_path
        self.problems: list[str] = []   # what the last expressions asked for that the language does not have

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

        if fn in _SET_FUNCTIONS and not args:
            self.problems.append(f"{fn} takes a set as its first argument")
            return set()

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
            problem = _git_safe().ref_problem(ref)
            if problem is not None:
                return QueryError("invalid-argument", problem)
            cmd_diff_functions(g, ref)
            return set()

        self.problems.append(f"unknown function: {fn}")
        return set()

    def _collect_deps(
        self, nid: str, visited: set[str], max_depth: int, depth: int, result: set[str],
    ) -> None:
        # Level by level: a module is followed from the nearest depth that reaches it, so none within the
        # depth is left out because a longer path came first.
        frontier = [nid]
        for _depth in range(depth, max_depth + 1):
            found: list[str] = []
            for module in frontier:
                for tgt in _collect_dep_mods(self._g, module):
                    result.add(tgt)
                    if tgt not in visited:
                        visited.add(tgt)
                        found.append(tgt)
            frontier = found

    def _collect_rdeps(
        self, nid: str, visited: set[str], max_depth: int, depth: int, result: set[str],
    ) -> None:
        frontier = [nid]
        for _depth in range(depth, max_depth + 1):
            found: list[str] = []
            for module in frontier:
                for src in _collect_rdep_mods(self._g, module):
                    result.add(src)
                    if src not in visited:
                        visited.add(src)
                        found.append(src)
            frontier = found

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


def cmd_query(g: CodeGraph, expr: str, sexp_path: str) -> int:
    """Evaluate a single Scheme query expression; `(help)` lists the functions of the language. Returns the
    exit status: 2 when the expression gives Git a ref that is refused, else 0."""
    if expr.strip() in ("(help)", "help"):
        print(_QUERY_HELP)
        return 0
    problem = _query_problem(expr)
    if problem is not None:
        print(f'(error invalid-query "{_escaped(expr)}" "{problem}")')
        return 0
    evaluator = _SchemeEval(g, sexp_path)
    result = evaluator.eval_str(expr)
    if evaluator.problems:
        print(f'(error invalid-query "{_escaped(expr)}" "{evaluator.problems[0]}; (help) lists the functions")')
        return 0
    _format_result(result)
    return 2 if isinstance(result, QueryError) and result.kind == "invalid-argument" else 0


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

        problem = _query_problem(expr)
        if problem is not None:
            print(f"; {problem}")
            continue
        result = evaluator.eval_str(expr)
        for problem in evaluator.problems:
            print(f"; {problem}")
        evaluator.problems.clear()
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
    found, searched = _search_sexp_file(start)
    if found is not None:
        return found
    _print_no_project(searched)
    sys.exit(1)


def _search_sexp_file(start: Optional[str] = None) -> tuple[Optional[str], list[str]]:
    """The graph of the nearest folder at or above start, or None with the paths looked at."""
    current = os.path.abspath(start or os.getcwd())
    searched: list[str] = []
    while True:
        candidate = os.path.join(current, WINGBOX_DIRECTORY, INDEX_FILE)
        if os.path.isfile(candidate):
            return candidate, searched
        searched.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            return None, searched
        current = parent


def _print_no_project(searched: list[str]) -> None:
    shown = searched[:6] + ([f"... {len(searched) - 6} more"] if len(searched) > 6 else [])
    print(
        f"{NO_PROJECT}\n"
        f"  looked for: {', '.join(shown)}\n"
        "  or point at a graph: --map /path/to/project/.wingbox/index.sexp --root /path/to/project",
        file=sys.stderr,
    )


def _is_strict_boundaries(args: list[str]) -> bool:
    """True for `boundaries ... --strict`: the command is the first argument besides --map, --root and their values."""
    words: list[str] = []
    index = 0
    while index < len(args):
        if args[index] in ("--map", "--root"):
            index += 2
            continue
        words.append(args[index])
        index += 1
    return words[:1] == ["boundaries"] and "--strict" in words


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
    # `grep` and `query` say what is wrong with an empty text in their own words.
    if args[0] not in ("grep", "query") and any(not argument.strip() for argument in args[1:]):
        return f"{args[0]}: an argument is empty"
    pattern_commands = {
        "entity",
        "search",
        "find_function",
        "find_type",
        "grep_functions",
        "files",
    }
    if args[0] in pattern_commands and len(args) >= 2:
        pattern_result = parse_search_pattern(args[1])
        if isinstance(pattern_result, SearchPatternErr):
            return f"invalid pattern {args[1]}: {pattern_result.error}"

    if args[0] == "impact":
        # The options of `impact` that are no form (--only, --diff, --summary) are read by impact_options;
        # what is checked below is the rest. The MCP server checks its calls here too.
        options = impact_options(args[1:])
        if isinstance(options, str):
            return options
        args = ["impact", *options[0]]

    if args[0] in ("usages", "impact"):
        unknown = [argument for argument in args[1:] if argument.startswith("--")]
        if unknown:
            more = ", --only tests, --diff [ref] and --summary" if args[0] == "impact" else ""
            return f"unknown option {unknown[0]}: {args[0]} takes --human, --json, --sexp and --all{more}"

    if args[0] == "grep":
        asked = _grep_arguments(args[1:])
        if isinstance(asked, str):
            return asked
        text_result = parse_search_pattern(asked[0], text=True)
        if isinstance(text_result, SearchPatternErr):
            return f"invalid pattern {asked[0]}: {text_result.error}"
        if asked[1] is not None:
            path_result = parse_search_pattern(asked[1])
            if isinstance(path_result, SearchPatternErr):
                return f"invalid pattern {asked[1]}: {path_result.error}"
        return None

    if args[0] == "source":
        if len(args) < 2:
            return "source requires a file path, a module ID or a name: <file>, Name or <file>::Name"
        if len(args) > 4:
            return "source takes a target and at most two line numbers: first and last"
        numbers = args[2:4]
        if not all(number.isdecimal() and int(number) >= 1 for number in numbers):
            return "source: first and last are line numbers from 1"
        if len(numbers) == 2 and int(numbers[1]) < int(numbers[0]):
            return "source: last is before first"
        return None

    if args[0] == "usages":
        if len(args) < 2:
            return "usages requires a symbol: Name, <file>::Name or <file>::Class.method"
        return None

    if args[0] == "impact":
        targets, depth = impact_arguments(args[1:])
        if not targets:
            return "impact requires a file path or module ID"
        if depth is not None:
            _impact_depth, impact_depth_error = _parse_depth(depth)
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


_WHOLE_NUMBER = re.compile(r"-?\d+\Z")
DEFAULT_IMPACT_DEPTH = 2
# The option that names a form of the answer, and the form; without one the form is "sexp".
ANSWER_FORMS = {"--human": "human", "--json": "json", "--sexp": "flat"}
# The forms whose lists stop at their limits unless --all: what people read, and what 0.13 printed.
LIMITED_FORMS = frozenset({"human", "flat"})


def impact_arguments(positional: Sequence[str]) -> tuple[list[str], Optional[str]]:
    """The files asked and the depth as written: of several arguments a last whole number is the depth."""
    if len(positional) >= 2 and _WHOLE_NUMBER.match(positional[-1]):
        return list(positional[:-1]), positional[-1]
    return list(positional), None


def answer_form(args: Sequence[str]) -> tuple[str, list[str], Optional[str]]:
    """(form, the other arguments, error): "sexp" unless --human, --json or --sexp is given; two of them at
    once are an error."""
    asked = list(dict.fromkeys(argument for argument in args if argument in ANSWER_FORMS))
    rest = [argument for argument in args if argument not in ANSWER_FORMS]
    if len(asked) > 1:
        return "sexp", rest, f"{' and '.join(asked)} are two forms of one answer: give one"
    return (ANSWER_FORMS[asked[0]] if asked else "sexp"), rest, None


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


def main(args: Optional[list[str]] = None, graph_stale: Optional[int] = None) -> int:
    """Run one query command. graph_stale is the number of changed files the graph is known to miss, when the
    caller checked (the `wingbox` command does); `impact` reports it."""
    raw_args = list(sys.argv[1:] if args is None else args)
    form, raw_args, form_error = answer_form(raw_args)
    words, _map, _root, path_error = _extract_query_paths(raw_args)
    # The help text and the interactive prompt are not answers: they are printed as they are.
    no_answer = form_error is None and path_error is None and words[:1] in ([], ["-h"], ["--help"], ["help"], ["repl"])
    if form != "json" or no_answer:
        status, _document = _answer(raw_args, form, form_error, graph_stale)
        return status
    # JSON: one document. `impact` and `usages` build theirs; the fact lines of any other answer become objects.
    # A call that fails as a whole says so in one place: `error` is the first key of the document.
    lines = io.StringIO()
    document: Optional[dict] = None
    try:
        with contextlib.redirect_stdout(lines):
            status, document = _answer(raw_args, form, form_error, graph_stale)
    except SystemExit as stop:       # no graph to read: the message for people went to standard error
        status = stop.code if isinstance(stop.code, int) else 1
    if document is None:
        document = {"facts": fact_json.facts_json(lines.getvalue())}
    print(fact_json.render(fact_json.with_error(document, ("no-graph", NO_PROJECT) if status else None)))
    return status


def _answer(raw_args: list[str], form: str, form_error: Optional[str], graph_stale: Optional[int]) -> tuple[int, Optional[dict]]:
    """Run a command in the form asked: (exit status, the JSON document of a command that builds its own).
    Everything else is printed as it is found."""
    # boundaries --strict: every way out prints a boundaries-result line; a check that could not run exits 3.
    strict = _is_strict_boundaries(raw_args)
    if strict:
        raw_args = [argument for argument in raw_args if argument != "--strict"]
    args, explicit_map, explicit_root, option_error = _extract_query_paths(raw_args)
    option_error = option_error or form_error
    if option_error is not None:
        print(f"(error invalid-argument {_q(option_error)})")
        return (_strict_error("invalid-argument") if strict else 2), None
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0, None
    # The lists of the text for people and of the flat facts stop at their limits unless --all; the default
    # form and JSON are complete.
    show_all = "--all" in args or form not in LIMITED_FORMS
    args = [argument for argument in args if argument != "--all"]
    only, diff_ref, summary = "", None, False
    if args and args[0] == "impact":
        options = impact_options(args[1:])
        if isinstance(options, str):
            print(f"(error invalid-argument {_q(options)})")
            return 2, None
        rest, only, diff_ref, summary = options
        if summary and form == "flat":
            print(f"(error invalid-argument {_q('--summary has no --sexp form: use it alone, with --json or with --human')})")
            return 2, None
        args = ["impact", *rest]

    # `impact --diff` names its files itself: it needs no file argument.
    command_error = None if diff_ref is not None and len(args) == 1 else _validate_cli_command(args)
    if command_error is not None:
        print(f"(error invalid-argument {_q(command_error)})")
        return 2, None

    if strict and not explicit_map:
        found, searched = _search_sexp_file(explicit_root)
        if found is None:
            _print_no_project(searched)
            return _strict_error("no-graph"), None
    sexp_path = explicit_map or find_sexp_file(explicit_root)
    g = CodeGraph()
    focus = _load_focus(args)
    load_result = g.load(sexp_path, explicit_root, focus=focus)
    if isinstance(load_result, MapLoadOk) and focus is not None and args[0] == "entity" \
            and not g.definitions_named(next(argument for argument in args[1:] if argument != "--positions")):
        # No name is spelled as asked: `entity` then matches names by pattern, the letter case ignored
        # (`entity engine` finds Engine), and the facts read for the one name do not hold them.
        load_result = g.load(sexp_path, explicit_root)
    if isinstance(load_result, MapLoadErr):
        print(f"(error invalid-map {_q(load_result.error)})")
        return (_strict_error("invalid-graph") if strict else 1), None

    cmd = args[0]
    if cmd == "impact" and diff_ref is not None:
        # The files a diff changes are the files asked; a number after the options is the depth.
        depth_word = next((argument for argument in args[1:] if _WHOLE_NUMBER.match(argument)), None)
        max_depth = DEFAULT_IMPACT_DEPTH if depth_word is None else int(depth_word)
        changes = diff_changes(g, diff_ref, only)
        if isinstance(changes, str):
            print(changes)
            return _diff_error_status(changes), None
        targets, changed, gone = changes
        # The empty form is for a diff in which Git reports no change below the graph root, and for no other.
        if not targets and not gone and form != "json":
            print(f"(impact-diff-empty {_q(diff_ref)})")
            return 0, None
        status, document = 0, None
        if not targets:
            document = impact_forms.impact_json([], graph_header(g, graph_stale), changed) if form == "json" else None
        elif summary:
            status, document = cmd_impact_summary(g, targets, max_depth, form, graph_stale, only, changed)
        elif form == "json":
            status, document = impact_document(g, targets, max_depth, graph_stale, only, changed)
        else:
            status = cmd_impact(g, targets, max_depth, form, show_all, graph_stale, only, changed)
        # The paths the diff removes follow the answers of the files that are there.
        if document is not None:
            document["changed_files"] = [impact_forms.changed_file_json(item) for item in gone]
        elif form == "human":
            sys.stdout.write(("\n" if targets and gone else "") + impact_forms.changed_files_text(gone))
        else:
            for item in gone:
                print(impact_forms.changed_file_fact(item))
        return status, document
    if cmd == "impact" and len(args) >= 2:
        targets, depth = impact_arguments(args[1:])
        max_depth = DEFAULT_IMPACT_DEPTH if depth is None else int(depth)
        if summary:
            return cmd_impact_summary(g, targets, max_depth, form, graph_stale, only)
        if form == "json":
            return impact_document(g, targets, max_depth, graph_stale, only)
        return cmd_impact(g, targets, max_depth, form, show_all, graph_stale, only), None
    if cmd == "usages" and len(args) >= 2:
        if form == "json":
            answer = usages_answer(g, args[1])
            return _usages_status(g, args[1], answer), usages_forms.usages_json(answer)
        return cmd_usages(g, args[1], form, show_all), None
    g.gate.stale_files = graph_stale   # boundaries --strict: a graph known to miss changes is not a pass
    return _run_fact_command(g, args, sexp_path, show_all, strict), None


def _run_fact_command(g: CodeGraph, args: list[str], sexp_path: str, show_all: bool, strict: bool) -> int:
    """Run a command whose answer is a list of fact lines; they are printed as they are found."""
    cmd = args[0]
    if cmd == "analyze" and len(args) >= 2:
        return cmd_analyze(g, args[1], show_all=show_all)
    elif cmd == "boundary" and len(args) >= 2:
        files_only = "--files-only" in args
        module_arg = [a for a in args[1:] if a != "--files-only"][0]
        return cmd_boundary(g, module_arg, files_only=files_only)
    elif cmd == "quick" and len(args) >= 2:
        cmd_quick(g, args[1], sexp_path, show_all=show_all)
    elif cmd == "module" and len(args) >= 2:
        module_args = [argument for argument in args[1:] if argument != "--lines"]
        if not module_args:
            print(f"(error invalid-argument {_q('module requires a module ID')})")
            return 2
        return cmd_module(g, module_args[0], lines="--lines" in args)
    elif cmd == "deps" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        return cmd_deps(g, args[1], depth)
    elif cmd == "rdeps" and len(args) >= 2:
        depth = int(args[2]) if len(args) >= 3 else 2
        return cmd_rdeps(g, args[1], depth)
    elif cmd == "entity" and len(args) >= 2:
        entity_args = [argument for argument in args[1:] if argument != "--positions"]
        cmd_entity(g, entity_args[0], positions="--positions" in args)
    elif cmd == "flow" and len(args) >= 3:
        return cmd_flow(g, args[1], args[2])
    elif cmd == "search" and len(args) >= 2:
        search_args = [argument for argument in args[1:] if argument != "--lines"]
        if not search_args:
            print(f"(error invalid-argument {_q('search requires a pattern')})")
            return 2
        cmd_search(g, search_args[0], sexp_path, lines="--lines" in args)
    elif cmd == "boundaries":
        target = args[1] if len(args) >= 2 else "all"
        return cmd_boundaries(g, target, strict=strict) or 0
    elif cmd == "layers":
        cmd_layers(g)
    elif cmd == "cycles":
        cmd_cycles(g, args[1] if len(args) >= 2 else "")
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
        return cmd_diff_functions(g, ref)
    elif cmd == "files":
        cmd_files(g, args[1] if len(args) >= 2 else None)
    elif cmd == "source" and len(args) >= 2:
        cmd_source(g, args[1], *(int(number) for number in args[2:4]))
    elif cmd == "grep":
        asked = _grep_arguments(args[1:])
        if not isinstance(asked, str):   # _validate_cli_command refused anything else
            cmd_grep(g, *asked)
    elif cmd == "query" and len(args) >= 2:
        return cmd_query(g, " ".join(args[1:]), sexp_path)
    elif cmd == "repl":
        cmd_repl(g, sexp_path)
    else:
        print(f"(error unknown-command {_q(cmd if args else 'none')})")
        print("; Commands: analyze, boundary, quick, boundaries, layers, invariants, module, deps, rdeps, impact, entity, flow, search,")
        print(";          find_function, find_type, find_callchain, find_callers, find_calls, grep_functions, diff_functions,")
        print(";          files, source, grep, query, repl")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
