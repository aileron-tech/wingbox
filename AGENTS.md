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
