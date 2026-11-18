# Changelog

## 0.12.0 - 2026-11-18

### Changes

- Language parsers on demand. The package carries the parsers of TypeScript, JavaScript and Rust
  (Python and Pascal need none); the parsers of C#, Java, Go, Ruby, PHP and Dart are added with one
  command, `wingbox add <language>`. **After upgrading, a project in one of these six languages
  needs one `wingbox add <language>`** (for example `wingbox add csharp`); until then a build maps
  the other languages, says how many files it left out and names the command. `wingbox add csharp
  php` adds several, `wingbox add --list` shows which are installed and from where, and `wingbox
  add csharp --from FILE` takes a wheel file or a folder of wheels on a machine without network
  access.
- Nothing is downloaded on its own. `wingbox add` is the only command that fetches a parser: one
  file, the pinned wheel for your system from `files.pythonhosted.org`, refused unless its sha256
  is the one recorded in the package. A build, a refresh, a hook and the MCP server never
  download; a test proves that the download code is reachable from `wingbox add` alone.
- The parser goes into your home folder, `~/.wingbox/parsers/<platform>/<package>-<version>/`
  (`WINGBOX_PARSERS_DIR` names another folder), and is shared by every Wingbox on the machine:
  pipx, `uv tool`, `uvx` (the Claude Code plugin, the Claude Desktop extension), Homebrew and pip.
  No pip is needed to add one.
- A clean install is 10.2 MB smaller: 17,211,725 -> 7,023,433 bytes installed with its
  dependencies (CPython 3.12, macOS x86-64).
- A language that was left out is said wherever the graph is read, as one line,
  `(skipped-language csharp :files 214 :add "wingbox add csharp")`: on stderr of every query
  command, at the head of every MCP answer, in the context the search hook gives, in the last
  lines of `wingbox build` and under the key findings of `REPORT.md`. `wingbox add` marks the
  graph of the current project, so its next use rebuilds it with the language.
- Every on-demand parser is also a pip extra with the same pin, and `all` has them all:
  `pipx install "wingbox[csharp]"`, `pip install "wingbox[all]"`. A parser installed that way is
  used first. The Docker image and the map-request workflow install `wingbox[all]`.
- The graphs are unchanged: for 61 projects in the six languages, the graph built after
  `wingbox add` is byte for byte the graph 0.11.0 builds.

### Fixes

- A `data-flow` fact no longer carries `:validated` on the strength of a text match in the imported
  file. The mark is now `:validation-module :evidence "<kind> <name>"`. It is written only when the
  syntax tree of the imported file defines a validator, and it names what was found: a pydantic model,
  validator decorator or schema call, an attrs field with a validator, a call of zod, joi, yup or ajv,
  a Go struct tag `validate` or `binding`, a Bean Validation annotation, a DataAnnotations attribute or
  a FluentValidation validator, a Laravel form request, `Validator::make` or Livewire's `Validate`
  attribute, an ActiveModel `validates` declaration, a Dart `validator:` argument. A comment, a string,
  an unused import, `TypedDict`, `NamedTuple` and dataclasses give no mark. The mark describes the
  imported file; it does not say that the importer calls the validator. A graph built by an older
  version is read without its `:validated` marks; the next `wingbox build` parses every file again
  ([#121](https://github.com/aileron-tech/wingbox/issues/121)).
- `wingbox analyze` and the MCP tool `analyze` printed `:validated` on every `analyze-dataflow` line.
  They now print the marks of the stored fact and nothing else
  ([#122](https://github.com/aileron-tech/wingbox/issues/122)).

## 0.11.0 - 2026-11-18

### Changes

- The package is about 3 MB smaller (wheel 3.56 MB -> 0.60 MB): the built-in word-vector table and
  the `--synonyms` option of `wingbox init` and `wingbox build` are removed. On the held-out half
  of the architecture benchmark the table did not beat the style dictionaries. Folder names are
  matched by the dictionaries and by spelling, as before; output without the option is unchanged.
- `WINGBOX_JOBS` sets the most worker processes a build or a background refresh starts (a whole
  number from 1; default: one per processor). `WINGBOX_JOBS=1` parses in the one process, for a
  laptop that has other work to do or a shared build machine.

## 0.10.2 - 2026-11-18

### Fixes

- `wingbox impact` does about half the work per call: the graph file is read with one pattern instead
  of character by character, the layer guess is cached, and the project folder is resolved once.
  Output is unchanged. Thanks to @just4just2 for the profile
  ([#106](https://github.com/aileron-tech/wingbox/issues/106)).
- In Claude Code's Code Mode the search hook no longer adds a context block for every search a script
  makes, and it never holds a tool call up: it stops itself after 5 seconds.
- On Windows, two `wingbox mcp --build-missing` servers started at the same moment (Claude Desktop
  starts its server twice) could leave one of them stopped by `PermissionError` on
  `.wingbox/.gitignore` while the other was replacing that file. The file is now read again after a
  moment.

## 0.10.1 - 2026-11-18

### Fixes

- A source file or a manifest (`Cargo.toml`, `go.mod`, `pubspec.yaml`) that starts with a UTF-8 byte
  order mark is read like the same file without it. Before, a Python file saved with a mark (Visual
  Studio and some Windows editors write one) lost its functions, classes and references in the graph,
  and a `Cargo.toml` or `go.mod` with a mark lost the edges between packages.

Thanks to @just4just2 for the report with a reproduction
([#105](https://github.com/aileron-tech/wingbox/issues/105)).

## 0.10.0 - 2026-11-18

The answers of `wingbox impact` for Rust change: a file that uses an item through a module facade now
depends on the file that defines the item.

### Changes

- Rust: a file that names an item through a module facade (`use crate::domain::ForkContext` where
  `src/domain/mod.rs` has `pub use types::*`) now also depends on the file that defines the item, an
  import edge marked `:item`; the edge to the facade stays. Chains of facades, renamed re-exports
  (`pub use child::Name as Alias`), `pub(crate)` re-exports, a use path that starts at a module brought
  in by an earlier `use`, and items reached through a glob import whose body names them, alone or as
  the head of a path (`Handler::new()`), are followed (`:item :candidate`); a child that glob-imports
  its parent (`use super::*`) also reaches what the parent imports privately. A private item is never
  carried by a glob re-export. An item defined in the facade itself stays on the facade; so does one a
  macro makes in a module the facade re-exports with a glob (the build log counts those). `wingbox impact` on a Rust file
  therefore lists the files that use its items, not only the facade: on claudes-c-compiler,
  `src/backend/x86/assembler/encoder/registers.rs` goes from 1 direct dependent (the facade) to 7.
- Rust: a member named through a type path (`Running::from_env()`, `Self::LIMIT`, `<Type>::new()`,
  `Type::<T>::new()`, the type named by a `use`, a facade, a glob import or a type alias) depends on
  the file whose inherent `impl` block of the type defines the member, an import edge marked `:item`;
  the edge to the file of the type stays. A type's `impl` blocks often lie in several files: on
  agent-browser, `cli/src/session_settings/seed.rs` goes from 1 direct dependent to 5. A method called
  on a value (`value.method()`) and a member a trait gives need the type checker and give no edge.
- Rust module files follow the compiler's layout rules: the root file of every Cargo target owns its
  folder whatever its name (`mod common;` in `tests/it.rs` is `tests/common/mod.rs`, `mod helper;` in
  `src/bin/tool.rs` is `src/bin/helper.rs`) and `crate::` in such a file is that target, not the
  library; `#[path = "f.rs"]` and `#[cfg_attr(.., path = "f.rs")]` name the file of a module (every
  candidate is kept) and paths through the module follow it; a module declared inside an inline
  module lies in that module's folder; `mod r#type;` is `type.rs`.
- Rust crate names follow Cargo: `[lib] name` and `path`, `[[bin]] path`, a dependency renamed with
  `package = ".."`, `extern crate a as b;` and `extern crate self as me;`. In a package of edition
  2015 (no `edition` key) a `use` path and `::name` start at the crate root.
- The cache format changes (Rust items record whether they are private; Rust files record their
  `impl` blocks and type aliases): the first build after the upgrade parses every file again.
- `wingbox impact` says why each file is on the list: the import, or the items the file uses (for Rust
  with their names and how they arrive: a `use`, a facade re-export, a glob import, a member of a type,
  a macro), and below depth 1 the file it is reached through; the `--sexp` facts carry `:through`,
  `:more` and the name lists, and the MCP tool prints the same. The list of files is unchanged.
- The `wingbox` skill now installs the program by itself when it is missing: a skill that came from a
  plugin directory tells you, runs `pipx install wingbox` or `uv tool install wingbox`, and builds the
  map. `plugins/wingbox-skill` is the skill alone, the form the Claude plugin directory accepts from any
  organization (it takes hooks and a local MCP server only from organizations it has verified); the
  full plugin in `plugins/wingbox` is unchanged.

### Fixes

- Rust: a named re-export of an item that a macro invocation defines (`pub use spec::Key;` with
  `keys! { .. }` in `spec.rs`) leads to the module the re-export names; it stayed on the facade.
- Rust: after a glob import, a name the file defines itself or imports by name is no longer taken
  from the glob (it gave an edge to the wrong file); variants re-exported with `pub use Kind::*;`,
  a `macro_rules!` macro re-exported as an item and a module re-exported under a name
  (`pub use self::inner as api;`) are followed; a `#[macro_export]` macro of another workspace crate
  resolves to the file that defines it. An item declared `pub(super)` or `pub(in path)` is carried by
  a glob re-export only into that scope, and `pub(self)` is private.

### Benchmarks

- The Rust reference of the benchmarks (`tools/rust_resolve.py`, no Wingbox code) counts, besides the
  module a path reaches, the file that defines a named item (through `pub use` re-exports), the items a
  module names after a glob import, and a member named through a type (`Type::member`) to the file whose
  `impl` block defines it. A file that uses an item depends on the file that defines it; the earlier
  reference stopped at the module facade. `tools/rust_compiler_check.py` checks the reference against
  rust-analyzer: every required pair the compiler can see is confirmed on both Rust repositories.
  Against this reference the graph has 0 wrong and 0 missing Rust edges (456 and 2,084), and
  `wingbox impact` is complete on 100 of 100 questions. The 100 questions stay the same draw.
- The agent tests ask a question that states, for each language, what the reference counts; before,
  its Rust clause named only a `mod`, a `use` or a path of the module, and agents left out the files
  that use an item through a re-export. They run on `claude-opus-5-5`. Impact test, 55 questions:
  complete answers 55 of 55 with Wingbox against 48 of 55 with the agent's own search, cost per answer
  -38 % (0.110 against 0.179 USD), tokens -32 %. Comparison test: Wingbox F1 1.000 in 15 of 15 trials,
  at 0.121 USD per trial against 0.354 to 0.402 for the other setups.

## 0.9.2 - 2026-11-18

### Fixes

- On Windows `wingbox update` replaces the `wingbox.exe` it runs from. Windows lets nobody
  overwrite or delete a running program, so in a `uv venv` the upgrade failed with
  `failed to remove file ...\Scripts\wingbox.exe (os error 32)` and the version stayed, and with
  `uv tool` the package was upgraded while the command reported a failure
  (`failed to copy file ... to ...\bin\wingbox.exe`) and skipped the refresh of skills and hooks.
  An install made with pip or pipx failed in the same place.
  For the time of the upgrade the launcher is now renamed, the installer writes the new one in its
  place, and the old file is removed when the command has ended; when there is nothing to upgrade
  it is renamed back. CI installs the checkout with uv pip, `uv tool`, pip and pipx on Windows,
  macOS and Linux and updates each one through its own `wingbox` program.

Thanks to @just4just2, who tested 0.9.1 on Windows 11 in live sessions and reported this with the
exact errors ([#48](https://github.com/aileron-tech/wingbox/issues/48)).

## 0.9.1 - 2026-11-17

### Fixes

- Inside a Homebrew install `wingbox update` changes nothing and names `brew upgrade wingbox` (it
  chose `uv pip` when uv was on PATH and would have upgraded the package behind Homebrew's back).
  Wingbox installs with Homebrew from the tap: `brew install aileron-tech/wingbox/wingbox`.
- The Python reference of the benchmarks counts a module loaded by a literal name
  (`importlib.import_module("a.b")`, `__import__("a.b")`) like an import. FastAPI: 1,802 import
  pairs, Wingbox 1,819 edges, 0 wrong, 0 missing (it was 1,801 pairs; the graph had found the extra
  dependency since 0.6.0). The impact questions and their reference lists are unchanged; the
  comparison's FastAPI rows are scored again for every tool.

## 0.9.0 - 2026-11-17

Wingbox writes in two places only: a repository's `.wingbox/` folder and your
own settings in your home folder. A test runs every command in a temporary
repository with a temporary home folder and fails on any other changed path.

### Changes

- `wingbox install` shows what it is about to put in place, one line with a
  checkbox per item, grouped by assistant: `skill`, `block`, and for Claude
  Code `search-hook`, `edit-hook`, `session-hook` and `git-hook`. In a
  terminal you untick items (arrow keys and space, or typed numbers where the
  terminal gives no single keys) and press Enter; Enter alone installs
  everything as before. Started by an agent, a script, CI or with `--yes`, it
  asks nothing; `--without ITEM[,ITEM]` (or `ASSISTANT:ITEM`) leaves items out
  and `--without none` puts them back. The choice is recorded in
  `~/.wingbox/installed.sexp`, so `wingbox update` refreshes only the items you
  kept. `wingbox uninstall --without ...` keeps the named items. `--dry-run`
  prints the list.
- Two new Claude Code hooks keep the graph fresh after a branch change,
  installed by `wingbox install` and carried by the plugin: `SessionStart`
  (`wingbox hook session-start`) refreshes a graph older than the code in the
  background when a session starts, and `PostToolUse` on `Bash`
  (`wingbox hook git`) does the same after the agent ran `git switch`,
  `checkout`, `pull`, `merge`, `rebase`, `reset` or `stash pop`. Both print
  nothing and return at once. On angular/components, switching from 20.1.0
  to 20.2.0 (256 changed sources) with the first search 5 s later, the graph
  was fresh 8.8 to 9.5 s after the switch instead of 13.6 to 13.7 s.
- `wingbox init` writes only into `.wingbox/`: `config.yaml`, and
  `assistant-setup.md` with the lines a team may add by hand to the
  repository's own files (the always-on block for `AGENTS.md` and `CLAUDE.md`,
  the hook entries for a committed `.claude/settings.json`); it prints them
  too. The file is meant to be committed.
- The build lock and the first-build lock of the MCP server live in
  `.wingbox/` instead of the system's temporary folder.

### Removed

- `wingbox install --project`. It wrote the skill, the block and the hooks
  into the repository, outside `.wingbox/`. It now exits with an error that
  says what to run instead and writes nothing. If you used it (as in issue
  #48): in each such repository run `wingbox uninstall --project`, which
  removes only the marked blocks, the hook entries and the skill folders that
  install wrote, then run `wingbox install` once for your own assistants. A
  team that wants the setup in the repository commits the lines of
  `.wingbox/assistant-setup.md` by hand. Until then `wingbox update` names each
  repository where those files are still there; it no longer refreshes them.

### Fixes

- A folder named `vendor` inside the sources that holds first-party code (no
  dependency manager's files in it, no project manifest next to it, below
  `src`, `lib`, `libs`, `app` or `packages`) is part of the graph; vendored
  dependencies of Go, Composer, Bundler and a `vendor` folder next to a
  manifest stay out. On bitwarden/clients 9 files and 57 import edges of
  `libs/common/src/tools/extension/vendor` were missing; they are in the graph,
  and every one of them is an edge dependency-cruiser finds too.
- TypeScript imports written from `baseUrl` without a path alias
  (`import ... from "src/app/core/x"`) resolve, from the `baseUrl` of the
  nearest `tsconfig`. On thingsboard (ui-ngx) the 6 edges that were missing
  against dependency-cruiser are in the graph; what still differs there is
  only imports of non-code files and `.d.ts` declarations.

## 0.8.2 - 2026-11-17

Fixes from a test on Windows 11 with live Claude Code sessions, reported in
issue #48.

### Fixes

- Windows: a console window no longer flashes on a background refresh. The
  refresh started with `DETACHED_PROCESS` had no console, so every console
  program it ran (git, its worker processes) opened a visible window of its
  own; it now starts with a console that has no window (`CREATE_NO_WINDOW`),
  which those programs inherit, and every other child Wingbox starts (git,
  installers, the first build of the MCP server, a language server) gets no
  window either.
- When two search hooks run at once (Claude Code runs Grep and Read in
  parallel), the second refresh found nothing left to do and still ran a
  build, which could outlast the hook's wait and answer `wingbox-graph-stale`.
  A refresh that finds nothing changed once it holds the lock now ends at
  once.
- `wingbox update` in an environment without pip (made by `uv venv`) upgrades
  through `uv pip install --python <this interpreter>`, and without uv says
  what to run instead of failing on "No module named pip".
- A plain `wingbox build` leaves `git status` clean: the `.gitignore` it
  writes in `.wingbox/` ignores itself, and `decisions.sexp` is started only
  once the project is set up (`wingbox init`, `learn`, `align --learn` or
  `advice`).
- The Codex plugin's MCP server runs the pinned release through `uvx` (with
  `--build-missing`), so a plugin installed by clicks works without a
  `wingbox` command on PATH; the Codex plugin needs uv, as the Claude Code
  plugin does.
- `wingbox mcp --build-missing` started twice at once (Claude Desktop does)
  builds once: the second server says `a build is already running: waiting
  for it` instead of logging an error. In a folder that is in no repository it
  builds nothing, and every tool answers "No repository here".

### Changes

- One short description in every manifest (the plugins, the marketplace, the
  Claude Desktop extension): "A map of your codebase for coding agents: what a
  change touches, where a symbol is used."

- README FAQ: why the first searches after a branch switch say
  `wingbox-graph-stale`, and why a large switch takes a build.

## 0.8.1 - 2026-11-17

### Fixes

- `wingbox init` on a repository with fewer than 12 source files wrote
  `parts:` with no entry into `.wingbox/config.yaml`, and the next
  `wingbox build` stopped with "must have a mapping `parts`" and wrote no
  graph. Init now writes `parts: {}`, and a config whose `parts` is empty or
  missing (as 0.6.0 to 0.8.0 wrote it) is read as no parts declared: the whole
  repository is one undetermined part, and the commands say so in one line.
- An error in `.wingbox/config.yaml` names the line of the file
  (`.wingbox/config.yaml line 4: part app: unknown style ...`).

## 0.8.0 - 2026-11-17

### Changes

- Claude Desktop extension: `wingbox-<version>.mcpb`, published with each
  GitHub release and built by `python3 tools/build_mcpb.py`. An MCP Bundle
  (manifest 0.4, server type `uv`) that installs this release of `wingbox`
  from PyPI and runs `wingbox mcp` for the repository the user picks in the
  extension settings; the first use builds its graph.
- `wingbox mcp --build-missing`: when the repository has no code graph, the
  server builds it in the background; a tool call waits up to 45 seconds
  (`WINGBOX_FIRST_BUILD_WAIT`) and otherwise says that the first build is
  still running.

### Fixes

- The search hook no longer stays silent in a source repository without a
  graph: once per session it answers `(wingbox-no-graph :root <root> :hint
  ...)`, naming `wingbox build`, so the agent learns that Wingbox is there. It
  never builds by itself, writes nothing into the project, and says nothing
  outside a source repository (the home folder, a folder of documents).
- The search hook matches a Grep pattern with smart case, as `wingbox quick`
  does: `subscription` now finds `Subscription`; a pattern with an upper-case
  letter keeps its case, and Grep's `-i` always ignores it.

## 0.7.2 - 2026-11-17

A release of the documents; the tool behaves as in 0.7.1.

### Changes

- README, which is also the PyPI page: the title "Wingbox: a map of your
  codebase for AI coding agents", the comparison of four tools from the current
  run, a section on the find-usages benchmark, macOS, Linux and Windows named
  in the lead and the FAQ, the plugin install commands in their verified forms.
- A test keeps the numbers of the README's summary tables equal to the
  generated benchmark reports.
- The roadmap lists what shipped in 0.6 and 0.7 apart from what is in progress.

## 0.7.1 - 2026-11-17

### Fixes

- The hooks and the MCP server work on Windows without Git for Windows (Git
  Bash), under PowerShell or cmd, and with it. The Claude Code plugin no longer
  goes through `sh`: its hooks and MCP server start `uvx` directly (a real
  executable on every system) with the release pinned to its commit; the
  launcher script `scripts/wingbox-run` is removed. The plugin now needs uv.
- Every hook entry, from `wingbox install` (global or `--project`) and from the
  plugin, is written in exec form (`command` and `args`, no shell) with
  `"onFailure": "continue"`, so a missing program or a timeout never blocks a
  search, a read or an edit. `--project` writes `"command": "wingbox"`, found
  on `PATH`. Entries written by earlier versions are recognised and replaced.

### Changes

- The plugin hooks always run the pinned release through `uvx --offline`; an
  installed `wingbox` command is no longer preferred. Each search hook takes
  about 0.4 s longer for those who had it installed.
- CI runs every hook entry Wingbox writes as Claude Code does, in a project
  folder whose name holds a space and non-ASCII letters, on Linux, macOS and
  Windows (there with Git for Windows taken off `PATH`).

## 0.7.0 - 2026-11-17

Wingbox runs natively on Windows (issue #48). The test suite runs on Linux,
macOS and Windows with Python 3.11, 3.12 and 3.13 in GitHub Actions.

### Changes

- Windows support: the build, queries, the hooks, `wingbox install`,
  `wingbox update` and the background refresh of the graph work on Windows.
  Locks, detached processes, atomic file replacement and worker processes go
  through one module of platform differences (`scripts/portable.py`).
- The worker processes of the build start with `spawn` where `fork` does not
  exist; the graph is byte for byte the same whatever the start method.
- On Windows, `wingbox install` writes the Claude Code hooks in exec form (the
  full path of `wingbox.exe` and its arguments apart), which Claude Code starts
  without Git Bash or PowerShell.
- The plugin's Claude Code hooks are in exec form too (`sh` with the launcher
  and its arguments as a list), as the Claude Code documentation advises for a
  path under `${CLAUDE_PLUGIN_ROOT}`; on macOS and Linux they run as before.
- With no `EDITOR` or `VISUAL` set, `wingbox learn` offers Notepad on Windows.
- README: PowerShell forms of the Docker and script examples, and how to use
  Wingbox on Windows without Git for Windows.

### Fixes

- Every path Wingbox stores in `.wingbox/`, prints or compares is written with
  `/` on every system; paths from a person or a hook (backslashes, a Git Bash
  drive such as `/c/Users/...`, the `\\?\` prefix, another drive) are
  normalised where they come in.
- Every file Wingbox writes uses UTF-8 and LF line endings; source files with
  CRLF line endings give the same graph, line numbers included.
- Output is written as UTF-8 whatever the console code page, and colours are
  used only on a terminal that shows them (`NO_COLOR` is respected).
- `git` output is read as UTF-8, with file names unquoted.
- No `SyntaxWarning` on the first run under Python 3.13; a test compiles every
  source file with warnings turned into errors.

## 0.6.2 - 2026-11-17

A release of the package page and the documents; the tool behaves as in 0.6.1.

### Changes

- The PyPI page is brought up to date: summary, keywords, classifiers (Python
  3.11 and 3.12) and project links (documentation, benchmarks, release notes).
- The links of the README are absolute, so they work on the PyPI page too.
- `docs/edge-model.md`: a proposal, not built, for kinds of edges, external
  packages as a layer of their own and outbound access (databases, HTTP,
  messaging) found through the dependency graph. The roadmap links it.

## 0.6.1 - 2026-11-17

### Changes

- The MCP server can be listed in the MCP Registry: `server.json` at the root
  names the PyPI package and starts it with `wingbox mcp`, and the README
  carries the ownership line the registry checks.

### Fixes

- `wingbox boundaries` no longer prints `boundaries-ok` for parts that were not
  checked. A part whose declared rules were not read (a dependency-cruiser or
  import-linter configuration this version cannot read), whose style is
  undetermined or that has no layer style comes first as
  `(boundaries-not-checked :part . :reason declared-rules-not-read :files
  (.dependency-cruiser.js) :files-in-part 1177)`, and `(boundaries-partial
  :checked N :not-checked M)` replaces `ok`. Exit status 2 when a part's
  declared rules were not read, 1 when a violation was found, 0 otherwise.
  REPORT.md, `wingbox advice` and the MCP `boundaries` tool say the same.
- `wingbox init` no longer tells people to edit `.wingbox/config.yaml`: it
  names the next step, `wingbox learn`, which settles the open points (the
  coding agent's task, asking the operator when the code does not tell, or
  questions in a terminal), then `wingbox build`. The command help, the
  README example and the style library notes say the same.

## 0.6.0 - 2026-11-17

### Changes

- Release summary: Wingbox now judges the architecture of each part of a
  repository and keeps it. `wingbox init` finds the style of each part
  (layered, onion and clean, hexagonal, MVC, NestJS, microkernel, route-tree,
  Feature-Sliced Design, CQRS as an overlay) or reads the rules the project
  declares; `learn`, `align`, `place`, `advice` and `compare` work from it. The
  layer configuration is settled through the coding agent, which asks the
  operator when the code does not tell, and `.wingbox/config.yaml` starts with
  instructions. Capability rules check what each layer may do: SQL and the
  database client in the repositories, outbound HTTP in infrastructure, no web
  framework in the domain, with three pattern checks (a controller that skips
  the service, a service that builds an HTTP response, a domain that imports
  the ORM). Python modules loaded at run time by names from configuration files
  are followed.
- README: the header opens with "Give your coding agent a map of your codebase.
  Half the cost, no missed files." and the benchmark numbers behind it.
- `benchmarks/graphify` measures CodeGraph 1.6.2 (npm `@colbymchenry/codegraph`)
  next to Wingbox and Graphify on the same repositories, questions, reference
  lists and scoring: the impact command, every file-to-file edge against the
  import statements, the agent test, and a new build-time test of the three
  tools on one machine, cold and after a one-line edit. `run.sh` installs
  CodeGraph from npm. The Wingbox column is measured with 0.5.1 and `run.sh`
  pins it; the report opens with "Which tool for which job" and explains in
  "Why the numbers differ" how each tool's design produces its numbers.
- New command `wingbox init [PATH] [--force] [--sexp]`: splits the repository
  into parts (a folder with a manifest: `package.json`, `pyproject.toml`,
  `Cargo.toml`, `go.mod`, ...), gives each its C4 level and judges its
  architecture style with a score and the folders that support it: layered,
  onion / clean, hexagonal, MVC, NestJS, microkernel (plug-ins), route-tree
  frontends, Feature-Sliced Design, CQRS as an overlay. A part that declares
  its own rules (dependency-cruiser layers, import-linter, tach, deptrac,
  packwerk, ESLint boundaries, ArchUnit) is judged by them; a
  dependency-cruiser layer list in plain data is read. It writes
  `.wingbox/config.yaml` (kept when it exists, unless `--force`). The text is a
  printer over S-expression facts (`arch-part`, `arch-evidence`,
  `arch-candidate`, `arch-declared`, `arch-config-written`).
- The styles are TOML files shipped in `scripts/architecture/styles/`, with
  their sources, detection groups, layers, rules and target layout.
- `wingbox init` proposes the full architecture of each part: the target
  layout of its style, with the building blocks of the domain tier (entities,
  value objects, aggregates, domain events, domain services, repository
  interfaces, specifications), each folder present or proposed
  (`(arch-target ...)`, `target:` in `.wingbox/config.yaml`).
- Recommendations: `wingbox init` ends each part with its advice, REPORT.md
  has a "Recommendations" section, the map carries them, and `analyze` and
  `impact` print the advice that concerns the module:
  `(advice <part> :kind move|fix-violation|missing-folder :target ... :style
  ... :rule ... :reason ... :evidence ...)`. A file its dependents import
  against the rules is a move (with the counts), a violation comes with the
  fix its style prescribes, a missing folder of the target layout is
  proposed. No advice for a part whose style is not determined.
- `.wingbox/decisions.sexp` and `wingbox advice`: builds log the advice and
  its outcome (followed, done differently, ignored); `wingbox advice
  accept|reject <id>` records a decision. A file moved to another folder than
  advised teaches that folder's layer (`:learned-from <id>` on later labels);
  rejected items, and items ignored for 3 builds, are no longer shown, and
  advice of the same kind and rule in the part ranks lower.
- `wingbox align [PART] [--to STYLE] [--learn]`: architecture gaps of each
  part, a migration view against another style, and learning: folders
  without a layer get one from their place in the dependency graph, folders
  whose name contradicts a restatement are listed for review, the learned
  folders go into `.wingbox/config.yaml` and the remaining breaks into
  `.wingbox/baseline.sexp`. A pair listed in a baseline (the project's own or
  this one) is a `violation-known` and is not counted.
- `wingbox learn`: for a coding agent, a task (closest style, its layers, the
  folders it cannot place with files, imports and a name guess, the questions,
  the rules and the answer format); `wingbox learn --apply` validates the
  answer, rejects it whole with every error, or writes it into
  `.wingbox/config.yaml` (never over an entry of the user),
  `.wingbox/learned.sexp` and `decisions.sexp`, and rebuilds. For a person in a
  terminal, the deterministic learning, confirmed part by part. Who runs it:
  `--agent`/`--human`, then `CLAUDECODE=1` or `GEMINI_CLI=1`, then no terminal
  means an agent. `.wingbox/.gitignore` keeps the graph and the cache out of
  version control. The task also lists nested folders whose files break a
  rule or rest on a guess (`lib/hooks`), 25 per part, most breaks first; an
  answer for a folder path decides for the files below it. The rebuild after
  `--apply` is quiet unless `--verbose`.
- `wingbox place "<what>"`: the layer, the folder (present, or new from the
  target layout) and the allowed imports for new code, from the placement
  rules of the part's style.
- `wingbox init` in an empty folder starts a new project: it offers the
  styles that fit the language (monorepo siblings first, then the
  `$HOME/.wingbox/config.yaml` favourite, then the library), never blocks
  without a terminal (`--language`, `--style`, `--yes`), writes the full target
  layout and creates the folders only with `--scaffold`. Style files in
  `$HOME/.wingbox/styles/` replace built-in ones.
- `wingbox compare`: the parts of a monorepo side by side per language, with
  their differences and an advice item for a part whose style differs from
  the majority of its language.
- `wingbox build` reads `.wingbox/config.yaml`, or detects the styles when it
  is absent. Layers come from the style of each part; boundary rules are the
  rules of that style and apply inside a part; a part whose style is not
  determined gets no layers and no violations. Every label and violation
  names its part, style and rule: `(layer ...)` and `(arch-part ...)` facts in
  the graph, `:part :style :rule` on `boundaries` and `layers` lines, a
  "Style and rule" column and an "Architecture" section in REPORT.md, the rule
  of each module in the map. A violation the project already lists in its own
  baseline carries `:known <file>`.
- Folder names match style words as whole names (lowercase, `-` and `_`
  dropped), then as a spelling variant; with `--synonyms` (init and build), a
  name still unmatched goes through a built-in table of word vectors for
  architecture terms (GloVe 6B, public domain, 3 MB). The step is recorded as
  `:match`. Wingbox does not embed your code.
- `benchmarks/architecture`: 476 hand-labelled folders of 19 public
  repositories and 33 parts with an expected style; on the held-out half the
  word vectors do not beat dictionaries and Levenshtein (75.4 % against
  76.2 %), so they are off by default and style detection never uses them.
- The files a person or an agent decides on stay in `.wingbox/` next to the
  index and are not in `.wingbox/.gitignore`: `config.yaml`, `decisions.sexp`,
  `learned.sexp` and `baseline.sexp` can be committed.
- Capability rules: what a layer may do. The build finds, by syntax and with
  the line as evidence, what each file does - `sql` (a SQL string passed to
  `execute`, `query` or `raw`, query builders, `session.query`, Prisma model
  calls, `DB::select`, `find_by_sql`, `createQuery`, `FromSqlRaw`,
  `db.Query`), `db-client`, `http-client`, `framework`, `http-response`,
  `env`, `filesystem`, `process` - in Python, TypeScript and JavaScript, PHP,
  Ruby, Java, C# and Go. Each style states where they are allowed (layered:
  SQL and the database client in infrastructure, outbound HTTP in
  infrastructure, no web framework in the domain, the environment in
  configuration; hexagonal: all I/O in the driven adapters; MVC: queries in
  the models), and `capabilities:` of a part in `.wingbox/config.yaml`
  restates a rule. `wingbox boundaries` prints `(capability-violation <file>
  :capability sql :layer service :allowed (infra) :line 42 :evidence "...")`;
  a query only kept in a variable, or an `import type` of a client, is a
  `capability-candidate`. Pattern checks of the style: a controller that
  imports a repository with database access (skipping the service), a service
  that builds an HTTP response, a domain file that imports the ORM.
  REPORT.md gets a "What layers do" section, `wingbox advice` fix-capability
  and fix-pattern items, and `align --learn` accepts the current ones as
  `baseline-capability` lines of `.wingbox/baseline.sexp`. Findings are cached
  by content hash in `.wingbox/cache.capabilities.json`.
- Nobody edits the layer config by hand: the `learn` task tells the agent to
  ask the operator in its chat when the code does not decide a folder, and to
  pass the answer on (`:because "operator: ..."`). A person at a terminal
  (`wingbox learn`, or `wingbox align --learn`) gets a questionnaire: the style
  of each part among its candidates with scores and one-line descriptions,
  then one question per open folder; the answers are recorded like an
  agent's, and at the end `learn` offers to open `.wingbox/config.yaml` in
  `$VISUAL` or `$EDITOR`. `.wingbox/config.yaml` starts with a header of
  instructions (each key, the rule that decides a file's layer, how to move a
  folder, the baseline, what to run after a change, `align --learn` when
  unsure), rewritten on every write above the parts.
- The Flutter and Laravel layer conventions are label rules of the parts: a
  Dart file takes its layer from the Flutter folders (`features/*/presentation`
  ui, `domain`, `data` infra, `bloc`/`cubit`/`state` service, `lib/core`
  shared; `lib/` is the package source root), `integration_test/` holds tests,
  and the layered style places `livewire/` in ui and `listeners/`, `policies/`
  in service. A graph patched after a change carries the same architecture
  facts as a build.

- Python modules loaded at run time are in the graph. A literal name given to
  `importlib.import_module` or `__import__` is an import. A name built from a
  literal package path and one variable (`f".plugins.{name}"`,
  `"app.plugins." + name`) is a new fact `(registry-wires <loader> <module>
  :config <file>)` for each module of that package named by a string of a
  JSON, YAML or TOML file in the folder of the loader; `deps`, `rdeps` and
  `impact` follow it.
- A method called in a loop on an object that such a registry returns for the
  loop variable is `(calls <fn> <module>::<method> :lines N :registry)` to each
  wired module that defines it; when the call replaces its own argument
  (`data = stage.process(data)`) the modules of one configuration list get
  `(data-flow <a> <b> :pipeline)` in list order. `impact` does not follow a
  pipeline step backwards.
- `benchmarks/tocs`: Wingbox on the Theory of Code Space benchmark, scored with
  its own metric: Dep F1 0.953 on the paper's three medium codebases (0.802
  with 0.2.4), with the scripts, the maps, eight generated codebases and a plan
  for a model that starts from the Wingbox map.

### Fixes

- `wingbox advice`, `align`, `place`, `learn` and `compare` bring the graph up
  to date before they read it, as the query commands do, and print the same
  stale or unconfirmed fact when they cannot.
- A change to a JSON, YAML or TOML file beside a Python module loader (the
  names its registry loads) marks the graph out of date, through the edit hook
  and the comparison with the last build; the next query rebuilds the
  registry-wires edges.

- Advice the user accepted is never hidden as ignored; the decisions store
  logs one line per build plus changes of state instead of a line per open
  item; with a `.wingbox/config.yaml` the config wins over learned folders, and
  a learned folder removed from it is recorded.
- `align --learn` matches the part against the style library, keeps what the
  closest style places, and derives only the other folders, from file names,
  folder names in other styles and imports, each with its evidence; it never
  displaces the style's own folder of a layer (immich: `place` named `bin/`
  for a REST endpoint) and rewrites the config only when something changed.
  It counts breaks with the rule set of `boundaries`.
- A rebuild with nothing changed no longer resolves every import of a file
  once per imported module to tell type-only imports: univer (6,834 files)
  rebuilds from the cache in 46 s instead of about 70 s.
- A new package inside a monorepo becomes a part of the root
  `.wingbox/config.yaml` (language from its manifest, level from its folder);
  `init --yes` without `--style` takes the first choice (siblings, then the
  `$HOME` favourite).
- Output for people is text: `align`, `place` and the new-project `init` print
  S-expressions only with `--sexp`; `init --sexp` summarises targets and
  missing folders per part (`--details` for every folder); `advice` names the
  parts without advice in one line; one advice item per pair; outside git the
  build says once that `.gitignore` rules are not applied.
- `place` matches layer names, folder words and file patterns as well as the
  placement texts, works in parts with declared rules, gives 2-3 example
  files, says "not stated" when the style states no imports, and never turns
  the style's prose into a path.
- Domain building blocks are proposed only where the style has a domain layer
  and the part has domain code, as `value_objects` in Python, Rust, Go and Ruby.
- `tests-integration/`, `test-utils/` and `e2e-cases/` hold tests (a folder
  named by a test word first; a feature named after tests is not one); a
  `shared/` or `common/` folder inside a layer folder belongs to that layer.
- A pair imported for types only is listed once, as type-only, even when a
  data-flow edge joins the same files.
- The map no longer trembles: in the layers view the layout comes to rest
  before it is drawn, and choosing a file or changing the hops redraws without
  restarting it. The map opens on the most depended-on file whose 2-hop
  radius stays under 5 % of the modules, not on the one that paints half the
  graph.
- Layer rules match whole folder names: `src/class3.ts`, `utils3d/` and `s3/`
  are no longer infrastructure, a file named `types.ts` or `models.py` is no
  longer placed by its name.
- Folders below the routing root of a Next.js, Remix or SvelteKit frontend
  are URL segments: `app/mcp/gateways/gateway-tools.ts` is route code, not
  infrastructure.
- Folders of a plug-in package (`commands/`, `controllers/`, `menu/`) are no
  longer read as backend layers.
- A composition root (`main.ts`, `app.module.ts`, `Program.cs`) may import
  every layer; test helpers named `x.test-helpers.ts` are tests.
- A file that starts the application (it imports `NestFactory` from
  `@nestjs/core`, or a nest-commander `Command`/`CommandRunner`) and a file
  that nothing imports and that imports a composition root (a worker
  bootstrap) are composition roots, not sources of violations.
- `*.command.ts`, `*.query.ts`, `commands/` and `queries/` are CQRS commands
  and queries of the domain only when the part shows the CQRS overlay with its
  own evidence; a nest-commander CLI command is not domain code.
- A utility that imports infrastructure, or a frontend utility that imports a
  component or state, is a `violation-candidate` ("utility depends on
  infrastructure; move it to services or invert"), not a violation.
- In a route-tree frontend, a private folder at the top of the routing root
  (`app/_parts`) is shared by the routes (ui), and `*.query.ts`, `*.store.ts`,
  `*.context.tsx` and `use-*.ts` modules are state.

## 0.5.1 - 2026-11-17

### Changes

- Dart and Flutter: a `dart` adapter for `.dart` files with a tree-sitter Dart
  grammar. `package:<name>/...` imports of a package a `pubspec.yaml` in the
  repository declares resolve to its `lib/...` files, the nearest pubspec
  first, and relative imports resolve to the neighbouring file; `dart:` core
  libraries and third-party pub packages are external and create no edge.
  `import`, `export` and `part`/`part of` make the edges, and a missing
  generated part (`*.g.dart`, `*.freezed.dart`) is skipped instead of breaking
  the build. `.dart_tool/` and Flutter's `build/` output are not indexed.
  Flutter needs nothing special: widgets are ordinary classes. Runtime wiring
  (`get_it`, `go_router`, `retrofit`) is not visible to static analysis.
  The Dart grammar is an opt-in extra, `pipx install "wingbox[dart]"`;
  without it `.dart` files are skipped with a warning and the build goes on.
## 0.5.0 - 2026-11-17

### Changes

- Everything Wingbox writes into a project lives in `.wingbox/`: the graph
  `index.sexp`, `index.uses.sexp`, `index.uses-cache.json`, the build cache
  `cache.json`, `REPORT.md`, `map.html` and the freshness state; the build
  writes nothing else in the project. `.wingbox/.gitignore` keeps the
  generated files out of git, so files a person writes there later stay.
- `.wingbox/` marks the project: every command, the hooks and the MCP server
  find the nearest folder at or above the current one (or `--root`) that holds
  `.wingbox/index.sexp` and use it as the root. Without one a query says
  "no .wingbox/ in this folder or its parents: run wingbox build", names the
  paths it looked at and the `--map`/`--root` options, and exits 1.
- `wingbox open [PATH]` opens `.wingbox/map.html` in the browser, or prints its
  path when there is no browser.
- `wingbox update` names the files that earlier versions wrote outside
  `.wingbox/` (`.code_graph*`, `wingbox-out/`), one yellow warning line per
  project, and deletes nothing.
- `wingbox build --help` describes `wingbox build [PATH]` and its options.
  `--output` is gone: the graph is always `PATH/.wingbox/index.sexp`.

### Fixes

- A graph read from a folder it was not built from (a copied or moved
  `.wingbox/`, or a graph without its snapshot) was refreshed against that
  folder and replaced by an empty graph. The snapshot now records the folder
  the graph was built from; such a graph is read as it is, with
  `(wingbox-graph-unconfirmed :reason moved|no-snapshot :hint "rebuild: ...")`,
  and never refreshed. An automatic refresh that finds no source never
  replaces a graph that has modules.
- `wingbox search` with no match printed nothing; it prints
  `(result-count 0)` (and the count after matching facts).

## 0.4.0 - 2026-11-16

### Changes

- PHP 8 and Laravel: a `php` adapter for `.php` and `.blade.php` files with
  Composer PSR-4 classes, Blade, Livewire, Eloquent, events and jobs.
- The graph follows the code without `wingbox build`. In Claude Code a new
  PostToolUse hook on Edit and Write, `wingbox hook dirty`, marks the edited
  file in `.wingbox/graph-state.json` and returns; it never builds. Before the
  graph is read (the search hook, every MCP tool, the query commands) the
  files changed since the last build are brought in: the marked ones and
  those an editor, git or a shell command changed, found by comparing size and
  modification time with the snapshot the build wrote. Several edits are
  brought in by one refresh; an unchanged project costs one stat per source.
- A refresh patches the graph instead of building it: only the changed files
  are parsed again, and only the modules that read them, that resolved a name
  a created or deleted file could now answer, or that looked up a name the
  change added or removed are resolved again. The result is the graph a build
  publishes, byte for byte. A changed resolution config (package.json,
  tsconfig, pyproject.toml, .gitignore, ...), a language other than Python and
  TypeScript, or a change that reaches more than a quarter of the modules
  takes the incremental build instead.
- The search hook waits up to `WINGBOX_HOOK_WAIT` seconds (3) for a refresh;
  a longer one goes on in the background and publishes later. Meanwhile, after
  a failed refresh, or with `WINGBOX_AUTO_REFRESH=0`, the hook gives no context
  from the old graph but `(wingbox-graph-stale :reason ...)`, the MCP tools
  start their answer with that line and the query commands warn on stderr. A
  failed refresh is not repeated until something changes again.
- Builds resolve the modules in parallel (forked processes, same graph): on
  fastapi an incremental build after one edit takes 4.4 s instead of 11.2 s
  and a cold build 7.0 s instead of 24.1 s. The TypeScript config files are
  found in one walk of the tree instead of three.
- `wingbox install` adds the edit hook next to the search hook (timeout 10 s
  for the search hook, which may wait for a refresh), without duplicates on
  reinstall; `wingbox uninstall` removes both; the Claude Code plugin carries
  both.
- The first `wingbox usages` after a build in which a few files changed reads
  and resolves only those files: `.code_graph.uses-cache.json` keeps the use
  walk of each file by content hash and, while no definition, import or file
  was added or removed, its resolved uses. A later `usages` or `entity` reads
  from the graph only the facts that name the symbol asked, and every query
  stops reading the path section of the graph once it has it.

### Fixes

- `.wingbox/` and `wingbox-out/` are no longer walked by the build: a file
  Wingbox wrote there that the repository ignores (a `*.log`) changed the
  cache fingerprint and made the next build parse every file again.

## 0.3.0 - 2026-11-16

### Changes

- `wingbox usages <symbol>` lists every place that uses one function, class,
  type or constant, through re-exports and aliases (`usages pkg/__init__.py::X`
  answers for the definition the package re-exports): grouped by file, with
  `line:column` and the kind of use (import, re-export, call, type, extends,
  reference). The symbol is `Name`, `<file>::Name` or `<file>::Class.method`;
  a name defined in several files lists the candidates; for a method, calls
  whose receiver type is not in sight are listed as `unresolved-call`. At most
  200 uses unless `--all`; `--sexp` prints `usage` facts. The MCP server has a
  `usages` tool. On the 100 refactoring questions it names exactly the files
  of the language tooling in all 100; its positions equal the SCIP index for
  Python (234 of 234) and Rust (587 of 587); for TypeScript it lists 106
  `new C(...)` sites more, which SCIP files under the constructor.
- A method call is a use of `Class.method` when the receiver's class is in
  sight (`self`/`this`, an annotation, a constructor, a base class); every
  other method call is counted as unresolved, never guessed. On
  the five benchmark repositories 2 to 12 % of method calls resolve.
- `entity S --positions` lists every place that names S, with its file, line
  and column, as an editor's find usages does.
- The use index costs a build almost nothing: `wingbox build` reads only the
  uses that can name another file and writes them, with module-level
  variables, to `.code_graph.uses.sexp` next to the graph; the first `usages`
  or `entity --positions` fills in every use position (a parallel pass over
  the files, from the build cache) and keeps them until a file changes. A
  build without changed files does not resolve uses again. `.code_graph.sexp`
  stays within 3 % of 0.2.6 on immich-server and fastapi.
- `wingbox build` resolves each import of a file once (0.2.6 resolved the
  same import up to dozens of times): a build without changed files takes
  2.6 s instead of 4.7 s on immich-server and 6.5 s instead of 14.6 s on
  fastapi, a full build 4.8 s instead of 6.6 s and 11.7 s instead of 25.0 s.
- TypeScript use sites in code count: names, `ns.S` after `import * as ns`,
  qualified types, JSX elements, shorthand properties and `export { k }`; a
  parameter, local variable, catch parameter or type parameter of the same name
  shadows the import and is not a use; a `let`, `const` or class shadows it
  only in its block.
- TypeScript names in import and export statements resolve to the defining
  file through barrels: named, aliased and default imports, `export * from`,
  `export { X as Y } from`, `export * as ns from` and `export default`, with
  re-export cycles ending without an answer.
- The symbol table of a TypeScript file holds every top-level definition:
  abstract classes, generator functions, `var`, `let` without a value,
  `declare` statements and namespaces are entities, and enums list their
  members. Every Python, TypeScript and Rust file records its import bindings
  and what it re-exports (`pub use`, `export ... from`, package imports).
- `entity S` names every file that uses S in Python and Rust, as the language
  tooling's find usages does. Python: public module-level variables, constants
  and type aliases are entities, `m.S` after `from pkg import m` or `import
  pkg.m as m` is a use, and a name re-exported by a package `__init__.py` leads
  to its defining file. Rust: names reached through glob imports
  (`use a::b::*`), `pub use` chains and qualified paths (`screenshot::take()`
  after `use crate::screenshot`) are uses, and a `use` that names S is one. A
  parameter or local variable of the same name is not a use. On the refactoring
  benchmark the exact answers go from 8 to 40 of 40 (Rust) and from 7 to 20 of
  20 (Python), with no file outside the reference lists; TypeScript stays at 40
  of 40 and `impact` answers do not change.
- Plugin for Claude Code in `plugins/wingbox`, and this repository as its
  marketplace `aileron-tech`: `/plugin install wingbox --marketplace
  aileron-tech/wingbox`. The plugin bundles the `wingbox` skill, the MCP
  server `wingbox mcp` and the search hook; it runs the installed `wingbox`
  command or, without it, `uvx --from git+https://github.com/aileron-tech/wingbox@<commit> wingbox`,
  pinned to the full commit of the latest release tag, as the Claude plugin
  directory requires.
- `docs/store/` holds the listing, the privacy statement, the icon and
  screenshots for Anthropic's plugin directory.
- The same plugin for Codex: `codex plugin marketplace add
  aileron-tech/wingbox`, then `codex plugin add wingbox@aileron-tech`. It
  bundles the `wingbox` skill and the MCP server `wingbox mcp` from `PATH`.
- `tools/build_openai_plugin.py` builds the ZIP packages for the OpenAI plugin
  directory (ChatGPT and Codex): skills only, and skills with the local MCP
  server; `docs/store/` lists what that directory requires.
- The skill and the plugins say what they are for: a code graph for large
  repositories and monorepos, to answer what a change will break, who uses a
  file or function, where a feature is implemented and which files to read
  first, before a refactor or a cross-package change. New keywords: monorepo,
  large-codebase, codebase-map, call-graph, refactoring, code-navigation.
- `wingbox mcp`: every tool carries MCP annotations (a title, read-only for
  every tool but `build`, none destructive or open-world), as Anthropic's
  directory policy requires.
- SECURITY.md: report vulnerabilities privately through GitHub.
- `plugins/wingbox/evals/`: a `claude plugin eval` suite that checks the skill
  is chosen for those requests in a monorepo and not for unrelated ones.

### Fixes

- A TypeScript or JavaScript import with a Vite query or hash suffix
  (`import("./x.worker?worker&inline")`, `./annotate.ts?api-404-guard`,
  `@/notes?raw`) names the file without the suffix, as Vite resolves it; it
  was unresolved before. A `#` subpath import keeps its name.

## 0.2.7 - 2026-11-16

### Changes

- README: install with `pipx install wingbox && wingbox install`, then
  `/wingbox` in the agent; the supported assistants and MCP clients are
  named; the examples are rerun on 0.2.6.
- Map requests: an issue form asks for the map of a public repository; a
  workflow builds it with the released Wingbox and attaches REPORT.md and
  map.html to the run.

### Fixes

- `wingbox update` chooses pipx when `pipx list` exits 1 because some other
  pipx package has a missing interpreter; it chose pip before.

## 0.2.6 - 2026-11-16

### Changes

- `wingbox install` sets up Claude Code, Codex, Gemini CLI, Cursor, GitHub
  Copilot and the shared `~/.agents/skills` folder: the `wingbox` skill (so
  `/wingbox` exists), an always-on block between `<!-- wingbox:begin -->` and
  `<!-- wingbox:end -->` in CLAUDE.md, AGENTS.md and the other instruction
  files, and a Claude Code hook before Grep, Glob and Read that adds the
  matching modules and their dependents to the search. `--global` (default)
  or `--project`, `--assistant`, `--dry-run`; running it twice changes
  nothing. `wingbox uninstall` removes exactly what it added.
- `wingbox update` upgrades with the installer that installed Wingbox (pipx,
  uv or pip), `--check` shows the latest version, and the installed skills,
  blocks and hook are refreshed after the upgrade. Building and querying
  still never use the network.
- `/wingbox` in SKILL.md: no argument builds the graph when it is missing or
  stale and summarizes the report; `/wingbox <file>` runs `impact`;
  `/wingbox <command>` runs that command. The wheel now ships SKILL.md and
  `references/`.
- `wingbox mcp [--root DIR]` runs an MCP server on stdio, so that MCP
  clients (Claude Code, Codex, Cursor, Claude Desktop) call Wingbox as tools:
  `build`, `impact`, `analyze`, `quick`, `boundary`, `boundaries`, `layers`,
  `deps`, `rdeps`, `entity`, `find_function`, `find_type`, `find_callers`,
  `find_calls`, `find_callchain`, `diff_functions` and `query`. It speaks
  protocol revision 2026-07-28 and the `initialize` handshake of 2025-11-25
  and earlier, needs no new dependency and opens no network connection. The
  README section "Use from an MCP client" gives the configuration of each
  client.
- `python -m wingbox` runs the `wingbox` command.

## 0.2.5 - 2026-11-16

### Fixes

- Rust: after `use other_crate::{bridge}` a path `bridge::start::run()` names
  the module file in the other crate of the workspace; it used to stop at
  `bridge`.
- Rust: a file under `tests/<name>/`, `benches/<name>/` or `examples/<name>/`
  belongs to the crate rooted at that folder, as Cargo builds it, so
  `crate::harness` there names `tests/<name>/harness.rs`; it used to name a
  module of `src/` with the same name.

## 0.2.4 - 2026-11-16

### Changes

- `.mts` and `.cts` files are TypeScript modules; an import of `./x.mjs` or
  `./x.cjs` names `x.mts` or `x.cts` when it exists, as TypeScript reads it.
- A test that mocks a module (`vi.mock`, `vi.doMock`, `vi.importActual`,
  `vi.importMock`, `jest.mock`, `jest.doMock`, `jest.requireActual`,
  `jest.requireMock` with a literal) depends on it. On one TypeScript monorepo
  this adds 1,105 dependencies that a change to the mocked module breaks.
- README: the header opens with "AI agents should know what their changes will
  break before editing code."; the website wingbox.dev; FAQ entries on price,
  offline use, embeddings, RAG, editor find usages, Sourcegraph and ctags, and
  keeping the graph current.

### Fixes

- A file that git tracks stays in the graph even when an ignore pattern
  matches its folder, as git itself reads the rules: a pattern `core` meant for
  core dumps hid tracked `core/` source folders.
- `import(/* comment */ "./x")` and `require` with a comment before the literal
  keep their target.

## 0.2.3 - 2026-11-16

### Changes

- The package page on PyPI shows the current README.
- The README states what makes Wingbox usable inside a security perimeter:
  no network, no model, no telemetry, code read and never run, an offline
  install. Two tests keep it true (REQ-SEC-001, REQ-SEC-002).
- `benchmarks/graphify` is back, next to `benchmarks/impact`: Wingbox and
  Graphify measured on the same public repositories, with the scripts and the
  result files. The README has a section and a FAQ entry on the two tools.
- Added `benchmarks/impact`: Wingbox measured on public repositories. One
  hundred impact questions drawn with a fixed seed and checked against
  reference lists from independent resolvers, a coding agent with and without
  Wingbox, every edge against the import statements of the code, build time;
  with confidence intervals, the scripts and every result file.
  `Dockerfile.benchmark` reproduces the tests that need no model in a clean
  container and compares the results with the committed ones. The README shows
  the summary.

### Fixes

- `boundaries-summary` counts pairs of files, as the report does; a pair
  printed once per relation (imports and data-flow) used to count twice.
- The graph of a repository without TypeScript builds as fast as in 0.2.0
  again: 0.2.1 resolved every import a second time while looking for imports
  of types only (a Python library of 1,227 files: 9.4 s, now 7.7 s on the same
  machine; 0.2.0: 7.4 s).

## 0.2.2 - 2026-11-16

### Fixes

- Layers: a Python `__init__.py` is its package. It takes the layer of its
  folder or, when no folder rule applies, the layer where most modules of the
  package are; the names it re-exports no longer decide its layer.

## 0.2.1 - 2026-11-16

### Changes

- `analyze` and `impact` print the complete counts and list at most 200
  dependent files, nearest first; `--all` lists every file. A line
  `analyze-more`, `key-entity-more`, `impact-more` or `read-first-more` gives
  the number left out. On a file with 650 direct dependents the `analyze`
  answer went from about 55,000 tokens to about 8,800.
- `analyze` names each dependent file once, on its `analyze-dependent` line:
  the `rdep` lines are gone and `boundary-file` names the module and the files
  it imports. Paths in `analyze` are relative to the repository root.
- `boundaries` prints a TypeScript import of types only across layers as
  `(violation-type-only ...)` and counts it as `:type-only` in
  `boundaries-summary`, not as a violation; the report lists these imports in
  their own table. The builder marks such an import edge `:type-only`.
- The builder records `import.meta.resolve('<literal>')` as a dependency (a
  worker file loaded at run time).

### Fixes

- Layers: a migration or seed file is infrastructure wherever its folder lies;
  under a `schema` folder it used to be domain, which reported migrations that
  import a repository as "domain imports infrastructure".
- Layers: a file whose name or folder only begins with the letters "test"
  (`test.email.tsx`, `testclient.py`, `testimonials/`) is no longer a test.
- Layers: a file named like a layer folder (`types.ts`, `models.py`) and the
  index file of such a folder (`controllers/index.ts`) belong to that layer.
- The purpose of a file is inferred from its path inside the repository;
  folders above the repository root (`/app/`, `/test/`) no longer change it.
- A class whose name ends in `API` (`FastAPI`, `OpenAPI`) no longer makes its
  module an "API client" in the infrastructure layer.
- `wingbox <command> | head` ends quietly when the reader closes the pipe.
- The map keeps the selected file in view when the window is resized right
  after it opens with `#select=<file>`; the names of the layers no longer
  overlap in the layers view; the layers keep turning while a file is selected.
- The report says that a boundary violation is listed when both layers come
  from folders or from the file type.
- The report names the count of definitions "Functions, constants, classes and
  types": module-level constants are recorded and counted with the functions.

## 0.2.0 - 2026-11-15

### Changes

- `wingbox build` now also writes `wingbox-out/REPORT.md` (key findings, the
  most depended-on files, layers, boundary violations, entry points) and
  `wingbox-out/map.html`, an interactive map of the code graph in one file
  that works offline: click a file to see its blast radius on rings, switch to
  the 3D layers view, link to a file with `map.html#select=<file>`.
- Added a `Dockerfile`: `docker run --rm --network none -v "$PWD":/repo wingbox`
  builds the graph, the report and the map with nothing installed on the
  machine and no network access.
- `boundaries` prints `(violation ...)` only when both layers come from the
  folders or the file type; a pair that rests on a layer guessed from the role
  of a file is printed as `(violation-candidate ...)`, and the summary gives
  both counts.
- Layers: a `.tsx` component is UI whatever its folder is called; folders named
  `tools` or `cli` are no longer classified as configuration; test files are
  recognised by folder (`tests/`, `__tests__/`, `e2e/`) and by name (`*.test`,
  `*.spec`, `*_test`, `test_*`) before any other rule.
- Files that would share a module ID (`x.test.ts` next to `x.test.tsx`,
  `tool.py` next to `tool.ts`) are all kept: the first keeps the plain ID, the
  others are named by their full path. A repository with such files used to
  stop the build with "module ID collision".
- A folder named `bin` is skipped only as a .NET build output (next to `.cs`
  or project files); elsewhere its sources are part of the graph.
- `impact` accepts the path of an index file (`src/lib/index.ts`) and of a
  file named by its full path.
- JavaScript files (`.js`, `.jsx`, `.mjs`, `.cjs`) are modules: an import of a
  real JavaScript file and the dependencies between JavaScript files are part
  of the graph. `./x.js` still names `x.ts` when it exists. Minified files
  (`*.min.js`, or any line longer than 5,000 characters) and bundles (`bundle`
  in the file name) are left out. A project with JavaScript files now needs
  the TypeScript parser packages.
- TypeScript: an import that names a package of the repository (a workspace
  member such as `@scope/shared`) resolves to its source through `exports`,
  `source`, `types`, `module` or `main`, subpaths included.
- TypeScript: an import that goes through the `outDir` of a tsconfig
  (`../dist/index.js` in a test or a script) resolves to the source file under
  `rootDir`.
- Added the `wingbox` command and the Python package metadata (version 0.1.1):
  `wingbox build [PATH]` builds the code graph and every query command runs as
  `wingbox <command>`, for example `wingbox impact path/to/file.py`. Install it
  with `pipx install wingbox` ([PyPI](https://pypi.org/project/wingbox/)).

### Fixes

- A class is no longer linked to a file that merely defines a class of the
  same name. A Python base written as `module.Class` (`nn.Module` after
  `import torch.nn as nn`) follows the module that the import binds, so a
  module of an external package gives no dependency; a base that the file does
  not import is matched by name only inside the same language, and in Python,
  TypeScript and JavaScript only through a wildcard import. Before, `impact`
  could list files that do not depend on the changed file.
- `impact`, `deps` and `rdeps` name the relation as `imports` whenever the file
  imports the target; `data-flow` or `calls-api` was printed instead when such
  an edge existed next to the import, which made a plain import look like
  something else.
- Rust: in `impl From<eyre::Report> for AppError` the implemented trait is
  `From` and in `impl Runner for Wrapper<T>` the type is `Wrapper`; a generic
  argument was taken as the trait or the type.
- The graph reader hung, growing memory without bound, when an entity listed a
  quoted bracket, for example a constant `[")", "]", "}"]`. Every query and
  the report step hung on such a repository.
- Rust: `use super::*` inside an inline module (`mod tests { ... }`) created a
  false dependency on the parent module.
- Rust: dependencies written as a path without `use` (`crate::a::b::f()`,
  `super::x::Y`), through an imported module (`x86::linker::link()`), through
  the crate name in a binary, inside macro arguments, through a relative path
  that starts at a child module, and on an item the parent only re-exports are
  now part of the graph. `use crate::name` of a `#[macro_export]` macro points
  at the file that defines the macro, not at the crate root.
- Folder rules such as `/tests/` did not match a top-level folder, so test
  files were classified by a guess from the file role.
- Go, Java and C# files were reported without their extension.
- A Go build scanned every file of the project for every import; a 1,200-file
  project now builds in a third of the time. The report step of a 7,800-module
  project takes 5 seconds.
- TypeScript: a tsconfig with a URL in a string value (`"$schema":
  "https://..."`), a block comment or a byte order mark was skipped as
  unreadable, and every import through its `paths` aliases was lost.
- TypeScript: `paths` and `baseUrl` inherited through `extends` were not read,
  so a project whose tsconfig only extends a base file had no aliases.

## 0.1.1 - 2026-11-15

### Changes

- Added the `impact <file|id> [depth] [--sexp]` command: the modules that
  depend on a file, grouped by shortest distance, and the files to read first.
  It accepts an ordinary file path, prints readable text by default and
  compact S-expression facts with `--sexp`.
- Renamed the project and the skill from `codespaces` to `wingbox`; the
  repository is now `aileron-tech/wingbox`.
- Replaced the term "belief map" with "code graph" in the documentation, the
  scripts and the generated files: `scripts/build_belief_map.py` is now
  `scripts/build_code_graph.py`, `scripts/belief_search.py` is now
  `scripts/code_graph_search.py`, `.belief_map.sexp` is now `.code_graph.sexp`
  and `.belief_map_cache.json` is now `.code_graph_cache.json`. The header of
  the generated S-expression file is now `code-graph`.
- Existing maps are not read under the old names: rebuild the code graph once,
  delete the old generated files, and update agent instructions and ignore
  rules that mention the old script or file names.

## 2026-08-11

### Changes

- Added dependency-free Free Pascal and Lazarus indexing for units, programs,
  imports, includes, types, inheritance, methods, and routines.
- Applied repository Git ignore rules to source, language configuration,
  package, and LSP project discovery, including tracked paths matched by
  `.gitignore`.

### Fixes

- Resolved Rust module IDs to physical `.rs` or nested `mod.rs` files in
  source-aware search output.
- Preserved conservative dependency edges from bare `use super::*` imports to
  their parent module, including crate-root parents.
- Rejected path-shaped `--exclude-dir` values consistently across POSIX and
  Windows syntax.
- Applied caller exclusions to TypeScript alias discovery instead of retaining
  a separate internal directory list.

## 2026-08-09

### Changes

- Added repeatable `--exclude-dir NAME` options so callers can omit tracked
  source snapshots, generated fixtures, and similar non-product trees from
  source, package, project, and LSP discovery.
- Included caller-provided directory exclusions in cache provenance so changing
  the active exclusion set forces a safe rebuild.

## 2026-08-08

### Changes

- Corrected the README and GitHub description to present AI coding agents as
  the audience rather than another supported language.
- Added dynamically loaded Ruby and Ruby on Rails indexing for `.rb` and
  `.rake` files, including classes, modules, instance and singleton methods,
  inheritance, explicit requires, and Ruby LSP metadata.
- Added conservative Rails/Zeitwerk dependency resolution for application and
  custom autoload roots, project acronyms, concerns, associations, callbacks,
  jobs, mailers, and impacted specs.
- Added official Tree-sitter support for C#, Java, and Go source discovery,
  entity extraction, local dependency resolution, project recognition, and LSP
  metadata.
- Made Tree-sitter grammars optional and language-scoped: the builder loads only
  detected languages and reports exact per-language install commands when a
  required parser is absent or incompatible.
- Added a language-neutral adapter contract so source discovery, parsing,
  module identity, dependency resolution, output, and LSP metadata can be
  extended without adding language branches to graph orchestration.
- Added Rust source discovery, Tree-sitter entity extraction, Cargo workspace
  import resolution, and rust-analyzer metadata.
- Reworked the README around natural code-search questions, a shorter quick
  start, supported languages, common impact-analysis workflows, and honest
  accuracy limits.
- Expanded the skill and repository descriptions with architecture-aware code
  search, dependency graph, Rust, monorepo, and AI coding-agent discovery terms.

### Fixes

- Prevented ambiguous or reopened Ruby constants, polymorphic associations, and
  RSpec `include` matchers from creating guessed Rails dependency edges.
- Resolved inherited Ruby constants and `through` associations with explicit
  `source` models, and preserved Rails relation types in map facts.
- Excluded standard .NET `bin`/`obj` outputs and Go `vendor` dependencies from
  source discovery.
- Prevented unused C# namespace and Java wildcard imports from creating guessed
  dependency edges based only on package cardinality.
- Prevented unused C#, Java, Go, Rust, or TypeScript grammars from blocking
  Python-only and other single-language map builds.
- Rust files, local Cargo modules, workspace crate dependencies, and imported
  Rust entity references are now represented in generated architecture maps.

## 2026-07-30

### Changes

- Required an explicit absolute project root for map builds and added custom
  output/map-root query options.
- Added pinned Python dependencies and documented the supported TypeScript and
  safe search-pattern contracts.

### Fixes

- Made `CALLS_API` resolution deterministic by following explicit imports and
  failing closed on ambiguous unqualified providers.
- Rejected colliding module IDs before publication instead of silently
  discarding source files.
- Versioned incremental caches by schema and builder provenance.
- Published cache and map files atomically under a per-project writer lock.
- Rejected malformed maps, unsafe search patterns, and invalid query depths
  with structured diagnostics.
- Preserved source paths containing spaces and recognized TypeScript
  `import = require()` plus literal template imports.

## 2026-07-14

### Changes

- Added dependency discovery for TypeScript re-exports, literal dynamic imports, and literal CommonJS requires.
- Added project-scoped TypeScript path aliases and exact package self-import resolution.

### Fixes

- Fixed missing dependency edges from TSX files.
- Fixed path aliases with the same name resolving through an unrelated monorepo package.
- Fixed exact path aliases and package self-imports being omitted from architecture maps.
