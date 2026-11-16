# Wingbox plugin

Wingbox builds a code graph of the repository you work in and answers from it
before the agent reads source files: which modules exist and where their
boundaries are, what depends on a file, what a change could break, and which
few files to read first. It parses Python, TypeScript and TSX, Rust, C#, Java,
Go, Ruby and Ruby on Rails, and writes an offline HTML map of the repository.
Project page: https://wingbox.dev. Source: https://github.com/aileron-tech/wingbox.

## What the plugin contains

| Part | Claude Code | Codex |
|---|---|---|
| The `wingbox` skill (`skills/wingbox/`) | yes | yes |
| MCP server `wingbox mcp` over stdio (17 tools: build, impact, analyze, ...) | yes | yes |
| Search hook: before Grep, Glob and Read, adds the matching modules and their dependents to the context | yes | no |

## Requirements

The plugin runs the open-source `wingbox` command (MIT, on PyPI). Install it
once:

```bash
pipx install wingbox
```

In Claude Code the plugin also works without that step when `uv` is
installed: its launcher, `scripts/wingbox-run`, runs the installed `wingbox`
command first and otherwise `uvx --from wingbox==<version> wingbox`, which
downloads the pinned version from PyPI on first use. The search hook never
downloads anything and stays silent when `wingbox` is missing. In Codex the
MCP server is `wingbox mcp` from your PATH, so install the command.

## Install

Claude Code:

```bash
/plugin install wingbox --marketplace aileron-tech/wingbox
```

or `/plugin marketplace add aileron-tech/wingbox` and then
`/plugin install wingbox@aileron-tech`.

Codex:

```bash
codex plugin marketplace add aileron-tech/wingbox
codex plugin add wingbox@aileron-tech
```

If you already ran `wingbox install`, the skill and the search hook are
installed twice; run `wingbox uninstall` to keep only the plugin.

## Privacy

Everything runs on your computer. The plugin sends nothing anywhere: the
skill, the hook and the MCP server run the local `wingbox` command, which
reads the files of your repository and writes the graph next to them
(`.code_graph.sexp`, `wingbox-out/`). The MCP server talks over stdio only.
The only network access is the one-time download of the package from PyPI
when you install it, or when uvx fetches it. See
https://github.com/aileron-tech/wingbox/blob/main/docs/store/PRIVACY.md.

## License

MIT, see LICENSE.
