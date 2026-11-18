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
EXPECTED_TOOLS = {"build", "impact", "module", "definitions", "usages", "dependencies", "calls", "layers", "boundaries", "changes", "query"}


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
        self.assertIn("build", first["instructions"])
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
        """/* REQ-MCP-003: `tools/list` must name the build tool and the read commands, each with a one-line description and a JSON object input schema. */"""
        session = McpSession(self.root, [_initialize(), _request(2, "tools/list")])
        tools = session.reply(2)["result"]["tools"]
        self.assertEqual(EXPECTED_TOOLS, {tool["name"] for tool in tools})
        for tool in tools:
            self.assertTrue(tool["description"].strip(), tool["name"])
            self.assertNotIn("\n", tool["description"], tool["name"])
            self.assertEqual("object", tool["inputSchema"]["type"], tool["name"])
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True), tool["name"])
        impact = next(tool for tool in tools if tool["name"] == "impact")
        self.assertEqual(["target"], impact["inputSchema"]["required"])
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
            self.assertIs(annotations["readOnlyHint"], tool["name"] != "build", tool["name"])
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
        """/* REQ-MCP-004: the `build` tool must build the code graph of --root, and `impact` must then return the text answer of `wingbox impact` only, as one text item. */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [
                _initialize(),
                _call(2, "build", {}),
                _call(3, "impact", {"target": "shop/pricing.py"}),
                _call(4, "dependencies", {"module": "shop/checkout", "depth": 1}),
            ],
        )
        build = session.reply(2)["result"]
        self.assertFalse(build["isError"], build)
        self.assertTrue((self.root / ".wingbox/index.sexp").is_file())
        impact = session.reply(3)["result"]
        self.assertFalse(impact["isError"], impact)
        texts = _texts(session.reply(3))
        self.assertEqual(len(texts), 1)
        self.assertIn("Impact of changing shop/pricing.py", texts[0])
        self.assertIn("shop/checkout.py", texts[0])
        self.assertNotIn("(impact ", texts[0])
        deps = session.reply(4)["result"]
        self.assertFalse(deps["isError"], deps)
        self.assertIn("(dep shop/checkout shop/pricing", _texts(session.reply(4))[0])

    def test_module_states_validation_only_with_the_evidence_of_the_stored_fact(self) -> None:
        """/* REQ-MCP-020: after `build`, the `module` tool (the `analyze` command) prints the validation mark of a data-flow fact with its evidence for a module that imports a pydantic model, and no data-flow line for a module that imports a file whose comment only names a validator; no answer holds the retired mark `:validated`. */"""
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
                _call(2, "build", {}),
                _call(3, "module", {"module": "shop/api"}),
                _call(4, "module", {"module": "shop/report"}),
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
        """/* REQ-MCP-011: the `usages` tool must return the text answer of `wingbox usages`: every use of the symbol by file with line:column and kind, as one text item and no S-expression facts. */"""
        _make_shop(self.root)
        session = McpSession(
            self.root,
            [_initialize(), _call(2, "build", {}), _call(3, "usages", {"symbol": "shop/pricing.py::total"})],
        )
        answer = session.reply(3)["result"]
        self.assertFalse(answer["isError"], answer)
        texts = _texts(session.reply(3))
        self.assertEqual(1, len(texts))
        self.assertIn("Usages of total (function, shop/pricing.py:1)", texts[0])
        self.assertIn("shop/checkout.py\n  1:26   import\n  5:12   call", texts[0])
        self.assertNotIn("(usage ", texts[0])

    def test_a_query_without_a_graph_asks_for_build(self) -> None:
        """/* REQ-MCP-005: a query tool called before any graph exists must return a tool error that tells the agent to call `build` first. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "impact", {"target": "shop/pricing.py"})])
        result = session.reply(2)["result"]
        self.assertTrue(result["isError"])
        self.assertIn("call the build tool first", _texts(session.reply(2))[0])

    def test_with_build_missing_the_first_query_builds_the_graph(self) -> None:
        """/* REQ-MCP-015: `wingbox mcp --root DIR --build-missing` (what the Claude Desktop extension starts) builds the graph of DIR in the background when it has none; a tool call waits for that build up to WINGBOX_FIRST_BUILD_WAIT seconds (default 45) and then answers from the new graph. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "impact", {"target": "shop/pricing.py"})], ("--build-missing",))
        result = session.reply(2)["result"]
        self.assertFalse(result["isError"], _texts(session.reply(2)))
        self.assertIn("shop/checkout", _texts(session.reply(2))[0])
        self.assertTrue((self.root / ".wingbox" / "index.sexp").is_file())

    def test_a_first_build_longer_than_the_wait_is_reported_not_hung(self) -> None:
        """/* REQ-MCP-016: when the first build takes longer than WINGBOX_FIRST_BUILD_WAIT seconds (a large repository), a tool call returns at once with a tool error that says the graph of DIR is being built for the first time and to call again shortly; the build goes on in the background. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "impact", {"target": "shop/pricing.py"})], ("--build-missing",),
                             {"WINGBOX_FIRST_BUILD_WAIT": "0"})
        result = session.reply(2)["result"]
        self.assertTrue(result["isError"])
        self.assertIn("is building the code graph", _texts(session.reply(2))[0])
        self.assertIn("Call the tool again", _texts(session.reply(2))[0])

    def test_without_build_missing_nothing_is_built(self) -> None:
        """/* REQ-MCP-017: without --build-missing the server never builds on its own (the plugin starts it in whatever folder the session runs in); an unknown argument exits 2 with the usage. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "impact", {"target": "shop/pricing.py"})])
        self.assertTrue(session.reply(2)["result"]["isError"])
        self.assertFalse((self.root / ".wingbox").exists())
        self.assertEqual(2, McpSession(self.root, [], ("--bogus",)).returncode)

    def test_two_servers_started_together_build_once_without_an_error(self) -> None:
        """/* REQ-MCP-018: two `wingbox mcp --build-missing` started at the same moment on a repository without a graph (Claude Desktop starts its server twice) run one build: the other server says `[wingbox] a build is already running: waiting for it`, no line contains ERROR, and both answer impact from the graph. */"""
        _make_shop(self.root)
        environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        lines = "".join(line + "\n" for line in [_initialize(), _call(2, "impact", {"target": "shop/pricing.py"})])
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
        session = McpSession(self.root, [_initialize(), _call(2, "impact", {"target": "notes.txt"}), _call(3, "build", {})], ("--build-missing",))
        for identifier in (2, 3):
            text = _texts(session.reply(identifier))[0]
            self.assertIn("No repository here", text)
        self.assertFalse((self.root / ".wingbox").exists())

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
                _call(4, "impact", {"depth": 2}),
                _call(5, "impact", {"target": "a.py", "depth": "deep"}),
                _call(6, "impact", {"target": "a.py", "colour": "red"}),
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
            [_initialize(), _call(2, "build", {"full": True}), _call(3, "layers", {}), "garbage"],
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
