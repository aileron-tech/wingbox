# Changelog

## Unreleased

### Changes

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
