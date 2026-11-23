# Wingbox

Code graph for large repositories and monorepos. Wingbox builds a graph of the
repository you work in and answers from it before the agent reads source
files: what a change will break, across packages (blast radius); who uses a
file, function or type; where a feature is implemented; where module and
package boundaries are; and which few files to read first, before a refactor
or a cross-package change. It parses Python, TypeScript and TSX, Rust, C and
C++, C#, Java, Go, Ruby and Ruby on Rails, PHP and Laravel, Dart and Flutter, and
writes an offline HTML map of the repository.
Project page: https://wingbox.dev. Source: https://github.com/aileron-tech/wingbox.

## What this package contains

- The `wingbox` skill (`skills/wingbox/`). It tells the agent to run the
  `wingbox` command in the project: `wingbox build .` to build the graph, then
  `wingbox impact <file>`, `wingbox analyze <module>`, `wingbox quick "<words>"`
  and the other queries listed in the skill.
- In the local MCP package only: `.mcp.json`, which starts the local MCP server
  `wingbox mcp` over stdio in the project folder.

## Requirements

A local shell (desktop) and the open-source `wingbox` command, MIT licensed,
installed once from PyPI:

```bash
pipx install wingbox
```

## What it runs, reads and sends

The skill and the MCP server run only the local `wingbox` command. It reads
the files of the repository you work in and writes the graph next to them
(`.wingbox/`). Nothing is sent anywhere:
no code, graph, query or telemetry leaves your computer. The MCP server talks
over stdio only. The only network access is the installation of the package
from PyPI, which you run yourself. Privacy statement:
https://github.com/aileron-tech/wingbox/blob/main/docs/store/PRIVACY.md.

## License

MIT, see LICENSE.
