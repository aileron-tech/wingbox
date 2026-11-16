# Wingbox privacy statement

Applies to the Wingbox plugins for coding assistants and to the `wingbox`
command they run. Publisher: Aileron Technologies. Last updated: 2026-11-16.

## What Wingbox collects and sends

Nothing. Wingbox has no server, no account, no telemetry and no analytics.
No code, file name, graph, query or usage data leaves your computer.

## What runs on your computer

- The skill tells the coding agent to run the local `wingbox` command in the
  repository you work in.
- The MCP server is the same command, `wingbox mcp`, started by your coding
  assistant and connected to it over stdio (standard input and output). It
  opens no network port and no network connection.
- In Claude Code, a hook runs `wingbox hook pre-search` before the Grep, Glob
  and Read tools. It reads the code graph of the project and adds a few lines
  of context to the search. It opens no network connection.

## What it reads and writes

Wingbox reads the source files of the repository it is run in and writes the
code graph and its report next to them: `.code_graph.sexp`,
`.code_graph_cache.json`, `.wingbox/` and `wingbox-out/`. Delete these files
to remove everything Wingbox wrote. `wingbox install`, which the plugin does
not run, writes the files listed in its README section and records them in
`~/.wingbox/installed.sexp`.

## Network access

The only network access is the installation of the package from PyPI: when
you run `pipx install wingbox`, or when the Claude Code plugin starts
`uvx --from git+https://github.com/aileron-tech/wingbox@<commit> wingbox` because the
command is not installed. uv then fetches that release commit from GitHub and the parsers from
PyPI the first time. The
search hook never downloads.

## Personal data and children

Wingbox processes no personal data beyond what your own source files contain,
and it processes those only on your computer. It is a developer tool and is
not directed at people under 18.

## Contact

Questions and reports: https://github.com/aileron-tech/wingbox/issues.
Source code (MIT): https://github.com/aileron-tech/wingbox.
