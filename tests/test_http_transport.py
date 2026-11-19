import ast
import contextlib
import http.client
import importlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
PACKAGE = ROOT / "packages" / "wingbox-http"
for folder in (SOURCE, PACKAGE / "src"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from wingbox import mcp_server  # noqa: E402
from wingbox_http import listener, settings as http_settings, transport  # noqa: E402

LEGACY = "2025-11-25"
MODERN = "2026-07-28"
TOKEN = "t" * 40
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"


def _policy(access=transport.Open(), origins=()):
    return transport.Policy(
        access=access,
        origins=frozenset(origins),
        versions=mcp_server.SUPPORTED_VERSIONS,
        modern_versions=mcp_server.MODERN_VERSIONS,
        meta_version=META_VERSION,
    )


def _message(method="ping", identifier=1, params=None):
    message = {"jsonrpc": "2.0", "method": method}
    if identifier is not None:
        message["id"] = identifier
    if params is not None:
        message["params"] = params
    return message


def _post(message=None, headers=None, body=None, method="POST", path="/mcp"):
    base = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    base.update(headers or {})
    raw = body if body is not None else json.dumps(message if message is not None else _message()).encode("utf-8")
    return transport.Request(method=method, path=path, headers=base, body=raw)


def _error_code(refusal):
    return json.loads(refusal.body)["error"]["code"]


class DecisionTest(unittest.TestCase):
    def test_only_the_mcp_endpoint_exists(self) -> None:
        """/* REQ-HTTP-001: the transport has one endpoint, /mcp; any other path is answered 404 and never reaches the server. */"""
        decision = transport.decide(_policy(), _post(path="/"))
        self.assertIsInstance(decision, transport.Refuse)
        self.assertEqual(404, decision.status)
        self.assertIsInstance(transport.decide(_policy(), _post(path="/mcp?x=1")), transport.Forward)

    def test_a_foreign_origin_is_refused(self) -> None:
        """/* REQ-HTTP-002: a request with an Origin header that is neither a loopback origin nor one given with --allow-origin is answered 403 (DNS rebinding); a request without Origin, as agents send it, passes. */"""
        refused = transport.decide(_policy(), _post(headers={"origin": "https://evil.example"}))
        self.assertIsInstance(refused, transport.Refuse)
        self.assertEqual(403, refused.status)
        for origin in ("http://localhost:3000", "http://127.0.0.1", "http://[::1]:8080"):
            self.assertIsInstance(transport.decide(_policy(), _post(headers={"origin": origin})), transport.Forward, origin)
        allowed = _policy(origins=("https://ide.example",))
        self.assertIsInstance(transport.decide(allowed, _post(headers={"origin": "https://ide.example"})), transport.Forward)
        self.assertEqual(403, transport.decide(allowed, _post(headers={"origin": "https://ide.example.evil.test"})).status)
        self.assertEqual(403, transport.decide(_policy(), _post(headers={"origin": "http://localhost.evil.test"})).status)

    def test_a_request_without_the_token_is_refused(self) -> None:
        """/* REQ-HTTP-003: with a token set, a request passes only with `Authorization: Bearer <token>`; a missing, wrong or differently spelled credential is answered 401 with a WWW-Authenticate header, whatever the method. */"""
        policy = _policy(access=transport.Token(TOKEN))
        for headers in ({}, {"authorization": "Bearer " + "x" * 40}, {"authorization": TOKEN}, {"authorization": "Basic " + TOKEN}):
            decision = transport.decide(policy, _post(headers=headers))
            self.assertIsInstance(decision, transport.Refuse, headers)
            self.assertEqual(401, decision.status)
            self.assertIn("Bearer", dict(decision.headers)["WWW-Authenticate"])
        self.assertEqual(401, transport.decide(policy, _post(method="GET")).status)
        self.assertIsInstance(transport.decide(policy, _post(headers={"authorization": "Bearer " + TOKEN})), transport.Forward)
        self.assertNotIn(TOKEN.encode("ascii"), transport.decide(policy, _post()).body)

    def test_get_and_delete_are_not_offered(self) -> None:
        """/* REQ-HTTP-004: the server opens no stream of its own and keeps no session, so GET and DELETE on the endpoint are answered 405 with `Allow: POST`. */"""
        for method in ("GET", "DELETE", "PUT"):
            decision = transport.decide(_policy(), _post(method=method))
            self.assertEqual(405, decision.status, method)
            self.assertEqual("POST", dict(decision.headers)["Allow"])

    def test_a_body_that_is_not_one_message_is_refused(self) -> None:
        """/* REQ-HTTP-005: a body that is not JSON is answered 400 with a JSON-RPC parse error, and a batch (a JSON array) 400 with an invalid-request error. */"""
        broken = transport.decide(_policy(), _post(body=b"{not json"))
        self.assertEqual((400, mcp_server.PARSE_ERROR), (broken.status, _error_code(broken)))
        batch = transport.decide(_policy(), _post(body=b"[]"))
        self.assertEqual((400, mcp_server.INVALID_REQUEST), (batch.status, _error_code(batch)))
        undecodable = transport.decide(_policy(), _post(body=b"\xff\xfe"))
        self.assertEqual((400, mcp_server.PARSE_ERROR), (undecodable.status, _error_code(undecodable)))

    def test_an_unknown_protocol_version_header_is_refused(self) -> None:
        """/* REQ-HTTP-006: an MCP-Protocol-Version header that names a revision the server does not speak is answered 400 with the list of supported revisions; a request without the header is served. */"""
        decision = transport.decide(_policy(), _post(headers={"mcp-protocol-version": "1999-01-01"}))
        self.assertEqual(400, decision.status)
        error = json.loads(decision.body)["error"]
        self.assertEqual(mcp_server.UNSUPPORTED_PROTOCOL_VERSION, error["code"])
        self.assertEqual(list(mcp_server.SUPPORTED_VERSIONS), error["data"]["supported"])
        self.assertIsInstance(transport.decide(_policy(), _post(headers={"mcp-protocol-version": LEGACY})), transport.Forward)
        self.assertIsInstance(transport.decide(_policy(), _post()), transport.Forward)

    def test_the_headers_of_a_2026_request_must_match_its_body(self) -> None:
        """/* REQ-HTTP-007: a request of revision 2026-07-28 must carry MCP-Protocol-Version and Mcp-Method equal to its body, and Mcp-Name equal to the tool of a tools/call; a missing or different header is answered 400 with error -32020. */"""
        meta = {META_VERSION: MODERN, META_CAPABILITIES: {}}
        call = _message("tools/call", params={"name": "analyze_impact", "arguments": {"target": "a.py"}, "_meta": meta})
        good = {"mcp-protocol-version": MODERN, "mcp-method": "tools/call", "mcp-name": "analyze_impact"}
        passed = transport.decide(_policy(), _post(call, headers=good))
        self.assertIsInstance(passed, transport.Forward)
        self.assertTrue(passed.modern)
        for name, value in (("mcp-protocol-version", LEGACY), ("mcp-method", "tools/list"), ("mcp-name", "usages")):
            for headers in ({**good, name: value}, {k: v for k, v in good.items() if k != name}):
                decision = transport.decide(_policy(), _post(call, headers=headers))
                self.assertIsInstance(decision, transport.Refuse, headers)
                self.assertEqual((400, transport.HEADER_MISMATCH), (decision.status, _error_code(decision)), headers)
        listing = _message("tools/list", params={"_meta": meta})
        self.assertIsInstance(
            transport.decide(_policy(), _post(listing, headers={"mcp-protocol-version": MODERN, "mcp-method": "tools/list"})),
            transport.Forward,
        )
        legacy = transport.decide(_policy(), _post(_message("tools/list"), headers={"mcp-protocol-version": LEGACY}))
        self.assertFalse(legacy.modern)
        claimed = transport.decide(_policy(), _post(_message("tools/list"), headers={"mcp-protocol-version": MODERN}))
        self.assertEqual((400, transport.HEADER_MISMATCH), (claimed.status, _error_code(claimed)))

    def test_the_status_follows_the_reply(self) -> None:
        """/* REQ-HTTP-008: a notification is answered 202 without a body, a result 200, a message the server cannot accept 400, an unknown method 404 under revision 2026-07-28 and 200 with the JSON-RPC error under the handshake revisions. */"""
        self.assertEqual(202, transport.reply_status(None, modern=False))
        self.assertEqual(200, transport.reply_status({"jsonrpc": "2.0", "id": 1, "result": {}}, modern=True))
        invalid = {"jsonrpc": "2.0", "id": None, "error": {"code": mcp_server.INVALID_REQUEST, "message": "x"}}
        self.assertEqual(400, transport.reply_status(invalid, modern=False))
        unknown = {"jsonrpc": "2.0", "id": 1, "error": {"code": mcp_server.METHOD_NOT_FOUND, "message": "x"}}
        self.assertEqual(404, transport.reply_status(unknown, modern=True))
        self.assertEqual(200, transport.reply_status(unknown, modern=False))
        params = {"jsonrpc": "2.0", "id": 1, "error": {"code": mcp_server.INVALID_PARAMS, "message": "x"}}
        self.assertEqual(200, transport.reply_status(params, modern=True))

    def test_what_the_client_sends_and_accepts_is_checked(self) -> None:
        """/* REQ-HTTP-009: a body that is not declared as application/json is answered 415, an Accept header that allows neither JSON nor an event stream 406, a body over the size limit 413. */"""
        self.assertEqual(415, transport.decide(_policy(), _post(headers={"content-type": "text/plain"})).status)
        self.assertEqual(406, transport.decide(_policy(), _post(headers={"accept": "text/html"})).status)
        self.assertIsInstance(transport.decide(_policy(), _post(headers={"accept": "*/*"})), transport.Forward)
        self.assertIsInstance(
            transport.decide(_policy(), _post(headers={"content-type": "application/json; charset=utf-8"})), transport.Forward
        )
        self.assertEqual(413, transport.decide(_policy(), _post(body=b" " * (transport.MAX_BODY_BYTES + 1))).status)


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = os.path.realpath(self.folder.name)

    def _read(self, args, env=None):
        return http_settings.read(list(args), self.root, env or {})

    def test_the_default_is_this_machine_only(self) -> None:
        """/* REQ-HTTP-010: without arguments the server listens on 127.0.0.1 only, for the current directory, with no token required. */"""
        result = self._read([])
        self.assertIsInstance(result, http_settings.Settings)
        self.assertEqual(("127.0.0.1", 8765, self.root), (result.host, result.port, result.root))
        self.assertIsInstance(result.access, transport.Open)

    def test_another_address_needs_a_token(self) -> None:
        """/* REQ-HTTP-011: listening on any address other than loopback is refused without a token; the token comes from WINGBOX_HTTP_TOKEN or from --token-file, is at least 32 visible ASCII characters, and cannot be given on the command line, where the process list would show it. */"""
        refused = self._read(["--host", "0.0.0.0"])
        self.assertIsInstance(refused, http_settings.Problem)
        self.assertIn("token", refused.message)
        self.assertIsInstance(self._read(["--host", "0.0.0.0"], {"WINGBOX_HTTP_TOKEN": TOKEN}).access, transport.Token)
        token_file = Path(self.root) / "token"
        token_file.write_text(TOKEN + "\n", encoding="ascii")
        from_file = self._read(["--host", "0.0.0.0", "--token-file", str(token_file)])
        self.assertEqual(transport.Token(TOKEN), from_file.access)
        for weak in ("short", "s p a c e " * 5, "é" * 40):
            problem = self._read([], {"WINGBOX_HTTP_TOKEN": weak})
            self.assertIsInstance(problem, http_settings.Problem, weak)
            self.assertNotIn(weak, problem.message)
        self.assertIsInstance(self._read(["--token", TOKEN]), http_settings.Problem)
        self.assertIsInstance(self._read(["--token-file", str(Path(self.root) / "absent")]), http_settings.Problem)

    def test_the_other_arguments_are_checked(self) -> None:
        """/* REQ-HTTP-012: --root must be a directory, --port a port number, --tls-cert and --tls-key come together, and --allow-origin takes a scheme://host[:port] origin. */"""
        self.assertIsInstance(self._read(["--root", "absent"]), http_settings.Problem)
        self.assertIsInstance(self._read(["--port", "70000"]), http_settings.Problem)
        self.assertIsInstance(self._read(["--tls-cert", "a.pem"]), http_settings.Problem)
        self.assertIsInstance(self._read(["--allow-origin", "ide.example"]), http_settings.Problem)
        good = self._read(["--allow-origin", "https://ide.example", "--allow-origin", "https://b.example:8443", "--port", "0", "--build-missing"])
        self.assertEqual(frozenset({"https://ide.example", "https://b.example:8443"}), good.origins)
        self.assertTrue(good.build_missing)


class Running:
    """A listener on a free loopback port, served from a thread."""

    def __init__(self, handle, access=transport.Open(), first_wait=1.0, keep_alive=10.0, tls=None) -> None:
        policy = _policy(access=access)
        self.httpd = listener.make_listener("127.0.0.1", 0, policy, handle, first_wait=first_wait, keep_alive=keep_alive, tls=tls)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)

    def post(self, message, headers=None, context=None):
        if context is None:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        else:
            connection = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=30, context=context)
        base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        base.update(headers or {})
        try:
            connection.request("POST", "/mcp", body=json.dumps(message).encode("utf-8"), headers=base)
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type", ""), response.read().decode("utf-8")
        finally:
            connection.close()


def _make_shop(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "shop").mkdir()
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (root / "shop" / "checkout.py").write_text(
        "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n", encoding="utf-8"
    )


class ListenerTest(unittest.TestCase):
    def test_a_client_talks_to_the_real_server_over_http(self) -> None:
        """/* REQ-HTTP-013: over HTTP the same server answers as over stdio: initialize, the initialized notification (202, no body), tools/list with every tool, then build and impact on a repository; a request without the token gets 401. */"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(os.path.realpath(folder))
            _make_shop(root)
            server = mcp_server.Server(str(root))
            with Running(lambda line: mcp_server.handle_line(server, line), access=transport.Token(TOKEN)) as running:
                auth = {"Authorization": "Bearer " + TOKEN}
                status, _, _ = running.post(_message("ping"))
                self.assertEqual(401, status)
                status, kind, body = running.post(
                    _message("initialize", params={"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
                    auth,
                )
                self.assertEqual((200, "application/json"), (status, kind))
                self.assertEqual(LEGACY, json.loads(body)["result"]["protocolVersion"])
                status, _, body = running.post(_message("notifications/initialized", identifier=None), {**auth, "MCP-Protocol-Version": LEGACY})
                self.assertEqual((202, ""), (status, body))
                status, _, body = running.post(_message("tools/list", 2), {**auth, "MCP-Protocol-Version": LEGACY})
                self.assertEqual(200, status)
                self.assertEqual({tool.name for tool in mcp_server.TOOLS}, {tool["name"] for tool in json.loads(body)["result"]["tools"]})
                status, kind, body = running.post(_message("tools/call", 3, {"name": "build_graph", "arguments": {}}), auth)
                self.assertEqual(200, status)
                built = _event_or_json(kind, body)["result"]
                self.assertFalse(built["isError"], built)
                status, kind, body = running.post(
                    _message("tools/call", 4, {"name": "analyze_impact", "arguments": {"target": "shop/pricing.py"}}), auth
                )
                answer = _event_or_json(kind, body)["result"]
                self.assertFalse(answer["isError"], answer)
                self.assertIn("checkout", answer["content"][0]["text"])
                meta = {META_VERSION: MODERN, META_CAPABILITIES: {}}
                status, _, body = running.post(
                    _message("nothing/here", 5, {"_meta": meta}), {**auth, "MCP-Protocol-Version": MODERN, "Mcp-Method": "nothing/here"}
                )
                self.assertEqual((404, mcp_server.METHOD_NOT_FOUND), (status, json.loads(body)["error"]["code"]))

    def test_a_long_call_is_answered_as_an_event_stream(self) -> None:
        """/* REQ-HTTP-014: a tools/call that takes longer than the first wait is answered as an event stream when the client accepts one: keep-alive comment lines while the tool runs, then one `message` event with the reply; a quick call is answered as plain JSON. */"""

        def slow(line):
            message = json.loads(line)
            if message["params"]["name"] == "slow":
                time.sleep(0.9)
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [], "isError": False}}

        with Running(slow, first_wait=0.2, keep_alive=0.2) as running:
            status, kind, body = running.post(_message("tools/call", 7, {"name": "slow"}))
            self.assertEqual((200, "text/event-stream"), (status, kind))
            self.assertGreaterEqual(body.count(": keep-alive\n\n"), 2, body)
            self.assertTrue(body.endswith("\n\n"))
            event = body.split(": keep-alive\n\n")[-1]
            self.assertTrue(event.startswith("event: message\ndata: "), event)
            self.assertEqual(7, json.loads(event.split("data: ", 1)[1])["id"])
            status, kind, body = running.post(_message("tools/call", 8, {"name": "quick"}))
            self.assertEqual((200, "application/json", 8), (status, kind, json.loads(body)["id"]))

    def test_a_client_that_takes_json_only_waits_for_json(self) -> None:
        """/* REQ-HTTP-015: a client whose Accept header has no text/event-stream gets the reply of a long call as plain JSON. */"""

        def slow(line):
            time.sleep(0.5)
            return {"jsonrpc": "2.0", "id": json.loads(line)["id"], "result": {}}

        with Running(slow, first_wait=0.1, keep_alive=0.1) as running:
            status, kind, body = running.post(_message("tools/call", 9, {"name": "slow"}), {"Accept": "application/json"})
            self.assertEqual((200, "application/json", 9), (status, kind, json.loads(body)["id"]))

    def test_one_call_runs_at_a_time(self) -> None:
        """/* REQ-HTTP-016: requests that arrive together reach the server one after another, as on stdio: the server keeps one loaded graph and its commands are not written for concurrent use. */"""
        active, most = [0], [0]
        lock = threading.Lock()

        def handle(line):
            with lock:
                active[0] += 1
                most[0] = max(most[0], active[0])
            time.sleep(0.15)
            with lock:
                active[0] -= 1
            return {"jsonrpc": "2.0", "id": json.loads(line)["id"], "result": {}}

        with Running(handle) as running:
            threads = [threading.Thread(target=running.post, args=(_message("ping", number),)) for number in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertEqual(1, most[0])

    def test_a_request_without_a_length_or_over_the_limit_is_refused_unread(self) -> None:
        """/* REQ-HTTP-017: a POST without Content-Length is answered 411 and one that announces more than the size limit 413, before the body is read. */"""
        with Running(lambda line: None) as running:
            connection = http.client.HTTPConnection("127.0.0.1", running.port, timeout=10)
            try:
                connection.putrequest("POST", "/mcp")
                connection.putheader("Content-Type", "application/json")
                connection.endheaders()
                self.assertEqual(411, connection.getresponse().status)
            finally:
                connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", running.port, timeout=10)
            try:
                connection.putrequest("POST", "/mcp")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(transport.MAX_BODY_BYTES + 1))
                connection.endheaders()
                self.assertEqual(413, connection.getresponse().status)
            finally:
                connection.close()

    def test_tls(self) -> None:
        """/* REQ-HTTP-018: with --tls-cert and --tls-key the listener speaks HTTPS: a client that trusts the certificate gets its answer, and plain HTTP on the same port gets none. */"""
        openssl = shutil.which("openssl")
        if openssl is None:
            self.skipTest("openssl is not installed")
        with tempfile.TemporaryDirectory() as folder:
            cert, key = os.path.join(folder, "cert.pem"), os.path.join(folder, "key.pem")
            subprocess.run(
                [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                 "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", cert],
                check=True, capture_output=True,
            )
            reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
            with Running(lambda line: reply, tls=listener.Tls(cert, key)) as running:
                context = ssl.create_default_context(cafile=cert)
                status, _, body = running.post(_message("ping"), context=context)
                self.assertEqual((200, reply), (status, json.loads(body)))
                with self.assertRaises((http.client.HTTPException, OSError)):
                    running.post(_message("ping"))


def _event_or_json(kind: str, body: str):
    if kind == "text/event-stream":
        return json.loads(body.rsplit("data: ", 1)[1])
    return json.loads(body)


class CommandTest(unittest.TestCase):
    def test_wingbox_mcp_http_names_the_package_when_it_is_missing(self) -> None:
        """/* REQ-HTTP-019: `wingbox mcp --http` runs the transport of the separate package wingbox-http with the remaining arguments, and without that package says how to install it and exits 2: the core package holds no listening code. */"""
        errors = io.StringIO()
        with mock.patch.object(importlib, "import_module", side_effect=ImportError("no wingbox_http")), contextlib.redirect_stderr(errors):
            self.assertEqual(2, mcp_server.main(["--http", "--port", "0"], os.getcwd()))
        self.assertIn('pip install "wingbox[http]"', errors.getvalue())
        seen = []
        fake = mock.Mock()
        fake.main.side_effect = lambda args, cwd: seen.append((args, cwd)) or 0
        with mock.patch.object(importlib, "import_module", return_value=fake) as loader:
            self.assertEqual(0, mcp_server.main(["--http", "--root", "x", "--port", "9"], "/somewhere"))
        loader.assert_called_once_with("wingbox_http")
        self.assertEqual([(["--root", "x", "--port", "9"], "/somewhere")], seen)

    def test_a_refused_start_explains_itself(self) -> None:
        """/* REQ-HTTP-020: `wingbox-http --host 0.0.0.0` without a token exits 2 with the reason and opens no port. */"""
        errors = io.StringIO()
        with mock.patch.object(listener, "make_listener") as opened, contextlib.redirect_stderr(errors):
            from wingbox_http import main as http_main

            self.assertEqual(2, http_main(["--host", "0.0.0.0"], os.getcwd(), {}))
        opened.assert_not_called()
        self.assertIn("WINGBOX_HTTP_TOKEN", errors.getvalue())


class PackagingTest(unittest.TestCase):
    def test_the_adapter_only_listens(self) -> None:
        """/* REQ-SEC-010: the HTTP transport lives in its own distribution, outside the folders REQ-SEC-001 scans, and only listens: its code imports no HTTP client and opens no outbound connection. */"""
        outbound = re.compile(
            r"^\s*(?:import|from)\s+(?:urllib|http\.client|requests|httpx|aiohttp|urllib3|websockets?|smtplib|ftplib|telnetlib|xmlrpc)\b"
            r"|create_connection|\.connect(?:_ex)?\("
        )
        offenders = []
        files = sorted((PACKAGE / "src").rglob("*.py"))
        self.assertTrue(files)
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if outbound.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual([], offenders)
        self.assertFalse((SOURCE / "wingbox_http").exists())

    def test_the_core_loads_the_adapter_only_for_http(self) -> None:
        """/* REQ-HTTP-022: the core package never imports wingbox_http at start: its name is used in one place, `_http_main` of the MCP server, which only `main` calls and only for `--http`; no other file of the core mentions the package. */"""
        mentions = [
            str(path.relative_to(ROOT))
            for folder in ("src", "scripts")
            for path in sorted((ROOT / folder).rglob("*.py"))
            if "wingbox_http" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(["src/wingbox/mcp_server.py"], [mention.replace(os.sep, "/") for mention in mentions])
        tree = ast.parse((SOURCE / "wingbox" / "mcp_server.py").read_text(encoding="utf-8"))
        users, callers = set(), set()
        for function in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]:
            for node in ast.walk(function):
                if isinstance(node, ast.Name) and node.id == "HTTP_PACKAGE":
                    users.add(function.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_http_main":
                    callers.add(function.name)
        self.assertEqual(({"_http_main"}, {"main"}), (users, callers))
        self.assertNotIn("wingbox_http", {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names})
        errors = io.StringIO()
        with mock.patch.object(importlib, "import_module") as loader, contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, mcp_server.main(["--no-such-option"], os.getcwd()))
            self.assertEqual(0, mcp_server.main(["--help"], os.getcwd()))
        loader.assert_not_called()

    def test_the_versions_move_together(self) -> None:
        """/* REQ-HTTP-021: wingbox-http has the version of wingbox, requires exactly that version of it, and the `http` extra of wingbox pins exactly that version of wingbox-http. */"""
        core = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        adapter = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        version = core["version"]
        self.assertEqual(version, adapter["version"])
        self.assertEqual([f"wingbox=={version}"], adapter["dependencies"])
        self.assertEqual([f"wingbox-http=={version}"], core["optional-dependencies"]["http"])
        self.assertEqual("wingbox_http:run", adapter["scripts"]["wingbox-http"])


if __name__ == "__main__":
    unittest.main()
