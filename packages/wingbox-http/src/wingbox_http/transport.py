"""The Streamable HTTP transport of MCP as pure functions: one HTTP request in, one decision out.

`decide` says whether a request is refused, and with which status, or forwarded
to the MCP server as one JSON-RPC message; `reply_status` gives the HTTP status
of the server's reply. Nothing here reads a socket, a file or the environment;
the listener (listener.py) does the I/O.

Followed: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
and https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
(one endpoint, POST only, no session, no stream opened by the server).
"""
from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Union

ENDPOINT = "/mcp"
MAX_BODY_BYTES = 4 * 1024 * 1024

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
SERVER_BUSY = -32000   # implementation-defined: more connections or waiting calls than the listener takes
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

# The methods of revision 2026-07-28 whose target is repeated in the Mcp-Name header.
NAMED_METHODS = frozenset({"tools/call", "resources/read", "prompts/get"})
_LOOPBACK_ORIGIN = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$", re.IGNORECASE)
_STATUS_400 = frozenset({PARSE_ERROR, INVALID_REQUEST, HEADER_MISMATCH, UNSUPPORTED_PROTOCOL_VERSION})
# What a browser asks leave for before a page of another origin may POST here (its preflight).
_PREFLIGHT = (
    ("Access-Control-Allow-Methods", "POST"),
    ("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, MCP-Protocol-Version, Mcp-Method, Mcp-Name"),
    ("Access-Control-Max-Age", "600"),
)


@dataclass(frozen=True)
class Open:
    """No credential is asked: the listener is bound to this machine only."""


@dataclass(frozen=True)
class Token:
    """Every request must carry `Authorization: Bearer <value>`."""

    value: str


Access = Union[Open, Token]


@dataclass(frozen=True)
class Policy:
    access: Access
    origins: frozenset[str]            # origins allowed besides the loopback ones
    versions: tuple[str, ...]          # every protocol revision the server speaks
    modern_versions: tuple[str, ...]   # the revisions with per-request _meta
    meta_version: str                  # the _meta key of the protocol version


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: Mapping[str, str]   # names in lower case
    body: bytes


@dataclass(frozen=True)
class Refuse:
    """Answered here, not by the MCP server: a refusal, or the answer to a browser's preflight (204)."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True)
class Admit:
    """The request line and the headers are acceptable; the body is not looked at yet."""


@dataclass(frozen=True)
class Forward:
    line: str      # the JSON-RPC message for the MCP server
    method: str    # its method, "" for a message without one
    modern: bool   # a request of a revision with per-request _meta


def error_body(code: int, message: str, data: Any = None) -> bytes:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": None, "error": error}, separators=(",", ":")).encode("ascii")


def _refuse(status: int, message: str, code: int = INVALID_REQUEST, headers: tuple[tuple[str, str], ...] = (), data: Any = None) -> Refuse:
    return Refuse(status, headers, error_body(code, message, data))


def _origin_allowed(policy: Policy, origin: str) -> bool:
    return origin in policy.origins or _LOOPBACK_ORIGIN.match(origin) is not None


def cors_headers(policy: Policy, origin: Optional[str]) -> tuple[tuple[str, str], ...]:
    """The headers that let a page of `origin` read an answer: only for an origin given with --allow-origin.

    Never for a loopback origin, although its requests pass: a page of any local site would otherwise read
    a listener that asks for no token. Without these headers its browser hands it nothing."""
    if origin is None or origin not in policy.origins:
        return ()
    return (("Access-Control-Allow-Origin", origin), ("Vary", "Origin"))


def _authorized(access: Access, header: str) -> bool:
    if isinstance(access, Open):
        return True
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(credential.strip().encode("utf-8"), access.value.encode("utf-8"))


def admit(policy: Policy, request: Request) -> Union[Refuse, Admit]:
    """What the request line and the headers alone decide; the listener asks this before it reads a body."""
    if request.path.split("?", 1)[0] != ENDPOINT:
        return _refuse(404, f"Not found: the MCP endpoint is {ENDPOINT}")
    origin = request.headers.get("origin")
    if origin is not None and not _origin_allowed(policy, origin):
        return _refuse(403, "Origin not allowed")
    if request.method == "OPTIONS" and origin in policy.origins and "access-control-request-method" in request.headers:
        return Refuse(204, _PREFLIGHT, b"")   # before the token: a browser sends none with its preflight
    if not _authorized(policy.access, request.headers.get("authorization", "")):
        return _refuse(401, "Unauthorized: send Authorization: Bearer <token>", headers=(("WWW-Authenticate", 'Bearer realm="wingbox"'),))
    if request.method != "POST":
        return _refuse(405, "Method not allowed: the MCP endpoint takes POST only", headers=(("Allow", "POST"),))
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return _refuse(415, "Unsupported media type: send Content-Type: application/json")
    accept = request.headers.get("accept")
    if accept is not None and not accepts_json(accept) and not accepts_stream(accept):
        return _refuse(406, "Not acceptable: accept application/json and text/event-stream")
    return Admit()


def _media_types(accept: str) -> frozenset[str]:
    return frozenset(part.split(";", 1)[0].strip().lower() for part in accept.split(","))


def accepts_json(accept: str) -> bool:
    return bool(_media_types(accept) & {"application/json", "application/*", "*/*"})


def accepts_stream(accept: str) -> bool:
    return "text/event-stream" in _media_types(accept)


def _declared_version(message: Mapping[str, Any], key: str) -> Optional[str]:
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    value = meta.get(key) if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None


def _mismatch(message: str) -> Refuse:
    return _refuse(400, f"Header mismatch: {message}", code=HEADER_MISMATCH)


def decide(policy: Policy, request: Request) -> Union[Refuse, Forward]:
    """Refuse the request with a status, or forward its body to the MCP server."""
    admitted = admit(policy, request)
    if isinstance(admitted, Refuse):
        return admitted
    if len(request.body) > MAX_BODY_BYTES:
        return _refuse(413, f"Request body over {MAX_BODY_BYTES} bytes")
    try:
        text = request.body.decode("utf-8")
        message = json.loads(text)
    except (ValueError, RecursionError) as error:   # UnicodeDecodeError, json.JSONDecodeError; brackets nested too deeply
        return _refuse(400, f"Parse error: {error}", code=PARSE_ERROR)
    if not isinstance(message, dict):
        return _refuse(400, "Invalid Request: expected one JSON-RPC object, batches are not supported")
    header = request.headers.get("mcp-protocol-version")
    if header is not None and header not in policy.versions:
        return _refuse(
            400,
            "Unsupported protocol version",
            code=UNSUPPORTED_PROTOCOL_VERSION,
            data={"supported": list(policy.versions), "requested": header},
        )
    method = message.get("method")
    method = method if isinstance(method, str) else ""
    declared = _declared_version(message, policy.meta_version)
    if declared is None:
        if header in policy.modern_versions:
            return _mismatch(f"MCP-Protocol-Version is {header} but the body carries no {policy.meta_version}")
        return Forward(text, method, modern=False)
    if header != declared:
        return _mismatch(f"MCP-Protocol-Version must equal {policy.meta_version} of the body ({declared})")
    if declared not in policy.modern_versions:
        return Forward(text, method, modern=False)
    if request.headers.get("mcp-method") != method:
        return _mismatch(f"Mcp-Method must equal the method of the body ({method})")
    if method in NAMED_METHODS:
        params = message.get("params")
        name = params.get("name", params.get("uri")) if isinstance(params, dict) else None
        if not isinstance(name, str) or request.headers.get("mcp-name") != name:
            return _mismatch("Mcp-Name must equal the name the body asks for")
    return Forward(text, method, modern=True)


def reply_status(reply: Optional[Mapping[str, Any]], modern: bool) -> int:
    """The HTTP status of the MCP server's reply; None is its answer to a notification."""
    if reply is None:
        return 202
    error = reply.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code in _STATUS_400:
        return 400
    if code == METHOD_NOT_FOUND and modern:
        return 404
    return 200
