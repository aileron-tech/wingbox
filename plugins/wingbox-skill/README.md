# Wingbox skill

Before your coding agent edits a file, it should know which other files depend
on that file. This plugin gives Claude the `wingbox` skill for that: Claude
asks a map of your repository (a code graph) what a change will break, who
uses a file, function or type, where a feature is implemented, and which few
files to read first, before it greps or reads source.

Type `/wingbox` in a repository, or just ask "what breaks if I change this
file?".

## What happens on first use

The skill works through the open-source `wingbox` program (MIT, on PyPI). When
the program is not on your machine yet, Claude tells you so and installs it
with `pipx install wingbox` or `uv tool install wingbox`, with your approval
as for any command it runs. Then it builds the map with `wingbox build .`,
which writes one folder in the project, `.wingbox/`, and nothing else.

Everything runs on your machine: plain static parsing, no model, no account,
and your code never leaves it.

## What this plugin holds

The skill and its two reference pages, and nothing that runs by itself: no
hooks and no MCP server. For those (context added before each search, an MCP
server with the same queries as tools), install the program and run
`wingbox install`, or add the full plugin from the repository:
https://github.com/aileron-tech/wingbox#install-as-a-plugin

Languages: Python, TypeScript and TSX, Rust, C#, Java, Go, Ruby and Ruby on
Rails, PHP and Laravel, Dart and Flutter.

Project page: https://wingbox.dev. Source: https://github.com/aileron-tech/wingbox.
