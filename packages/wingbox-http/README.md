# wingbox-http

Call the [Wingbox](https://wingbox.dev) code graph of a repository from another
machine. `wingbox-http` serves the Wingbox MCP server over the Streamable HTTP
transport of the Model Context Protocol: the same tools as `wingbox mcp` on
stdio (`impact`, `usages`, `graph` and the rest), at one URL.

Use it when the repository and its graph live on a build server, a dev
container or a remote workstation, and the coding agent runs somewhere else.

## Install

```bash
pip install "wingbox[http]"          # or: pipx install wingbox && pipx inject wingbox wingbox-http
```

The transport is a package of its own on purpose: `wingbox` itself holds no
listening code. `wingbox-http` uses the Python standard library only, listens
and answers, and opens no connection of its own.

## Run

On the machine that holds the repository:

```bash
export WINGBOX_HTTP_TOKEN="$(openssl rand -hex 32)"     # keep it; clients send it
wingbox mcp --http --root /path/to/project --host 0.0.0.0 --port 8765 \
  --tls-cert fullchain.pem --tls-key privkey.pem
```

`wingbox-http ...` is the same command. The server answers at
`https://<host>:8765/mcp`.

| Option | Meaning |
|---|---|
| `--root DIR` | The repository to serve (default: the current directory) |
| `--host ADDRESS` | Address to listen on (default `127.0.0.1`). Any other address needs a token |
| `--port N` | Port (default `8765`) |
| `--token-file FILE` | Read the token from the first line of FILE instead of `WINGBOX_HTTP_TOKEN` |
| `--tls-cert FILE`, `--tls-key FILE` | Serve HTTPS with this certificate and key |
| `--allow-origin ORIGIN` | Also accept browser requests from this origin; repeatable |
| `--build-missing` | Build the code graph in the background when the repository has none |

Without arguments the server listens on `127.0.0.1` only and asks for no
token: that is for an SSH tunnel (`ssh -L 8765:127.0.0.1:8765 host`) or a
reverse proxy that checks who calls.

## Connect an agent

Claude Code:

```bash
claude mcp add --transport http wingbox https://host.example:8765/mcp \
  --header "Authorization: Bearer $WINGBOX_HTTP_TOKEN"
```

Codex:

```bash
codex mcp add wingbox --url https://host.example:8765/mcp --bearer-token-env-var WINGBOX_HTTP_TOKEN
```

Any other MCP client: the Streamable HTTP transport, URL `https://host.example:8765/mcp`,
header `Authorization: Bearer <token>`.

## What is exposed

Whoever holds the token can read the code graph of the repository (file names,
symbols, dependencies, the lines Wingbox quotes) and rebuild the graph (the tool `graph`), which
writes `.wingbox/` in the repository. No tool reads a file outside the
repository, runs a command you give it, or deletes anything.

- The token is a shared secret of at least 32 characters, compared in constant
  time. It is taken from the environment or a file, never from the command
  line, and is never logged.
- Listening on any address other than loopback is refused without a token.
- Without `--tls-cert` and `--tls-key` the token travels unencrypted: use them,
  or put the port behind a TLS proxy or a private network.
- A request with an `Origin` header that is not a loopback origin or one given
  with `--allow-origin` is answered 403, so a web page cannot call the server
  through the browser of someone on the same network.

## Protocol

One endpoint, `POST /mcp`, for protocol revisions 2026-07-28 and 2025-11-25
down to 2024-11-05. Each request is answered with JSON; a `tools/call` that
runs longer than a second is answered as an event stream, with a keep-alive
line every ten seconds, when the client accepts one, so a long build of the graph is not
cut by an idle timeout on the way. The server keeps no session and opens no
stream of its own: `GET` and `DELETE` are answered 405. Calls run one after
another, as on stdio.

License: MIT.
