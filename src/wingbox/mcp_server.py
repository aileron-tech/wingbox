"""MCP server for Wingbox: `wingbox mcp [--root DIR] [--build-missing]`.

Serves the code graph of one repository to an MCP client over stdio:
newline-delimited JSON-RPC 2.0 on stdin and stdout, logs on stderr, no network.
`wingbox mcp --http` hands over to the separate package wingbox-http, which
serves the same `handle_line` over the Streamable HTTP transport.

Protocol revisions:
- 2026-07-28 (current): stateless; every request carries its protocol version
  and client capabilities in `params._meta`; `server/discover` lists the
  supported versions; results carry `resultType`.
- 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05: the `initialize` handshake.
  A request without `_meta` protocol fields is served under these revisions,
  which is what most clients speak today.

Each tool is a thin call into the functions behind the `wingbox` command
(`build`, `impact`, `analyze`, ...); the answer is the command's output.
The loaded graph is kept between calls and reloaded when the file changes.
Before each tool reads it, the graph is brought up to date with the files
changed since it was built (freshness.ensure_graph_fresh); an answer from a
graph that may miss changes starts with `(wingbox-graph-stale ...)`.

With --build-missing (the Claude Desktop extension passes it, for the folder
the user picked) the server starts `wingbox build DIR` in a child process when
DIR has no graph; tool calls wait for it up to WINGBOX_FIRST_BUILD_WAIT seconds
and then say that the first build is still running.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import IO, Any, Optional

from wingbox import cli, freshness

MODERN_VERSIONS = ("2026-07-28",)
HANDSHAKE_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = MODERN_VERSIONS + HANDSHAKE_VERSIONS

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

ANSWER_LINE_LIMIT = 200
LIST_TTL_MS = 3_600_000
BUILD_LOG_TAIL = 15
MAX_DEPTH = 50

FIRST_BUILD_WAIT_SECONDS = 45   # below the usual 60 s tool-call limit of MCP clients

USAGE = """usage: wingbox mcp [--root DIR] [--build-missing]
       wingbox mcp --http [--root DIR] [--host ADDRESS] [--port N] ...

Run the Wingbox MCP server on stdio for the repository at DIR (default: the
current directory). With --build-missing it builds the code graph of DIR in the
background when there is none. An MCP client starts this command; see the README
section "Use from an MCP client".

With --http the server is reached over HTTP instead, from this or another
machine. That transport is the separate package wingbox-http
(pip install "wingbox[http]"); `wingbox mcp --http --help` lists its options."""
HTTP_PACKAGE = "wingbox_http"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolAnswer:
    texts: tuple[str, ...]
    is_error: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    run: Callable[["Server", dict[str, Any]], ToolAnswer]

    def input_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            schema["required"] = list(self.required)
        return schema


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "description": description}


def _depth(default: int) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_DEPTH,
        "default": default,
        "description": "How many hops to follow.",
    }


_ALL = {
    "type": "boolean",
    "default": False,
    "description": f"List everything instead of stopping at {ANSWER_LINE_LIMIT} items.",
}
_MODULE = _string("Module ID or file path, as printed by other tools (e.g. src/billing/invoice).")
_FUNCTION = _string("Function or method name; literal text with optional .* wildcards.")


def argument_errors(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Check tool arguments against the subset of JSON Schema the tools use."""
    if not isinstance(arguments, dict):
        return ["arguments must be a JSON object"]
    properties: dict[str, Any] = schema.get("properties", {})
    errors = [f"missing required argument: {name}" for name in schema.get("required", []) if name not in arguments]
    for name, value in arguments.items():
        rule = properties.get(name)
        if rule is None:
            if schema.get("additionalProperties", True) is False:
                errors.append(f"unknown argument: {name}")
            continue
        error = _value_error(name, rule, value)
        if error:
            errors.append(error)
    return errors


def _value_error(name: str, rule: dict[str, Any], value: Any) -> Optional[str]:
    kind = rule.get("type")
    if kind == "string":
        if not isinstance(value, str):
            return f"{name} must be a string"
        if len(value) < rule.get("minLength", 0):
            return f"{name} must not be empty"
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name} must be an integer"
        if value < rule.get("minimum", value) or value > rule.get("maximum", value):
            return f"{name} must be between {rule.get('minimum')} and {rule.get('maximum')}"
    elif kind == "boolean":
        if not isinstance(value, bool):
            return f"{name} must be true or false"
    elif kind == "array":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return f"{name} must be a list of strings"
    return None


def cap_answer(text: str, show_all: bool) -> str:
    """Stop an answer without its own list limit at ANSWER_LINE_LIMIT lines."""
    lines = text.splitlines()
    if show_all or len(lines) <= ANSWER_LINE_LIMIT:
        return text
    hidden = len(lines) - ANSWER_LINE_LIMIT
    return "\n".join(lines[:ANSWER_LINE_LIMIT] + [f'(answer-more :hidden {hidden} :rerun "call again with all=true")'])


# ---------------------------------------------------------------------------
# Graph access
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphReady:
    graph: Any
    path: str
    warning: str = ""  # (wingbox-graph-stale ...) when the graph may miss recent changes


@dataclass(frozen=True)
class GraphMissing:
    message: str


GraphResult = GraphReady | GraphMissing


def find_graph(root: str) -> Optional[str]:
    """The code graph of root: .wingbox/index.sexp in root or the nearest parent, as `wingbox <command>` finds it."""
    project = freshness.find_project(root)
    return None if project is None else freshness.graph_path(project)


@dataclass
class Server:
    root: str
    _first_build: Optional[subprocess.Popen] = field(default=None, repr=False)
    _first_build_started: float = 0.0
    _first_build_lock: Any = field(default=None, repr=False)
    _repository: str = ""
    _waiting: bool = False
    _no_repository: bool = False
    _cache_key: Optional[tuple[str, int, int]] = None
    _cache_graph: Any = None
    _search: Any = field(default=None, repr=False)

    def search(self) -> Any:
        if self._search is None:
            self._search = cli.load_script("code_graph_search")
        return self._search

    def start_first_build(self) -> None:
        """Build the graph of root's repository in a child process when it has none (--build-missing).

        Nothing is built in a folder that is in no source repository (an app may start the server in the
        home folder). Two servers started together build once: the first takes the first-build lock, the
        other waits for its graph; a `wingbox build` already running is waited for the same way."""
        if freshness.find_project(self.root) is not None:
            return
        from pathlib import Path

        from wingbox import hook

        repository = hook.source_repository(self.root, str(Path.home()))
        if repository is None:
            self._no_repository = True
            return
        self._repository = repository
        self._first_build_started = time.monotonic()
        self._try_first_build()

    def _try_first_build(self) -> bool:
        """Start the first build unless another one runs; True when one was started here."""
        helpers = freshness.portable()
        builder = cli.load_script("build_code_graph")
        builder.prepare_wingbox_directory(self._repository)
        handle = open(os.path.join(builder.wingbox_directory(self._repository), "first-build.lock"), "a+", encoding="utf-8")
        if not helpers.lock(handle, blocking=False) or self._lock_busy(builder._lock_path(self._repository)):
            handle.close()
            if not self._waiting:
                print("[wingbox] a build is already running: waiting for it", file=sys.stderr)
            self._waiting = True
            return False
        print(f"[wingbox] no code graph in {self._repository}: building it", file=sys.stderr)
        self._first_build_lock = handle   # held until the build ends: the lock of the other servers
        self._first_build = subprocess.Popen(
            [sys.executable, "-m", "wingbox", "build", self._repository],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sys.stderr,
            **helpers.hidden_process_options(),
        )
        self._waiting = False
        return True

    @staticmethod
    def _lock_busy(path: str) -> bool:
        helpers = freshness.portable()
        try:
            with open(path, "a+", encoding="utf-8") as handle:
                if helpers.lock(handle, blocking=False):
                    helpers.unlock(handle)
                    return False
                return True
        except OSError:
            return False

    def first_build_message(self) -> Optional[str]:
        """None once no first build runs or is waited for; else waits a moment and says why there is no answer yet."""
        if self._no_repository:
            return (f"No repository here: {self.root} is in no source repository (no .git and no project manifest "
                    "with source files), so Wingbox has nothing to map. Open a repository and ask again.")
        deadline = time.monotonic() + float(os.environ.get("WINGBOX_FIRST_BUILD_WAIT", FIRST_BUILD_WAIT_SECONDS))
        while self._waiting:
            if freshness.find_project(self.root) is not None:
                self._waiting = False
                return None
            if self._try_first_build():
                break
            if time.monotonic() >= deadline:
                return self._building_message()
            time.sleep(0.2)
        builder = self._first_build
        if builder is None:
            return None
        try:
            status = builder.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return self._building_message()
        self._first_build = None
        if self._first_build_lock is not None:
            self._first_build_lock.close()
            self._first_build_lock = None
        if status != 0:
            return f"The first build of the code graph of {self.root} failed (status {status}). Call the build tool to see why."
        return None

    def _building_message(self) -> str:
        elapsed = int(time.monotonic() - self._first_build_started)
        return (f"Wingbox is building the code graph of {self.root} for the first time ({elapsed} s so far; "
                "a large repository can take several minutes). Call the tool again in a minute.")

    def stop_first_build(self) -> None:
        """When the client closes the server: stop a first build still running (the next start begins again)."""
        if self._first_build is not None and self._first_build.poll() is None:
            self._first_build.terminate()
            self._first_build.wait()

    def graph(self) -> GraphResult:
        waiting = self.first_build_message()
        if waiting is not None:
            return GraphMissing(waiting)
        project = freshness.find_project(self.root)
        if project is None:
            from pathlib import Path

            from wingbox import hook

            if hook.source_repository(self.root, str(Path.home())) is None:
                return GraphMissing(f"No repository here: {self.root} is in no source repository (no .git and no "
                                    "project manifest with source files), so Wingbox has nothing to map.")
            return GraphMissing(f"No .wingbox/ in {self.root} or its parents: call the build tool first.")
        path = freshness.graph_path(project)
        fresh = freshness.ensure_graph_fresh(project, freshness.refresh_wait())
        try:
            status = os.stat(path)
        except OSError as error:
            return GraphMissing(f"Cannot read the code graph {path}: {error}. Call the build tool.")
        key = (path, status.st_mtime_ns, status.st_size)
        if key != self._cache_key:
            search = self.search()
            graph = search.CodeGraph()
            loaded = graph.load(path, project)
            if isinstance(loaded, search.MapLoadErr):
                return GraphMissing(f"Cannot read the code graph {path}: {loaded.error}. Call the build tool.")
            self._cache_key, self._cache_graph = key, graph
        return GraphReady(self._cache_graph, path, freshness.notes(fresh, project))


def _captured(action: Callable[[], Any]) -> tuple[Any, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        value = action()
    return value, out.getvalue(), err.getvalue()


def _answer(text: str) -> ToolAnswer:
    body = text.strip("\n") or "(empty)"
    return ToolAnswer((body,), is_error=body.startswith("(error "))


def _query(
    server: Server,
    argv: list[str],
    command: Callable[[Any, Any, str], None],
    show_all: bool,
    capped: bool = True,
) -> ToolAnswer:
    """Validate argv as `wingbox <command>` does, then run the command on the cached graph."""
    search = server.search()
    problem = search._validate_cli_command(argv)
    if problem is not None:
        return ToolAnswer((f"(error invalid-argument {search._q(problem)})",), is_error=True)
    ready = server.graph()
    if isinstance(ready, GraphMissing):
        return ToolAnswer((ready.message,), is_error=True)
    _value, out, err = _captured(lambda: command(search, ready.graph, ready.path))
    text = (ready.warning + "\n" if ready.warning else "") + out + ("\n" + err if err.strip() else "")
    answer = _answer(text if not capped else cap_answer(text.strip("\n"), show_all))
    # A note before the answer (a stale graph, a skipped language) must not hide that the answer is an error.
    return ToolAnswer(answer.texts, answer.is_error or out.lstrip("\n").startswith("(error "))


def _run_build(server: Server, arguments: dict[str, Any]) -> ToolAnswer:
    waiting = server.first_build_message()
    if waiting is not None:
        return ToolAnswer((waiting,), is_error=True)
    argv = ["build", freshness.find_project(server.root) or server.root]
    if arguments.get("full"):
        argv.append("--full")
    for name in arguments.get("exclude_dirs", []):
        argv.extend(["--exclude-dir", name])
    status, out, err = _captured(lambda: cli.main(argv))
    log_tail = [line for line in err.splitlines() if line.strip()][-BUILD_LOG_TAIL:]
    text = "\n".join(log_tail + [line for line in out.splitlines() if line.strip()])
    if status != 0:
        return ToolAnswer((f"Build failed (status {status}).\n{text}",), is_error=True)
    return ToolAnswer((text or "Build finished.",))


def _run_impact(server: Server, arguments: dict[str, Any]) -> ToolAnswer:
    target, depth, show_all = arguments["target"], arguments.get("depth", 2), arguments.get("all", False)
    argv = ["impact", target, str(depth)]
    text = _query(
        server,
        argv,
        lambda s, g, _p: s.cmd_impact(g, target, depth, sexp=False, show_all=show_all),
        show_all,
        capped=False,
    )
    return text


def _simple(
    argv: Callable[[dict[str, Any]], list[str]],
    command: Callable[[Any, Any, str, dict[str, Any]], None],
    capped: bool = True,
) -> Callable[[Server, dict[str, Any]], ToolAnswer]:
    def run(server: Server, arguments: dict[str, Any]) -> ToolAnswer:
        return _query(
            server,
            argv(arguments),
            lambda s, g, p: command(s, g, p, arguments),
            arguments.get("all", False),
            capped,
        )

    return run


TOOLS: tuple[Tool, ...] = (
    Tool(
        "build",
        "Build or refresh the code graph of the repository (incremental; full=true reparses every file); "
        "call it first and after large edits.",
        {
            "full": {"type": "boolean", "default": False, "description": "Reparse every file instead of only changed ones."},
            "exclude_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Directory names to skip, e.g. [\"generated\"].",
            },
        },
        (),
        _run_build,
    ),
    Tool(
        "impact",
        "Blast radius of changing a file: dependents by depth, counts, and the files to read first.",
        {
            "target": _string("File path relative to the repository root, or a module ID."),
            "depth": _depth(2),
            "all": _ALL,
        },
        ("target",),
        _run_impact,
    ),
    Tool(
        "analyze",
        "Full analysis of one module: entities, imports, dependents, key entities, layer and boundary files.",
        {"module": _MODULE, "all": _ALL},
        ("module",),
        _simple(
            lambda a: ["analyze", a["module"]],
            lambda s, g, _p, a: s.cmd_analyze(g, a["module"], show_all=a.get("all", False)),
            capped=False,
        ),
    ),
    Tool(
        "quick",
        "Find the module that best matches a keyword and analyze it in one step.",
        {"keyword": _string("A word from a module path, file name or entity name."), "all": _ALL},
        ("keyword",),
        _simple(
            lambda a: ["quick", a["keyword"]],
            lambda s, g, p, a: s.cmd_quick(g, a["keyword"], p, show_all=a.get("all", False)),
            capped=False,
        ),
    ),
    Tool(
        "boundary",
        "The files to read before changing a module: the module, what it imports and what imports it.",
        {
            "module": _MODULE,
            "files_only": {"type": "boolean", "default": False, "description": "Print file paths only."},
            "all": _ALL,
        },
        ("module",),
        _simple(
            lambda a: ["boundary", a["module"]],
            lambda s, g, _p, a: s.cmd_boundary(g, a["module"], files_only=a.get("files_only", False)),
        ),
    ),
    Tool(
        "boundaries",
        "Check architecture layer violations (e.g. domain importing infrastructure) for one module or all.",
        {"scope": {"type": "string", "minLength": 1, "default": "all", "description": "Module ID or \"all\"."}, "all": _ALL},
        (),
        _simple(
            lambda a: ["boundaries", a.get("scope", "all")],
            lambda s, g, _p, a: s.cmd_boundaries(g, a.get("scope", "all")),
        ),
    ),
    Tool(
        "layers",
        "List the modules grouped by architecture layer.",
        {"all": _ALL},
        (),
        _simple(lambda _a: ["layers"], lambda s, g, _p, _a: s.cmd_layers(g)),
    ),
    Tool(
        "deps",
        "Outgoing dependency tree of a module: what it uses, hop by hop.",
        {"module": _MODULE, "depth": _depth(2), "all": _ALL},
        ("module",),
        _simple(
            lambda a: ["deps", a["module"], str(a.get("depth", 2))],
            lambda s, g, _p, a: s.cmd_deps(g, a["module"], a.get("depth", 2)),
        ),
    ),
    Tool(
        "rdeps",
        "Reverse dependency tree of a module: who uses it, hop by hop.",
        {"module": _MODULE, "depth": _depth(2), "all": _ALL},
        ("module",),
        _simple(
            lambda a: ["rdeps", a["module"], str(a.get("depth", 2))],
            lambda s, g, _p, a: s.cmd_rdeps(g, a["module"], a.get("depth", 2)),
        ),
    ),
    Tool(
        "entity",
        "Find where a class, function, type or constant is defined and every module that references it.",
        {"name": _string("Entity name; literal text with optional .* wildcards."), "all": _ALL},
        ("name",),
        _simple(lambda a: ["entity", a["name"]], lambda s, g, _p, a: s.cmd_entity(g, a["name"])),
    ),
    Tool(
        "usages",
        "Every place that uses one function, class, type or constant, through re-exports and aliases: "
        "by file, with line:column and the kind of use (import, re-export, call, type, extends, reference); "
        "method calls whose receiver type is not in sight are marked unresolved.",
        {
            "symbol": _string("Name, <file>::Name or <file>::Class.method (a file path or module ID)."),
            "all": _ALL,
        },
        ("symbol",),
        _simple(
            lambda a: ["usages", a["symbol"]],
            lambda s, g, _p, a: s.cmd_usages(g, a["symbol"], show_all=a.get("all", False)),
            capped=False,
        ),
    ),
    Tool(
        "find_function",
        "Find function and method definitions by name, with file and line.",
        {"name": _FUNCTION, "all": _ALL},
        ("name",),
        _simple(lambda a: ["find_function", a["name"]], lambda s, g, _p, a: s.cmd_find_function(g, a["name"])),
    ),
    Tool(
        "find_type",
        "Find class, interface, struct, enum and type definitions by name, with file and line.",
        {"name": _string("Type name; literal text with optional .* wildcards."), "all": _ALL},
        ("name",),
        _simple(lambda a: ["find_type", a["name"]], lambda s, g, _p, a: s.cmd_find_type(g, a["name"])),
    ),
    Tool(
        "find_callers",
        "Who calls a function: incoming calls, hop by hop.",
        {"function": _FUNCTION, "depth": _depth(2), "all": _ALL},
        ("function",),
        _simple(
            lambda a: ["find_callers", a["function"], str(a.get("depth", 2))],
            lambda s, g, _p, a: s.cmd_find_callers(g, a["function"], a.get("depth", 2)),
        ),
    ),
    Tool(
        "find_calls",
        "What a function calls: outgoing calls, hop by hop.",
        {"function": _FUNCTION, "depth": _depth(2), "all": _ALL},
        ("function",),
        _simple(
            lambda a: ["find_calls", a["function"], str(a.get("depth", 2))],
            lambda s, g, _p, a: s.cmd_find_calls(g, a["function"], a.get("depth", 2)),
        ),
    ),
    Tool(
        "find_callchain",
        "A call path from one function to another, or a module path when no call path exists.",
        {"source": _FUNCTION, "target": _FUNCTION, "depth": _depth(5), "all": _ALL},
        ("source", "target"),
        _simple(
            lambda a: ["find_callchain", a["source"], a["target"], str(a.get("depth", 5))],
            lambda s, g, _p, a: s.cmd_find_callchain(g, a["source"], a["target"], a.get("depth", 5)),
        ),
    ),
    Tool(
        "diff_functions",
        "Functions changed in the git diff against a ref (default HEAD), to check before review or commit.",
        {"ref": {"type": "string", "minLength": 1, "default": "HEAD", "description": "Git ref, e.g. HEAD~3 or main."}, "all": _ALL},
        (),
        _simple(
            lambda a: ["diff_functions", a.get("ref", "HEAD")],
            lambda s, g, _p, a: s.cmd_diff_functions(g, a.get("ref", "HEAD")),
        ),
    ),
    Tool(
        "query",
        "Evaluate a Scheme query on the graph, e.g. (files (intersect (deps \"A\" 2) (rdeps \"B\" 1))).",
        {"expression": _string("A Scheme expression; (help) lists the functions."), "all": _ALL},
        ("expression",),
        _simple(lambda a: ["query", a["expression"]], lambda s, g, p, a: s.cmd_query(g, a["expression"], p)),
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


# ---------------------------------------------------------------------------
# JSON-RPC handling
# ---------------------------------------------------------------------------


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _error(identifier: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": identifier, "error": error}


def _server_info() -> dict[str, str]:
    return {"name": "wingbox", "title": "Wingbox", "version": cli._version()}


def _instructions(root: str) -> str:
    return (
        f"Wingbox answers questions about the code graph of {root}. Call impact before editing a file, "
        "analyze or quick to understand a module, boundary for the files to read. "
        "If a tool says the graph is missing, call build first; call build again after large edits."
    )


def _modern_version(params: dict[str, Any]) -> Optional[str]:
    """The per-request protocol version of revision 2026-07-28, or None for a handshake-era request."""
    meta = params.get("_meta")
    if not isinstance(meta, dict) or META_VERSION not in meta:
        return None
    version = meta[META_VERSION]
    if not isinstance(version, str) or version not in SUPPORTED_VERSIONS:
        raise RpcError(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_VERSIONS), "requested": version},
        )
    if version not in MODERN_VERSIONS:
        return None
    if not isinstance(meta.get(META_CAPABILITIES), dict):
        raise RpcError(INVALID_PARAMS, f"missing _meta field {META_CAPABILITIES}")
    return version


# MCP ToolAnnotations: a human title per tool; every tool only reads the code
# graph except `build`, which writes .wingbox/ (the graph, its cache, REPORT.md, map.html)
# in the repository (never deletes) and gives the same result when repeated.
TOOL_TITLES = {
    "build": "Build the code graph",
    "impact": "Impact of changing a file",
    "analyze": "Analyze a module",
    "quick": "Find a feature",
    "boundary": "Boundary files of a module",
    "boundaries": "Architecture boundaries",
    "layers": "Architecture layers",
    "deps": "Dependencies of a module",
    "rdeps": "Dependents of a module",
    "entity": "Look up an entity",
    "usages": "Find every use of a symbol",
    "find_function": "Find a function",
    "find_type": "Find a type",
    "find_callers": "Callers of a function",
    "find_calls": "Calls made by a function",
    "find_callchain": "Call chain between functions",
    "diff_functions": "Functions changed in a diff",
    "query": "Query the code graph",
}
WRITING_TOOLS = frozenset({"build"})


def tool_annotations(name: str) -> dict[str, Any]:
    return {
        "title": TOOL_TITLES[name],
        "readOnlyHint": name not in WRITING_TOOLS,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def _tool_listing() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.input_schema(), "annotations": tool_annotations(t.name)}
        for t in TOOLS
    ]


def _call_tool(server: Server, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise RpcError(INVALID_PARAMS, "tools/call requires a tool name")
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    problems = argument_errors(tool.input_schema(), arguments)
    if problems:
        answer = ToolAnswer(("Invalid arguments: " + "; ".join(problems),), is_error=True)
    else:
        try:
            answer = tool.run(server, arguments)
        except (Exception, SystemExit) as error:  # a failing command is a tool error, not a crash
            print(f"[wingbox] tool {name} failed: {error!r}", file=sys.stderr)
            answer = ToolAnswer((f"{name} failed: {error}",), is_error=True)
    return {"content": [{"type": "text", "text": text} for text in answer.texts], "isError": answer.is_error}


def handle_request(server: Server, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """The result of one request; raises RpcError for a protocol error."""
    modern = _modern_version(params)
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in HANDSHAKE_VERSIONS else HANDSHAKE_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": _server_info(),
            "instructions": _instructions(server.root),
        }
    if method == "server/discover":
        if modern is None:
            raise RpcError(INVALID_PARAMS, f"server/discover requires _meta {META_VERSION} and {META_CAPABILITIES}")
        result: dict[str, Any] = {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}},
            "instructions": _instructions(server.root),
            "ttlMs": LIST_TTL_MS,
            "cacheScope": "public",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tool_listing()}
        if modern:
            result.update({"ttlMs": LIST_TTL_MS, "cacheScope": "public"})
    elif method == "tools/call":
        result = _call_tool(server, params)
    else:
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
    if modern:
        result = {"resultType": "complete", **result, "_meta": {META_SERVER_INFO: _server_info()}}
    return result


def handle_line(server: Server, line: str) -> Optional[dict[str, Any]]:
    """The reply to one line of input, or None for a notification."""
    try:
        message = json.loads(line)
    except ValueError as error:
        return _error(None, PARSE_ERROR, f"Parse error: {error}")
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Invalid Request: expected one JSON-RPC object per line")
    identifier = message.get("id")
    method = message.get("method")
    valid_id = isinstance(identifier, (str, int)) and not isinstance(identifier, bool)
    if "id" not in message and isinstance(method, str) and message.get("jsonrpc") == "2.0":
        return None  # notification: initialized, cancelled, ... need no reply
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or not valid_id:
        return _error(identifier if valid_id else None, INVALID_REQUEST, "Invalid Request")
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(identifier, INVALID_PARAMS, "params must be an object")
    started = time.monotonic()
    try:
        result = handle_request(server, method, params)
    except RpcError as error:
        return _error(identifier, error.code, error.message, error.data)
    except Exception as error:  # never let one request stop the server
        print(f"[wingbox] {method} failed: {error!r}", file=sys.stderr)
        return _error(identifier, INTERNAL_ERROR, f"Internal error: {error}")
    finally:
        if method == "tools/call":
            print(f"[wingbox] tools/call {params.get('name')} {time.monotonic() - started:.3f}s", file=sys.stderr)
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def serve(server: Server, source: IO[bytes], sink: IO[bytes]) -> int:
    """Answer each line of source on sink until end of input."""
    for raw in source:
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            reply: Optional[dict[str, Any]] = _error(None, PARSE_ERROR, f"Parse error: {error}")
        else:
            if not line:
                continue
            reply = handle_line(server, line)
        if reply is not None:
            sink.write(json.dumps(reply, separators=(",", ":")).encode("ascii") + b"\n")
            sink.flush()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_root(args: list[str], cwd: str) -> tuple[Optional[str], Optional[str]]:
    """(root, error) from the arguments of `wingbox mcp`."""
    root = cwd
    index = 0
    while index < len(args):
        if args[index] == "--build-missing":
            index += 1
            continue
        if args[index] == "--root" and index + 1 < len(args):
            root = os.path.normpath(os.path.join(cwd, args[index + 1]))
            index += 2
            continue
        return None, f"unknown argument: {args[index]}"
    if not os.path.isdir(root):
        return None, f"--root is not a directory: {root}"
    return os.path.realpath(root), None


def _http_main(args: list[str], cwd: str) -> int:
    """Hand over to the HTTP transport, which is a distribution of its own: this package listens on no port."""
    try:
        transport = importlib.import_module(HTTP_PACKAGE)
    except ImportError:
        print(
            '[wingbox] ERROR: the HTTP transport is a separate package. Install it with: pip install "wingbox[http]" '
            '(pipx: pipx inject wingbox wingbox-http)',
            file=sys.stderr,
        )
        return 2
    return transport.main(args, cwd)


def main(args: list[str], cwd: str) -> int:
    if "--http" in args:
        return _http_main([argument for argument in args if argument != "--http"], cwd)
    if any(argument in ("-h", "--help") for argument in args):
        print(USAGE)
        return 0
    root, problem = parse_root(args, cwd)
    if root is None:
        print(f"[wingbox] ERROR: {problem}\n{USAGE}", file=sys.stderr)
        return 2
    # stdout carries protocol messages only: keep a private handle on it and point
    # file descriptor 1 at stderr, so that a stray print or a child process cannot corrupt it.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    print(f"[wingbox] MCP server {cli._version()} on stdio for {root}", file=sys.stderr)
    server = Server(root)
    try:
        if "--build-missing" in args:
            server.start_first_build()
        return serve(server, sys.stdin.buffer, protocol)
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop_first_build()
        protocol.close()
