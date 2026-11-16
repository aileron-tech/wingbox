# Wingbox plugin

Code graph for large repositories and monorepos. Wingbox builds a graph of the
repository you work in and answers from it before the agent reads source
files: what a change will break, across packages (blast radius); who uses a
file, function or type; where a feature is implemented; where module and
package boundaries are; and which few files to read first, before a refactor
or a cross-package change. It parses Python, TypeScript and TSX, Rust, C#,
Java, Go, Ruby and Ruby on Rails, PHP and Laravel, Dart and Flutter, and
writes an offline HTML map of the repository.
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
command first and otherwise `uvx --from git+https://github.com/aileron-tech/wingbox@<commit> wingbox`,
pinned to the commit of the latest release tag, which uv fetches and builds on first use (its
parser dependencies come from PyPI). The search hook never
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

## Security

Report a vulnerability privately through "Report a vulnerability" on the
Security tab of https://github.com/aileron-tech/wingbox; see
https://github.com/aileron-tech/wingbox/blob/main/SECURITY.md.

## License

MIT, see LICENSE.
