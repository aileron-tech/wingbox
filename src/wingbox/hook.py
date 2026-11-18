"""`wingbox hook pre-search`: the Claude Code PreToolUse hook on Grep, Glob and Read.

Claude Code writes the hook input as JSON on stdin (tool_name, tool_input, cwd).
When the project has a Wingbox graph, the hook prints one JSON object whose
hookSpecificOutput.additionalContext names, in S-expressions:

- for Grep and Glob, the modules whose path matches the pattern and the
  definitions whose name matches it, each module with its direct dependents;
- for Read, the module of the file, its direct dependents and the hint to run
  `wingbox impact` before changing it.

The context is capped at CONTEXT_LIMIT characters. Without a graph, or for a
pattern that names nothing in it, the hook prints nothing. It never blocks the
tool: it always exits 0, and an error goes to stderr, which Claude Code does
not show to the model for exit code 0. No network.

Before it reads the graph, the hook brings it up to date
(freshness.ensure_graph_fresh): it may start a refresh of the graph in a
detached process and wait for it up to WINGBOX_HOOK_WAIT seconds. When the
graph still misses known changes it says so, `(wingbox-graph-stale ...)`, and
gives no context from it. `wingbox hook dirty` (PostToolUse on Edit and Write)
marks the edited file.

The graph is read as text with regular expressions, not with the query
engine, so that a graph of 8,000 files is answered in a fraction of a second.

A tool call made from a script (Claude Code Code Mode) gets no context: a
script may make hundreds of calls and each context block is put into the
model's context beside the script's result. And the hook ends itself, silently
and with exit code 0, when it has not answered within BUDGET_SECONDS: Claude
Code refuses a call made from code when a matching hook does not finish.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Union

from wingbox import freshness
from wingbox.facts import atom

# Files the builder parses (scripts/build_code_graph.py LANGUAGES; a test keeps them equal).
SOURCE_EXTENSIONS = frozenset({
    ".cjs", ".cs", ".cts", ".dart", ".go", ".inc", ".java", ".js", ".jsx", ".lpr", ".mjs", ".mts",
    ".pas", ".php", ".pp", ".py", ".rake", ".rb", ".rs", ".ts", ".tsx",
})
PROJECT_MANIFESTS = (
    "pyproject.toml", "setup.py", "package.json", "tsconfig.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml",
)
_SCAN_SKIP = frozenset({"node_modules", "venv", "__pycache__", "dist", "build", "target", "vendor", "obj", "coverage"})
_SCAN_ENTRIES = 5000   # the no-graph check looks at no more folder entries than this
_SCAN_DEPTH = 4
HINT_MARKER_DAYS = 7
NO_GRAPH_HINT = ("no Wingbox graph here yet: run `wingbox build` in this repository once (a few seconds), "
                 "then impact / usages / layers answer from it")

CONTEXT_LIMIT = 1500
# Observed on Claude Code 2.1.327 with Code Mode, 2026-11-18 (not documented): a tool call made from a script
# has a tool_use_id that starts with "repl_"; a call the model makes itself has "toolu_...". An absent id or
# any other form is treated as a call the model made itself.
CODE_CALL_ID_PREFIX = "repl_"
# Claude Code gives the hook 10 s (install.HOOK_TIMEOUT_SECONDS). A hook that has not finished by then lets a
# direct call run, but refuses a call made from code; so the hook stops itself well before that.
BUDGET_SECONDS = 5.0
_SHOWN_MODULES = 6
_SHOWN_DEFINITIONS = 6
_RANKED_CANDIDATES = 40  # best name matches whose dependents are counted to order the answer
_EDGE_KINDS = frozenset(("imports", "calls-api", "data-flow", "refs", "calls", "http-calls"))
_ENTITY_KINDS = frozenset(("fn", "cls", "mod", "ifc", "typ", "enm"))
_NODE_LINE = re.compile(r"\n\(node (\S+) ")
_DEFINITION_LIMIT = 300  # definitions verified per answer; the count is given as "300+" beyond it
_HIT_LIMIT = 5000  # pattern hits looked at in the entity section
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_COMMON_WORDS = frozenset(
    "class def function const export import from return async await self this public private static "
    "struct impl interface type enum module package void string".split()
)
_FOLDER_MODULE_FILES = ("index", "__init__", "mod")


@dataclass(frozen=True)
class PatternQuery:
    regex: "re.Pattern[str]"
    shown: str
    under: str  # path prefix relative to the project root; "" for all


@dataclass(frozen=True)
class FileQuery:
    path: str


Query = Union[PatternQuery, FileQuery]


@dataclass(frozen=True)
class LightGraph:
    """The graph text with the offsets of its sections; nothing is parsed beyond paths and nodes."""

    root: str
    file: str
    text: str
    paths: dict[str, str]  # short ID -> module path
    nodes: frozenset[str]  # short IDs of the modules
    entities_at: int
    edges_at: int

    def module(self, short_id: str) -> str:
        return self.paths.get(short_id, short_id)


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str
    module: str  # short ID
    line: str


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def smart_case(pattern: str, ignore_case_flag: bool) -> bool:
    """Whether a Grep pattern matches without case: the tool's -i flag, or no upper-case letter in the
    pattern outside its escapes (\\S, \\W, \\D do not count), as `wingbox quick` and ripgrep's smart case do."""
    return ignore_case_flag or not any(char.isupper() for char in re.sub(r"\\.", "", pattern))


def _compile(pattern: str, ignore_case: bool) -> "re.Pattern[str]":
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(pattern, flags)
    except re.error:
        return re.compile(re.escape(pattern), flags)


def _glob_keyword(pattern: str) -> str:
    """The literal part of a Glob's last segment before its extension: '**/*order*.ts' -> 'order'."""
    segment = pattern.replace("\\", "/").rstrip("/").split("/")[-1]
    stem = segment.split(".")[0]
    return re.sub(r"[*?\[\]{}!]", "", stem)


def read_query(payload: object, cwd: str) -> tuple[Optional[Query], str]:
    """The query of a hook input and the folder to look for the graph from."""
    if not isinstance(payload, dict):
        return None, cwd
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    local_path = freshness.portable().local_path   # paths from Claude Code in this system's form
    base = local_path(payload["cwd"]) if isinstance(payload.get("cwd"), str) and payload["cwd"] else cwd
    if not isinstance(tool_input, dict):
        return None, base
    path = tool_input.get("path")
    path = local_path(path) if isinstance(path, str) and path else None
    start = os.path.join(base, path) if path else base
    if tool == "Read":
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return None, base
        full = os.path.join(base, local_path(file_path))
        return FileQuery(full), os.path.dirname(full)
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None, base
    under = start if path else ""
    if tool == "Grep":
        return PatternQuery(_compile(pattern, smart_case(pattern, bool(tool_input.get("-i")))), pattern, under), start
    if tool == "Glob":
        keyword = _glob_keyword(pattern)
        if len(keyword) < 3:
            return None, start
        return PatternQuery(re.compile(re.escape(keyword), re.IGNORECASE), keyword, under), start
    return None, base


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def find_graph(start: str) -> Optional[tuple[str, str]]:
    """(project root, graph file): the nearest folder at or above start that holds .wingbox/index.sexp."""
    root = freshness.find_project(start)
    return None if root is None else (root, freshness.graph_path(root))


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return token


def parse_paths(text: str) -> dict[str, str]:
    """Short ID -> module path, from the `def` lines and the `(paths ...)` tree."""
    paths: dict[str, str] = {}
    stack: list[str] = []
    in_paths = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_paths:
            if stripped.startswith("(def "):
                short_id, _, value = stripped[5:-1].partition(" ")
                paths[short_id] = _unquote(value.strip())
            elif stripped == "(paths":
                in_paths = True
            elif stripped.startswith("(node "):
                break
            continue
        if stripped == ")":
            if not stack:
                break
            stack.pop()
            continue
        if not stripped.startswith("("):
            continue
        inner = stripped[1:]
        if inner.endswith(")"):
            short_id, _, leaf = inner[:-1].partition(" ")
            leaf = _unquote(leaf.strip())
            if leaf == ":self":
                paths[short_id] = "/".join(stack) if stack else short_id
            elif leaf:
                paths[short_id] = "/".join(stack + [leaf])
        else:
            stack.append(_unquote(inner.strip()))
    return paths


def _section(text: str, marker: str, default: int) -> int:
    at = text.find(marker)
    return default if at < 0 else at


def load_graph(root: str, graph_file: str) -> LightGraph:
    with open(graph_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    nodes_at = _section(text, "\n; --- nodes ---", 0)
    entities_at = _section(text, "\n; --- entities ---", nodes_at)
    edges_at = _section(text, "\n; --- edges ---", entities_at)
    node_end = entities_at if entities_at > nodes_at else len(text)
    nodes = frozenset(_NODE_LINE.findall(text, nodes_at, node_end))
    return LightGraph(root, graph_file, text, parse_paths(text[:nodes_at or len(text)]), nodes, entities_at, edges_at)


def _line_at(text: str, position: int) -> tuple[int, str]:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return start, text[start : len(text) if end < 0 else end]


def dependents(graph: LightGraph, short_ids: set[str]) -> dict[str, int]:
    """Distinct modules that depend on each module (any relation, the module itself excluded).

    One regular expression of the module IDs jumps to the edge lines that name
    them; each line is then checked to name the module as the target.
    """
    sources: dict[str, set[str]] = {short_id: set() for short_id in short_ids}
    if not short_ids:
        return {}
    names = "|".join(re.escape(short_id) for short_id in sorted(short_ids, key=len, reverse=True))
    text = graph.text
    for match in re.compile(r" (" + names + r")(?=[ ):])").finditer(text, graph.edges_at):
        target = match.group(1)
        _start, line = _line_at(text, match.start())
        parts = line[1:].split(" ", 3)
        if len(parts) >= 3 and parts[0] in _EDGE_KINDS and parts[2].rstrip(")").split("::", 1)[0] == target:
            source = parts[1].split("::", 1)[0]
            if source != target:
                sources[target].add(source)
    return {short_id: len(found) for short_id, found in sources.items()}


def definitions(graph: LightGraph, regex: "re.Pattern[str]", under: str) -> tuple[list[Definition], bool]:
    """Definitions whose name matches; the pattern runs over the entity section, then each hit line is checked."""
    found: list[Definition] = []
    seen: set[int] = set()
    text = graph.text
    for hits, match in enumerate(regex.finditer(text, graph.entities_at, graph.edges_at)):
        if hits >= _HIT_LIMIT or len(found) >= _DEFINITION_LIMIT:
            return found, True
        start, line = _line_at(text, match.start())
        if start in seen:
            continue
        seen.add(start)
        parts = line[1:].split(" ", 4)
        if len(parts) < 4 or parts[0] not in _ENTITY_KINDS or not regex.search(parts[2]):
            continue
        module = parts[1].split("::", 1)[0]
        if graph.module(module).startswith(under):
            found.append(Definition(parts[2], parts[0], module, parts[3].rstrip(")")))
    return found, False


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _relative(graph: LightGraph, path: str) -> Optional[str]:
    return freshness.portable().relative_posix(path, graph.root)


def _file_module(graph: LightGraph, relative: str) -> Optional[str]:
    """Short ID of the module of a file: the path as given, without its extension, or its folder."""
    by_path = {path: short_id for short_id, path in graph.paths.items()}
    stem, _extension = os.path.splitext(relative)
    candidates = [relative, stem]
    folder, name = os.path.split(stem)
    if name in _FOLDER_MODULE_FILES and folder:
        candidates.append(folder)
    for candidate in candidates:
        short_id = by_path.get(candidate, candidate)
        if short_id in graph.nodes:
            return short_id
    return None


def _graph_name(graph: LightGraph) -> str:
    return os.path.relpath(graph.file, graph.root).replace(os.sep, "/")


def answer_file(graph: LightGraph, query: FileQuery) -> list[str]:
    relative = _relative(graph, query.path)
    if not relative:
        return []
    short_id = _file_module(graph, relative)
    if short_id is None:
        return []
    count = dependents(graph, {short_id})[short_id]
    lines = [f"(wingbox-file {atom(relative)} :module {atom(graph.module(short_id))} :dependents {count})"]
    if count:
        lines.append(f'(wingbox-hint "before changing it: wingbox impact {relative}")')
    return lines


def _search_word(query: PatternQuery) -> Optional["re.Pattern[str]"]:
    """A literal word of the pattern ('def\\s+create_order' -> 'create_order') to retry with."""
    words = [word for word in _WORD.findall(query.shown) if word.lower() not in _COMMON_WORDS]
    if not words:
        return None
    longest = max(words, key=len)
    if longest == query.shown:
        return None
    return re.compile(re.escape(longest), query.regex.flags & re.IGNORECASE)


def _matches(graph: LightGraph, regex: "re.Pattern[str]", under: str) -> tuple[list[str], list[Definition], bool]:
    paths = graph.paths
    modules = [
        short_id
        for short_id in graph.nodes
        if regex.search(paths.get(short_id, short_id)) and paths.get(short_id, short_id).startswith(under)
    ]
    found, more = definitions(graph, regex, under)
    return modules, found, more


def _module_rank(graph: LightGraph, regex: "re.Pattern[str]", short_id: str) -> tuple[int, int, str]:
    path = graph.module(short_id)
    return (0 if regex.search(path.rsplit("/", 1)[-1]) else 1, len(path), path)


def _definition_rank(shown: str, definition: Definition) -> tuple[int, int, str]:
    name, wanted = definition.name.lower(), shown.lower()
    closeness = 0 if name == wanted else 1 if name.startswith(wanted) else 2
    return (closeness, len(definition.name), definition.name)


def answer_pattern(graph: LightGraph, query: PatternQuery) -> list[str]:
    under = ""
    if query.under:
        relative = _relative(graph, query.under)
        if relative is None:
            return []
        under = os.path.splitext(relative)[0] if os.path.isfile(query.under) else relative
    regex = query.regex
    modules, found, more = _matches(graph, regex, under)
    if not modules and not found:
        retry = _search_word(query)
        if retry is None:
            return []
        regex = retry
        modules, found, more = _matches(graph, regex, under)
        if not modules and not found:
            return []
    module_candidates = sorted(modules, key=lambda short_id: _module_rank(graph, regex, short_id))[:_RANKED_CANDIDATES]
    definition_candidates = sorted(found, key=lambda item: _definition_rank(query.shown, item))[:_RANKED_CANDIDATES]
    counts = dependents(graph, set(module_candidates) | {item.module for item in definition_candidates})
    # Among the best name matches, the most depended-on come first.
    shown_modules = sorted(module_candidates, key=lambda short_id: -counts[short_id])[:_SHOWN_MODULES]
    shown_definitions = sorted(
        definition_candidates,
        key=lambda item: (counts[item.module] == 0, _definition_rank(query.shown, item)[0], -counts[item.module]),
    )[:_SHOWN_DEFINITIONS]
    total = f"{len(found)}+" if more else str(len(found))
    lines = [f"(wingbox-match {atom(query.shown)} :modules {len(modules)} :definitions {total})"]
    for short_id in shown_modules:
        lines.append(f"(module {atom(graph.module(short_id))} :dependents {counts[short_id]})")
    for item in shown_definitions:
        lines.append(
            f"(definition {atom(item.name)} :kind {item.kind} :module {atom(graph.module(item.module))}"
            f" :line {item.line} :dependents {counts[item.module]})"
        )
    lines.append('(wingbox-hint "wingbox analyze <module> for boundary files; wingbox impact <file> before a change")')
    return lines


def capped(lines: list[str], limit: int = CONTEXT_LIMIT) -> str:
    """Whole lines up to limit characters; a last line says how many were left out."""
    kept: list[str] = []
    size = 0
    for index, line in enumerate(lines):
        left = len(lines) - index
        more = f"(wingbox-more :lines {left})"
        reserve = len(more) + 1 if left > 1 else 0
        if size + len(line) + 1 + reserve > limit:
            kept.append(more)
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


def _has_source(root: str) -> bool:
    """A file of a supported language within a few levels of root (bounded: the hook's time budget)."""
    folders, seen = [(root, 0)], 0
    while folders:
        folder, depth = folders.pop(0)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > _SCAN_ENTRIES:
                return False
            if entry.name.startswith(".") or entry.name in _SCAN_SKIP:
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth + 1 < _SCAN_DEPTH:
                    folders.append((entry.path, depth + 1))
            elif os.path.splitext(entry.name)[1].lower() in SOURCE_EXTENSIONS:
                return True
    return False


def source_repository(start: str, home: str) -> Optional[str]:
    """The root of the source repository around start: the nearest folder at or above start with .git or a
    project manifest, holding files of a supported language. None for the home folder, a folder that is in
    no repository, or a repository of documents only."""
    folder = os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start))
    home = os.path.normcase(os.path.abspath(home))
    while os.path.normcase(folder) != home:
        if os.path.exists(os.path.join(folder, ".git")) or any(os.path.isfile(os.path.join(folder, name)) for name in PROJECT_MANIFESTS):
            return folder if _has_source(folder) else None
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent
    return None


def first_hint(home: str, session: str, root: str) -> bool:
    """True the first time a session meets root without a graph: a marker under ~/.wingbox/hook-hints (never in
    the project, which Wingbox has not built), pruned after HINT_MARKER_DAYS days."""
    folder = os.path.join(home, ".wingbox", "hook-hints")
    marker = os.path.join(folder, hashlib.sha1(f"{session}\0{root}".encode("utf-8")).hexdigest()[:20])
    if os.path.exists(marker):
        return False
    try:
        os.makedirs(folder, exist_ok=True)
        cutoff = time.time() - HINT_MARKER_DAYS * 86400
        for entry in os.scandir(folder):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
        with open(marker, "w", encoding="utf-8"):
            pass
    except OSError as error:   # the hint is still given; it may come again
        print(f"[wingbox] hook: cannot record the hint: {error}", file=sys.stderr)
    return True


def no_graph_context(payload: object, start: str) -> str:
    """The one-time hint for a source repository that has no graph yet; "" otherwise."""
    from pathlib import Path

    home = str(Path.home())
    root = source_repository(start, home)
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if root is None or not first_hint(home, session if isinstance(session, str) else "", root):
        return ""
    return f"(wingbox-no-graph :root {atom(Path(root).as_posix())} :hint {atom(NO_GRAPH_HINT)})"


def is_call_from_code(payload: object) -> bool:
    """True for a tool call that a script made (Code Mode), judged by the form of its tool_use_id."""
    call_id = payload.get("tool_use_id") if isinstance(payload, dict) else None
    return isinstance(call_id, str) and call_id.startswith(CODE_CALL_ID_PREFIX)


def context_for(payload: object, cwd: str) -> str:
    if is_call_from_code(payload):
        return ""
    query, start = read_query(payload, cwd)
    if query is None:
        return ""
    found = find_graph(start)
    if found is None:
        return no_graph_context(payload, start)
    fresh = freshness.ensure_graph_fresh(found[0], freshness.hook_wait())
    note = freshness.stale_line(fresh, found[0])
    if freshness.withholds_context(fresh):
        return note  # the graph misses known changes: no context from it
    graph = load_graph(*found)
    lines = answer_file(graph, query) if isinstance(query, FileQuery) else answer_pattern(graph, query)
    if not lines:
        return note
    return capped([f"(wingbox-graph {atom(_graph_name(graph))})"] + ([note] if note else []) + lines)


def hook_output(context: str) -> str:
    if not context:
        return ""
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
    )


def run_pre_search(stdin_text: str, cwd: str) -> str:
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else None
    except json.JSONDecodeError:
        return ""
    return hook_output(context_for(payload, cwd))


_GIT_COMMAND = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:switch|checkout|pull|merge|rebase|reset|stash\s+pop)\b")


def background_refresh(stdin_text: str, cwd: str, kind: str) -> None:
    """The SessionStart hook (kind "session-start") and the hook after a Bash call (kind "git"): when the project
    of the session has a graph that misses changes, start its refresh in the background. Silent."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    if kind == "git":
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if payload.get("tool_name") != "Bash" or not isinstance(command, str) or not _GIT_COMMAND.search(command):
            return
    start = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload["cwd"] else cwd
    project = freshness.find_project(freshness.portable().local_path(start))
    if project is not None:
        freshness.refresh_in_background(project)


def main(args: list[str]) -> int:
    """Entry point of `wingbox hook pre-search|dirty|session-start|git`: always exit 0 so nothing is ever blocked."""
    if args[:1] in (["session-start"], ["git"]):
        try:
            background_refresh(sys.stdin.read(), os.getcwd(), args[0])
        except Exception as error:  # a hook must never block or break the session or the tool call
            print(f"[wingbox] hook {args[0]}: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    if args[:1] == ["dirty"]:
        try:
            freshness.run_post_edit(sys.stdin.read())
        except Exception as error:  # the hook must never block or break the tool call
            print(f"[wingbox] hook dirty: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    if args[:1] != ["pre-search"]:
        print("usage: wingbox hook pre-search|dirty|session-start|git  (reads the Claude Code hook input on stdin)",
              file=sys.stderr)
        return 0
    # Out of time: end the process at once, with nothing printed and exit code 0 (the tool call then runs).
    out_of_time = threading.Timer(BUDGET_SECONDS, os._exit, args=(0,))
    out_of_time.daemon = True
    out_of_time.start()
    try:
        output = run_pre_search(sys.stdin.read(), os.getcwd())
    except Exception as error:  # the hook must never block or break the tool call
        print(f"[wingbox] hook pre-search: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    finally:
        out_of_time.cancel()   # an answer that is ready is printed whole
    if output:
        sys.stdout.write(output + "\n")
    return 0
