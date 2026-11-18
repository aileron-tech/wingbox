---
name: wingbox
description: >
  Code graph for large repositories and monorepos. Use it, before grep or
  reading files, when the question is what a change will break (blast radius,
  impact analysis), who uses a file, function, type or package, where a feature
  is implemented, which files to read first, or how modules and packages depend
  on each other; before a refactor, a cross-package change or any non-trivial
  edit; and when exploring an unfamiliar codebase. Builds and queries architecture-aware code search,
  dependency graphs, call flows, module boundaries and architecture violations
  for Python, TypeScript, TSX, Rust, C#, Java, Go, Ruby, Ruby on Rails, PHP,
  Laravel, Blade, Livewire, Eloquent, Dart and Flutter.
---

# Wingbox -- Code Graph Architecture Discovery

Query `.wingbox/index.sexp` to discover modules, boundaries, and dependencies before reading source files. Never read code blindly -- query the graph first.

## /wingbox

When the user types `/wingbox` (in Codex `$wingbox`), do this before anything else:
(when `command -v wingbox` finds nothing, read "Running the commands" below first)

- `/wingbox` with no argument: find the graph (`.wingbox/index.sexp` in the
  project root; every `wingbox` command finds it from a subfolder too). Build
  it with `wingbox build .` when it is missing or stale. The graph is stale
  when a file of the project was modified after the graph file; this check
  finds one:

  ```bash
  G=.wingbox/index.sexp
  [ ! -f "$G" ] || find . -type f -newer "$G" -not -path './.git/*' \
    -not -path './.wingbox/*' -not -path '*/node_modules/*' -print -quit
  ```

  A rebuild re-parses only the files whose content changed. Then read
  `.wingbox/REPORT.md` and give the user its "Key findings", the most
  depended-on modules and where to start reading, and say that the map is
  `.wingbox/map.html` (`wingbox open` shows it in a browser, offline).
- `/wingbox <file>`: run `wingbox impact <file> 2` and summarize what depends
  on the file and which files to read first.
- `/wingbox <command> ...`: run `wingbox <command> ...` (for example
  `/wingbox quick "file upload"`, `/wingbox analyze src/billing/invoice`).

## Running the commands

When the `wingbox` command is installed (`command -v wingbox`), run
`wingbox <command> ...` for every query below and `wingbox build PATH` to build;
they take the same arguments as the scripts. A skill installed by
`wingbox install` has no `scripts/` folder and always uses the command.

When the command is missing and this skill has no `scripts/` folder (a skill
installed from a plugin directory), install the program before the first
query. Tell the user in one line that Wingbox needs its open-source program
(MIT, it runs on this machine), then run the first of these the machine has:

```bash
pipx install wingbox
uv tool install wingbox
```

With neither pipx nor uv, ask the user to install one of them
([pipx](https://pipx.pypa.io/stable/installation/),
[uv](https://docs.astral.sh/uv/getting-started/installation/)) and stop there.
After the install run `wingbox build .` in the project: it writes only the
`.wingbox/` folder. Leave `wingbox install` to the user: it changes their own
assistant settings.

| Command | Purpose |
|---|---|
| `wingbox install [--assistant NAME ...] [--without ITEM,...] [--yes] [--dry-run]` | Set up the assistants in the user's own settings (home folder only): this skill, the always-on block in CLAUDE.md / AGENTS.md, the Claude Code hooks. Items: `skill`, `block`, `search-hook`, `edit-hook`, `session-hook` (refresh a stale graph at session start), `git-hook` (refresh after git switch/pull/merge...); `--without` leaves items out (`ITEM` or `ASSISTANT:ITEM`, comma separated), and the choice is remembered. An agent runs it with `--yes`; it asks only a person at a terminal |
| `wingbox uninstall [--assistant NAME ...] [--without ITEM,...]` | Remove exactly what install added (keeps the `--without` items); `--project` removes what versions before 0.9.0 wrote into a repository |
| `wingbox open [PATH]` | Open `.wingbox/map.html` of the project in the browser (prints its path when there is no browser) |
| `wingbox update [--check] [--dry-run]` | Upgrade with pipx, uv or pip, then refresh the installed items only (never adds an item the user left out); warns about files an earlier version left (`.code_graph*`, `wingbox-out/`, `install --project` files) |
| `wingbox hook pre-search` | The Claude Code PreToolUse hook; reads the hook input on stdin; refreshes the graph before it reads it |
| `wingbox hook dirty` | The Claude Code PostToolUse hook on Edit and Write: marks the changed file so the graph is refreshed before its next use |

Each of these prints facts as text; add `--sexp` for S-expressions.

## Scripts

This skill bundles Python scripts. Resolve the skill and target roots before
running them so the scanned project is always explicit:

- `scripts/code_graph_search.py` -- query the code graph
- `scripts/build_code_graph.py` -- generate the code graph from source
- `scripts/build_infra_topology.py` -- extract Kustomize, Helm, and Terraform topology facts
- `scripts/git_descendants.py` -- find all commits descending from HEAD (or a given ref)

Never use project-local copies of these scripts -- they may be outdated and output JSON instead of sexp.

## Prerequisite

Locate the code graph. If absent, build it:

```bash
ls .wingbox/index.sexp 2>/dev/null
SKILL_ROOT="$(pwd -P)"
TARGET_ROOT="$(cd /absolute/path/to/project && pwd -P)"
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT"
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT" --full
```

Python indexing has no external parser dependency. For other detected
languages, the first build fails before publication when a parser is absent or
has the wrong version and prints the exact per-language command to run, such as:

```bash
python3 -m pip install -r "$SKILL_ROOT/requirements/typescript.txt"
python3 -m pip install -r "$SKILL_ROOT/requirements/csharp.txt"
python3 -m pip install -r "$SKILL_ROOT/requirements/ruby.txt"
python3 -m pip install -r "$SKILL_ROOT/requirements/php.txt"
python3 -m pip install -r "$SKILL_ROOT/requirements/dart.txt"
```

Install only the requirement files named by the builder, then rerun it.
`requirements.txt` is reserved for the infrastructure-topology dependency.

## Mandatory Workflow: Query Before Code

For non-trivial code work, run a code-graph query before `rg`, `find`, `sed`,
`cat`, `nl`, opening directories, or reading source files. The skill only helps
when it is used as the first scoping step, not as confirmation after source has
already been inspected.

**Fast first pass when the task has a clear keyword:**
```bash
python3 scripts/code_graph_search.py quick "keyword"
```
`quick` searches for the first matching module and immediately runs `analyze`.
Use it to get boundary files, dependencies, reverse dependencies, layers, and
violations in one step. If `quick` returns the wrong module or no match, use the
explicit `search` -> `analyze` flow below.

**Step 1 -- Find the module ID:**
```bash
python3 scripts/code_graph_search.py search "keyword"
python3 scripts/code_graph_search.py entity EntityName
```
Use `search` or `entity` to locate the module ID. Module IDs are **full relative paths** like `billing-service/src/modules/invoice/invoice.service`, not short codes like `bs34`. The code graph may contain short path-map IDs internally, but they are resolved automatically -- always pass human-readable path-like strings. Do NOT guess or invent module IDs -- always confirm with `search` first.

**Step 2 -- Analyze the module (do this IMMEDIATELY, not optionally):**
```bash
python3 scripts/code_graph_search.py analyze <module-id>
```
`analyze` is the PRIMARY command. It returns the full context: boundary files, dependencies, reverse dependencies, architecture layer, and violations. Always run it before reading any source.

**Step 3 -- Read only the boundary files listed in the analyze output.**

**Step 4 -- Do NOT read entire directories.** If the boundary shows 2-3 relevant files, read only those files. Ignore everything else in the directory.

## Command Reference

| Command | Purpose | Example |
|---|---|---|
| `search "<pattern>"` | Safe literal/`.*` search on graph | `search "operative.*service"` |
| `analyze <id> [--all]` | Full module analysis (PRIMARY command); names at most 200 dependents unless `--all`; ends with the `(advice ...)` that concerns the module | `analyze drive/modules/drive/drive_schema` |
| `quick <keyword>` | Search + analyze first match (shortcut) | `quick operatives` |
| `boundary <id>` | Files to read for a change | `boundary operatives.service` |
| `boundary <id> --files-only` | File paths only (compact) | `boundary operatives.service --files-only` |
| `entity <Name> [--positions]` | Find definitions + the modules that use them; `--positions`: every use with line and column | `entity DrivePath --positions` |
| `usages <symbol> [--sexp] [--all]` | Every use of one symbol (`Name`, `<file>::Name`, `<file>::Class.method`) through re-exports and aliases, by file, `line:col` and kind (import, re-export, call, type, extends, reference; `unresolved-call` for a method call whose receiver type is not in sight); at most 200 unless `--all`; a name defined in several files lists the candidates | `usages src/billing/invoice.ts::Invoice` |
| `deps <id> [depth]` | Outgoing dependency tree | `deps operatives.service 3` |
| `rdeps <id> [depth]` | Blast radius (who depends on this) | `rdeps drive_schema 2` |
| `impact <file\|id> [depth] --sexp [--all]` | Blast radius of one file: dependents by shortest depth + files to read first; lists at most 200 unless `--all`, counts are always complete; ends with the advice that concerns the file | `impact src/billing/invoice.service.ts --sexp` |
| `flow <id> <fn>` | Trace call/data flow | `flow drive_api fetchFiles` |
| `module <id>` | Single module with edges | `module operatives.controller` |
| `wingbox init [PATH] [--force] [--sexp] [--synonyms]` | Split the repository into parts, judge the architecture style of each (with score, evidence and sources) and write `.wingbox/config.yaml` (kept unless `--force`) and `.wingbox/assistant-setup.md` (the lines a team may add by hand to AGENTS.md, CLAUDE.md and .claude/settings.json; Wingbox writes nothing outside `.wingbox/`); `--synonyms` also maps folder names through the built-in word vectors; in an empty folder `--language L --style S --yes [--scaffold]` starts a new project (without them it prints the choices and exits 2) | `wingbox init --sexp` |
| `wingbox advice [--sexp] [--learned]` / `wingbox advice accept\|reject <id> [--reason TEXT]` | Open recommendations of the last build (move, fix-violation, missing-folder) without the rejected or long-ignored ones; record a decision in `.wingbox/decisions.sexp` | `wingbox advice --sexp` |
| `wingbox align [PART] [--to STYLE] [--learn] [--sexp]` | Gaps of each part against its style (placed files, rules followed, unplaced folders, broken rules); `--to` against another style; `--learn` writes learned folders and a baseline so that only new violations are reported | `wingbox align --sexp` |
| `wingbox place "<what>" [--part PART] [--sexp]` | Layer and folder for new code by the placement rules of the part's style (`:new` when the folder does not exist yet) and what it may import | `wingbox place "repository for invoices" --sexp` |
| `wingbox compare [--sexp]` | Parts of a monorepo per language: style, layers, differences (missing layers, a folder name meaning another layer, a style unlike the majority) and advice for the part that deviates | `wingbox compare --sexp` |
| `wingbox learn --agent [PART] [--sexp]` / `wingbox learn --apply [FILE]` | `/wingbox learn`: run `wingbox learn --agent`, read the files it names, answer only in its format (`(layer <part> <folder> <layer> :because "...")`, `(unknown ...)`, at most one `(style ...)`), pipe the answer to `wingbox learn --apply`, show the result; on errors fix the named lines and send the whole answer again. When the code does not decide a folder, ask the user in the chat and answer with `:because "operator: <their words>"`; never guess. To change `.wingbox/config.yaml`, follow the instructions at its top | `wingbox learn --agent` |
| `boundaries [id\|all]` | Check the boundary rules of each part's style (or of rules the part declares); each line names `:part`, `:style` and `:rule`; type-only imports and guessed layers are reported apart; then what files do that their layer may not (`capability-violation`: SQL outside the repositories, outbound HTTP outside infrastructure, the framework in the domain, with `:line` and `:evidence`) and the style's pattern checks (`pattern-violation`). `boundaries-ok` only when every part was checked; a part whose declared rules were not read, or without a determined style, is `boundaries-not-checked` with `boundaries-partial` instead of ok. Exit status: 2 not checked (declared rules not read), 1 violations, 0 otherwise - never report a pass on exit 2 | `boundaries all` |
| `invariants [id\|all]` | Check naming convention violations | `invariants all` |
| `layers` | Show all modules by layer, with the part, style and rule that set each layer | `layers` |
| `find_function <name>` | Find function/method definitions | `find_function createOrder` |
| `find_type <name>` | Find type/class/module/interface/enum definitions | `find_type DriveDbCommitResponse` |
| `find_callchain <src> <tgt> [depth]` | Trace call path between functions | `find_callchain createOrder processPayment` |
| `find_callers <fn> [depth]` | Who calls this function | `find_callers handleRequest 3` |
| `find_calls <fn> [depth]` | What does this function call | `find_calls createOrder 2` |
| `grep_functions <pattern>` | Search function bodies in source | `grep_functions "validate.*input"` |
| `diff_functions [ref]` | Changed functions in git diff | `diff_functions HEAD~3` |
| `query '<sexp>'` | Composable Scheme query | `query '(count (boundary "X"))'` |
| `repl` | Interactive Scheme REPL | `repl` |
| `wingbox mcp [--root DIR]` | Run these commands as tools of an MCP server on stdio (`build`, `impact`, `usages`, `analyze`, `quick`, `boundary`, `deps`, ...) for MCP clients | `claude mcp add wingbox -- wingbox mcp` |

`impact` accepts an ordinary file path. Without `--sexp` it prints readable text
for people; agents pass `--sexp` to get the same facts in the compact
S-expression form, which is the format every other command uses.

## Quick Reference: rg Queries

When you need fast, targeted lookups without invoking the Python script, query the code graph directly with `rg`. The builder writes the graph to `.wingbox/index.sexp`:

```bash
# All facts about a module
rg 'drive/modules/drive_db/api ' .wingbox/index.sexp

# Who imports a module
rg 'imports .* drive/modules/commons/result' .wingbox/index.sexp

# What does a module import
rg 'imports drive/modules/drive_db/api' .wingbox/index.sexp

# All references to an entity
rg 'DriveDbCommitResponse' .wingbox/index.sexp

# All classes in a repo
rg '^\(cls drive/' .wingbox/index.sexp

# Data flow edges
rg 'data-flow' .wingbox/index.sexp

# Python modules loaded by a name read from a configuration file
rg 'registry-wires' .wingbox/index.sexp
```

These one-liners are useful for quick checks. For structured output and full analysis, use the Python script commands.

## Output Notation

All output is S-expression facts, one per line. See [references/sexp-notation.md](references/sexp-notation.md) for the full format.

Key patterns:
```scheme
(boundary <mod> :lang py :file <path> :purpose "<desc>")
(boundary-dep <mod> <dep> :lang ts :file <path> :relation imports)
(boundary-rdep <mod> <rdep> :lang tsx :file <path> :relation imports)
(boundary-summary <mod> :total 10 :deps 7 :rdeps 2)
(entity-def cls <mod> <Name> <line> :lang py :file <path>)
(violation <src> <tgt> :src-layer domain :tgt-layer api :via imports :lang ts "<reason>")
(result <module-or-path>)
(result-count 42)
```

**`:lang` values**: `py` = Python (snake_case), `ts` = TypeScript
(camelCase), `tsx` = TSX (PascalCase), `rs` = Rust (snake_case), `cs` = C#,
`java` = Java, `go` = Go, `rb` = Ruby/Rails, `php` = PHP/Laravel (PascalCase),
`blade` = Blade templates, and `dart` = Dart/Flutter.

## Scheme Query Language

Compose queries for complex analysis. See [references/scheme-queries.md](references/scheme-queries.md) for full reference.

**Essentials:**
```scheme
(boundary "id")                ; target + deps + rdeps (change boundary)
(entity "Name")                ; modules defining entity
(deps "id" depth)              ; outgoing dependencies
(rdeps "id" depth)             ; reverse dependencies
(find-function "name")         ; modules with matching function defs
(find-type "name")             ; modules with matching type defs
(find-callers "fn" depth)      ; modules calling a function
(find-calls "fn" depth)        ; modules a function calls
(intersect A B)                ; set intersection
(filter SET "pattern")         ; safe literal/.* filter
(files SET)                    ; resolve to file paths
(count SET)                    ; count members
(violations)                   ; modules with boundary violations
(layer "id")                   ; architecture layer of a module
```

## Analysis Plan Pattern

Before modifying code, compose a plan from queries:

```scheme
; 1. Scope
(boundary "operatives.service")
(count (boundary "operatives.service"))

; 2. Architecture constraints
(violations "operatives.service")
(layer "operatives.service")

; 3. Cross-language boundaries
(filter (refs-to "DrivePath") "app-service")

; 4. Narrow to actionable files
(filter (boundary "operatives.service") "dto")
(intersect (deps "controller" 2) (deps "service" 2))

; 5. After changes: verify
(violations "operatives.service")
```

## Trigger Examples

Use this skill before reading source for:

- Root-cause investigations that mention services, APIs, jobs, events, UI flows, or failing behavior.
- Cross-service or cross-module questions, including "how does X call Y?" and "where is this stored?"
- Redmine clarification when the answer depends on code behavior.
- Architecture reviews, impact analysis, public contract changes, or blast-radius checks.
- Reviews of non-trivial staged changes when dependency direction or module ownership matters.

Do not force it for commit-only tasks, git-log summaries, manual demo/server runs, pure browser testing, or session-log analysis.

## Error Recovery

If a command returns `(error no-match ...)`:
1. Do NOT retry with the same ID or guess another ID
2. Run `search` with a broader keyword to find the correct module ID
3. Use the `:suggestions` field if present -- it lists similar module IDs

```bash
# Wrong: guessing IDs
python3 scripts/code_graph_search.py analyze bs34        # fails: bs34 is a short path-map ID
python3 scripts/code_graph_search.py analyze invoice.service  # fails: incomplete path

# Right: search first, then use the full path from results
python3 scripts/code_graph_search.py search "invoice.*service"
# Output shows: billing-service/src/modules/invoice/invoice.service
python3 scripts/code_graph_search.py analyze billing-service/src/modules/invoice/invoice.service
```

## Anti-patterns

- **Guessing module IDs.** Module IDs are full relative paths (e.g., `billing-service/src/modules/invoice/invoice.service`). Short codes like `bs34` are internal path-map IDs and must not be used directly. Always `search` first.
- **Build but never query.** Building the code graph and then never running `search`, `analyze`, or `boundary` provides zero value.
- **Querying after reading source.** The first source-code read should come from the `boundary-file` lines and the `:file` of the `analyze-dependent` lines in `analyze` output.
- **Reading `.wingbox/index.sexp` as a whole file.** Use `search`, `analyze`, or `rg` to query specific facts.
- **Skipping the code graph for cross-module tasks.** Always query the graph to understand boundaries before reading source files.
- **Reading all files in a directory.** When the boundary shows 2-3 files, read only those.
- **Using project-local scripts.** Always use this skill's bundled scripts. Project copies may output JSON instead of sexp.

## Rebuilding the Graph

```bash
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT"
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT" --full
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT" --lsp
python3 "$SKILL_ROOT/scripts/build_code_graph.py" \
  --root "$TARGET_ROOT" --exclude-dir review-bundles
```

Use repeatable `--exclude-dir NAME` options for tracked snapshots, generated
fixtures, or other source-shaped trees that are not part of the live product.
Each value is a directory basename and applies at any depth. Changing exclusions
invalidates the incremental cache so excluded files cannot leak from an earlier
build.

Targets inside Git worktrees also use Git's repository ignore rules. The same
resolved policy filters source files, TypeScript aliases and packages, Go and
Rust package manifests, Ruby project configuration, and LSP project discovery.
Git is invoked with index checks disabled so tracked snapshot paths that match
`.gitignore` remain excluded from the code graph.

Rebuild after structural changes (add/remove files, change imports, add classes).
Search patterns support literals, `.*`, boundary `^`/`$` anchors, and
backslash escapes. Other regex operators are rejected.

The TypeScript AST contract covers TS/TSX imports and re-exports, literal
`import()`/`require()`, `import = require()`, literal template imports, emitted
`.js`/`.jsx` specifiers, project-scoped path aliases including `paths`
inherited through a relative `extends` chain, and imports that name a package
of the repository: the package entry (`exports`, `source`, `types`, `module`,
`main`) and its subpaths resolve to the TypeScript source, also when the entry
points at build output. Packages outside the repository stay external. It does
not promise aliases from an `extends` preset that lives in a package or
`baseUrl`-only resolution.

JavaScript files (`.js`, `.jsx`, `.mjs`, `.cjs`) are modules under the same
contract. An import written as `./x.js` names `x.ts` when it exists and the
real `x.js` otherwise; `.mjs` and `.cjs` are matched by their own extension.
Minified files (`*.min.js`, or any line longer than 5,000 characters) and
bundles (`bundle` in the file name) are left out, like everything in excluded
or ignored directories.

An import that goes through the `outDir` of a `tsconfig*.json`, written as a
relative path, through an alias or as a package entry, resolves to the file
under `rootDir` that it is compiled from. Without `rootDir` the source is
looked up next to that tsconfig, then in its `src` folder.

Python indexing follows `import` and `from` imports and modules loaded at run
time: a literal name given to `importlib.import_module` or `__import__` is an
import; a name built from a literal package path and one variable
(`f".plugins.{name}"`) is a `registry-wires` edge to each module of that
package named by a string of a JSON, YAML or TOML file in the folder of the
loader. A method called in a loop on an object such a registry returns for the
loop variable is a `calls ... :registry` edge to each wired module that defines
it, and `data-flow ... :pipeline` when the call replaces its own argument
(`data = stage.process(data)`), in the order of the configuration list. Names
computed any other way stay unlinked.

C# indexing follows namespaces and referenced local types; Java indexing
follows packages, explicit imports, and referenced wildcard types. Unused
namespace or wildcard imports do not create guessed edges. Go indexing resolves
local module imports to the source files that make up the imported package.
Standard .NET `bin`/`obj` output and Go `vendor` dependencies are excluded.

Ruby indexing discovers `.rb` and `.rake` files and resolves explicit requires,
local constants, Rails application roots, statically configured autoload roots,
project acronyms, concerns, associations, callbacks, jobs, mailers, and spec
references. It never boots Rails. Reopened or otherwise ambiguous constants and
polymorphic associations remain unlinked instead of producing guessed edges.
Rails relation flags are preserved as `:association`, `:concern`, `:callback`,
`:job`, `:mailer`, or `:spec` in the generated map.

PHP 8 indexing discovers `.php` files and `.blade.php` templates. It resolves
classes through the `autoload.psr-4` and `autoload-dev.psr-4` prefixes of every
`composer.json` in the repository, longest prefix first, and follows `use`
aliases, inheritance, traits, Blade `@extends`/`@include*`/`@component`/`@each`
directives, `<x-*>` components, Livewire aliases and registrations, Eloquent
associations, events and jobs. `view()` references resolve through
`resources/views` and through view namespaces registered with `loadViewsFrom`
or `View::addNamespace`. It never boots Laravel. Unresolved or ambiguous class,
view and component names remain unlinked, and a duplicate class declaration
such as an IDE-helper stub does not shadow the PSR-4 file.

Dart indexing discovers `.dart` files and reads each one with a tree-sitter
Dart grammar: classes, mixins, enums, extension types, extensions, type
aliases, top-level functions, the members of each type, and `extends`, `with`,
`implements` and `on`. It resolves `import` and `export` directives and
`part`/`part of` pairs. A `package:<name>/path.dart` URI whose name is the
top-level `name:` of a `pubspec.yaml` in the repository resolves to
`lib/path.dart` of that package, the nearest owning pubspec first, so a Dart
monorepo links across its packages. A relative URI resolves to the neighbouring
file. `dart:` core libraries and `package:` names that no local pubspec
declares are external and create no edge. A directive whose file is missing,
such as a `*.g.dart` or `*.freezed.dart` part that `build_runner` has not
written yet, adds nothing and fails nothing. Flutter needs no separate handling:
widgets are ordinary classes. Runtime wiring is not visible to static analysis:
what a `get_it` locator registers, the pages a `go_router` configuration builds
and the clients `retrofit` generates are bound at runtime, so expect no edge for
them and start an impact query from the concrete file instead. `.dart_tool/` is
excluded by its name, and so is `build/`, the default skip folder in every
language that holds Flutter output.

Pascal indexing discovers `.pas`, `.pp`, `.lpr`, and `.inc` files. It extracts
units, programs, `uses`/`contains`/`requires` clauses, include directives,
classes, records, interfaces, inheritance, methods, and routines. Unit names
and paths resolve case-insensitively; explicit `UnitName in 'path.pas'` paths
take precedence, followed by a unique unit in the importing file's directory.
Other ambiguous unit names remain unlinked. Conditional compiler branches are
indexed conservatively. Pascal has no LSP enrichment yet. Pascal files inside
conventional Lazarus `backup/` directories are excluded; other supported
languages in directories with that name remain indexable.

## Infrastructure Topology

Extract service topology from Kustomize, Helm, and Terraform files. This script requires `PyYAML` (`pip3 install pyyaml`).

```bash
python3 scripts/build_infra_topology.py                    # auto-detect all IaC
python3 scripts/build_infra_topology.py --kustomize .      # kustomize only
python3 scripts/build_infra_topology.py --helm ./charts    # helm charts only
python3 scripts/build_infra_topology.py --terraform ./infra # terraform only
python3 scripts/build_infra_topology.py --append .wingbox/index.sexp  # merge into graph
```

Emits `infra-node`, `k8s-depends`, `k8s-service`, `helm-depends`, `tf-depends`, and `infra-maps` edges. Query with:
```bash
rg 'infra-node' .wingbox/index.sexp                  # all services
rg 'k8s-depends' .wingbox/index.sexp                 # service dependencies
rg 'infra-node.*:component database' .wingbox/index.sexp  # all databases
rg 'infra-maps.*app-service' .wingbox/index.sexp     # infra -> source code mapping
```
