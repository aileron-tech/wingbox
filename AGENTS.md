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
  `add.py` is `wingbox add`, kept for scripts written for 0.12: it adds
  nothing and says so.
  The wheel bundles `SKILL.md` and `references/` as `wingbox/_skill`, the
  skill that `wingbox install` copies.
- Plugins: `plugins/wingbox` is the folder Claude Code and Codex install
  (manifests with the pinned `uvx` commands, a copy of the skill);
  `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`
  list it. `tools/sync_plugin.py` keeps the copy in step with the repository;
  `tools/build_openai_plugin.py` builds the OpenAI directory ZIPs from it
  (listing texts in `docs/store/`). `benchmarks/` holds the reproducible
  impact and build-time benchmarks.
- Supported Python: 3.11, 3.12, 3.13 (the builder imports `tomllib`). macOS,
  Linux and Windows (the test matrix of `tests.yml` runs all three).

## 2. Rules for changes

- Default flow: fresh clone, branch, one commit, pull request, squash merge
  (`gh pr merge <n> --squash --delete-branch`). `main` is not pushed directly.
- Write the failing test first, then the implementation. Every test docstring
  carries `/* REQ-<PREFIX>-<n>: <behaviour> */`; take the next free number of
  the prefix (`CS` search commands, `CLI` the `wingbox` command, `INSTALL`
  install, uninstall and the search hook, `UPDATE`, `MCP` the MCP server,
  `HTTP` its HTTP transport in `packages/wingbox-http`,
  `PLUGIN` the plugins and their tools, `OUT` the report and the map page,
  `BENCH` the benchmarks, `SEC` the local-only guarantees, `REL` the release
  files (pinned workflow actions, checksums, SBOM, reproducible build),
  `DARTACC` and
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
- After a test is added, removed or reworded, run
  `python3 tools/requirements_index.py`: it writes
  `docs/assurance/requirements.md`, the list of every requirement with the
  test that verifies it, and a test fails while the committed file differs
  from that output (REQ-DOC-004). An id belongs to one test: the tool names
  an id that two tests carry, writes nothing and exits 1. The other pages of `docs/assurance/` are
  written by hand and cite their sources; a statement the sources do not
  support is written "not assessed".
- This repository is public. Code, tests, documentation, commit messages and
  pull request texts contain no names of real clients, employers, internal
  projects, repositories, hosts or people; examples use invented paths. Check
  the diff for such names before each push.
- Terms: the product is Wingbox, the skill is `wingbox`, the map is a "code
  graph".
- Output formats: S-expressions are the internal and agent-facing format
  (compact, few tokens). A command that answers a question (the query
  commands, `advice`, `align`, `place`, `compare`) prints S-expressions by
  default, complete; `--human` prints the text for people and `--json` the
  same answer as one JSON object. A command that sets something up for a
  person (`init`, `learn`, `install`, `uninstall`, `update`) prints text and
  offers `--sexp`.
- `--sexp` on a command that answers a question is a compatibility promise:
  it prints, byte for byte, what version 0.13.0 printed with it (the flat
  facts of `impact` and `usages` that the pull request check of
  `aileron-tech/wingbox-action` reads), as do `wingbox module <id>`,
  `wingbox boundaries` and `wingbox boundaries --strict` without an option.
  `tests/data/outputs_0_13_0.json` holds those outputs; a change that alters
  one of them is a change of the pull request check too.
- Agree the name, the arguments and the output of a new command with the
  maintainer before writing its code.
- `scripts/` stays loadable by path: the tests import the scripts with
  `importlib` from `scripts/`, and the skill calls them by path.
- The parser of every language is a dependency of the package. Its pin is
  written in three places that a test compares (REQ-DEPS-012): the language
  adapter (`scripts/lang/<language>/__init__.py`),
  `requirements/<language>.txt` and `pyproject.toml` (the dependency and,
  for C#, Java, Go, Ruby, PHP and Dart, the extras: its own and `all`).
  No file of `src/` or `scripts/` imports a module that opens a network
  connection (REQ-SEC-001): nothing in Wingbox downloads anything.
- After a change to `SKILL.md`, `references/` or the version, run
  `python3 tools/sync_plugin.py`: a test keeps the plugin's copy of the skill
  byte for byte identical, and `--check` exits 1 while anything is stale. The
  Claude Code manifest pins the commit of the newest `vX.Y.Z` tag in every
  `uvx` command, so run it again once the release tag exists.
- A `uses:` line of a workflow names its action at a full commit, with the
  release tag of that commit as a trailing comment
  (`uses: actions/checkout@<40 hex>  # v4.4.0`; REQ-REL-001). Read the commit
  of a tag with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`; when the
  answer has `"type": "tag"` the tag is annotated, and
  `gh api repos/<owner>/<repo>/git/tags/<sha>` gives the commit. A newer
  release of an action is taken by changing the commit and the comment
  together.
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
7. Write the checksum file and the SBOM for the files that were uploaded and
   attach both to the GitHub release, next to the `.mcpb` assets:

   ```bash
   python3 tools/release_files.py /tmp/wingbox-dist
   gh release upload vX.Y.Z /tmp/wingbox-dist/SHA256SUMS \
                            /tmp/wingbox-dist/wingbox-X.Y.Z.cdx.json
   ```

   `SHA256SUMS` gets one line per file of the folder, so the folder holds the
   files of this release and nothing else: the two files of step 5 and, to
   have them listed too, the files of `wingbox-http`
   (`uv build packages/wingbox-http --out-dir /tmp/wingbox-dist`) and the
   extension (`python3 tools/build_mcpb.py --out /tmp/wingbox-dist`).
   `wingbox-X.Y.Z.cdx.json` is the CycloneDX 1.5 SBOM: the package, the
   SHA-256 of its built files and every dependency `pyproject.toml` pins.
   `docs/assurance/release-integrity.md` says what each file proves and how a
   reader checks it.

A release is not finished here: go on with section 6, "After every release".

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
eighteen tools the grade was B (3.4 of 5, 2026-11-17); eleven one-word names
got A (4.0, then 4.6 with rewritten descriptions, 2026-11-19); thirteen
verb_noun tools got A 4.4 (2026-11-19: names 5 of 5, and a remark that no tool
searches the text of the source files); the list is now fourteen, with
`search_source`.

Default: a change of the server keeps the tool list as it is. A new question
becomes a parameter of the tool that already answers the nearest question. Add,
rename or remove a tool only when the maintainer asks for it, in one release,
with a line under "Changes" in the changelog that names the tools.

Check every change of the server against this list before the pull request:

1. Count: 3 to 15 tools.
2. One job per tool. Two tools that differ only by direction or by kind are
   one tool with a parameter (`list_dependencies` in and out, `trace_calls`
   in, out and to, `find_definitions` by kind). A wrapper around another tool is an option of
   that tool (`describe_module` takes a keyword).
3. Names: a verb and a noun in lowercase snake_case (`build_graph`,
   `find_usages`, `list_changes`). One pattern for every tool; no name is a
   prefix of another. A directory read one-word names that were not plural
   nouns (`graph`, `facts`, `module`) as a break of the pattern.
4. Description: at most 900 characters, in this order. A verb first, saying
   what the tool does and what comes back ("Lists ...", "Traces ..."); when
   to use it ("Use it before editing a file"); the neighbour for the next
   question, by name ("for one function use `usages`"); and what a caller
   cannot see in the schema: what each parameter means, named in backticks
   (the forms it takes, how two of them interact, the default), that a list
   stops at 200 items unless `all` is true, and what an unknown name returns. Plain sentences, no telegraphic
   "take X".
5. Parameters: every one has a description. A choice is an `enum` with a
   default, and the server refuses any other value. An integer has a lower and
   an upper bound.
6. Annotations: a title of at most 40 characters; `readOnlyHint` is false only
   for a tool that writes (today `build_graph`); every tool is idempotent, not
   destructive and not open-world.
7. The answer is the output of the `wingbox` command behind the tool. An
   `(error ...)` answer is a tool error. Answers stop at 200 lines unless the
   call sets `all`; `impact` answers in the default form of the command, one
   complete form per file, and is never cut.
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

Before the pull request, run the directory's own linter on the server of the
branch (it needs no key and no model; `tdqs score`, the graded part, needs a
model endpoint):

```bash
npx --yes mcp-tdqs lint --command "python -m wingbox mcp" --server-name wingbox
```

Expected: 0 errors and 0 warnings.

After a release that changes the list or a description: the workflow
`publish-mcp` publishes to the MCP registry; on Glama press "Sync Server" in
the admin area of the listing (Repository), wait for its build and release,
and read the new score (https://glama.ai/mcp/servers/aileron-tech/wingbox/score);
write the grade and the notes of the score page into LOG.md.

## 6. After every release

Default: a release is finished only when every place that describes Wingbox
says what the release says. Run this section in the same working session as
the release, top to bottom. Each item ends in a check; write the command and
what it answered into the `LOG.md` entry of the release. Skip an item only
when the release did not change what the item describes, and write
"not applicable" with the reason. A check that fails is fixed or reported to
the maintainer, never left for the next release.

### 6.1 The release exists everywhere

| Check | Command | Expected |
|---|---|---|
| PyPI, both distributions complete | `curl -s https://pypi.org/pypi/wingbox/X.Y.Z/json` and the same for `wingbox-http`: count the entries of `urls` | two files each, the sdist and the wheel: a half-finished upload still installs |
| PyPI, both distributions install | `uvx --refresh --from "wingbox[http]==X.Y.Z" wingbox --version` and `... wingbox mcp --http --help` | X.Y.Z; the options of the HTTP transport |
| GitHub release | `gh release view vX.Y.Z --json tagName,assets` | the tag, the notes, the assets `wingbox-X.Y.Z.mcpb` and `wingbox.mcpb` |
| Checksums and SBOM | `gh release view vX.Y.Z --json assets --jq '.assets[].name'`, then `gh release download vX.Y.Z --pattern SHA256SUMS --output -` and `curl -s https://pypi.org/pypi/wingbox/X.Y.Z/json \| python3 -c "import json,sys; [print(u['digests']['sha256'], '', u['filename']) for u in json.load(sys.stdin)['urls']]"` | the assets `SHA256SUMS` and `wingbox-X.Y.Z.cdx.json` are listed; each of the two lines PyPI answers is a line of `SHA256SUMS` |
| Claude Desktop extension link | `curl -sIL https://github.com/aileron-tech/wingbox/releases/latest/download/wingbox.mcpb` | 200 |
| GitHub Actions on `main` | `gh run list --branch main --limit 10` | `tests`, `docs` and `wingbox` green on the release commit; `publish-mcp` succeeded for the release |
| MCP registry | `curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.aileron-tech/wingbox&version=latest"` | version X.Y.Z, status `active` |
| Plugin pin | `python3 tools/sync_plugin.py`, then `python -m unittest tests.test_plugins` | no change left; the manifest pins the commit of the tag |
| Homebrew tap | `gh workflow run bump.yml -R aileron-tech/homebrew-wingbox` right after PyPI (it also runs hourly), then `gh run list -R aileron-tech/homebrew-wingbox --limit 3` and the formula file | the bump ran; the formula names X.Y.Z and, when the dependencies of the package changed, every one of them as a resource |
| Clean install | section 3, step 6 | `wingbox build .` and `wingbox impact <file>` answer in a small project |

`wingbox-http` is built with `uv build packages/wingbox-http` and published
after `wingbox`, because it requires exactly that version of it.

A macOS job that ends "cancelled" with no steps never got a runner:
`gh run rerun <run id> --failed` restarts only that job. A red job is read
(`gh run view <run id> --log-failed`) before anything is rerun. The `tests`
runs of `main` share one concurrency group, which holds one running and one
waiting run: restarting a run while another waits cancels the waiting one, so
restart them one at a time, the oldest first. A job of the release commit that
runs after the tag exists is red in `test_plugins` until the pin commit: the
run of the pin commit is the one to read.

### 6.2 The descriptions

Read the changelog entry of the release, then check each text against it.
The slogan and the headlines are the maintainer's: they change only on the
maintainer's word; everything else follows the release.

- `README.md`: the first screen (what Wingbox is, the install command, the
  languages, the assistants), the tables of commands and of MCP tools, every
  command and option the release added, renamed or removed. Checks:
  `python -m unittest tests.test_readme_numbers tests.test_english_only`, and
  each command the README shows for the changed feature run once as written.
- GitHub "About" of the repository: description, website and topics.
  `gh repo view aileron-tech/wingbox --json description,homepageUrl,repositoryTopics`;
  change with `gh repo edit --description "..."` and `--add-topic`. The
  description names the languages, the assistants and "no model, nothing
  leaves your machine"; a release that adds a language or an assistant adds
  it here.
- `pyproject.toml` `description` and `keywords` (the text PyPI shows) and the
  same lines of `packages/wingbox-http/pyproject.toml`: PyPI keeps the text
  of a version for good, so a correction goes into the next release.
- The plugin manifests and store texts: `plugins/wingbox/`, `docs/store/`
  (`python3 tools/sync_plugin.py` copies what is generated; read what is
  written by hand).

### 6.3 Where the MCP server is listed

Update a listing when the release changed what it shows: the description, the
tool list, the install command or the transport. For each place: what it
takes its text from, and how to check it.

| Place | Text comes from | After a release |
|---|---|---|
| Official MCP registry, `io.github.aileron-tech/wingbox` | `server.json` (description, version, package, transport) | Published by the workflow `publish-mcp` on the GitHub release; check as in 6.1. A new description is an edit of `server.json` before the release. |
| Glama, `glama.ai/mcp/servers/aileron-tech/wingbox` | The repository (`glama.json`, README, the running server's tool list) | When tools or descriptions changed: ask for a new score on its score page and write the grade into `LOG.md` (section 5). |
| `punkpeye/awesome-mcp-servers` | Our line in its README (through our pull request) | When the one-line description changed: update the line in the open pull request, or open one for the merged line. |
| `mcpservers.org` | The submission form | Submit again when the description or the install command changed. |
| Best MCP, `bestmcp.it/code-analysis/wingbox` | Its own crawl of the registry and the repository; the claimed listing is edited in the submitter dashboard | Read the page; correct the description, the install command and the categories there when they differ. |
| Claude Desktop extension | The manifest inside `wingbox.mcpb` (`tools/build_mcpb.py`) | Built for every release; `python -m unittest tests.test_mcpb`. |

A new directory is added to this table in the same change that submits the
server to it.

### 6.4 The pull request check: GitHub Actions and GitLab CI

The check lives in `aileron-tech/wingbox-action` and installs Wingbox from
PyPI, so a Wingbox release is followed there:

1. Run `python3 scripts/bump_version.py X.Y.Z` in that repository: it sets the
   default of the `version` input in `action.yml`, the version its README
   names, and `WINGBOX_VERSION` in every template under `gitlab/`. Run its
   tests; pull request; merge; commit "Release X" on `main`. Then wait for the
   `test` workflow of that commit: its fixture job runs the action itself with
   the new default Wingbox on Linux, macOS and Windows. Only when it is green,
   tag `vX` (a patch version when only the default moved), move the tag `v1`
   and create the release. A tag before that run is how a wrong default would
   reach every user of `@v1`.
2. GitHub: rerun the action on the example pull request the site links. The
   comment is updated in place, its review plan says `:wingbox "X.Y.Z"`, and
   the result and the counts are the ones expected for that change.
3. GitLab: run the pipeline of the test project with the released template
   (`include` of the tag). The job succeeds, the note on the merge request is
   updated in place, and `wingbox-out/` holds the report, the plan and the
   evidence record. Pin nothing of the test project to a commit of a
   development branch: after the squash merge that commit is in no branch and
   the job fails with "unable to read tree".
4. The Marketplace page of the action shows the new version, and the workflow
   `.github/workflows/wingbox.yml` of this repository (the check on our own
   pull requests) ran green on a pull request opened after the release. That
   workflow names the action at a commit (REQ-REL-001), so it follows a
   release of the action only when its `uses:` line is changed to the commit
   of the new tag (`gh api repos/aileron-tech/wingbox-action/git/ref/tags/vX.Y.Z`)
   with the tag as the comment; that pull request is the one to watch.
5. A new input, output or variable of the action is in its README tables, in
   both GitLab templates and on the site page (6.5). A path that only unit
   tests cover is said to be so and is not written on the site before a real
   pipeline ran it.

### 6.5 The sites

- `wingbox.dev` (repository `aileron-tech/wingbox-site`): the install lines,
  the list of commands and MCP tools on the home page, the page
  `/pull-request-check` (inputs, outputs, `WINGBOX_VERSION`), the benchmark
  pages when a result file changed (`tools/import_benchmark.py`,
  `import_comparison.py`, `import_projects.py`, `import_usages.py` copy the
  results of this repository). A number is on the site only when it is in a
  published result file of this repository, and a new or changed benchmark
  number is shown to the maintainer before it is published. Deploy, then
  check the live pages: the new text is there (`curl -s <page> | grep`), and
  the changed block has no horizontal overflow at 1440 and 390 px.
- The company page for Wingbox: the version-independent summary still fits
  the release.
- Wait until 6.1 is green before a site or a listing names the release.

### 6.6 Report

The `LOG.md` entry of the release lists 6.1 to 6.5 with the evidence of each
item, or "not applicable" and the reason. Announcements are the maintainer's
decision: prepare the checked headlines (what a user can now do, in the
user's words, each one verified by a command) and hand them over.
