"""The listening side of the transport: sockets, TLS, threads. The decisions are in transport.py.

One worker thread runs the calls of the MCP server one after another, as on
stdio: the server keeps one loaded graph and its commands are not written for
concurrent use. A tools/call that is not answered within `first_wait` seconds
is answered as an event stream, when the client accepts one, with a comment
line every `keep_alive` seconds, so that a long build is not cut by an idle
timeout on the way.
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any, Optional

from wingbox_http import transport
from wingbox_http.settings import Tls

Handle = Callable[[str], Optional[Mapping[str, Any]]]
FIRST_WAIT_SECONDS = 1.0
KEEP_ALIVE_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30


class Listener(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], policy: transport.Policy, handle: Handle, first_wait: float, keep_alive: float, log: IO[str]) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.policy = policy
        self.handle = handle
        self.first_wait = first_wait
        self.keep_alive = keep_alive
        self.log = log
        self.calls = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wingbox-mcp")
        super().__init__(address, _Handler)

    def server_close(self) -> None:
        self.calls.shutdown(wait=False, cancel_futures=True)
        super().server_close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # a failed TLS handshake, a reset connection: one line, not a traceback
        print(f"[wingbox] http {client_address[0]}: {sys.exc_info()[1]!r}", file=self.log)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wingbox-http"
    sys_version = ""
    timeout = READ_TIMEOUT_SECONDS
    server: Listener

    def log_message(self, format: str, *args: Any) -> None:
        # the request line and the status only: never a header, so never the token
        print(f"[wingbox] http {self.address_string()} {format % args}", file=self.server.log)

    def _answer(self) -> None:
        headers = {name.lower(): value for name, value in self.headers.items()}
        head = transport.Request(self.command, self.path, headers, b"")
        admitted = transport.admit(self.server.policy, head)
        if isinstance(admitted, transport.Refuse):
            self._refuse(admitted)
            return
        length = headers.get("content-length", "")
        if not length.isdigit():
            self._refuse(transport.Refuse(411, (), transport.error_body(transport.INVALID_REQUEST, "Length required: send Content-Length")))
            return
        if int(length) > transport.MAX_BODY_BYTES:
            self._refuse(transport.Refuse(413, (), transport.error_body(transport.INVALID_REQUEST, f"Request body over {transport.MAX_BODY_BYTES} bytes")))
            return
        body = self.rfile.read(int(length))
        decision = transport.decide(self.server.policy, transport.Request(self.command, self.path, headers, body))
        if isinstance(decision, transport.Refuse):
            self._refuse(decision)
            return
        future = self.server.calls.submit(self.server.handle, decision.line)
        stream = decision.method == "tools/call" and transport.accepts_stream(headers.get("accept", ""))
        try:
            reply = future.result(timeout=self.server.first_wait if stream else None)
        except TimeoutError:
            self._stream(future)
            return
        except Exception as error:  # the MCP server answers every message; this is a defect in it
            self._failed(error)
            return
        status = transport.reply_status(reply, decision.modern)
        self._send(status, (), b"" if reply is None else _encode(reply), close=status >= 400)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = _answer

    def _failed(self, error: BaseException) -> None:
        print(f"[wingbox] http call failed: {error!r}", file=self.server.log)
        self._send(500, (), transport.error_body(transport.INTERNAL_ERROR, f"Internal error: {error}"), close=True)

    def _refuse(self, refusal: transport.Refuse) -> None:
        # the body of a refused request is not read, so the connection cannot be used again
        self._send(refusal.status, refusal.headers, refusal.body, close=True)

    def _send(self, status: int, headers: tuple[tuple[str, str], ...], body: bytes, close: bool) -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _stream(self, future: Future) -> None:
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                try:
                    reply = future.result(timeout=self.server.keep_alive)
                    payload = _encode(reply) if reply is not None else b""
                    break
                except TimeoutError:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except Exception as error:
                    print(f"[wingbox] http call failed: {error!r}", file=self.server.log)
                    payload = transport.error_body(transport.INTERNAL_ERROR, f"Internal error: {error}")
                    break
            self.wfile.write(b"event: message\ndata: " + payload + b"\n\n")
            self.wfile.flush()
        except OSError as error:
            # the client left; the call itself cannot be interrupted and its answer is dropped
            print(f"[wingbox] http {self.address_string()} left before the answer: {error!r}", file=self.server.log)


def _encode(reply: Mapping[str, Any]) -> bytes:
    return json.dumps(reply, separators=(",", ":")).encode("ascii")


def make_listener(
    host: str,
    port: int,
    policy: transport.Policy,
    handle: Handle,
    first_wait: float = FIRST_WAIT_SECONDS,
    keep_alive: float = KEEP_ALIVE_SECONDS,
    tls: Optional[Tls] = None,
    log: Optional[IO[str]] = None,
) -> Listener:
    """A bound listener; the caller runs serve_forever() and server_close()."""
    httpd = Listener((host, port), policy, handle, first_wait, keep_alive, sys.stderr if log is None else log)
    if tls is not None:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(tls.certificate, tls.key)
            # the handshake runs in the thread of the connection, so a slow client cannot hold the accept loop
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        except (OSError, ssl.SSLError):
            httpd.server_close()
            raise
    return httpd
