# Wingbox plugin

Code graph for large repositories and monorepos. Wingbox builds a graph of the
repository you work in and answers from it before the agent reads source
files: what a change will break, across packages (blast radius); who uses a
file, function or type; where a feature is implemented; where module and
package boundaries are; and which few files to read first, before a refactor
or a cross-package change. It parses Python, TypeScript and TSX, Rust, C and
C++, C#, Java, Go, Ruby and Ruby on Rails, PHP and Laravel, Dart and Flutter, and
writes an offline HTML map of the repository.
Project page: https://wingbox.dev. Source: https://github.com/aileron-tech/wingbox.

## What the plugin contains

| Part | Claude Code | Codex |
|---|---|---|
| The `wingbox` skill (`skills/wingbox/`) | yes | yes |
| MCP server `wingbox mcp` over stdio (14 tools: build_graph, analyze_impact, find_usages, read_source, search_source, ...) | yes | yes |
| Search hook: before Grep, Glob and Read, adds the matching modules and their dependents to the context | yes | no |

## Requirements

The plugin runs the open-source `wingbox` command (MIT, on PyPI). Install it
once:

```bash
pipx install wingbox
```

In Claude Code the plugin needs `uv` instead: its MCP server and hooks run
`uvx --from git+https://github.com/aileron-tech/wingbox@<commit> wingbox`,
pinned to the commit of the latest release tag, which uv fetches and builds on first use (its
parser dependencies come from PyPI). Claude Code starts `uvx` with no shell, so this works on
macOS, Linux and Windows alike. The hooks run uvx offline: they never download anything, and
when uv or its copy of Wingbox is missing they fail without blocking the tool call. In Codex the
MCP server runs the same pinned release through `uvx` (`wingbox mcp --build-missing`: the first
question in a repository without a graph builds it), so the Codex plugin needs uv too.

## Install

Claude Code:

```text
/plugin marketplace add aileron-tech/wingbox
/plugin install wingbox@aileron-tech
```

From a shell, in one line: `claude plugin install wingbox --marketplace aileron-tech/wingbox`.

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
(`.wingbox/`). The MCP server talks over stdio only.
The only network access is the one-time download of the package from PyPI
when you install it, or when uvx fetches it. See
https://github.com/aileron-tech/wingbox/blob/main/docs/store/PRIVACY.md.

## Security

Report a vulnerability privately through "Report a vulnerability" on the
Security tab of https://github.com/aileron-tech/wingbox; see
https://github.com/aileron-tech/wingbox/blob/main/SECURITY.md.

## License

MIT, see LICENSE.
