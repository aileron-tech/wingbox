# Changelog

## 2026-11-16

### Changes

- Package version 0.2.3. The package page shows the current README.
- Package version 0.2.2 (0.2.1 plus the layer of a Python `__init__.py`).
- Package version 0.2.1.
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
- Layers: a Python `__init__.py` is its package. It takes the layer of its
  folder or, when no folder rule applies, the layer where most modules of the
  package are; the names it re-exports no longer decide its layer.
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

## 2026-11-15

### Changes

- Package version 0.2.0.
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
