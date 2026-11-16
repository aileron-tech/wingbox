# Completion Log

## 2026-11-16 - public benchmark against Graphify

### Changes
- `benchmarks/graphify/run.sh`: one command installs Wingbox and Graphify from PyPI, fetches seven public repositories at pinned commits, builds both graphs and runs three tests that need no model: Graphify's own token formula, every dependency edge against the import statements, and the impact command of each tool against reference lists.
- `benchmarks/graphify/tools/`: the references come from Python's `ast` (`py_edges.py`, checked against grimp), an own TypeScript resolver and an own Rust resolver, never from either tool; `agent_test.py` runs the agent trials; `make_report.py` writes the report from the result files.
- `benchmarks/graphify/gold/`, `benchmarks/graphify/results/`, `benchmarks/graphify/README.md`: the reference lists, the result files of the run on Wingbox 0.2.0 and Graphify 0.9.110, and the report.
- `README.md`: section "Measured: Wingbox, Graphify and plain search".
- Tests: REQ-BENCH-001 (the report equals the output of the report script on the committed results), REQ-BENCH-002 (no committed benchmark file names a path of a local machine).

### Motivation
- Token-reduction figures published for code-graph tools divide the size of a corpus by the size of an answer and check no answer. A reader needs numbers that can be run again: the same repositories, the same questions, reference lists built without either tool.

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
