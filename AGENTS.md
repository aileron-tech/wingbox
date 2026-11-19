# AGENTS.md - wingbox

Scope: instructions for people and coding agents who change this repository.
If you only use Wingbox as an installed skill, follow `SKILL.md`; nothing in
this file applies to you.

## 1. What this repository is

- Wingbox: code graph and blast radius analysis for AI coding agents. It is an
  agent skill (`SKILL.md`, `scripts/`) and a Python package, `wingbox` on PyPI,
  with the `wingbox` command (`src/wingbox/cli.py`). The package bundles
  `scripts/` unchanged as `wingbox/_scripts`.
- Layout: `scripts/build_code_graph.py` (builder), `scripts/code_graph_search.py`
  (queries), `scripts/build_infra_topology.py`, `scripts/lang/` (parsers),
  `tests/`, `references/`, `docs/`. `src/wingbox/install.py`, `hook.py` and
  `update.py` are `wingbox install`/`uninstall`, the Claude Code search hook
  and `wingbox update`; `mcp_server.py` is `wingbox mcp`, the local stdio MCP
  server; `overview.py` and `map_page.py` write `REPORT.md` and `map.html`.
  `add.py` is `wingbox add`, which installs a parser on demand, and `fetch.py`
  its download: the only module that opens a network connection, imported by
  `add.py` alone (REQ-SEC-003). `scripts/lang/ondemand.py` says where those
  parsers live and makes them importable.
  The wheel bundles `SKILL.md` and `references/` as `wingbox/_skill`, the
  skill that `wingbox install` copies.
- Plugins: `plugins/wingbox` is the folder Claude Code and Codex install
  (manifests with the pinned `uvx` commands, a copy of the skill);
  `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`
  list it. `tools/sync_plugin.py` keeps the copy in step with the repository;
  `tools/build_openai_plugin.py` builds the OpenAI directory ZIPs from it
  (listing texts in `docs/store/`). `benchmarks/` holds the reproducible
  impact and build-time benchmarks.
- Supported Python: 3.11, 3.12, 3.13 (the builder imports `tomllib`). macOS and
  Linux.

## 2. Rules for changes

- Default flow: fresh clone, branch, one commit, pull request, squash merge
  (`gh pr merge <n> --squash --delete-branch`). `main` is not pushed directly.
- Write the failing test first, then the implementation. Every test docstring
  carries `/* REQ-<PREFIX>-<n>: <behaviour> */`; take the next free number of
  the prefix (`CS` search commands, `CLI` the `wingbox` command, `INSTALL`
  install, uninstall and the search hook, `UPDATE`, `MCP` the MCP server,
  `HTTP` its HTTP transport in `packages/wingbox-http`,
  `PLUGIN` the plugins and their tools, `OUT` the report and the map page,
  `BENCH` the benchmarks, `SEC` the local-only guarantees, `DARTACC` and
  `DARTLAYER` the Dart acceptance harness and the Flutter purpose and layer
  rules, `DEPS`, `LANG`, and one prefix per language such as `DART`, `GO` or
  `RUBY`).
- Run the tests before each push:

  ```bash
  cat requirements.txt requirements/*.txt | sort -u > /tmp/wingbox-requirements.txt
  uv run --no-project --with-requirements /tmp/wingbox-requirements.txt \
    python -m unittest discover -s tests
  ```

  Add `--python 3.11` to check the oldest supported version.
- This repository is public. Code, tests, documentation, commit messages and
  pull request texts contain no names of real clients, employers, internal
  projects, repositories, hosts or people; examples use invented paths. Check
  the diff for such names before each push.
- Terms: the product is Wingbox, the skill is `wingbox`, the map is a "code
  graph".
- Output formats: S-expressions are the internal and agent-facing format
  (compact, few tokens). What people read is text or HTML; a command meant for
  people prints text by default and offers `--sexp` for agents.
- Agree the name, the arguments and the output of a new command with the
  maintainer before writing its code.
- `scripts/` stays loadable by path: the tests import the scripts with
  `importlib` from `scripts/`, and the skill calls them by path.
- The parsers of TypeScript and Rust are dependencies of the package; those
  of C#, Java, Go, Ruby, PHP and Dart are installed on demand. The pin of
  such a parser is written in four places that a test compares (REQ-DEPS-012):
  the language adapter (`scripts/lang/<language>/__init__.py`),
  `requirements/<language>.txt`, the extras of `pyproject.toml` (its own and
  `all`) and `src/wingbox/assets/parsers.json`, which
  `python3 tools/update_parsers.py` rewrites from the package index (it needs
  the network; `--check` compares). No code path but `wingbox add` may
  download anything.
- After a change to `SKILL.md`, `references/` or the version, run
  `python3 tools/sync_plugin.py`: a test keeps the plugin's copy of the skill
  byte for byte identical, and `--check` exits 1 while anything is stale. The
  Claude Code manifest pins the commit of the newest `vX.Y.Z` tag in every
  `uvx` command, so run it again once the release tag exists.
- ASCII only, and English only: no Russian or other non-English text anywhere in the repository, including LOG.md entries and quotes of what the maintainer said (paraphrase them in English; REQ-DOC-001). `.gitignore` changes only when the name of a generated file
  changes.
- A user-facing change updates `README.md` and `SKILL.md` (command tables),
  `CHANGELOG.md` (under `## Unreleased`, `### Changes` before `### Fixes`)
  and `LOG.md` (what changed and why).

## 3. Publishing a version

Follow these steps in order when the maintainer asks for a release.

1. Choose X.Y.Z: patch for fixes and documentation, minor for a new command.
   Set `version` in `pyproject.toml`, rename `## Unreleased` in `CHANGELOG.md`
   to `## X.Y.Z - YYYY-MM-DD` (one heading per version), add the entry to
   `LOG.md`, bump `WINGBOX_VERSION` in `.github/workflows/map-request.yml`,
   run `python3 tools/sync_plugin.py`; pull request, squash merge.
2. Create the GitHub release; the tag equals the version in `pyproject.toml`:

   ```bash
   gh release create vX.Y.Z --target main --title "Wingbox X.Y.Z" -F notes.md
   ```

   Then run `python3 tools/sync_plugin.py` again so the manifest pins the new
   tag's commit, and merge that through a pull request.
3. On `main` at the tagged commit, build outside the repository:

   ```bash
   uv build --out-dir /tmp/wingbox-dist
   ```

4. Check the archives before the upload. `tar -tzf` on the sdist and
   `unzip -l` on the wheel list only `src/`, `scripts/`, `SKILL.md`,
   `references/`, `README.md`, `LICENSE`, `CHANGELOG.md`, `pyproject.toml`
   and the package metadata.
5. Upload both files. A version is uploaded to PyPI once and for good; a
   mistake is corrected by the next version.

   ```bash
   UV_PUBLISH_TOKEN="$(security find-generic-password -s pypi-wingbox -w)" \
     uv publish /tmp/wingbox-dist/wingbox-X.Y.Z.tar.gz \
                /tmp/wingbox-dist/wingbox-X.Y.Z-py3-none-any.whl
   ```

6. Verify from PyPI in a clean environment:

   ```bash
   uvx --refresh --from wingbox==X.Y.Z wingbox --version
   ```

   then run `wingbox build .` and `wingbox impact <file>` in a small project.

## 4. Upload token

- The upload token is scoped to the PyPI project `wingbox` and is kept only in
  the maintainer's macOS login keychain: service `pypi-wingbox`, account
  `__token__`. Its value is never printed and never written to a file of the
  repository, a log or a pull request.
- When the keychain entry is missing or PyPI answers 403, the maintainer
  creates a new project-scoped token at
  `https://pypi.org/manage/account/token/`. Check that the value starts with
  `pypi-` before storing it:

  ```bash
  security add-generic-password -a __token__ -s pypi-wingbox -w "<token>" -U
  ```

  Read it back with the command from step 5 before deleting any temporary copy.

## 5. Changing the MCP server

The tool list of `wingbox mcp` (`TOOLS` in `src/wingbox/mcp_server.py`) is
graded by MCP directories, and the grade is shown next to the listing. Glama
grades four things: whether the tools can be told apart, whether their names
follow one pattern, how many there are, and whether they cover the domain. With
eighteen tools the grade was B (3.4 of 5, 2026-11-17); the list was then cut to
eleven.

Default: a change of the server keeps the tool list as it is. A new question
becomes a parameter of the tool that already answers the nearest question. Add,
rename or remove a tool only when the maintainer asks for it, in one release,
with a line under "Changes" in the changelog that names the tools.

Check every change of the server against this list before the pull request:

1. Count: 3 to 15 tools.
2. One job per tool. Two tools that differ only by direction or by kind are
   one tool with a parameter (`dependencies` in and out, `calls` in, out and
   to, `definitions` by kind). A wrapper around another tool is an option of
   that tool (`module` takes a keyword).
3. Names: one lowercase word, the noun of the answer (`impact`, `usages`,
   `changes`); the two actions are `build` and `query`. No name shares its
   first six letters with another.
4. Description: one line of at most 400 characters. It says what the answer
   contains, and a tool with a neighbour says for which question to take the
   neighbour, by name ("for the places that use a name take usages").
5. Parameters: every one has a description. A choice is an `enum` with a
   default, and the server refuses any other value. An integer has a lower and
   an upper bound.
6. Annotations: a title of at most 40 characters; `readOnlyHint` is false only
   for a tool that writes (today `build`); every tool is idempotent, not
   destructive and not open-world.
7. The answer is the output of the `wingbox` command behind the tool. An
   `(error ...)` answer is a tool error. Answers stop at 200 lines unless the
   call sets `all`.
8. Standard output carries JSON-RPC messages only; logs go to standard error;
   no network.
9. The command line keeps its command names: a change of the tool list does
   not rename a command.
10. Documents that follow the list: the table in the README section "Use from
    an MCP client" and the docstring of `mcp_server.py`. The manifest of the
    Claude Desktop extension is built from the list (`tools/build_mcpb.py`).

`tests/test_mcp_tools.py` checks points 1, 3, 4 and 5, `tests/test_mcp_server.py`
points 6 to 8. Run them with the packaging tests:

```bash
python3 -m unittest tests.test_mcp_tools tests.test_mcp_server tests.test_mcpb tests.test_mcp_registry
```

After a release that changes the list or a description: publish to the MCP
registry as section 3 says, and ask Glama to score the server again
(https://glama.ai/mcp/servers/aileron-tech/wingbox/score); write the new grade
into LOG.md.
