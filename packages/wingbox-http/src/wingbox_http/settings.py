"""The settings of `wingbox-http` from its arguments, the environment and the token file."""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional, Union

from wingbox_http.transport import Access, Open, Token

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_VARIABLE = "WINGBOX_HTTP_TOKEN"
TOKEN_MIN_LENGTH = 32
_TOKEN = re.compile(r"^[\x21-\x7e]+$")
_ORIGIN = re.compile(r"^https?://[^/\s]+$")

USAGE = f"""usage: wingbox-http [--root DIR] [--build-missing] [--host ADDRESS] [--port N]
                    [--token-file FILE] [--tls-cert FILE --tls-key FILE]
                    [--allow-origin ORIGIN]...

Serve the Wingbox MCP server for the repository at DIR (default: the current
directory) over the Streamable HTTP transport, at http://ADDRESS:N/mcp
(default {DEFAULT_HOST}:{DEFAULT_PORT}). `wingbox mcp --http` is the same command.

  --host ADDRESS       the address to listen on. Any address other than loopback
                       needs a token.
  --token-file FILE    read the token from the first line of FILE; without it
                       the token is taken from the environment variable
                       {TOKEN_VARIABLE}. At least {TOKEN_MIN_LENGTH} characters. Clients send it
                       as `Authorization: Bearer <token>`.
  --tls-cert, --tls-key  serve HTTPS with this certificate and private key.
  --allow-origin ORIGIN  also accept browser requests from this origin
                       (scheme://host[:port]); loopback origins always pass.
  --build-missing      build the code graph of DIR in the background when it
                       has none."""


@dataclass(frozen=True)
class Tls:
    certificate: str
    key: str


@dataclass(frozen=True)
class Settings:
    root: str
    host: str
    port: int
    access: Access
    origins: frozenset[str]
    tls: Optional[Tls]
    build_missing: bool


@dataclass(frozen=True)
class Problem:
    message: str


class _Rejected(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would print and exit
        raise _Rejected(message)


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _token(path: Optional[str], env: Mapping[str, str]) -> Union[Access, Problem]:
    if path is not None:
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.readline().strip()
        except (OSError, UnicodeDecodeError) as error:
            return Problem(f"--token-file cannot be read: {error}")
        source = f"the first line of {path}"
    else:
        value = env.get(TOKEN_VARIABLE, "")
        if not value:
            return Open()
        source = TOKEN_VARIABLE
    if len(value) < TOKEN_MIN_LENGTH or _TOKEN.match(value) is None:
        return Problem(f"{source} is not a usable token: at least {TOKEN_MIN_LENGTH} visible ASCII characters without spaces")
    return Token(value)


def read(args: list[str], cwd: str, env: Mapping[str, str]) -> Union[Settings, Problem]:
    parser = _Parser(prog="wingbox-http", add_help=False, allow_abbrev=False)
    parser.add_argument("--root", default=".")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token-file")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--build-missing", action="store_true")
    try:
        parsed = parser.parse_args(args)
    except _Rejected as error:
        return Problem(str(error))
    root = os.path.normpath(os.path.join(cwd, parsed.root))
    if not os.path.isdir(root):
        return Problem(f"--root is not a directory: {root}")
    if not 0 <= parsed.port <= 65535:
        return Problem(f"--port is not a port number: {parsed.port}")
    if (parsed.tls_cert is None) != (parsed.tls_key is None):
        return Problem("--tls-cert and --tls-key come together")
    for origin in parsed.allow_origin:
        if _ORIGIN.match(origin) is None:
            return Problem(f"--allow-origin takes scheme://host[:port], not {origin}")
    access = _token(parsed.token_file, env)
    if isinstance(access, Problem):
        return access
    if isinstance(access, Open) and not is_loopback(parsed.host):
        return Problem(
            f"listening on {parsed.host} needs a token: set {TOKEN_VARIABLE} or pass --token-file "
            f"(without one the server listens on {DEFAULT_HOST} only)"
        )
    return Settings(
        root=os.path.realpath(root),
        host=parsed.host,
        port=parsed.port,
        access=access,
        origins=frozenset(parsed.allow_origin),
        tls=None if parsed.tls_cert is None else Tls(parsed.tls_cert, parsed.tls_key),
        build_missing=parsed.build_missing,
    )
