"""The listening side of the transport: sockets, TLS, threads. The decisions are in transport.py.

One worker thread runs the calls of the MCP server one after another, as on
stdio: the server keeps one loaded graph and its commands are not written for
concurrent use. A tools/call that is not answered within `first_wait` seconds
is answered as an event stream, when the client accepts one, with a comment
line every `keep_alive` seconds, so that a long build is not cut by an idle
timeout on the way.

What a client can hold is bounded, with or without a token (`Limits`): the
number of open connections, each of which has a thread, and the number of
calls that run or wait for the worker, beyond which the answer is 503; the
header and the body of a request must arrive within a total time; and a call
that still waits for its turn is dropped when its client has closed the
connection. A call that is running cannot be interrupted.
"""
from __future__ import annotations

import io
import json
import socket
import ssl
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent import futures
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any, Optional

from wingbox_http import transport
from wingbox_http.settings import Tls

Handle = Callable[[str], Optional[Mapping[str, Any]]]
FIRST_WAIT_SECONDS = 1.0
KEEP_ALIVE_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 30
WATCH_SECONDS = 0.5   # how often a connection that waits for its call looks whether its client is still there


@dataclass(frozen=True)
class Limits:
    connections: int = 64           # open connections, one thread each
    calls: int = 16                 # calls running or waiting for the one worker
    request_seconds: float = 30.0   # for the header and the body of one request, in all


_BUSY = transport.error_body(transport.SERVER_BUSY, "Server busy: too many open connections; try again")
_BUSY_ANSWER = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: " + str(len(_BUSY)).encode("ascii")
    + b"\r\nRetry-After: 1\r\nConnection: close\r\n\r\n" + _BUSY
)


class Listener(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], policy: transport.Policy, handle: Handle, first_wait: float, keep_alive: float,
                 log: IO[str], limits: Limits = Limits()) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.policy = policy
        self.handle = handle
        self.first_wait = first_wait
        self.keep_alive = keep_alive
        self.log = log
        self.limits = limits
        self._counts = threading.Lock()
        self._open = 0      # connections that have a thread
        self._taken = 0     # calls running or waiting
        self.calls = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wingbox-mcp")
        super().__init__(address, _Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        with self._counts:
            admitted = self._open < self.limits.connections
            self._open += admitted
        if not admitted:
            # No thread for it. A plain connection is told why; under TLS that would take the handshake first.
            if not isinstance(request, ssl.SSLSocket):
                try:
                    request.setblocking(False)
                    request.send(_BUSY_ANSWER)
                except OSError:
                    pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._closed_one()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._closed_one()

    def _closed_one(self) -> None:
        with self._counts:
            self._open -= 1

    def submit(self, line: str) -> Optional[Future]:
        """The call in the queue of the one worker; None when `limits.calls` run or wait already."""
        with self._counts:
            if self._taken >= self.limits.calls:
                return None
            self._taken += 1
        try:
            future = self.calls.submit(self.handle, line)
        except BaseException:
            self._call_ended(None)
            raise
        future.add_done_callback(self._call_ended)   # ended, or taken out of the queue
        return future

    def _call_ended(self, _future: Optional[Future]) -> None:
        with self._counts:
            self._taken -= 1

    def server_close(self) -> None:
        self.calls.shutdown(wait=False, cancel_futures=True)
        super().server_close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # a failed TLS handshake, a reset connection: one line, not a traceback
        print(f"[wingbox] http {client_address[0]}: {sys.exc_info()[1]!r}", file=self.log)


class _Left(Exception):
    """The client closed the connection while its call still waited for its turn; the call was not run."""


class _UntilDeadline(io.RawIOBase):
    """The bytes of a connection, readable until a deadline that the handler sets for each request.

    The timeout of a socket alone restarts with every byte, so a client that sends one byte at a time would
    hold its thread for as long as it likes."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.until = 0.0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        left = self.until - time.monotonic()
        if left <= 0:
            raise TimeoutError("the request did not arrive in time")
        self.connection.settimeout(left)
        try:
            return self.connection.recv_into(buffer)
        finally:
            self.connection.settimeout(WRITE_TIMEOUT_SECONDS)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wingbox-http"
    sys_version = ""
    timeout = WRITE_TIMEOUT_SECONDS   # of a write; a read ends at the deadline of its request
    server: Listener

    def setup(self) -> None:
        super().setup()
        self.rfile.close()
        self._incoming = _UntilDeadline(self.connection)
        self.rfile = io.BufferedReader(self._incoming)

    def handle_one_request(self) -> None:
        # one deadline for the request line, the header and the body (a timeout ends the connection)
        self._incoming.until = time.monotonic() + self.server.limits.request_seconds
        super().handle_one_request()

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
        if not length:
            self._refuse(transport.Refuse(411, (), transport.error_body(transport.INVALID_REQUEST, "Length required: send Content-Length")))
            return
        if not (length.isascii() and length.isdigit()):   # isdigit() alone also takes digits that int() refuses
            self._refuse(transport.Refuse(400, (), transport.error_body(transport.INVALID_REQUEST, "Bad request: Content-Length is not a number")))
            return
        length = length.lstrip("0") or "0"
        if len(length) > len(str(transport.MAX_BODY_BYTES)) or int(length) > transport.MAX_BODY_BYTES:
            self._refuse(transport.Refuse(413, (), transport.error_body(transport.INVALID_REQUEST, f"Request body over {transport.MAX_BODY_BYTES} bytes")))
            return
        body = self.rfile.read(int(length))
        decision = transport.decide(self.server.policy, transport.Request(self.command, self.path, headers, body))
        if isinstance(decision, transport.Refuse):
            self._refuse(decision)
            return
        future = self.server.submit(decision.line)
        if future is None:
            busy = transport.error_body(transport.SERVER_BUSY, "Server busy: too many calls are waiting; try again")
            self._send(503, (("Retry-After", "1"),), busy, close=True)
            return
        stream = decision.method == "tools/call" and transport.accepts_stream(headers.get("accept", ""))
        try:
            if not self._wait(future, self.server.first_wait if stream else None):
                self._stream(future)
                return
            reply = future.result()
        except _Left:
            self._gone("its call was not run")
            return
        except Exception as error:  # the MCP server answers every message; this is a defect in it
            self._failed(error)
            return
        status = transport.reply_status(reply, decision.modern)
        self._send(status, (), b"" if reply is None else _encode(reply), close=status >= 400)

    def _wait(self, future: Future, seconds: Optional[float]) -> bool:
        """True once the call has ended, False when `seconds` are over first (None: wait to its end).

        While it waits it looks at the connection: when the client has closed it and the call still waits for
        its turn, the call is taken out of the queue and _Left is raised. A call that is running cannot be
        interrupted: it is waited for, and its answer is written for whoever still reads."""
        until = None if seconds is None else time.monotonic() + seconds
        while True:
            step = WATCH_SECONDS if until is None else min(WATCH_SECONDS, until - time.monotonic())
            if step <= 0 or futures.wait([future], timeout=step).done:
                return future.done()
            if self._client_left() and future.cancel():
                raise _Left()

    def _client_left(self) -> bool:
        """True when the client has closed the connection: it can be read and holds no byte. (Under TLS a
        client that first says goodbye with a close_notify record is not seen; its call runs.)"""
        try:
            self.connection.settimeout(0)
            try:
                return socket.socket.recv(self.connection, 1, socket.MSG_PEEK) == b""
            finally:
                self.connection.settimeout(WRITE_TIMEOUT_SECONDS)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return True

    def _gone(self, what: str) -> None:
        self.close_connection = True
        print(f"[wingbox] http {self.address_string()} left before the answer: {what}", file=self.server.log)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = _answer

    def _failed(self, error: BaseException) -> None:
        print(f"[wingbox] http call failed: {error!r}", file=self.server.log)
        self._send(500, (), transport.error_body(transport.INTERNAL_ERROR, f"Internal error: {error}"), close=True)

    def _refuse(self, refusal: transport.Refuse) -> None:
        # the body of a refused request is not read, so the connection cannot be used again
        self._send(refusal.status, refusal.headers, refusal.body, close=True)

    def _send(self, status: int, headers: tuple[tuple[str, str], ...], body: bytes, close: bool) -> None:
        self.send_response(status)
        for name, value in (*headers, *transport.cors_headers(self.server.policy, self.headers.get("Origin"))):
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
            for name, value in transport.cors_headers(self.server.policy, self.headers.get("Origin")):
                self.send_header(name, value)
            self.end_headers()
            while not self._wait(future, self.server.keep_alive):
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
            try:
                reply = future.result()
                payload = _encode(reply) if reply is not None else b""
            except Exception as error:
                print(f"[wingbox] http call failed: {error!r}", file=self.server.log)
                payload = transport.error_body(transport.INTERNAL_ERROR, f"Internal error: {error}")
            self.wfile.write(b"event: message\ndata: " + payload + b"\n\n")
            self.wfile.flush()
        except _Left:
            self._gone("its call was not run")
        except OSError as error:
            # a write failed: the call that was running cannot be interrupted, and its answer is dropped
            future.cancel()
            self._gone(repr(error))


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
    limits: Limits = Limits(),
) -> Listener:
    """A bound listener; the caller runs serve_forever() and server_close()."""
    httpd = Listener((host, port), policy, handle, first_wait, keep_alive, sys.stderr if log is None else log, limits)
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
