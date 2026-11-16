# Completion Log

## 2026-11-16 - README: the earlier repository is named without a link

### Changes
- `README.md`: the timeline item "published as the codespaces skill" no longer links to the earlier repository.

### Motivation
- The earlier repository is archived and is not maintained; the project lives here and on wingbox.dev.

## 2026-11-16 - version 0.2.5: Rust paths through other crates and test crates

### Changes
- `pyproject.toml`: version 0.2.5.
- `scripts/lang/rust/parser.py` (`_collect_inline_paths`): a name imported from a crate other than std, core and alloc expands an inline path to its full path; the resolver finds the file when the crate belongs to the workspace and nothing otherwise.
- `scripts/lang/rust/imports.py` (`_separate_crate_directory`): `tests/<name>/`, `benches/<name>/` and `examples/<name>/` are crate roots for `crate::` paths, with `<name>/main.rs` as the fallback entry.
- Tests: REQ-RUST-013, REQ-RUST-014.
- Verified against the independent Rust resolver of `benchmarks/impact` with its acceptance rules: a compiler of 351 files stays exact (1,462 dependencies); a browser CLI keeps its two known edges to a trait re-exported by a parent module; a platform monorepo of 145 Rust files goes from 14 missing dependencies and one wrong edge (a test file linked to `src/harness.rs`) to none missing; its 29 edges that the resolver lacks are uses of other workspace crates, each named in the source.

### Motivation
- The check of a public monorepo before publishing its report found Rust dependencies through `use other_crate::module` and `crate::` paths in integration test folders missing, and one edge to a module of the main crate that the test did not use.
## 2026-11-16 - where Wingbox comes from

### Changes
- `README.md`: new section "Where Wingbox comes from" before "Project resources": the paper Theory of Code Space (arXiv:2603.00601) with its code and the author's write-up, the parent paper Theory of Space (arXiv:2602.07055), three dated steps (belief map script in March 2026, `codespaces` in June, Wingbox in November) and a link to wingbox.dev/story.

### Motivation
- The project started from Theory of Code Space; the README did not say so or credit it. The paper's author is named by the maintainer's decision, as an exception to the rule of `AGENTS.md` against names of people: a citation of a public paper, not a client or a colleague.

### Verification
- arXiv pages, the post and the toolkit repository read on 2026-11-16; dates of the steps taken from the git history and the first specification of the belief map (2026-03-15). README stays ASCII.

## 2026-11-16 - version 0.2.4: modules and dependencies a check against two monorepos found missing

### Changes
- `pyproject.toml`: version 0.2.4.
- `scripts/lang/typescript/__init__.py`, `imports.py`: `.mts` and `.cts` are TypeScript sources (declarations `.d.mts`, `.d.cts` are not); module IDs keep the extension; `./x.mjs` and `./x.cjs` name `x.mts` and `x.cts` first.
- `scripts/lang/typescript/parser.py` (`_MODULE_CALLS`): module mocks of Vitest and Jest and `require.resolve` with a literal are dependencies; a comment before the literal argument of a module call is skipped.
- `scripts/git_ignore.py` (`_tracked_paths`, `_parent_folders`): tracked files and the folders on the way to them are never excluded by ignore rules.
- `README.md`: the FAQ entry that compared Wingbox with another tool is removed; that tool is named only in the benchmark section.
- Tests: REQ-TS-JS-006, REQ-TS-RT-002, REQ-CS-059; REQ-CS-036 and REQ-CS-037 now use untracked snapshots.
- Verified against the independent TypeScript resolver of `benchmarks/impact`: on a monorepo of 6,838 source files the graph equals the resolver (6,834 modules plus 4 declaration files, 31,533 dependencies); on a monorepo of 7,657 TypeScript files, 38,745 dependencies equal, and 17 tracked files under a folder named `core` are back.

### Motivation
- Building reports for two public monorepos and checking every number against an independent resolver showed ten `.mts` scripts missing, 1,105 dependencies of tests on mocked modules missing, four dynamic imports with a comment before the path losing their target, and 17 tracked source files hidden by an ignore pattern meant for core dumps.

## 2026-11-16 - FAQ for people comparing code-graph tools

### Changes
- `README.md`: FAQ entries "Is Wingbox free?", "Can I run it fully offline?", "Does it use embeddings or a vector database?", "How is Wingbox different from RAG or vector search?", "My editor already has go to definition and find usages. Why a graph?", "How is it different from Sourcegraph or ctags?", "Does the graph stay current as I change code?".
- `README.md`: the header opens with the maintainer's line "AI agents should know what their changes will break before editing code."
- `README.md`: the website wingbox.dev in the header block and under "Project resources".
- Checked before writing: a second `wingbox build` on a library of 1,227 files parses 0 files, and after one file changes it parses 1 (1,226 from the cache).

### Motivation
- These are the questions people ask when they compare code-graph tools; each answer states only what the code does.
## 2026-11-16 - benchmark results for version 0.2.3

### Changes
- `benchmarks/impact/run.sh`: the default version under test is 0.2.3.
- `benchmarks/impact/results/`: `versions.json`, `docker_run.log` (container run with 0.2.3) and `build_times.json` (hosted runner, run 43855169397); `benchmarks/impact/README.md` regenerated by `make_report.py`.

### Verification
- Container run with `WINGBOX_SPEC=wingbox==0.2.3`: REPRODUCED, the 105 result files are equal to the committed ones, so the answers of the command test and the edge test did not change with the version.
- Build time, median of 3 on a hosted runner: fastapi 2.05 s (0.2.2: 3.23 s), immich-server 1.34 s (1.84 s).
- `tests/test_benchmark_report.py`, `tests/test_local_only.py`: 10 passed.

## 2026-11-16 - links to the site wingbox.dev

### Changes
- `README.md`: the line under "See it in action" leads to wingbox.dev (benchmarks with charts, a sample report, the live map), which is live on its own domain.
- `pyproject.toml`: the Homepage link of the package is wingbox.dev; the package page shows it from the next release.

### Verification
- https://wingbox.dev answers 200 over HTTPS; no code changed.

## 2026-11-16 - version 0.2.3: pair counts in the boundaries summary, build time

### Changes
- `pyproject.toml`: version 0.2.3; the package page takes the README of this version.
- `scripts/code_graph_search.py` (`cmd_boundaries`): the summary counts pairs of files per kind; the lines stay one per relation.
- `scripts/build_code_graph.py` (`_imports_types_only`): returns at once for a file without import forms, so only TypeScript files pay for the check.
- `benchmarks/graphify/`: restored as it was before it was replaced by `benchmarks/impact` (results of Wingbox 0.2.0 and Graphify 0.9.110); `README.md`: section "Wingbox and Graphify on the same tests" and a FAQ entry; `pyproject.toml`: keywords `graphify`, `graphify-alternative`, `knowledge-graph`. People who look for one tool should find the comparison with the other.
- `README.md`: section "Runs fully locally, inside your security perimeter"; `tests/test_local_only.py`: the package imports no network module (REQ-SEC-001) and the map page loads no outside resource (REQ-SEC-002); `pyproject.toml`: keywords `offline`, `local-first`, `air-gapped`.
- Tests: REQ-CS-058, REQ-BENCH-008, REQ-SEC-001, REQ-SEC-002; REQ-BENCH-002 covers both benchmark folders.
- Measured on a Python library of 1,227 files, same machine, full build: 0.2.0 7.4 to 7.9 s, 0.2.2 9.4 to 9.5 s, this version 7.7 to 8.5 s.

### Motivation
- The summary of `boundaries` said 4 candidates where the report said 2 for the same repository. A timing on a hosted runner showed the build of a Python library slower in 0.2.2 than in 0.2.0.
## 2026-11-16 - README demo pictures rebuilt from 0.2.2

### Changes
- `docs/assets/demo-impact.svg`, `demo-layers.webp`, `map-layers-hub.webp`: generated again from Wingbox 0.2.2 on the same commit of the example repository. The stack of layers in the animation has six planes (the index file of the controllers folder is API, `types.ts` is shared), the recording and the screenshot show the layer sizes of 0.2.2. `README.md` names the version.

### Motivation
- The pictures were made with 0.2.0 and an unreleased build; a reader who runs the released tool on the example should see what the pictures show.

## 2026-11-16 - a Python package init takes the layer of its package

### Changes
- `scripts/code_graph_search.py` (`_package_layer`, `_graph_layer`): the layer of a Python `__init__.py` comes from the folder rules, or, when none applies, from the layer where most modules of the package are (its own modules, or all modules below when it holds only sub-packages; a tie gives `other`). The purpose guessed from the names it re-exports is not used. The result is kept per graph in `CodeGraph.package_layers`.
- `pyproject.toml`: version 0.2.2.
- Test: REQ-OUT-016.
- Verified on a Python library of 1,223 files: of 190 `__init__.py` files, 89 were placed in the configuration layer by a guess; now each stands with its package (81 unplaced like their packages, 92 tests, 9 api, 4 domain, 3 shared, 1 config). Import edges unchanged (1,818).

### Motivation
- In Python `__init__.py` is the package itself. Its layer was guessed from what it re-exports, so the root package of a web framework was placed in the api layer and most package files in configuration.

## 2026-11-16 - version 0.2.1: answer size, type-only imports, layer rules, map view

### Changes
- `pyproject.toml`: version 0.2.1.
- `scripts/code_graph_search.py` (`cmd_analyze`, `cmd_impact`, `cmd_quick`, `main`): the lists stop at 200 dependent files (`_ANSWER_FILE_LIMIT`), nearest first, and a `*-more` line gives the number left out; `--all` lists every file; the counts are always complete. `analyze` names a dependent once (no `rdep` lines, `boundary-file` holds the module and its imports) and prints paths relative to the repository root.
- `scripts/lang/typescript/parser.py` (`_ts_import_forms`), `scripts/lang/interface.py` (`FileResult.import_forms`), `scripts/build_code_graph.py` (`_imports_types_only`): an import edge is marked `:type-only` when every import of the target is `import type` or binds only names that the target defines as a type or an interface. `cmd_boundaries` prints such a crossing as `violation-type-only`; `src/wingbox/overview.py` lists it in its own table of the report and keeps it out of the violation count.
- `scripts/lang/typescript/parser.py`: `import.meta.resolve('<literal>')` is a dependency.
- `scripts/code_graph_search.py` (`_classify_layer_fallback`, `_classify_layer_by_path`): migrations and seeds are infrastructure before the schema rule; a file named like a layer folder and the index file of such a folder take that layer.
- `scripts/lang/purpose.py`: `_is_test_file` replaces the rule that matched any path containing `/test`; the purpose is inferred from the path inside the repository (`scan_root`, set by `parse_file`); the suffix rule that turned a class named `...API` into an "API client" is removed. `CACHE_SCHEMA_VERSION` is 2, so old caches are rebuilt.
- `src/wingbox/cli.py`: a closed output pipe ends the command with status 0.
- `pyproject.toml`: the paths of the source archive are anchored at the root, so the README of the benchmark folder is no longer packed as a second `README.md`.
- `src/wingbox/overview.py`: the summary row is named "Functions, constants, classes and types".
- `src/wingbox/assets/map_template.html`: a resize before the first interaction keeps the framing of the selected file instead of fitting the whole graph while the blast radius is still unfolding; the layer names use the same corner on every plane and are kept 32 px apart; the layers turn on slow frames too, so they keep turning while a file is selected; the note under the layer matrix and the violations list name the imports of types only.
- Tests: REQ-CS-054 to REQ-CS-057, REQ-OUT-013 to REQ-OUT-015, REQ-LANG-005 to REQ-LANG-007, REQ-TS-RT-001, REQ-CLI-007.
- Verified on open-source repositories: a TypeScript server of 707 modules keeps its 3,795 import edges, its boundary violations go from 8 to 0 with 6 type-only imports listed apart (every marked edge checked against the source text), its domain layer from 237 to 134 modules; on a Python library of 1,223 files the import edges are unchanged (1,818), the `analyze` answer for the file with 650 direct dependents goes from about 55,000 to about 8,800 tokens, and the questions of the benchmark keep their exact answers.

### Motivation
- A check of version 0.2.0 against the source of a TypeScript server showed migrations counted as domain, an email template counted as a test, and boundary violations that were imports of a type only. The benchmark showed that the answer for a hub file had no upper bound and named every dependent three times with an absolute path.

## 2026-11-16 - benchmark of impact questions, reproducible in a container

### Changes
- `benchmarks/impact/` (new): `run.sh` installs Wingbox from PyPI, fetches eight public repositories at pinned commits, builds the graphs and computes reference edges with resolvers that share no code with Wingbox (`tools/py_edges.py` on Python's `ast`, checked against grimp; `tools/ts_resolve.py`; `tools/rust_resolve.py`). `edge_test.py` compares every module dependency of the graph with them. `impact100.py` draws 20 imported files per repository with a fixed seed, writes the reference list of each (`results/gold/`), runs `wingbox impact <file> 2 --all` on the 100 questions and, as a separate paid step, lets a coding agent answer 55 of them with and without Wingbox. `stats.py` gives the Wilson, bootstrap and sign-test figures; `make_report.py` writes `README.md` from `results/`; `verify.py` compares a run with the committed results.
- `Dockerfile.benchmark` (new, at the root): the same run in a clean container; it ends with `REPRODUCED` or lists the result files that differ. `benchmarks/impact/results/docker_run.log` is the output of that run for the committed results. Workflow `benchmark-docker` runs the container on a hosted runner; workflow `benchmark-build-time` times `wingbox build` there.
- What the reference accepts without requiring it is written down in one place (`impact100.reference`): a Python package `__init__.py` on the way to the import, a TypeScript import of the compiled output of a file (mapped through `outDir` and `rootDir`), a Rust path that passes through a module.
- `README.md`: section "Measured: 100 questions, with and without Wingbox".
- Tests REQ-BENCH-001 to REQ-BENCH-007.

### Motivation
- A handful of questions gives wide intervals: 5 of 5 is compatible with a true rate of 57 %. One hundred questions drawn at random, intervals on every figure and a container that reproduces the run are what a reader needs to trust the numbers.
- The baseline is the agent's own search: it is what every reader already has.

## 2026-11-16 - README: "See it in action" with the layers view

### Changes
- `README.md`: new section "See it in action" before "What can Wingbox answer?": an animated picture (terminal with `wingbox impact` and a 3D stack of architecture layers), the real output of the command, a recording of `map.html` turning in the layers view and one screenshot of it.
- `docs/assets/`: `demo-impact.svg` (SMIL animation, its static state is the last frame), `demo-layers.webp`, `map-layers-hub.webp`.
- The example is the server of the open-source project immich at commit `e5d6eb7`. Every dependency in it was compared with an import resolver written separately from Wingbox: 3,795 on both sides, none extra and none missing; the two radii shown (10 / 30 and 65 / 281) were recounted from that resolver.

### Motivation
- The README described the commands but did not show the tool working. The pictures show the two things people ask about first: what one command answers, and what the map looks like.
## 2026-11-15 - version 0.2.0 and base classes matched by name

### Changes
- `pyproject.toml`: version 0.2.0 (report, map, Docker image, JavaScript modules and the resolution fixes of this date).
- `scripts/lang/python/parser.py`: a base class keeps its written form (`nn.Module`); for a base reached through a name that an import binds, the parser records the class as a name taken from that module, so the provider is resolved by the import and an external module gives no edge.
- `scripts/build_code_graph.py` (`_resolve_provider`): a base that the file does not import is matched by name only among providers of the same language; in Python and TypeScript/JavaScript the provider must also come from a wildcard import of the file.
- `scripts/lang/rust/parser.py` (`_type_name`): a generic type gives its own name, not the last type argument.
- `scripts/code_graph_search.py` (`_collect_dep_mods`, `_collect_rdep_mods`): a pair of files that has an import is reported as `imports`; another relation is named only when there is no import. An agent that was asked about imports and saw `data-flow` checked the whole list again with a text search.
- Tests: REQ-CS-051, REQ-CS-052, REQ-CS-053, REQ-RUST-012.
- Verified on six open-source repositories by listing every `calls-api` edge that has no `imports` edge between the same two files and reading the source: the wrong edges found (2 Python, 1 TypeScript to Go, Rust generic arguments read as traits) are gone, the 2 edges that remain without an import are a trait reached through a re-exporting module, and the `imports` edges are unchanged.

### Motivation
- A check on three small Python repositories placed in one folder showed two `calls-api` edges from files that use `torch.nn.Module` to a local file that defines its own `Module`; `impact` on that file listed 8 files where none depends on it. The same audit found a TypeScript `extends Error` linked to a Go type named `Error` and Rust generic arguments read as traits.

## 2026-11-15 - TypeScript and JavaScript import resolution

### Changes
- `scripts/lang/typescript/tsconfig.py` (new) reads tsconfig files: comments and trailing commas are removed outside string literals, a byte order mark is accepted, and `extends` (a relative file, a chain or a list) is applied as TypeScript applies it, the nearest `paths`, `baseUrl`, `outDir` and `rootDir` winning.
- `scripts/lang/typescript/imports.py`: an import that names a package of the repository resolves through `exports`, `source`, `types`, `module`, `main`, then `src/index` or `index`; a path inside a tsconfig `outDir` resolves to the file under `rootDir`; `./x.js` names `x.ts` first and the real `x.js` otherwise, `.mjs` and `.cjs` by their own extension.
- `scripts/lang/typescript/javascript.py` (new) and `discover_files`: `.js`, `.jsx`, `.mjs` and `.cjs` files are modules with the language code `js`, except minified files and bundles.
- Tests in `tests/test_typescript_language.py`: REQ-TS-ALIAS-001 to 006, REQ-TS-WS-001 to 008, REQ-TS-JS-001 to 005, REQ-TS-OUT-001 to 004.
- Verified against an independent import resolver on two open-source repositories: recall of the TypeScript and JavaScript dependencies went from 0.971 to 0.999 and from 0.784 to 0.997, precision stayed 1.000; on a monorepo of 7,900 modules the dependencies between modules went from 19,734 to 37,893 and none was removed.

### Motivation
- A comparison of the graph with the source showed where TypeScript dependencies were lost: a tsconfig that named its schema by URL was skipped, inherited `paths` were ignored, packages of the same repository imported by name stayed external, JavaScript files were not modules, and imports through a build output folder named files that are not in the repository.

## 2026-11-15 - maintainer instructions

### Changes
- Added `AGENTS.md` (with the `CLAUDE.md` symlink): scope, layout, rules for changes, the test command, the release steps and the handling of the upload token.

### Motivation
- The release and contribution procedure existed only in the maintainer's notes outside the repository.

## 2026-11-15 - published on PyPI

### Changes
- `wingbox` 0.1.1 (sdist and wheel built from `main` at the v0.1.1 tag) is on PyPI: https://pypi.org/project/wingbox/. README and CHANGELOG now give `pipx install wingbox` and `uvx wingbox`.
- Verified: `uvx --from wingbox==0.1.1 wingbox --version`, `build .` and `impact` in a clean environment.

### Motivation
- One-line install without the repository URL.

## 2026-11-15 - wingbox command and package

### Changes
- `pyproject.toml` (hatchling, version 0.1.1, Python 3.11+ because the builder imports `tomllib`): package `wingbox` with the console command `wingbox`; the `scripts/` directory is bundled unchanged as `wingbox/_scripts`, so the skill layout and the script paths stay as they are.
- `src/wingbox/cli.py`: `wingbox build [PATH]` calls `build_code_graph.main` with an absolute `--root` (default: the current directory); `wingbox infra` calls the topology builder; any other command goes to `code_graph_search.main`. Relative `--root`, `--map` and `--output` values are resolved against the current directory.
- The package depends on the parsers of all supported languages at the versions in `requirements/`.
- Tests: REQ-CLI-001 to REQ-CLI-006 in `tests/test_cli.py`. Verified: wheel built with `uv build`, installed in a clean environment with `uvx --from <wheel>` on Python 3.11 and 3.13, `wingbox build .` and `wingbox impact` run on a small project; the unit suite (106 tests) passes on Python 3.11, 3.12 and 3.13.
- README: install and quick start with the command; the image uses an absolute URL so it also renders outside GitHub.

### Motivation
- Using the tool from a terminal required cloning the repository and calling the scripts by path with absolute options.

## 2026-11-15 - impact command

### Changes
- `scripts/code_graph_search.py`: new `impact <file|id> [depth] [--sexp]` command. It resolves an ordinary file path (relative to the root or absolute, with its extension) to a module, walks reverse dependencies breadth-first so each dependent is listed once at its shortest depth, and lists the files to read first (the target and its direct dependents).
- Output: readable text by default; `--sexp` prints `impact-root`, `impact`, `impact-summary` and `read-first` facts. An ambiguous target names the module shown and the other matches.
- Tests: REQ-CS-038 to REQ-CS-044 in `tests/test_code_graph_search.py`; the full unit suite passes.
- Documentation: README (command table, blast radius section with a real run on this repository), SKILL.md, CHANGELOG.

### Motivation
- `rdeps` and `boundary` gave the facts separately, only as S-expressions, and did not accept a file path with its extension. S-expressions stay the compact format for agents; people get text.

## 2026-11-15 - README hero image

### Changes
- Added `docs/assets/wingbox-social-preview.png` (1280x640) with its SVG source and placed it under the README title; the same image is the repository social preview.

### Motivation
- A link to the repository had no picture, and the README opened with text only.

## 2026-11-15 - README discoverability

### Changes
- README: title and opening lines name the main search terms (code graph, blast radius, impact analysis, AI coding agents, Claude Code, Codex); added the sections "What is a code graph?", "What is blast radius analysis?" and an FAQ; "map" wording replaced by "code graph" in the quick start.
- Statements checked against the source: no network, LLM or embedding use in `scripts/`.

### Motivation
- The repository page and the README are what GitHub and web search index; the previous title did not contain the terms developers search for.

## 2026-11-15 - rename to Wingbox and code graph

### Changes
- Renamed the project and the skill from `codespaces` to `wingbox` in `README.md`, `SKILL.md`, `SAMPLE_AGENTS.md` and the design note.
- Replaced "belief map" with "code graph" everywhere outside the history files: scripts `build_code_graph.py` and `code_graph_search.py`, test file `test_code_graph_search.py`, class `CodeGraph`, generated files `.code_graph.sexp` and `.code_graph_cache.json`, S-expression header `code-graph`, lock file prefix `wingbox-code-graph-`, log prefix `[code-graph]`.
- Updated `.gitignore` to the new generated file names.
- Verification: the full unit suite (93 tests) passes before and after the rename.

### Motivation
- "codespaces" is the name of a GitHub product, and "belief map" is not a term developers search for; "code graph" is.

## 2026-11-15

### Changes
- Documentation: `SKILL.md` examples use a generic billing/invoice module
  path; verification notes in this log describe targets by language and kind.

## 2026-08-11

### Changes
- Added dependency-free Free Pascal and Lazarus language support for source
  discovery, structural parsing, module identity, import resolution, search,
  and boundary analysis.
- Added focused regressions for Pascal dependency isolation, resolver errors,
  source-path restoration, and public `analyze`/`boundary` output.
- Added one immutable discovery-exclusion contract shared by source,
  language-configuration, package, and LSP project discovery.
- Added repository-owned `.gitignore` resolution through Git with tracked-path
  matching and machine-global ignore rules disabled.
- Added focused issue #7 regression coverage for repeatable CLI basenames,
  cross-platform validation, TypeScript configuration, language package
  discovery, tracked ignored snapshots, and cache provenance.

### Fixes
- Surfaced Pascal resolver read failures instead of silently publishing partial
  import and type indexes.
- Normalized temporary discovery roots so Pascal backup-directory coverage is
  portable across macOS path aliases.
- Removed TypeScript alias discovery's private skip list so it consumes the
  same policy as every other target-root walk.
- Prevented POSIX and Windows path forms from entering the basename-only public
  CLI contract.

### Motivation
- Extend architecture discovery to Free Pascal and Lazarus repositories while
  preserving fail-closed resolution and language-scoped dependency loading.
- Keep tracked snapshots and repository-ignored source-shaped artifacts out of
  search, reverse-dependency, and architecture-violation results without
  duplicating Git ignore semantics.

## 2026-08-08

### Changes
- Added a dynamically loaded Ruby adapter under `scripts/lang/ruby` using the
  official Python `tree-sitter-ruby` grammar, with `.rb`/`.rake` discovery,
  declarations, qualified modules, methods, inheritance, and explicit requires.
- Added project-local Rails/Zeitwerk resolution for application roots, custom
  static autoload roots, acronyms, concerns, associations, callbacks, jobs,
  mailers, specs, and inherited constants.
- Added explicit `:association`, `:concern`, `:callback`, `:job`, `:mailer`, and
  `:spec` relationship metadata while keeping ambiguous and polymorphic targets
  fail-closed.
- Added an exact Ruby-only dependency manifest so repositories without Ruby do
  not install or load the Ruby grammar.
- Added a language-neutral `Language` protocol and registry for source
  ownership, parsing, import resolution, module IDs, output codes, and LSP
  configuration.
- Moved Python parsing and import resolution to `scripts/lang/python` and
  TypeScript/TSX parsing and import resolution to `scripts/lang/typescript`.
- Added a Rust adapter under `scripts/lang/rust` with Tree-sitter entity
  extraction, trait and inherent implementation methods, Cargo workspace
  resolution, and rust-analyzer metadata.
- Added official Tree-sitter adapters for C#, Java, and Go with language-owned
  parsing, project recognition, module identity, local import resolution, and
  language-server metadata.
- Moved Tree-sitter packages into exact per-language requirement files and made
  parser loading, dependency validation, and cache provenance depend only on
  languages discovered in the target repository.
- Added focused registry, script-mode CLI, Rust parser, and Cargo dependency
  tests while retaining the hardened builder and TypeScript recall coverage.
- Rewrote the README and public descriptions around user questions, quick code
  search, dependency graphs, blast-radius analysis, supported languages, and
  AI-agent workflows.

### Fixes
- Verified Ruby support on a Rails application: all 244 Ruby/Rake files
  parsed without syntax errors in 2.82 seconds, producing 244 nodes, 881
  entities, and 852 edges in a 120,660-byte map. A second build reused all 244
  cached results and completed in 2.04 seconds.
- A source-independent static oracle matched all reviewable inheritance (67),
  concern (11), association (30), job (9), mailer (2), and spec-impact (94)
  edges, measuring 100% precision and recall for each category. The constant
  index uniquely resolved 343 references, left 6 ambiguous references unlinked,
  and classified the remaining 337 as external or dynamic.
- Prevented RSpec `include(...)` matchers from appearing as concern edges,
  resolved constants inherited from local Ruby superclasses, respected
  association `source`, and left polymorphic associations open.
- Parser dispatch now surfaces unsupported languages and parse failures instead
  of silently dropping failed files.
- Preserved the hardened explicit-root, collision, cache-provenance, atomic
  publication, locking, and safe-query contracts while extracting languages.
- Compared the extracted Python and TypeScript adapters with the upstream
  pre-extraction builder on the current Upgraide mono checkout: all 7,215 files,
  7,215 nodes, and 55,972 edges produced identical facts and graph hashes.
- Verified Rust support against a small command-line project: all 4 tracked Rust files were
  discovered, all 256 source-oracle entities matched, and both local crate
  dependency edges matched, with 100% precision and recall.
- Verified C# on a private API codebase: 254/254 authored files, 286/286
  entities,
  and 728/728 source-derived local import edges matched with no parse errors.
- Verified Java on a private service codebase: 28/28 files, 28/28 entities,
  and 46/46 source-derived local import edges matched with no parse errors.
- Verified Go on a private single-file target: 1/1 file and 14/14 entities
  matched with no parse errors; its source has no local package-import recall
  denominator, so a focused multi-package fixture verified both expected local
  package edges instead.
- Excluded .NET build output and Go vendored dependencies, and required an
  actual type reference before resolving C# namespaces or Java wildcards.
- Verified a Python-only build with no site packages and a C#-only build with
  only `tree-sitter` and `tree-sitter-c-sharp` installed; missing TypeScript
  packages now produce a targeted install command without publishing artifacts.

### Motivation
- Make Rails model, concern, service, job, mailer, and spec blast-radius search
  useful without booting the application or installing Ruby dependencies for
  repositories that do not contain Ruby.
- Make new source-language support additive at one explicit boundary, prove
  Rust, C#, Java, and Go coverage on real source repositories, and make the
  project easier to find and use without weakening existing graph correctness
  guarantees.

## 2026-07-30

### Changes
- Added regression coverage for completion-order stability, module-ID
  collisions, cache provenance, atomic publication, writer locking, explicit
  roots/output, additional literal TypeScript imports, quoted paths, malformed
  maps, unsafe patterns, and invalid depths.
- Added exact Python dependency pins and explicit builder/query path contracts.

### Fixes
- Resolved `CALLS_API` providers through consumer imports and failed closed when
  an unqualified provider name remained ambiguous.
- Sorted parsed results and rejected every colliding normalized module ID before
  cache or map publication.
- Added cache/map schema validation, builder fingerprints, atomic replacement,
  and a per-project non-blocking writer lock.
- Replaced user-controlled regular expressions with a bounded linear pattern
  grammar and surfaced numeric, source-read, and structural map errors.
- Encoded unsafe paths as quoted definitions and recognized TypeScript
  `import = require()` and literal template imports.

### Motivation
- GitHub issue #1 reproduced silent semantic changes, discarded modules, stale
  cache trust, unsafe concurrent writes, ambiguous targeting, and unbounded
  query input. These changes make correctness deterministic and failures local,
  explicit, and non-destructive.

## 2026-07-14

### Changes
- Added focused regression coverage for TSX imports, re-exports, literal runtime imports, project-scoped aliases, exact aliases, and package self-imports.

### Fixes
- Resolved TypeScript and TSX dependencies through the same module resolver.
- Kept duplicate path aliases within their owning `tsconfig.json` boundary and supported exact aliases plus multiple configured targets.
- Added import edges for `export ... from`, literal `import()`, and literal `require()` forms.
- Resolved exact package self-imports to the nearest owning package source entrypoint without shadowing installed dependencies.

### Motivation
- An author-run external monorepo compiler oracle measured 99.899% local
  dependency recall and 99.832% precision. The oracle and dataset are not
  committed here, so this is provenance for the change rather than a
  reproducible repository benchmark.

## 2026-07-13

### Changes
- Added focused regression evals for cache validity, Python and TypeScript import impact, LSP batch progress, source-root resolution, layer classification, deterministic output, language gating, and infrastructure diagnostics.
- Made the existing belief-search tests load the checked-out repository script instead of a machine-specific installed path.

### Fixes
- Invalidated incremental cache entries by complete SHA-256 content hash instead of mtime and partial-file hashing.
- Restored source-aware queries by resolving source paths from the directory containing the belief map.
- Prevented reference-only LSP batches from looping without progress.
- Included nested and parent-relative Python imports in dependency edges.
- Resolved TypeScript ESM `.js` import specifiers to their checked-in `.ts` and `.tsx` sources.
- Stopped backend modules named as stores from being classified as UI unless their path has a frontend boundary.
- Removed generated timestamps from the semantic map and made empty infrastructure output name its supported formats.
- Canonicalized the scan root once so macOS path aliases do not leak into module IDs.

### Motivation
- The audit findings were reproducible on isolated fixtures and a large
  polyrepo workspace; these changes address the smallest correctness blockers
  while leaving unsupported languages and infrastructure formats explicit.

## 2026-07-12

### Changes
- Removed the obsolete `.agents` infrastructure-topology design notes and their README tree entry.

### Motivation
- The topology builder is implemented in the source, so retaining its original design notes duplicated completed work and left an unused `.agents` directory.

## 2026-06-19

### Changes
- Updated the codespaces skill workflow so agents are directed to query the belief map before reading source and to use `quick` as a first-pass `analyze` shortcut.
- Changed `belief_search.py quick` to run full `analyze` output instead of boundary-only output, and made it resolve legacy path-map aliases found in raw belief-map hits.
- Added focused unit coverage for quick analyze output, alias resolution from raw hits, boundary files-only output, and no-match suggestions.

### Motivation
- Session-log review showed that agents usually used `search` and `analyze` after activation, but often queried too late and did not discover shortcut or boundary commands.
