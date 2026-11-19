"""Streamable HTTP transport for the Wingbox MCP server: `wingbox-http`, or `wingbox mcp --http`.

The same server as `wingbox mcp` on stdio, reachable at http://ADDRESS:PORT/mcp.
It listens and answers; it opens no connection of its own.
"""
from __future__ import annotations

import os
import signal
import sys
from collections.abc import Mapping
from typing import Optional

from wingbox import cli, mcp_server

from wingbox_http import listener, settings, transport


def main(args: list[str], cwd: str, env: Optional[Mapping[str, str]] = None) -> int:
    if any(argument in ("-h", "--help") for argument in args):
        print(settings.USAGE)
        return 0
    chosen = settings.read(args, cwd, os.environ if env is None else env)
    if isinstance(chosen, settings.Problem):
        print(f"[wingbox] ERROR: {chosen.message}\n{settings.USAGE}", file=sys.stderr)
        return 2
    log = sys.stderr   # kept: a running tool redirects sys.stderr into its answer
    server = mcp_server.Server(chosen.root)
    policy = transport.Policy(
        access=chosen.access,
        origins=chosen.origins,
        versions=mcp_server.SUPPORTED_VERSIONS,
        modern_versions=mcp_server.MODERN_VERSIONS,
        meta_version=mcp_server.META_VERSION,
    )
    try:
        httpd = listener.make_listener(
            chosen.host, chosen.port, policy, lambda line: mcp_server.handle_line(server, line), tls=chosen.tls, log=log
        )
    except OSError as error:   # the port is taken, the address is not ours, the certificate cannot be read
        print(f"[wingbox] ERROR: cannot listen on {chosen.host}:{chosen.port}: {error}", file=sys.stderr)
        return 2
    scheme = "https" if chosen.tls is not None else "http"
    host = f"[{chosen.host}]" if ":" in chosen.host else chosen.host
    print(f"[wingbox] MCP server {cli._version()} on {scheme}://{host}:{httpd.server_address[1]}{transport.ENDPOINT} for {chosen.root}", file=log)
    if isinstance(chosen.access, transport.Open):
        print("[wingbox] no token is set: any program on this machine can call the tools", file=log)
    elif chosen.tls is None and not settings.is_loopback(chosen.host):
        print("[wingbox] WARNING: no TLS: the token travels unencrypted; pass --tls-cert and --tls-key or put a TLS proxy in front", file=log)
    try:
        if chosen.build_missing:
            server.start_first_build()
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_first_build()
        httpd.server_close()
    return 0


def _stop(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)   # a container or a service manager stops the server this way
    sys.exit(main(sys.argv[1:], os.getcwd()))
