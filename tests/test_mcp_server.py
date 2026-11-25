import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
MCP_PATH = SOURCE / "wingbox" / "mcp_server.py"

LEGACY_VERSION = "2025-11-25"
MODERN_VERSION = "2026-07-28"
EXPECTED_TOOLS = {"build_graph", "analyze_impact", "describe_module", "list_files", "read_source", "search_source", "find_definitions", "find_usages",
                  "list_dependencies", "trace_calls", "list_layers", "check_boundaries", "list_changes", "query_graph"}


def _load_mcp_module():
    sys.path.insert(0, str(SOURCE))
    spec = importlib.util.spec_from_file_location("wingbox_mcp_server", MCP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load the MCP server module from {MCP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(identifier, method, params=None):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


def _initialize(identifier=1, version=LEGACY_VERSION):
    return _request(
        identifier,
        "initialize",
        {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )


def _modern_meta(version=MODERN_VERSION):
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
    }


def _call(identifier, name, arguments):
    return _request(identifier, "tools/call", {"name": name, "arguments": arguments})


def _make_shop(root: Path) -> None:
    """A small Python repository (a git work tree: a folder in no repository is never built, REQ-MCP-019)."""
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "shop").mkdir()
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (root / "shop" / "checkout.py").write_text(
        "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n", encoding="utf-8"
    )


class McpSession:
    """Run `python -m wingbox mcp` on a list of input lines and collect what it writes."""

    def __init__(self, root: Path, lines: list[str], extra: tuple[str, ...] = (), env: dict[str, str] | None = None) -> None:
        environment = dict(os.environ, **(env or {}))
        environment["PYTHONPATH"] = str(SOURCE) + os.pathsep + environment.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, "-m", "wingbox", "mcp", "--root", str(root), *extra],
            input="".join(line + "\n" for line in lines).encode("utf-8"),
            capture_output=True,
            env=environment,
            cwd=str(root),
            timeout=180,
        )
        self.returncode = completed.returncode
        self.stdout = completed.stdout.decode("utf-8")
        self.stderr = completed.stderr.decode("utf-8")
        self.messages = [json.loads(line) for line in self.stdout.splitlines() if line.strip()]

    def reply(self, identifier):
        for message in self.messages:
            if message.get("id") == identifier:
                return message
        raise AssertionError(f"no reply with id {identifier}; stdout={self.stdout!r} stderr={self.stderr[-2000:]!r}")


def _texts(reply) -> list[str]:
    return [item["text"] for item in reply["result"]["content"] if item["type"] == "text"]


class McpProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.root = Path(tmpdir.name).resolve()

    def test_initialize_handshake_negotiates_the_protocol_version(self) -> None:
        """/* REQ-MCP-001: `wingbox mcp` must answer `initialize` with the requested protocol version when it supports it, otherwise with its latest handshake version, and declare the tools capability and its name. */"""
        session = McpSession(
            self.root,
            [
                _initialize(1, LEGACY_VERSION),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                _initialize(2, "2025-06-18"),
                _initialize(3, "2024-01-01"),
                _request(4, "ping"),
            ],
        )
        self.assertEqual(0, session.returncode, session.stderr)
        first = session.reply(1)["result"]
        self.assertEqual(LEGACY_VERSION, first["protocolVersion"])
        self.assertIn("tools", first["capabilities"])
        self.assertEqual("wingbox", first["serverInfo"]["name"])
        self.assertIn("`build_graph`", first["instructions"])
        self.assertEqual("2025-06-18", session.reply(2)["result"]["protocolVersion"])
        self.assertEqual(LEGACY_VERSION, session.reply(3)["result"]["protocolVersion"])
        self.assertEqual({}, session.reply(4)["result"])
        self.assertEqual(4, len(session.messages), "a notification must get no reply")

    def test_requests_with_per_request_metadata_follow_the_stateless_revision(self) -> None:
        """/* REQ-MCP-002: a request carrying `_meta` protocol fields must be served by the stateless revision 2026-07-28: `server/discover` lists the supported versions, results carry `resultType`, an unsupported version gets -32022 with the supported list, a request without client capabilities gets -32602. */"""
        session = McpSession(
            self.root,
            [
                _request(1, "server/discover", {"_meta": _modern_meta()}),
                _request(2, "tools/list", {"_meta": _modern_meta()}),
                _request(3, "tools/list", {"_meta": _modern_meta("1900-01-01")}),
                _request(4, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_VERSION}}),
            ],
        )
        discover = session.reply(1)["result"]
        self.assertEqual("complete", discover["resultType"])
        self.assertIn(MODERN_VERSION, discover["supportedVersions"])
        self.assertIn(LEGACY_VERSION, discover["supportedVersions"])
        self.assertIn("tools", discover["capabilities"])
        self.assertEqual("wingbox", discover["_meta"]["io.modelcontextprotocol/serverInfo"]["name"])
        self.assertGreaterEqual(discover["ttlMs"], 0)
        listing = session.reply(2)["result"]
        self.assertEqual("complete", listing["resultType"])
        self.assertEqual(EXPECTED_TOOLS, {tool["name"] for tool in listing["tools"]})
        self.assertIn(listing["cacheScope"], ("public", "private"))
        unsupported = session.reply(3)["error"]
        self.assertEqual(-32022, unsupported["code"])
        self.assertIn(MODERN_VERSION, unsupported["data"]["supported"])
        self.assertEqual("1900-01-01", unsupported["data"]["requested"])
        self.assertEqual(-32602, session.reply(4)["error"]["code"])

    def test_tools_list_describes_every_tool_for_an_agent(self) -> None:
        """/* REQ-MCP-003: `tools/list` must name the tool that builds the graph (`build_graph`) and the read commands, each with a one-line description and a JSON object input schema. */"""
        session = McpSession(self.root, [_initialize(), _request(2, "tools/list")])
        tools = session.reply(2)["result"]["tools"]
        self.assertEqual(EXPECTED_TOOLS, {tool["name"] for tool in tools})
        for tool in tools:
            self.assertTrue(tool["description"].strip(), tool["name"])
            self.assertNotIn("\n", tool["description"], tool["name"])
            self.assertEqual("object", tool["inputSchema"]["type"], tool["name"])
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True), tool["name"])
        impact = next(tool for tool in tools if tool["name"] == "analyze_impact")
        self.assertNotIn("required", impact["inputSchema"])   # target or targets: the tool says which is missing
        self.assertEqual("array", impact["inputSchema"]["properties"]["targets"]["type"])
        self.assertEqual("string", impact["inputSchema"]["properties"]["target"]["type"])
        self.assertEqual(2, impact["inputSchema"]["properties"]["depth"]["default"])
        self.assertIn("all", impact["inputSchema"]["properties"])

    def test_tools_carry_annotations(self) -> None:
        """/* REQ-MCP-011: every tool in `tools/list` must carry MCP annotations with a short `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint` false; `build` is the only tool that is not read-only (it writes the graph), and it is neither destructive nor open-world and is idempotent. */"""
        session = McpSession(self.root, [_initialize(), _request(2, "tools/list")])
        tools = session.reply(2)["result"]["tools"]
        for tool in tools:
            annotations = tool["annotations"]
            self.assertTrue(annotations["title"].strip(), tool["name"])
            self.assertLessEqual(len(annotations["title"]), 40, tool["name"])
            self.assertIs(annotations["readOnlyHint"], tool["name"] != "build_graph", tool["name"])
            self.assertIs(annotations["destructiveHint"], False, tool["name"])
            self.assertIs(annotations["idempotentHint"], True, tool["name"])
            self.assertIs(annotations["openWorldHint"], False, tool["name"])

    def test_tool_names_fit_the_directory_limit(self) -> None:
        """/* REQ-MCP-012: every tool name must be shorter than 64 characters and use only letters, digits, underscores and hyphens. */"""
        session = McpSession(self.root, [_initialize(), _request(2, "tools/list")])
        for tool in session.reply(2)["result"]["tools"]:
            self.assertLess(len(tool["name"]), 64)
            self.assertRegex(tool["name"], r"^[A-Za-z0-9_-]+$")

    def test_build_then_impact_return_text_and_s_expressions(self) -> None:
        """/* REQ-MCP-004: the `build_graph` tool must build the code graph of --root, and `analyze_impact` must then return the answer of `wingbox impact` (its default S-expression form, REQ-MCP-033) only, as one text item. */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [
                _initialize(),
                _call(2, "build_graph", {}),
                _call(3, "analyze_impact", {"target": "shop/pricing.py"}),
                _call(4, "list_dependencies", {"module": "shop/checkout", "depth": 1}),
            ],
        )
        build = session.reply(2)["result"]
        self.assertFalse(build["isError"], build)
        self.assertTrue((self.root / ".wingbox/index.sexp").is_file())
        impact = session.reply(3)["result"]
        self.assertFalse(impact["isError"], impact)
        texts = _texts(session.reply(3))
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("(impact shop/pricing.py :direct "), texts[0])
        self.assertIn("shop/checkout.py", texts[0])
        self.assertNotIn("Impact of changing", texts[0])
        deps = session.reply(4)["result"]
        self.assertFalse(deps["isError"], deps)
        self.assertIn("(dep shop/checkout shop/pricing", _texts(session.reply(4))[0])

    def test_module_states_validation_only_with_the_evidence_of_the_stored_fact(self) -> None:
        """/* REQ-MCP-020: after `build_graph`, the `describe_module` tool (the `analyze` command) prints the validation mark of a data-flow fact with its evidence for a module that imports a pydantic model, and no data-flow line for a module that imports a file whose comment only names a validator; no answer holds the retired mark `:validated`. */"""
        _make_shop(self.root)
        shop = self.root / "shop"
        (shop / "forms.py").write_text(
            "from pydantic import BaseModel\n\n\nclass Order(BaseModel):\n    total: int\n", encoding="utf-8"
        )
        (shop / "notes.py").write_text("# BaseModel, pydantic, validator\nLIMIT = 1\n", encoding="utf-8")
        (shop / "api.py").write_text("from shop.forms import Order\n\n\ndef read(raw):\n    return Order(**raw)\n", encoding="utf-8")
        (shop / "report.py").write_text("from shop.notes import LIMIT\n\n\ndef top():\n    return LIMIT\n", encoding="utf-8")
        session = McpSession(
            self.root,
            [
                _initialize(),
                _call(2, "build_graph", {}),
                _call(3, "describe_module", {"module": "shop/api"}),
                _call(4, "describe_module", {"module": "shop/report"}),
            ],
        )
        self.assertFalse(session.reply(2)["result"]["isError"], session.reply(2))
        with_model = _texts(session.reply(3))[0]
        without = _texts(session.reply(4))[0]
        self.assertIn(
            '(analyze-dataflow shop/api shop/forms :lang py :validation-module :evidence "base pydantic.BaseModel")',
            with_model.splitlines(),
        )
        self.assertIn("(analyze shop/report ", without)
        self.assertNotIn("analyze-dataflow", without)
        self.assertNotIn(":validated", with_model + without)
        self.assertNotIn(":validated", (self.root / ".wingbox/index.sexp").read_text(encoding="utf-8"))

    def test_usages_returns_the_text_answer(self) -> None:
        """/* REQ-MCP-046: the `find_usages` tool must return the text answer of `wingbox usages`: every use of the symbol by file with line:column and kind, as one text item and no S-expression facts. */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [_initialize(), _call(2, "build_graph", {}), _call(3, "find_usages", {"symbol": "shop/pricing.py::total"})],
        )
        answer = session.reply(3)["result"]
        self.assertFalse(answer["isError"], answer)
        texts = _texts(session.reply(3))
        self.assertEqual(1, len(texts))
        self.assertIn("Usages of total (function, shop/pricing.py:1)", texts[0])
        self.assertIn("shop/checkout.py\n  1:26   import\n  5:12   call", texts[0])
        self.assertNotIn("(usage ", texts[0])

    def test_a_query_without_a_graph_asks_for_build(self) -> None:
        """/* REQ-MCP-005: a query tool called before any graph exists must return a tool error that tells the agent to call `build_graph` first. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "analyze_impact", {"target": "shop/pricing.py"})])
        result = session.reply(2)["result"]
        self.assertTrue(result["isError"])
        self.assertIn("call the `build_graph` tool first", _texts(session.reply(2))[0])

    def test_with_build_missing_the_first_query_builds_the_graph(self) -> None:
        """/* REQ-MCP-015: `wingbox mcp --root DIR --build-missing` (what the Claude Desktop extension starts) builds the graph of DIR in the background when it has none; a tool call waits for that build up to WINGBOX_FIRST_BUILD_WAIT seconds (default 45) and then answers from the new graph. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "analyze_impact", {"target": "shop/pricing.py"})], ("--build-missing",))
        result = session.reply(2)["result"]
        self.assertFalse(result["isError"], _texts(session.reply(2)))
        self.assertIn("shop/checkout", _texts(session.reply(2))[0])
        self.assertTrue((self.root / ".wingbox" / "index.sexp").is_file())

    def test_a_first_build_longer_than_the_wait_is_reported_not_hung(self) -> None:
        """/* REQ-MCP-016: when the first build takes longer than WINGBOX_FIRST_BUILD_WAIT seconds (a large repository), a tool call returns at once with a tool error that says the graph of DIR is being built for the first time and to call again shortly; the build goes on in the background. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "analyze_impact", {"target": "shop/pricing.py"})], ("--build-missing",),
                             {"WINGBOX_FIRST_BUILD_WAIT": "0"})
        result = session.reply(2)["result"]
        self.assertTrue(result["isError"])
        self.assertIn("is building the code graph", _texts(session.reply(2))[0])
        self.assertIn("Call the tool again", _texts(session.reply(2))[0])

    def test_without_build_missing_nothing_is_built(self) -> None:
        """/* REQ-MCP-017: without --build-missing the server never builds on its own (the plugin starts it in whatever folder the session runs in); an unknown argument exits 2 with the usage. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "analyze_impact", {"target": "shop/pricing.py"})])
        self.assertTrue(session.reply(2)["result"]["isError"])
        self.assertFalse((self.root / ".wingbox").exists())
        self.assertEqual(2, McpSession(self.root, [], ("--bogus",)).returncode)

    def test_two_servers_started_together_build_once_without_an_error(self) -> None:
        """/* REQ-MCP-018: two `wingbox mcp --build-missing` started at the same moment on a repository without a graph (Claude Desktop starts its server twice) run one build: the other server says `[wingbox] a build is already running: waiting for it`, no line contains ERROR, and both answer impact from the graph. */"""
        _make_shop(self.root)
        environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        lines = "".join(line + "\n" for line in [_initialize(), _call(2, "analyze_impact", {"target": "shop/pricing.py"})])
        servers = [
            subprocess.Popen([sys.executable, "-m", "wingbox", "mcp", "--root", str(self.root), "--build-missing"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=str(self.root))
            for _ in range(2)
        ]
        outputs = [server.communicate(lines.encode("utf-8"), timeout=300) for server in servers]
        logs = "".join(err.decode("utf-8") for _out, err in outputs)
        self.assertNotIn("ERROR", logs)
        self.assertEqual(1, logs.count("building it"), logs)
        self.assertIn("[wingbox] a build is already running: waiting for it", logs)
        for out, _err in outputs:
            reply = next(json.loads(line) for line in out.decode("utf-8").splitlines() if json.loads(line).get("id") == 2)
            self.assertFalse(reply["result"]["isError"], reply)
            self.assertIn("shop/checkout", reply["result"]["content"][0]["text"])

    def test_a_folder_that_is_no_repository_is_never_built(self) -> None:
        """/* REQ-MCP-019: started with --build-missing in a folder that is in no source repository (no .git or project manifest with source files, as when an app starts the server in the home folder), the server builds nothing and every tool answers that there is no repository here. */"""
        (self.root / "notes.txt").write_text("hello\n", encoding="utf-8")
        session = McpSession(self.root, [_initialize(), _call(2, "analyze_impact", {"target": "notes.txt"}), _call(3, "build_graph", {})], ("--build-missing",))
        for identifier in (2, 3):
            text = _texts(session.reply(identifier))[0]
            self.assertIn("No repository here", text)
        self.assertFalse((self.root / ".wingbox").exists())

    def test_build_graph_builds_nothing_where_there_is_no_repository(self) -> None:
        """/* REQ-MCP-041: started without --build-missing in a folder that is in no source repository (source files, but no .git and no project manifest at or above it), `build_graph` answers what every other tool answers there, a tool error that says there is no repository here, and writes no .wingbox/ folder. */"""
        (self.root / "notes.txt").write_text("hello\n", encoding="utf-8")
        (self.root / "tool.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        session = McpSession(self.root, [_initialize(), _call(2, "list_files", {}), _call(3, "build_graph", {}), _call(4, "build_graph", {"full": True})])
        for identifier in (2, 3, 4):
            self.assertTrue(session.reply(identifier)["result"]["isError"], session.reply(identifier))
            self.assertIn("No repository here", _texts(session.reply(identifier))[0])
        self.assertFalse((self.root / ".wingbox").exists())

    def test_the_server_names_the_project_whose_graph_it_serves(self) -> None:
        """/* REQ-MCP-042: --root is where the search for the code graph starts, not a boundary: `wingbox mcp --root <project>/shop` serves the graph of <project>, and its first log line and its `instructions` name <project> (the log line also the --root it was given); started at the project folder itself, both name that folder only. */"""
        _make_shop(self.root)
        built = McpSession(self.root, [_initialize(), _call(2, "build_graph", {})])
        self.assertFalse(built.reply(2)["result"]["isError"], built.reply(2))
        self.assertTrue(any(line.endswith(f" on stdio for {self.root}") for line in built.stderr.splitlines()), built.stderr)
        self.assertIn(f"the code graph of {self.root}. ", built.reply(1)["result"]["instructions"])
        below = self.root / "shop"
        session = McpSession(below, [_initialize(), _call(2, "list_files", {})])
        self.assertIn("(file shop/pricing.py ", _texts(session.reply(2))[0])
        self.assertIn(f"the code graph of {self.root}. ", session.reply(1)["result"]["instructions"])
        self.assertNotIn(str(below), session.reply(1)["result"]["instructions"])
        named = f" on stdio for {self.root} (the project that holds --root {below})"
        self.assertTrue(any(line.endswith(named) for line in session.stderr.splitlines()), session.stderr)
        modern = McpSession(below, [_request(1, "server/discover", {"_meta": _modern_meta()})])
        self.assertIn(f"the code graph of {self.root}. ", modern.reply(1)["result"]["instructions"])

    def test_a_message_nested_too_deeply_is_a_parse_error_and_the_server_goes_on(self) -> None:
        """/* REQ-MCP-043: a line of 200,000 opening brackets, which the JSON reader gives up on with a recursion error, is answered -32700 with a null id like any other text that is not JSON; the server goes on and answers the next request, and exits 0 at the end of its input. */"""
        session = McpSession(self.root, ["[" * 200_000, '{"a":' * 200_000, _request(5, "ping")])
        self.assertEqual(0, session.returncode, session.stderr[-2000:])
        self.assertEqual(3, len(session.messages))
        for reply in session.messages[:2]:
            self.assertEqual((-32700, None), (reply["error"]["code"], reply["id"]))
        self.assertEqual({}, session.reply(5)["result"])
        self.assertNotIn("Traceback", session.stderr)

    def test_an_empty_item_of_a_list_argument_is_refused(self) -> None:
        """/* REQ-MCP-044: an empty string as an item of `targets` (analyze_impact) or of `exclude_dirs` (build_graph) is refused as an invalid argument that names the parameter, as an empty `target`, `symbol`, `scope` or `ref` is: no file is answered for in its place, and nothing is built; the input schema states the rule (`minLength` 1 for the items). */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [
                _initialize(),
                _call(2, "build_graph", {"exclude_dirs": [""]}),
                _call(3, "build_graph", {}),
                _call(4, "analyze_impact", {"targets": [""]}),
                _call(5, "analyze_impact", {"targets": ["shop/pricing.py", ""]}),
                _call(6, "analyze_impact", {"target": ""}),
                _call(7, "find_usages", {"symbol": ""}),
                _call(8, "check_boundaries", {"scope": ""}),
                _call(9, "list_changes", {"ref": ""}),
                _call(10, "analyze_impact", {"targets": ["shop/pricing.py"]}),
                _request(11, "tools/list"),
            ],
        )
        refused = _texts(session.reply(2))[0]
        self.assertTrue(session.reply(2)["result"]["isError"], refused)
        self.assertIn("exclude_dirs", refused)
        self.assertNotIn("Scanning", refused)
        self.assertFalse(session.reply(3)["result"]["isError"], session.reply(3))
        for identifier, name in ((4, "targets"), (5, "targets"), (6, "target"), (7, "symbol"), (8, "scope"), (9, "ref")):
            text = _texts(session.reply(identifier))[0]
            self.assertTrue(session.reply(identifier)["result"]["isError"], text)
            self.assertTrue(text.startswith("Invalid arguments: "), text)
            self.assertIn(name, text)
            self.assertNotIn("(impact", text)
        self.assertIn("(impact shop/pricing.py ", _texts(session.reply(10))[0])
        schemas = {tool["name"]: tool["inputSchema"]["properties"] for tool in session.reply(11)["result"]["tools"]}
        self.assertEqual({"type": "string", "minLength": 1}, schemas["analyze_impact"]["targets"]["items"])
        self.assertEqual({"type": "string", "minLength": 1}, schemas["build_graph"]["exclude_dirs"]["items"])

    def test_a_tool_answer_of_more_than_200_lines_stops_there_unless_all_is_set(self) -> None:
        """/* REQ-MCP-045: through `tools/call`, an answer without its own list limit stops at 200 lines and ends with one line that says how many are hidden and to call again with all=true, and with `all: true` it is complete: for a tool that prints the answer of one command (`list_files` on 230 files) and for `find_definitions` without a kind, which filters the answer of a command first. */"""
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "pkg").mkdir()
        for number in range(230):
            (self.root / "pkg" / f"part_{number:03}.py").write_text(f"def handler_{number:03}():\n    return {number}\n", encoding="utf-8")
        session = McpSession(
            self.root,
            [
                _initialize(),
                _call(2, "build_graph", {}),
                _call(3, "list_files", {}),
                _call(4, "list_files", {"all": True}),
                _call(5, "find_definitions", {"name": "handler_.*"}),
                _call(6, "find_definitions", {"name": "handler_.*", "all": True}),
            ],
        )
        self.assertFalse(session.reply(2)["result"]["isError"], session.reply(2))
        for cut, whole, first in ((3, 4, "(file pkg/part_000.py "), (5, 6, "")):
            lines = _texts(session.reply(cut))[0].splitlines()
            self.assertEqual(201, len(lines), lines[-3:])
            self.assertTrue(lines[0].startswith(first), lines[0])
            every = _texts(session.reply(whole))[0].splitlines()
            self.assertGreaterEqual(len(every), 230)
            self.assertEqual(f'(answer-more :hidden {len(every) - 200} :rerun "call again with all=true")', lines[200])
            self.assertEqual(every[:200], lines[:200])
            self.assertNotIn("answer-more", "\n".join(every))
            self.assertFalse(session.reply(cut)["result"]["isError"])

    def test_malformed_input_gets_json_rpc_errors_and_the_server_keeps_running(self) -> None:
        """/* REQ-MCP-006: malformed JSON must get -32700, a message that is not a request object -32600, and the server must go on answering the next request. */"""
        session = McpSession(
            self.root,
            [
                "{not json",
                "[1, 2]",
                json.dumps({"jsonrpc": "2.0", "id": 5}),
                json.dumps({"jsonrpc": "1.0", "id": 6, "method": "ping"}),
                _initialize(7),
            ],
        )
        self.assertEqual(0, session.returncode, session.stderr)
        self.assertEqual(-32700, session.messages[0]["error"]["code"])
        self.assertIsNone(session.messages[0]["id"])
        self.assertEqual(-32600, session.messages[1]["error"]["code"])
        self.assertEqual(-32600, session.reply(5)["error"]["code"])
        self.assertEqual(-32600, session.reply(6)["error"]["code"])
        self.assertEqual(LEGACY_VERSION, session.reply(7)["result"]["protocolVersion"])

    def test_unknown_methods_tools_and_bad_arguments(self) -> None:
        """/* REQ-MCP-007: an unknown method must get -32601, an unknown tool -32602, and arguments that do not match the input schema a tool result with isError true. */"""
        session = McpSession(
            self.root,
            [
                _initialize(),
                _request(2, "resources/read", {"uri": "file:///etc/passwd"}),
                _call(3, "no_such_tool", {}),
                _call(4, "analyze_impact", {"depth": 2}),
                _call(5, "analyze_impact", {"target": "a.py", "depth": "deep"}),
                _call(6, "analyze_impact", {"target": "a.py", "colour": "red"}),
                _request(7, "tools/call", {"arguments": {}}),
            ],
        )
        self.assertEqual(-32601, session.reply(2)["error"]["code"])
        self.assertEqual(-32602, session.reply(3)["error"]["code"])
        for identifier in (4, 5, 6):
            result = session.reply(identifier)["result"]
            self.assertTrue(result["isError"], result)
        self.assertIn("target", _texts(session.reply(4))[0])
        self.assertIn("depth", _texts(session.reply(5))[0])
        self.assertIn("colour", _texts(session.reply(6))[0])
        self.assertEqual(-32602, session.reply(7)["error"]["code"])

    def test_stdout_carries_only_json_rpc_messages(self) -> None:
        """/* REQ-MCP-008: every line `wingbox mcp` writes to stdout must be one JSON-RPC 2.0 message, also while it builds; logs go to stderr. */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [_initialize(), _call(2, "build_graph", {"full": True}), _call(3, "list_layers", {}), "garbage"],
        )
        self.assertEqual(0, session.returncode, session.stderr)
        lines = [line for line in session.stdout.split("\n") if line]
        self.assertEqual(4, len(lines))
        for line in lines:
            message = json.loads(line)
            self.assertEqual("2.0", message["jsonrpc"])
            self.assertTrue("result" in message or "error" in message)
        self.assertIn("wingbox", session.stderr)


class McpAnswerLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _load_mcp_module()

    def test_answers_without_their_own_limit_stop_at_the_answer_limit(self) -> None:
        """/* REQ-MCP-009: a tool answer without its own list limit must stop at 200 lines and say how many were left out, unless the call sets all. */"""
        text = "\n".join(f"(dep a m{index})" for index in range(250))
        capped = self.server.cap_answer(text, show_all=False)
        lines = capped.splitlines()
        self.assertEqual(201, len(lines))
        self.assertEqual("(dep a m199)", lines[199])
        self.assertIn(":hidden 50", lines[200])
        self.assertIn("all", lines[200])
        self.assertEqual(text, self.server.cap_answer(text, show_all=True))
        self.assertEqual("(dep a b)", self.server.cap_answer("(dep a b)", show_all=False))

    def test_arguments_are_checked_against_the_input_schema(self) -> None:
        """/* REQ-MCP-010: tool arguments must be checked against the tool's input schema: required names, types, integer bounds and unknown names. */"""
        schema = {
            "type": "object",
            "properties": {
                "target": {"type": "string", "minLength": 1},
                "depth": {"type": "integer", "minimum": 0, "maximum": 50},
                "all": {"type": "boolean"},
                "exclude_dirs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["target"],
            "additionalProperties": False,
        }
        check = self.server.argument_errors
        self.assertEqual([], check(schema, {"target": "a.py", "depth": 3, "all": True, "exclude_dirs": ["x"]}))
        self.assertTrue(check(schema, {}))
        self.assertTrue(check(schema, {"target": ""}))
        self.assertTrue(check(schema, {"target": "a.py", "depth": 51}))
        self.assertTrue(check(schema, {"target": "a.py", "depth": True}))
        self.assertTrue(check(schema, {"target": "a.py", "all": "yes"}))
        self.assertTrue(check(schema, {"target": "a.py", "exclude_dirs": [1]}))
        self.assertTrue(check(schema, {"target": "a.py", "other": 1}))
        self.assertTrue(check(schema, ["a.py"]))


if __name__ == "__main__":
    unittest.main()
