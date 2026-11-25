import ast
import contextlib
import http.client
import importlib
import io
import json
import os
import re
import shutil
import socket
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

    def test_a_body_nested_too_deeply_is_a_parse_error(self) -> None:
        """/* REQ-HTTP-9920: a body of 200,000 opening brackets, which the JSON reader gives up on with a recursion error, is answered 400 with a JSON-RPC parse error like any other body that is not JSON. */"""
        for body in (b"[" * 200_000, b'{"a":' * 200_000):
            decision = transport.decide(_policy(), _post(body=body))
            self.assertIsInstance(decision, transport.Refuse)
            self.assertEqual((400, mcp_server.PARSE_ERROR), (decision.status, _error_code(decision)))

    def test_only_an_origin_given_with_allow_origin_gets_the_headers_a_browser_page_needs(self) -> None:
        """/* REQ-HTTP-9921: for an origin given with --allow-origin the transport answers the preflight of a browser (OPTIONS with Access-Control-Request-Method, which carries no token) with 204 and Access-Control-Allow-Methods POST and the request headers it reads, and every answer to that origin carries Access-Control-Allow-Origin with that origin and `Vary: Origin`; a loopback origin, which passes without the option, gets neither (its OPTIONS is answered 405, or 401 when a token is set), and a request without Origin gets none. */"""
        ide = "https://ide.example"
        for access in (transport.Open(), transport.Token(TOKEN)):
            policy = _policy(access=access, origins=(ide,))
            preflight = transport.decide(policy, _post(method="OPTIONS", body=b"", headers={"origin": ide, "access-control-request-method": "POST", "access-control-request-headers": "authorization, content-type"}))
            self.assertIsInstance(preflight, transport.Refuse)
            self.assertEqual((204, b""), (preflight.status, preflight.body))
            headers = dict(preflight.headers)
            self.assertEqual("POST", headers["Access-Control-Allow-Methods"])
            allowed = {name.strip().lower() for name in headers["Access-Control-Allow-Headers"].split(",")}
            self.assertEqual({"authorization", "content-type", "accept", "mcp-protocol-version", "mcp-method", "mcp-name"}, allowed)
            self.assertTrue(headers["Access-Control-Max-Age"].isdigit())
            local = transport.decide(policy, _post(method="OPTIONS", body=b"", headers={"origin": "http://localhost:3000", "access-control-request-method": "POST"}))
            self.assertEqual(405 if isinstance(access, transport.Open) else 401, local.status)
            self.assertFalse([name for name, _ in local.headers if name.lower().startswith("access-control-")])
            plain = transport.decide(policy, _post(method="OPTIONS", body=b"", headers={"origin": ide}))
            self.assertEqual(405 if isinstance(access, transport.Open) else 401, plain.status)
            self.assertEqual((("Access-Control-Allow-Origin", ide), ("Vary", "Origin")), transport.cors_headers(policy, ide))
            for origin in ("http://localhost:3000", "http://127.0.0.1:8765", "http://[::1]", "https://other.example", None):
                self.assertEqual((), transport.cors_headers(policy, origin), origin)
        self.assertEqual((), transport.cors_headers(_policy(), ide))
        self.assertEqual(403, transport.decide(_policy(), _post(method="OPTIONS", body=b"", headers={"origin": ide, "access-control-request-method": "POST"})).status)


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

    def test_the_value_of_an_unknown_option_is_never_repeated(self) -> None:
        """/* REQ-HTTP-9922: an argument the command does not take is refused without repeating what was typed after it: `--token <secret>`, `--token=<secret>` and a bare value give a message that names the option (`--token`) or says that an argument is not expected, and never the value, so the entry point, which prints the message, cannot log a token; the message for --token says where the token is read from. */"""
        secret = "S3cret-" + "s" * 40
        for arguments in (["--token", secret], [f"--token={secret}"], [secret], ["--port", "0", "--token", secret, "--bogus", "-" + secret]):
            problem = self._read(arguments)
            self.assertIsInstance(problem, http_settings.Problem, arguments)
            self.assertNotIn(secret, problem.message)
            self.assertNotIn("s" * 8, problem.message)
        named = self._read(["--token", secret]).message
        self.assertIn("--token", named)
        self.assertIn("WINGBOX_HTTP_TOKEN", named)
        self.assertIn("--bogus", self._read(["--bogus", "x"]).message)
        errors = io.StringIO()
        with mock.patch.object(listener, "make_listener") as opened, contextlib.redirect_stderr(errors):
            from wingbox_http import main as http_main

            self.assertEqual(2, http_main(["--token", secret], self.root, {}))
        opened.assert_not_called()
        self.assertNotIn(secret, errors.getvalue())
        self.assertIn("--token", errors.getvalue())

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

    def __init__(self, handle, access=transport.Open(), first_wait=1.0, keep_alive=10.0, tls=None, origins=(), limits=None) -> None:
        policy = _policy(access=access, origins=origins)
        self.log = io.StringIO()
        more = {} if limits is None else {"limits": listener.Limits(**limits)}
        self.httpd = listener.make_listener("127.0.0.1", 0, policy, handle, first_wait=first_wait, keep_alive=keep_alive, tls=tls, log=self.log, **more)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)

    def raw(self, data: bytes, wait: float = 10.0) -> bytes:
        """Send bytes as they are and return what comes back until the server closes the connection."""
        with socket.socket() as client:
            client.settimeout(wait)
            client.connect(("127.0.0.1", self.port))
            client.sendall(data)
            return _read_all(client)

    def idle(self) -> socket.socket:
        """A connection that sends nothing."""
        client = socket.socket()
        client.settimeout(10)
        client.connect(("127.0.0.1", self.port))
        return client

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


def _read_all(client: socket.socket) -> bytes:
    chunks = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _raw_post(message, extra: bytes = b"") -> bytes:
    body = json.dumps(message).encode("utf-8")
    return (b"POST /mcp HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\n" + extra
            + b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\nConnection: close\r\n\r\n" + body)


def _until(condition, seconds: float = 15.0) -> bool:
    """Wait until condition() holds; False when it does not within the time."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _handler_threads() -> int:
    return sum(1 for thread in threading.enumerate() if "process_request_thread" in thread.name)


def _status(answer: bytes) -> int:
    return int(answer.split(b" ", 2)[1]) if answer.startswith(b"HTTP/1.1 ") else 0


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

    def test_a_request_the_listener_cannot_read_is_answered_and_the_listener_goes_on(self) -> None:
        """/* REQ-HTTP-9923: a Content-Length that is not a number in ASCII digits (the superscript two passes `isdigit()` and has no integer value; more digits than a number of the size limit) is answered 400, or 413 for a number over the limit, and a body nested too deeply for the JSON reader 400 with a parse error: each gets an HTTP answer instead of a dropped connection, and the listener answers the next request. */"""
        reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
        head = b"POST /mcp HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\nConnection: close\r\n"
        with Running(lambda line: reply) as running:
            for length, status in ((b"\xb2", 400), (b"12a", 400), (b"-1", 400), (b"9" * 5000, 413), (b"0" * 30 + b"2", 200)):
                answer = running.raw(head + b"Content-Length: " + length + b"\r\n\r\n{}")
                self.assertEqual(status, _status(answer), (length[:8], answer[:120]))
            deep = b"[" * 200_000
            answer = running.raw(head + b"Content-Length: " + str(len(deep)).encode("ascii") + b"\r\n\r\n" + deep)
            self.assertEqual(400, _status(answer), answer[:120])
            self.assertEqual(mcp_server.PARSE_ERROR, json.loads(answer.split(b"\r\n\r\n", 1)[1])["error"]["code"])
            status, _, body = running.post(_message("ping"))
            self.assertEqual((200, reply), (status, json.loads(body)))

    def test_the_number_of_open_connections_is_capped(self) -> None:
        """/* REQ-HTTP-9924: the listener keeps at most `Limits.connections` connections open, each with one thread (64 by default): a connection beyond that gets no thread and is answered 503 at once, with or without a token; when a connection ends, the next one is served. */"""
        reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
        self.assertEqual(64, listener.Limits().connections)
        with Running(lambda line: reply, limits={"connections": 3}) as running:
            threads = _handler_threads()   # of listeners of earlier tests; they can only become fewer
            held = [running.idle() for _ in range(3)]
            try:
                self.assertTrue(_until(lambda: running.httpd._open == 3), running.httpd._open)
                for _ in range(5):
                    with running.idle() as extra:
                        answer = _read_all(extra)
                    self.assertEqual(503, _status(answer), answer[:120])
                    self.assertIn(b"Connection: close", answer)
                self.assertEqual(3, running.httpd._open)
                self.assertLessEqual(_handler_threads() - threads, 3)
            finally:
                for client in held:
                    client.close()
            self.assertTrue(_until(lambda: running.httpd._open == 0), running.httpd._open)
            status, _, body = running.post(_message("ping"))
            self.assertEqual((200, reply), (status, json.loads(body)))

    def test_a_request_must_arrive_within_a_total_time(self) -> None:
        """/* REQ-HTTP-9925: the header and the body of one request must arrive within `Limits.request_seconds` in all (30 by default): a client that sends one byte at a time, each well within that time, is cut off when the total is over, in the header and in the body alike, so no client holds a thread for as long as it likes; a request that arrives in time is answered. */"""
        reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
        self.assertEqual(30.0, listener.Limits().request_seconds)
        body = json.dumps(_message("ping")).encode("utf-8")
        head = b"POST /mcp HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        with Running(lambda line: reply, limits={"request_seconds": 1.0}) as running:
            for sent_first, slow in ((b"", head + b"X-Slow: " + b"a" * 400), (head + b"\r\n", body)):
                with running.idle() as client:
                    client.sendall(sent_first)
                    started = time.monotonic()
                    closed = None
                    client.settimeout(0.01)
                    for byte in slow[:60]:
                        try:
                            client.sendall(bytes([byte]))
                            if client.recv(4096) == b"":
                                closed = time.monotonic() - started
                                break
                        except TimeoutError:
                            pass
                        except OSError:
                            closed = time.monotonic() - started
                            break
                        time.sleep(0.1)
                self.assertIsNotNone(closed, "the connection was held for as long as bytes came")
                self.assertLess(closed, 4.0)
                self.assertGreater(closed, 0.5)
            self.assertIn("timed out", running.log.getvalue())
            status, _, answer = running.post(_message("ping"))
            self.assertEqual((200, reply), (status, json.loads(answer)))

    def test_the_number_of_waiting_calls_is_capped(self) -> None:
        """/* REQ-HTTP-9926: at most `Limits.calls` calls run or wait for the one worker (16 by default): a call beyond that is answered 503 with a JSON-RPC error and Retry-After and never reaches the server; the calls that were taken are answered, and once they ended the next call is taken again. */"""
        release = threading.Event()
        seen = []

        def handle(line):
            message = json.loads(line)
            seen.append(message["id"])
            release.wait(20)
            return {"jsonrpc": "2.0", "id": message["id"], "result": {}}

        self.assertEqual(16, listener.Limits().calls)
        answers = {}
        with Running(handle, limits={"calls": 2}) as running:
            def call(number):
                answers[number] = running.post(_message("ping", number), {"Accept": "application/json"})

            taken = [threading.Thread(target=call, args=(number,)) for number in (1, 2)]
            for count, thread in enumerate(taken, start=1):
                thread.start()
                self.assertTrue(_until(lambda: running.httpd._taken == count), running.httpd._taken)   # one runs, one waits
            answer = running.raw(_raw_post(_message("ping", 3)))
            self.assertEqual(503, _status(answer), answer[:200])
            self.assertIn(b"Retry-After: ", answer)
            self.assertEqual(transport.SERVER_BUSY, json.loads(answer.split(b"\r\n\r\n", 1)[1])["error"]["code"])
            release.set()
            for thread in taken:
                thread.join(timeout=20)
            self.assertEqual({1: 200, 2: 200}, {number: answer[0] for number, answer in answers.items()})
            self.assertEqual([1, 2], seen)
            status, _, body = running.post(_message("ping", 4))
            self.assertEqual((200, 4), (status, json.loads(body)["id"]))

    def test_a_waiting_call_whose_client_has_gone_is_not_run(self) -> None:
        """/* REQ-HTTP-9927: a call that still waits for its turn when its client closes the connection is taken out of the queue and never reaches the server, so abandoned calls do not make the next caller wait; the call that is running cannot be interrupted and ends; the log names the client that left. */"""
        release = threading.Event()
        seen = []

        def handle(line):
            message = json.loads(line)
            seen.append(message["id"])
            if message["id"] == 1:
                release.wait(20)
            return {"jsonrpc": "2.0", "id": message["id"], "result": {}}

        with Running(handle) as running:
            first = threading.Thread(target=running.post, args=(_message("tools/call", 1, {"name": "slow"}), {"Accept": "application/json"}))
            first.start()
            self.assertTrue(_until(lambda: seen == [1]), seen)   # the first call runs; every later one waits for it
            for number in (2, 3, 4):
                with running.idle() as client:
                    accept = b"" if number == 2 else b"Accept: application/json, text/event-stream\r\n"
                    client.sendall(_raw_post(_message("tools/call", number, {"name": "abandoned"}), accept))
                    self.assertTrue(_until(lambda: running.httpd._taken == 2), running.httpd._taken)   # it is in the queue
                # the client is gone: its connection sees that within a few looks and takes the call out
                self.assertTrue(_until(lambda: running.httpd._taken == 1), running.httpd._taken)
            self.assertTrue(_until(lambda: running.log.getvalue().count("left before the answer") == 3), running.log.getvalue())
            release.set()
            first.join(timeout=20)
            status, _, body = running.post(_message("ping", 9))
            self.assertEqual((200, 9), (status, json.loads(body)["id"]))
            self.assertEqual([1, 9], seen)
            self.assertEqual(3, running.log.getvalue().count("left before the answer"), running.log.getvalue())

    def test_a_page_of_an_allowed_origin_can_read_the_answers(self) -> None:
        """/* REQ-HTTP-9928: over a real connection, a page of an origin given with --allow-origin gets its preflight answered 204 with the Access-Control headers, without a token, and its POST answered with Access-Control-Allow-Origin naming that origin, also when the answer is a refusal (401) or an event stream; a page of a loopback origin gets its POST served but no Access-Control header on any answer, and its preflight refused, so its browser hands it nothing. */"""
        ide = "https://ide.example"

        def handle(line):
            message = json.loads(line)
            if message.get("params", {}).get("name") == "slow":
                time.sleep(0.6)
            return {"jsonrpc": "2.0", "id": message["id"], "result": {}}

        def headers_of(answer: bytes) -> dict[str, str]:
            lines = answer.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")[1:]
            return {name.lower(): value.strip() for name, value in (line.split(":", 1) for line in lines)}

        preflight = (b"OPTIONS /mcp HTTP/1.1\r\nHost: test\r\nOrigin: %s\r\nAccess-Control-Request-Method: POST\r\n"
                     b"Access-Control-Request-Headers: authorization, content-type\r\nConnection: close\r\n\r\n")
        with Running(handle, access=transport.Token(TOKEN), origins=(ide,), first_wait=0.2, keep_alive=0.2) as running:
            auth = b"Authorization: Bearer " + TOKEN.encode("ascii") + b"\r\n"
            answer = running.raw(preflight % ide.encode("ascii"))
            self.assertEqual(204, _status(answer), answer[:200])
            sent = headers_of(answer)
            self.assertEqual((ide, "POST", "Origin"), (sent["access-control-allow-origin"], sent["access-control-allow-methods"], sent["vary"]))
            self.assertIn("authorization", sent["access-control-allow-headers"].lower())
            for request, status in (
                (_raw_post(_message("ping"), auth + b"Origin: " + ide.encode("ascii") + b"\r\n"), 200),
                (_raw_post(_message("ping"), b"Origin: " + ide.encode("ascii") + b"\r\n"), 401),
                (_raw_post(_message("tools/call", 2, {"name": "slow"}), auth + b"Accept: text/event-stream\r\nOrigin: " + ide.encode("ascii") + b"\r\n"), 200),
            ):
                answer = running.raw(request)
                self.assertEqual(status, _status(answer), answer[:200])
                self.assertEqual(ide, headers_of(answer).get("access-control-allow-origin"), answer[:300])
            self.assertIn(b"event: message", answer)
            local = b"Origin: http://localhost:3000\r\n"
            for request, status in (
                (_raw_post(_message("ping"), auth + local), 200),
                (_raw_post(_message("ping"), local), 401),
                (preflight % b"http://localhost:3000", 401),
                (_raw_post(_message("ping"), auth), 200),
            ):
                answer = running.raw(request)
                self.assertEqual(status, _status(answer), answer[:200])
                self.assertFalse([name for name in headers_of(answer) if name.startswith("access-control-")], answer[:300])

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


    def test_the_entry_point_listens_with_the_settings_it_was_given(self) -> None:
        """/* REQ-HTTP-9929: `wingbox-http` opens its listener with exactly what the arguments, the environment and the token file say: the host and the port, the token as the access rule (and no token only on a loopback address), the certificate and key of --tls-cert and --tls-key, the origins of --allow-origin, and the protocol revisions of the MCP server; its first log line names the address and the project whose graph is served, and never the token. */"""
        from wingbox_http import main as http_main

        with tempfile.TemporaryDirectory() as folder:
            root = os.path.realpath(folder)
            token_file = os.path.join(root, "token")
            Path(token_file).write_text(TOKEN + "\n", encoding="ascii")

            def start(arguments, env):
                httpd = mock.Mock()
                httpd.server_address = ("ignored", 4321)
                errors = io.StringIO()
                with mock.patch.object(listener, "make_listener", return_value=httpd) as opened, contextlib.redirect_stderr(errors):
                    status = http_main(arguments, root, env)
                self.assertEqual(0, status, errors.getvalue())
                httpd.serve_forever.assert_called_once_with()
                httpd.server_close.assert_called_once_with()
                opened.assert_called_once()
                return opened.call_args, errors.getvalue()

            (host, port, policy, handle), more = start(
                ["--host", "0.0.0.0", "--port", "9123", "--token-file", token_file, "--tls-cert", "c.pem", "--tls-key", "k.pem",
                 "--allow-origin", "https://ide.example"], {})[0]
            self.assertEqual(("0.0.0.0", 9123), (host, port))
            self.assertEqual(transport.Token(TOKEN), policy.access)
            self.assertEqual(frozenset({"https://ide.example"}), policy.origins)
            self.assertEqual((mcp_server.SUPPORTED_VERSIONS, mcp_server.MODERN_VERSIONS, mcp_server.META_VERSION),
                             (policy.versions, policy.modern_versions, policy.meta_version))
            self.assertEqual(listener.Tls("c.pem", "k.pem"), more["tls"])
            self.assertEqual("2.0", handle(json.dumps(_message("ping")))["jsonrpc"])

            arguments, log = start(["--host", "0.0.0.0", "--port", "9123"], {"WINGBOX_HTTP_TOKEN": TOKEN})
            self.assertEqual(transport.Token(TOKEN), arguments[0][2].access)
            self.assertIsNone(arguments[1]["tls"])
            self.assertEqual(frozenset(), arguments[0][2].origins)
            self.assertNotIn(TOKEN, log)
            self.assertIn("WARNING: no TLS", log)
            self.assertIn(f"on http://0.0.0.0:4321/mcp for {root}\n", log)

            arguments, log = start([], {})
            self.assertEqual(("127.0.0.1", 8765), arguments[0][:2])
            self.assertEqual(transport.Open(), arguments[0][2].access)
            self.assertIn("no token is set", log)

            sub = os.path.join(root, "pkg")
            os.makedirs(os.path.join(root, ".wingbox"))
            os.makedirs(sub)
            Path(root, ".wingbox", "index.sexp").write_text("", encoding="utf-8")
            _, log = start(["--root", "pkg"], {})
            self.assertIn(f"/mcp for {root} (the project that holds --root {sub})\n", log)


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
