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
  for Python, TypeScript, TSX, Rust, C#, Java, Go, Ruby, and Ruby on Rails.
---

# Wingbox -- Code Graph Architecture Discovery

Query `.code_graph.sexp` to discover modules, boundaries, and dependencies before reading source files. Never read code blindly -- query the graph first.

## /wingbox

When the user types `/wingbox` (in Codex `$wingbox`), do this before anything else:

- `/wingbox` with no argument: find the graph (`.wingbox/graph.sexp` or
  `.code_graph.sexp` in the project root). Build it with `wingbox build .`
  when it is missing or stale. The graph is stale when a file of the project
  was modified after the graph file; this check finds one:

  ```bash
  G=$(ls .wingbox/graph.sexp .code_graph.sexp 2>/dev/null | head -1)
  [ -z "$G" ] || find . -type f -newer "$G" -not -path './.git/*' -not -path './wingbox-out/*' \
    -not -path './.wingbox/*' -not -name '.code_graph*' -not -path '*/node_modules/*' -print -quit
  ```

  A rebuild re-parses only the files whose content changed. Then read
  `wingbox-out/REPORT.md` and give the user its "Key findings", the most
  depended-on modules and where to start reading, and say that the map is
  `wingbox-out/map.html` (it opens in a browser, offline).
- `/wingbox <file>`: run `wingbox impact <file> 2` and summarize what depends
  on the file and which files to read first.
- `/wingbox <command> ...`: run `wingbox <command> ...` (for example
  `/wingbox quick "file upload"`, `/wingbox analyze src/billing/invoice`).

## Running the commands

When the `wingbox` command is installed (`command -v wingbox`), run
`wingbox <command> ...` for every query below and `wingbox build PATH` to build;
they take the same arguments as the scripts. A skill installed by
`wingbox install` has no `scripts/` folder and always uses the command.

| Command | Purpose |
|---|---|
| `wingbox install [--global\|--project] [--assistant NAME ...] [--dry-run]` | Copy this skill to each assistant, add the always-on block to CLAUDE.md / AGENTS.md, add the Claude Code search hook |
| `wingbox uninstall [--global\|--project] [--assistant NAME ...]` | Remove exactly what install added |
| `wingbox update [--check] [--dry-run]` | Upgrade with pipx, uv or pip, then refresh the installed skills, blocks and hook |
| `wingbox hook pre-search` | The Claude Code PreToolUse hook; reads the hook input on stdin |

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
ls .code_graph.sexp code_graph.sexp 2>/dev/null
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
| `analyze <id> [--all]` | Full module analysis (PRIMARY command); names at most 200 dependents unless `--all` | `analyze drive/modules/drive/drive_schema` |
| `quick <keyword>` | Search + analyze first match (shortcut) | `quick operatives` |
| `boundary <id>` | Files to read for a change | `boundary operatives.service` |
| `boundary <id> --files-only` | File paths only (compact) | `boundary operatives.service --files-only` |
| `entity <Name> [--positions]` | Find definitions + the modules that use them; `--positions`: every use with line and column | `entity DrivePath --positions` |
| `usages <symbol> [--sexp] [--all]` | Every use of one symbol (`Name`, `<file>::Name`, `<file>::Class.method`) through re-exports and aliases, by file, `line:col` and kind (import, re-export, call, type, extends, reference; `unresolved-call` for a method call whose receiver type is not in sight); at most 200 unless `--all`; a name defined in several files lists the candidates | `usages src/billing/invoice.ts::Invoice` |
| `deps <id> [depth]` | Outgoing dependency tree | `deps operatives.service 3` |
| `rdeps <id> [depth]` | Blast radius (who depends on this) | `rdeps drive_schema 2` |
| `impact <file\|id> [depth] --sexp [--all]` | Blast radius of one file: dependents by shortest depth + files to read first; lists at most 200 unless `--all`, counts are always complete | `impact src/billing/invoice.service.ts --sexp` |
| `flow <id> <fn>` | Trace call/data flow | `flow drive_api fetchFiles` |
| `module <id>` | Single module with edges | `module operatives.controller` |
| `boundaries [id\|all]` | Check architecture violations; type-only imports and guessed layers are reported apart | `boundaries all` |
| `invariants [id\|all]` | Check naming convention violations | `invariants all` |
| `layers` | Show all modules by layer | `layers` |
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

When you need fast, targeted lookups without invoking the Python script, query the code graph directly with `rg`. The bundled builder writes `.code_graph.sexp`; if a repo still uses the legacy `code_graph.sexp`, substitute that filename in the commands below:

```bash
# All facts about a module
rg 'drive/modules/drive_db/api ' .code_graph.sexp

# Who imports a module
rg 'imports .* drive/modules/commons/result' .code_graph.sexp

# What does a module import
rg 'imports drive/modules/drive_db/api' .code_graph.sexp

# All references to an entity
rg 'DriveDbCommitResponse' .code_graph.sexp

# All classes in a repo
rg '^\(cls drive/' .code_graph.sexp

# Data flow edges
rg 'data-flow' .code_graph.sexp
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
`java` = Java, `go` = Go, and `rb` = Ruby/Rails.

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
- **Reading `.code_graph.sexp` as a whole file.** Use `search`, `analyze`, or `rg` to query specific facts.
- **Skipping the code graph for cross-module tasks.** Always query the graph to understand boundaries before reading source files.
- **Reading all files in a directory.** When the boundary shows 2-3 files, read only those.
- **Using project-local scripts.** Always use this skill's bundled scripts. Project copies may output JSON instead of sexp.

## Rebuilding the Graph

```bash
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT"
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT" --full
python3 "$SKILL_ROOT/scripts/build_code_graph.py" --root "$TARGET_ROOT" --lsp
python3 "$SKILL_ROOT/scripts/build_code_graph.py" \
  --root "$TARGET_ROOT" --output "$TARGET_ROOT/custom-map.sexp"
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
python3 scripts/build_infra_topology.py --append .code_graph.sexp  # merge into graph
```

Emits `infra-node`, `k8s-depends`, `k8s-service`, `helm-depends`, `tf-depends`, and `infra-maps` edges. Query with:
```bash
rg 'infra-node' .code_graph.sexp                  # all services
rg 'k8s-depends' .code_graph.sexp                 # service dependencies
rg 'infra-node.*:component database' .code_graph.sexp  # all databases
rg 'infra-maps.*app-service' .code_graph.sexp     # infra -> source code mapping
```
