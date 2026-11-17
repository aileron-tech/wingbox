# Completion Log

## 2026-11-17 - architecture commands read a fresh graph; loader configs keep it fresh

### Changes
- `src/wingbox/cli.py`: advice, align, compare, learn and place go through `_refresh_before_query`, the step the query commands use (refresh, or the stale or unconfirmed fact on stderr).
- `scripts/build_code_graph.py`: `TreeSnapshot.data_folders` and `record_loader_configs`: the snapshot of a build lists the JSON, YAML and TOML files beside each Python module loader and the loader's folder.
- `src/wingbox/freshness.py`: `Snapshot.data_folders`, `is_tracked_path` (a file of the last build, a tracked name, or a configuration file in a loader folder) for the PostToolUse hook and for new files found by the comparison with the snapshot.
- `scripts/graph_update.py`: a changed loader configuration makes the partial update step aside, so the refresh rebuilds and the registry-wires edges follow the new names.
- Tests REQ-FRESH-035..039 (`tests/test_architecture_freshness.py`).
- Why: the two gaps named in the capability-rules report; the maintainer asked to treat them as bugs.

### Verification
- tests/test_architecture_freshness.py, test_freshness, test_graph_update, test_project_folder, test_learn, test_cli: 77 OK (Python 3.12).

## 2026-11-17 - Find-usages benchmark: Wingbox, CodeGraph and Graphify on 100 symbols

### Changes
- `benchmarks/usages/` (new): 100 exported symbols drawn with a fixed seed from five public repositories (TypeScript 40, Rust 40, Python 20). Each tool answers "where is S of file X used" with its own command: `wingbox usages <X>::<S> --all`, `codegraph callers <S> --file <X>`, `graphify affected "<X>::<S>"` at depth 1 and at its default depth 2. Files are checked against the language tooling's find usages (TypeScript language service, pyright, rust-analyzer), use sites against the SCIP index of the same engines.
- Wingbox 0.5.1: exact files 100 of 100, 1,660 of 1,660 SCIP use lines, 0 outside, same column at 1,668 of 1,668 sites, median 62 tokens. CodeGraph 1.6.2: exact 79, 680 lines, 0 files outside, 48 missing (45 in Rust), median 73 tokens. Graphify 0.9.110: 91 answered; exact 76 at depth 1, 34 at depth 2 with 1,899 files outside.
- `run.sh` reproduces everything from nothing; `make_report.py` writes the README, which opens with "Which tool for which job" and keeps a "Why the numbers differ" section citing each tool's documentation and code; `verify.py` compares a rerun with the committed results. Tests REQ-BENCH-011 (README equals the generated report) and REQ-BENCH-012 (a site is scored by file and line); REQ-BENCH-002 covers the new folder.

### Decisions
- Sites are scored by file and line because Graphify prints no column; column agreement is reported for the two tools that print one.
- A TypeScript `new S(...)` is a site of class S: SCIP files it under the constructor symbol.
- Every tool is asked for its whole list (`--all`, `--limit 1000000`); tokens are characters of the text answer / 4, as in the other benchmarks. No timing: accuracy and answer size only.
- CodeGraph runs with the environment of `benchmarks/graphify/run.sh` (private HOME, no daemon, no watcher). Graphify has no find-usages command; `affected` is the nearest and is reported at both depths.
## 2026-11-17 - capability rules: what a layer may do (SQL in repositories, HTTP in infrastructure, no framework in the domain)

### Changes
- `scripts/architecture/capabilities.py` (new): detection by syntax, per line with evidence, of sql, db-client, http-client, framework, http-response, env, filesystem, process for Python, TypeScript/JavaScript, PHP, Ruby, Java/Kotlin, C#, Go. A SQL literal counts when a query call on the line (or opened up to three lines above) receives it; an upper-case SQL literal without one is suspected; text inside multi-line strings is skipped (embedded scripts, docstrings); `import type` is suspected; IndexedDB `db.delete('store', ...)` is not a query builder. Each pattern runs only on the lines that hold one of its trigger words.
- Style files: `[capabilities]` (`allowed_in` / `forbidden_in` per capability, with the fix) and `patterns` for layered, onion-clean, hexagonal, mvc, nestjs; `library.Style.capabilities` / `patterns`.
- `.wingbox/config.yaml`: `capabilities:` per part (validated: known capability, layers of the style), written back by `render_config`, explained in the header.
- Graph facts: `arch-capability`, `arch-pattern`, `capability` (per finding), `capability-known` (baseline); builder `_node_capabilities` with the cache `.wingbox/cache.capabilities.json` keyed by content hash and the detector's digest (ignored by main's `cache*` pattern); partial updates go through the same `project_architecture`.
- `scripts/code_graph_search.py`: `capability_violations`, `pattern_violations` (controller-skips-service needs a target repository with sql or db-client; service-builds-http-response; domain-imports-orm on the db-client violation of a domain file), printed by `boundaries` after the layer rules with `(capability-summary ...)`; composition roots, tests and files in no layer are not judged; declared parts have no capability rules; `fix-capability` / `fix-pattern` advice.
- `src/wingbox/overview.py`: REPORT.md section "What layers do"; `src/wingbox/cli.py`: `align --learn` writes `baseline-capability` lines.
- Tests REQ-CAP-001..009 (`tests/test_capabilities.py`).
- Why: the maintainer's request (SQL in the DAO/repository layer, no queries from controllers or services, clean-code patterns); design in docs/layer-control.md section 4.4 (main cd2d6d5).

### Verification
- Full suite: 487 tests OK on Python 3.12 and 3.11 (tests/test_capabilities.py 9).
- Real repositories (default rules of the detected style): immich server (nestjs) 15 capability violations, 0 candidates, 2 pattern breaks; archestra: backend declared (no capability rules), frontend route-tree (no rules): 0; fastapi (style none): 0; mastodon (mvc): 0 (raw SQL sits in models, lib, workers and migrations, never in controllers); laravel-realworld-example-app: style none detected, and with `style: mvc` stated 0 (Eloquent model calls are not counted as SQL; the app has no raw SQL or DB:: call).
- archestra backend, what layered defaults would flag (not counted as violations, the part is declared): SQL in 29 files and database clients in 47 files outside its data layers (models, models-agents, models-upper, schema), against 195 and 469 inside them.
- Precision, findings read against the source: immich 17 of 17 correct (12 of the 15 are imports of kysely type names - Selectable, Insertable, Updateable - written without `import type`, so a type-level coupling; 1 driver error class; 2 query-builder helpers in utils/; 2 services writing the HTTP response); archestra sample 10: 8 correct, 1 SQL inside an embedded Python script (fixed: strings skipped), 1 Salesforce SOQL query passed to `conn.query` (remains: an external API's query language); mastodon sample 9: 8 correct, 1 IndexedDB `db.delete` (fixed). The first run also listed 17 controller-skips-service breaks against immich's logging, email and storage "repositories" (no database access): fixed by requiring a repository with sql or db-client.
- archestra acceptance unchanged: 112 of 112; edges identical to main on immich and archestra.

### Not done
- Eloquent / ActiveRecord / Django ORM model calls are not counted as SQL (they would flag every controller of those frameworks); `wingbox check` and the exports are other work.

## 2026-11-17 - layer config settled through agents: operator rule, questionnaire, config header

### Changes
- `scripts/architecture/agent_learn.py`: `OPERATOR_RULE` in the learn task: when the code does not decide, ask the operator in the chat and pass the answer on (`:because "operator: ..."`); "never guess" replaces "answer unknown rather than guess".
- `src/wingbox/cli.py`: `learn` in human mode runs `_questionnaire` (style among the candidates of `judge`, with score and the first sentence of the style's summary; one question per open folder of the task, re-asked on an invalid layer; `--yes` takes the closest style and the guesses) and records the answers through `_learn_record`, then `_offer_editor` ($VISUAL or $EDITOR). The task of one part is built by `part_task(part, style)`, shared by the agent task and the questionnaire. `align --learn` by a person (terminal or `--human`, without `--to` or `--sexp`) runs the same questionnaire. The old confirm-per-part flow (`_learn_human`) is gone. A layer answered by a person is noted `answered by a person: ...` in the config.
- `scripts/architecture/config.py`: `config_header()` (instructions written at the top of every `.wingbox/config.yaml`) and `with_header()` (replaces the leading comment block, keeps the rest; used when init appends a part).
- Tests: REQ-LEARN-006 (questionnaire), REQ-LEARN-011 (align --learn at a terminal), REQ-LEARN-012 (operator rule), REQ-LEARN-013 (header).
- Why: the maintainer's rule "a person edits nothing by hand"; design in docs/layer-control.md (main).

### Not done
- The header states only what the config supports today: pinned single files and `required` imports are not keys of the config yet (the header says a single file cannot be pinned); `wingbox check` does not exist yet, so the header says to run `wingbox build` and `wingbox boundaries`.

### Verification
- tests.test_learn 13 OK; test_cli, test_architecture, test_align, test_place, test_new_project, test_compare, test_review_fixes, test_overview, test_plugins 108 OK (Python 3.12).

## 2026-11-17 - rebased on main 0.5.1: the graph is .wingbox/index.sexp

### Changes
- History: the ToCS commit replayed on main 0.5.1 (cd45cdd), then the 22 architecture commits since 0.2.7 squashed into one on top of it (previous head 544910b).
- Paths: the builder, the search script and the CLI commands init, advice, align, place, learn and compare use main's helpers (`freshness.find_project`, `freshness.graph_path`, `freshness.no_project_message`, `build_code_graph.index_path`, `prepare_wingbox_directory`); my `GRAPH_FILE`, `OUTPUT_FILE`, `INDEX_DIRECTORY`, `_graph_root` and the `.gitignore` writer of `agent_learn` are gone; `--output` stays refused as on main.
- `.wingbox/.gitignore` is main's: `config.yaml`, `decisions.sexp`, `learned.sexp` and `baseline.sexp` match none of its patterns (REQ-LEARN-005, REQ-FRESH-030 check it); `init` now writes the `.gitignore` with the config.
- `build_code_graph.project_architecture()`: the architecture facts of a graph, called by `_run_build` and by `graph_update.update_graph`, so a partially updated graph equals a build (updates use the default matching, without `--synonyms`).
- Registry edges of the ToCS commit are made in `assemble_graph`, with the other facts that need every module; a pipeline flag replaces the fragment's data-flow edge by a copy.
- Main's path classifier (`_classify_layer_fallback`, `_is_test_path`, `_classify_dart_layer_by_path`) is replaced by label rules: `labels.flutter_layer` for `.dart` files, a Dart `state store` role is service, `integration_test` is a test folder, layered.toml gets `livewire` (ui), `listeners` and `policies` (service). REQ-DARTLAYER-008 and REQ-PHP-017 ask `module_layer` instead of `_classify_layer`.
- `benchmarks/tocs/tocs_score.py` reads `.wingbox/index.sexp`, or `.code_graph.sexp` of the 0.2.4 build that run.sh scores as "before".

### Verification
- Full suite: 474 tests OK on Python 3.12 and 3.11.
- Graphs against main 0.5.1 built on the same copies: edges identical on immich (11,032), univer (68,252) and archestra (98,459); fastapi 2,813 -> 2,814, the one new edge an `importlib.import_module("docs_src.generate_clients.tutorial004")` literal of a test (the ToCS change).
- archestra acceptance (benchmarks/architecture/acceptance_archestra.py): all checks ok; backend upward imports 112 found, 112 in the project's baseline, 0 extra, 0 missing. boundaries: immich 0 violations (8 type-only, 15 candidates), fastapi and univer 0, archestra 59 violations + 92 known (frontend violations as before the rebase).

### Open
- The architecture commands (advice, align, place, learn, compare) read the graph as it is; the query commands refresh it first.
- A change of the JSON, YAML or TOML file beside a Python registry loader does not mark the graph stale.

## 2026-11-16 - learn asks about nested folders; a quiet apply

### Changes
- `wingbox learn`: nested folders whose files take part in rule breaks or rest on a role or synonym guess, at the deepest folder holding them, most breaks first, capped at 25 per part (`learn-more` counts the rest); example files leave tests out; a file -> module map computed once (the task on archestra took minutes, now 11 s). Answers may name folder paths (`lib/hooks`), validated against the part; `labels.py`: a folder path in the part's layers decides for the files below it, the longest path first. `--apply` silences the rebuild, Python streams and parser processes' descriptors, unless `--verbose`.
- Tests: REQ-LEARN-009, 010.

### Verification
- Full suite green on 3.11, 3.12, 3.13.
- archestra frontend: the task listed 25 folders (8 more counted). Answered after reading: five `app/.../_parts` folders that components import -> ui, `lib/hooks` and `lib/auth` -> state, `app/connection`, `app/settings`, `app/chat` unknown (they hold route files). Applied: frontend violations 59 -> 34, candidates 23 -> 16, type-only 34 -> 28; backend unchanged (92 known).

## 2026-11-16 - `wingbox learn`: the coding agent reads, Wingbox records

### Changes
- `scripts/architecture/agent_learn.py` (new): who runs it (flags, verified agent variables, terminals), the task facts and text, strict answer validation, `learned.sexp` lines and reading, the folder-changed rule. CLI: `wingbox learn` (agent task or human confirmation), `wingbox learn --apply [FILE]` (validate, write config with `learned by agent:`, keep user entries, learned.sexp, decisions, `.wingbox/.gitignore`, rebuild, counts). Library: layer meaning. Config: the agent comment.
- Docs: README, SKILL (`/wingbox learn`), CHANGELOG, `references/sexp-notation.md`. Tests: REQ-LEARN-001 to 008.

### Decisions
- Agent variables used: `CLAUDECODE=1` (seen in the environment of Claude Code's Bash tool) and `GEMINI_CLI=1` (set in gemini-cli-core's shellExecutionService for shell commands). Codex (`CODEX_THREAD_ID`, `CODEX_SANDBOX` appear in its binary) and Cursor (`CURSOR_AGENT` appears in its bundle) were not seen set for a command, so they are left to the terminal test.
- "Most files new" is read as: the folder has more than twice the files it had when learned.

### Verification
- Full suite green on 3.11, 3.12, 3.13.
- immich (fresh copy, nestjs 1.00): the task listed bin, commands, emails, workers with files and imports; answered after reading them: commands api, emails infra, workers module, bin unknown (build-time scripts); applied; violations 0 -> 0, candidates 15 -> 15; `place "a new REST endpoint for albums"` -> src/controllers.
- archestra frontend (route-tree 1.00): every top folder is placed by the style; the task says so and asks nothing; violations unchanged (59).

## 2026-11-16 - a feature named after tests is not a test folder

### Changes
- `detect.is_test_file`: a folder is a test folder when its name starts with test, tests, spec, specs or e2e; `openappa-policy-tests/` (a route of archestra) is not. REQ-ARCH-034 updated.

### Verification
- archestra after a cached rebuild (51 s): backend breaks of layers.mjs 112 of 112 again (the word rule had made one source a test file); full suite green on 3.11, 3.12, 3.13.

## 2026-11-16 - rebased on main 0.2.7; hook and MCP server read .wingbox/graph.sexp

### Changes
- Branch rebased on origin/main c74f890 (0.2.7): the CHANGELOG keeps main's versions and adds this branch under `## Unreleased`.
- `src/wingbox/hook.py`, `src/wingbox/mcp_server.py`, `src/wingbox/install.py`, README, SKILL: the graph is `.wingbox/graph.sexp` only (main added these with the old name as a second choice); the MCP server reads files relative to the folder above `.wingbox/`. Tests REQ-INSTALL-016, 019, 022 and REQ-MCP-004 follow the new location.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (300 tests).

## 2026-11-16 - cached rebuilds no longer resolve imports again (finding 12)

### Changes
- `scripts/build_code_graph.py`: `build_graph` resolves the import specifiers of a file once and passes them to `_imports_types_only`, which resolved every specifier again for each imported module (510,463 calls on univer). Test: REQ-CS-060.

### Motivation
- A rebuild with 0 changed files took longer than a cold build (archestra 93-128 s against 81 s; univer too). A profile of the cached univer rebuild: `build_graph` 79 s, of which `_imports_types_only` 49 s.

### Verification
- univer cached rebuild 46.3 s (before: 69.7 s); edge set identical (64,228). Cold builds parse in parallel, which is why they were faster than a cached rebuild that waited on the serial graph build.

## 2026-11-16 - monorepo packages, output for people (findings 7, 13)

### Changes
- CLI `init` in an empty package: joins the root `.wingbox/config.yaml` when the repository has one (level component under packages/), language from the manifest, `--yes` takes the first choice, text output unless `--sexp`. `init --details`; summary facts `arch-target-summary`, `advice-summary`. `align` text printer; `place` text printer, existing empty folders present; one `advice` line for parts without advice; advice items unique per id; "not read: it states no layer order"; tie note for undetermined parts; plain note outside git. README init example from a real run.
- Tests: REQ-NEW-006, REQ-CLI-024, REQ-ARCH-041.

### Verification
- Full suite green (259 tests).

## 2026-11-16 - learning by the style library; place; align and boundaries agree (findings 1, 3, 8, 11)

### Changes
- `scripts/architecture/learn.py` (new): closest style of the library as the base, unplaced folders derived by suffix, name (another style), imports. `align.layers_by_imports` replaces `learn_part` (no restatement reviews). CLI: `align --learn` prints `align-style`, `align-placed`, `align-learned :by`, writes the config only when the base or the learned folders change. Align counts breaks like `boundaries` (composition roots, type-only, candidate rules).
- `place.py`: layer words and file patterns scored with the placement texts, plural folding, prefix matching, ties to the layer's own words; `place_declared`. CLI `place`: declared parts, the part of the current folder, examples, `:where` for prose, `not stated`, a text printer.
- The build prints the architecture source; `architecture_plan` is quiet (place, align, compare no longer print a build line).
- Tests: REQ-ARCH-030 rewritten, REQ-ARCH-039, 040, REQ-CLI-019 updated, REQ-CLI-023.

### Motivation
- The maintainer: learning matches the library of architectures, takes the closest one, and derives the rest by names and patterns.

### Verification
- Full suite green (256 tests). immich (fresh copy): closest style nestjs 1.00 (runner-up layered 1.00, settled by nest-cli.json); placed by the style: api controllers, middleware; service services; infra migrations, repositories; domain dtos; shared utils, shared; derived: bin api (imports), commands, emails (imports), workers service (name in layered). `place "a new REST endpoint for albums"` answers src/controllers before and after learning.

## 2026-11-16 - findings of an independent run (1, 2, 4, 5, 6, 9, 10)

### Changes
- `decisions.py`: `build-run` lines, ignored logged once at the threshold (no per-build lines), accepted never ignored, `learned-removed`. `plan.apply_learned(from_config=True)` applies nothing; the CLI records learned folders the config does not list.
- `target.py`: the style's own folder first among the present folders of a layer; domain blocks only with a domain layer and domain code (new projects get the whole layout); snake_case block folders in Python, Rust, Go, Ruby, Elixir.
- `detect.is_test_file`: folder words test, tests, spec, specs, e2e. `labels.py`: shared/common inside a layer folder takes the enclosing layer. Search and overview: the type-only import decides the pair.
- Tests: REQ-ARCH-032 to 038, REQ-CLI-022; REQ-ARCH-027 updated to the new logging.

### Motivation
- An independent tester ran the branch as a coding agent would; these are its findings 1, 2, 4, 5, 6, 9 and 10.

### Verification
- Full suite green (253 tests).

## 2026-11-16 - the map stops trembling; a readable first selection

### Changes
- `src/wingbox/assets/map_template.html`: `settle(alpha)` (layers view: stop the simulation, tick it to rest, at most 300 ticks or alpha < 0.02, set alpha 0, fit, draw once; graph view: live settling as before), called by rebuild, setLayout and the hops change; `select` only redraws in the layers view. Ported from the wingbox-site fix b360e32. The first selection comes from `DATA.start`.
- `src/wingbox/map_page.py`: `start_module`: the most depended-on file whose 2-hop radius is under 5 % of the modules, else the widest radius (the former rule).
- Tests: REQ-OUT-017, REQ-OUT-018.

### Motivation
- The maintainer: the 3D demo "looks scary and trembles": every load, click and hops change restarted the force layout and 2,000+ files drifted for seconds; the first selection on archestra painted half the graph amber.

### Verification
- Full suite green on 3.11, 3.12, 3.13.
- Headless Chrome on the archestra map (7,980 modules), layers view, one page load: canvas checksum and node positions identical at 1, 2, 4, 6 and 8 s, with and without a selection. The map now opens on platform/backend/src/middleware/audit-log-hook.ts (128 direct dependents, 169 within 2 hops, 2 %).

## 2026-11-16 - README: "complete list" only with --all

### Changes
- `README.md`: the two sentences that say `impact` returns the complete list of dependents now say `--all`; without it, `impact` names at most 200 dependents and gives the complete counts.

### Motivation
- The coordinator: "complete list" must hold only with `--all`.

## 2026-11-16 - `wingbox compare`

### Changes
- `scripts/architecture/compare.py` (new); CLI `wingbox compare [--sexp]`. Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-CMP-001 (`tests/test_compare.py`).

### Verification
- Full suite green on 3.11, 3.12, 3.13.

## 2026-11-16 - init in an empty folder, $HOME defaults, monorepo siblings

### Changes
- `scripts/architecture/newproject.py` (new): language of a folder, choices in precedence order. Style files: `languages`. `library.py`: `$HOME/.wingbox/styles` overrides. `target.py`: a new project's source root is src/. `config.py`: target for a new part, `# chosen:` comment. CLI: the empty-folder path of `init` (`--language`, `--style`, `--yes`, `--scaffold`).
- Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-NEW-001 to 005 (`tests/test_new_project.py`).

### Decisions
- Precedence: declared rules > project config > siblings > `$HOME` favourite > library; the first two decide existing parts, so a new project's choices start at siblings. The `$HOME` favourite is used for new projects only.
- Without a terminal and without `--style`, init exits 2 after printing the choices.

### Verification
- Full suite green on 3.11, 3.12, 3.13.

## 2026-11-16 - `wingbox place`

### Changes
- `scripts/architecture/place.py` (new), `[[placement]]` read by the library, CLI `wingbox place "<what>" [--part PART] [--sexp]`. Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-ARCH-031 (`tests/test_place.py`), REQ-CLI-021.

### Decisions
- Matching is by shared words with the placement texts (plural folded, small words dropped); the vector table is not used here, in line with its benchmark.

### Verification
- Full suite green on 3.11, 3.12, 3.13.

## 2026-11-16 - `wingbox align`

### Changes
- `scripts/architecture/align.py` (new): gaps of a part, learning (unplaced folders placed by the dependency graph; review of restatements whose folder name states the current layer), baseline. `library.py`: `[from.<style>]` mappings. Search: `architecture_inputs` shared by advice and align; `violation-known` for pairs in a baseline, `:known` in the summary. Report: known breaks listed apart. Builder: reads `.wingbox/baseline.sexp`. CLI: `wingbox align [PART] [--to STYLE] [--learn] [--sexp]`.
- Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-ARCH-029, 030 (`tests/test_align.py`), REQ-CLI-019, 020; summaries now carry `:known`.

### Decisions
- A pair a baseline lists is not counted as a violation anywhere (boundaries, report, advice); it is printed as `violation-known`.
- Learning never restates a folder: a better layer for a mapped folder is only listed with `:needs-review`.
- MIN_LAYER_SHARE 5 %, a learned folder needs 2 files.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (236 tests).

## 2026-11-16 - decisions store and `wingbox advice`

### Changes
- `scripts/architecture/decisions.py` (new): read the log, reconcile a run (given, followed, done differently with a learned layer, ignored), user decisions, what is still shown and in which order, learned layers. `plan.apply_learned`: learned folders come before the style's words; labels say "learned from <id>", graph facts `:learned-from`.
- `src/wingbox/cli.py`: `wingbox build` reconciles the store and writes only the visible advice into REPORT.md and the map; `wingbox advice [--sexp] [--learned]`, `wingbox advice accept|reject <id> [--reason TEXT]`; `init` shows only visible advice.
- Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-ARCH-027, 028, REQ-CLI-018.

### Decisions
- The store is reconciled by `wingbox build` only (not by `init`), so that a build followed by an init does not count an ignored run twice.
- IGNORED_RUNS = 3.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (231 tests).

## 2026-11-16 - recommendations

### Changes
- `scripts/architecture/advice.py` (new): move, fix-violation and missing-folder advice with stable ids. Style files: a `fix` text per deny rule; declared parts get "move the imported code down, the importing code up, or invert". Graph facts `(arch-rank)`, `(arch-target)`, `:fix`. Search: `advice_items`, advice lines in `analyze` and `impact`. REPORT.md "Recommendations", map `advice`. `init`: advice per part (missing folders; move and fix from the last build when a graph exists).
- Docs: README, SKILL, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-ARCH-025, 026, REQ-CLI-017.

### Motivation
- The maintainer: Wingbox itself gives the recommendations, from the style files' rules and placement, each naming style, rule and evidence.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (228 tests). Counts on the four repositories: in the acceptance entry below.

## 2026-11-16 - target layout with the domain building blocks

### Changes
- `styles/index.toml`: `[domain_blocks]` (seven blocks with their meaning and source). Every style file: `[target]` (layer -> folder, further folders, where the domain blocks live, a note).
- `scripts/architecture/target.py` (new): present or proposed folders of a part. `init.py`: `(arch-target ...)` facts and a "target: N of M folders present; missing: ..." line. `config.py`: `target:` entries written and validated.
- Docs: README, CHANGELOG, `references/sexp-notation.md`. Tests: REQ-ARCH-023, 024; REQ-CLI-008 checks the target in the config and that nothing else is created.

### Motivation
- The maintainer asked that the system propose the full architecture of the domain tier at once; the coordinator fixed where each style keeps the blocks.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (225 tests).

## 2026-11-16 - word vectors off by default (`--synonyms`)

### Changes
- `words.py`: `DEFAULT_SETTINGS` has no synonym step, `SYNONYM_SETTINGS` has it. `wingbox init --synonyms`, `wingbox build --synonyms` (builder option) turn it on for the folder -> layer mapping; detection never uses it.
- README, SKILL, CHANGELOG, benchmarks/architecture/README.
- Tests: REQ-ARCH-022, REQ-CLI-016.

### Motivation
- The coordinator asked to measure vectors as a last step only and to ship them off unless they help. The benchmark's `+vectors` variant already was the last step (it only sees names that dictionaries and Levenshtein leave unmatched): unmatched 77.5 % -> 75.0 %, accuracy 76.2 % -> 75.4 % on the held-out half. It does not help, so it is off by default.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (223 tests). `benchmarks/architecture/run.sh` results unchanged.

## 2026-11-16 - false positives on NestJS entry points, utilities and route-tree frontends

### Changes
- `styles/index.toml`: `[composition_roots] imports` (NestFactory, nest-commander). `layered.toml`, `nestjs.toml`: the shared rules give candidates (`severity = "candidate"`), shared -> infra with the reason "utility depends on infrastructure; move it to services or invert". `route-tree.toml`: state file patterns, shared -> ui/state candidates.
- `labels.py`: composition root by import, CQRS words read as domain only with the CQRS overlay, private folders at the routing root are ui. `plan.py`, `config.py`: `overlays` per part (written to and read from `.wingbox/config.yaml`). `library.py`: rule severity, CQRS role words. Builder: the imported names of each file reach the labels. Search: `:severity candidate` rules give `violation-candidate`; a file nothing imports that imports a composition root is an entry point.
- Tests: REQ-ARCH-019 to 021, REQ-CLI-015.

### Motivation
- The maintainer's review of the immich and archestra rows: nest-commander commands read as CQRS commands, worker bootstraps importing the app module, utilities calling repositories (real, but a move or an inversion, not a layer break), app-wide private folders and query modules of a Next.js frontend.

### Verification
- Full suite green on 3.11, 3.12, 3.13 (221 tests); edge sets of the four copies unchanged.
- immich server: 0 violations, 8 type-only (each an `import` of a type: AssetStats, AuthRequest, PersonId, ArgOf/EmitEvent, ImmichReadStream, PluginMethodSearchResponse, the search repository types), 17 candidates (utils and services/shared importing repositories, read in the source).
- archestra frontend: of 20 sampled rows of 104, 13 real (a shared component or lib module imports code inside one route), 1 debatable (a lib hook imports a dialog component), 6 wrong (2 imports of app/_parts, 4 query or hook modules in lib read as utilities). After the fixes: 59 violations, 24 candidates, 14 type-only. Backend unchanged: 93 violations and 20 type-only, all in the project's baseline.

## 2026-11-16 - `wingbox init`, architecture styles per part, index files in .wingbox/

### Changes
- `scripts/architecture/` (new): `styles/*.toml` (the style library: index, layered, onion-clean, hexagonal, mvc, nestjs, microkernel, route-tree, feature-sliced, cqrs, c4, infra), `library.py` (typed TOML), `words.py` (normalise, Levenshtein moved here from the search script, word-vector synonyms), `detect.py` (parts, scores, decision, C4 level, declared rule files), `declared.py` (dependency-cruiser layer tables, known-exception baselines), `plan.py`, `config.py` (`.wingbox/config.yaml`), `labels.py` (layer + style + rule of a file), `facts.py` (graph facts), `init.py` (init facts and their text printer), `sexp.py`, `repository.py`, `vectors/` (GloVe table, 3 MB).
- `src/wingbox/cli.py`: `wingbox init`. `scripts/build_code_graph.py`: `.wingbox/graph.sexp`, `.wingbox/cache.json`, architecture plan (config or detection), `(arch-part)`, `(arch-deny)`, `(arch-known)`, `(layer ... :style :rule :match)` facts. `scripts/code_graph_search.py`: labels from the graph (a graph without architecture facts is read with the layered style), boundary rules per part, `:part :style :rule [:known]` on `boundaries`, `layers`, `analyze`; the old substring classifier is gone. `src/wingbox/overview.py`, `map_page.py`, the map template: style and rule of every label and violation, Architecture section.
- `benchmarks/architecture/` (new): labels, expected styles, path lists, `evaluate.py`, `run.sh`, `build_vectors.py`, results, README.
- Docs: README (init section, outputs, FAQ on vectors), SKILL command table, `references/sexp-notation.md`, `.gitignore`, `.dockerignore`, Dockerfile comment, CHANGELOG.
- Tests: REQ-ARCH-001 to 018, REQ-CLI-008 to 014; tests that used `.code_graph.sexp` or the removed classifier now use `.wingbox/` and the style labels.

### Motivation
- The maintainer agreed `wingbox init` and asked that layers and boundary rules come from the architecture style of each part, named with its source, after the 0.2 folder rules labelled a Next.js route folder `gateways` as infrastructure and read the plug-in folders of an editor monorepo (`commands`, `menu`) as backend layers (84 false rows). The style knowledge comes from the rule files written for the maintainer; a project that declares its own rules is judged by them.

### Verification
- Full suite green on Python 3.12, 3.11 and 3.13 (217 tests).
- Real repositories (copies, read-only; before = base commit 3399813, after = this branch, both `--full`): edge sets identical on all four (fastapi 2,474, immich server 11,008, univer 64,228, archestra 90,870).
- univer: 42 packages microkernel, 7 none, 1 undetermined; violations 84 -> 0.
- archestra: backend declared by `layers.mjs` (22 layers read), frontend route-tree, the rest none; `app/mcp/gateways/gateway-tools.ts` is route code (feature), `platform/shared/index.ts` other instead of config. Backend: 93 violations and 20 type-only crossings by the declared order, every one in the project's `layers.known-upward-imports.json`, and all 112 entries of that file found; of the 19 backend rows of 0.2.5, 2 are in that baseline. Frontend: 104 violations by the route-tree rules (shared components and lib importing route code, lib importing lib/hooks).
- immich server: detected nestjs (`nest-cli.json`); 29 violations and 8 type-only instead of 0 and 6: 18 utils -> repositories (rule "shared code imports a layer that uses it", new in the rule files), 9 nest-commander `*.command.ts` read as CQRS commands (domain) by nestjs.toml, worker bootstraps importing `app.module`. Migrations are infra. With `style: layered` the 0.2 rules give 1 violation (a worker bootstrap) and 6 type-only; the shared rule adds the rest.
- fastapi: none, no layers, 0 violations.
- `benchmarks/architecture/run.sh`: word vectors 75.4 % against 76.2 % for dictionaries + Levenshtein on the held-out half; detection 31 of 33 parts (30 with vectors).

## 2026-11-16 - modules loaded at run time, and the Theory of Code Space benchmark

### Changes
- `scripts/lang/python/dynamic.py` (new): literal `importlib.import_module` / `__import__` names, load patterns with one variable below a literal package, and method calls in a loop on objects a registry returns for the loop variable (fold when the result replaces an argument).
- `scripts/lang/config_values.py` (new): name-shaped string values of the JSON, YAML (without a YAML parser) and TOML files of one folder, in file order.
- `scripts/lang/interface.py`, `scripts/lang/python/parser.py`: `FileResult.module_loads`, `FileResult.registry_calls`.
- `scripts/build_code_graph.py`: `REGISTRY_WIRES` edges, made in `assemble_graph` with the facts that need every module (they read configuration files and other modules' entities, so a partial update recomputes them) (`registry-wires ... :config`), registry `CALLS` (`:registry`; `:lsp` only on LSP calls now), pipeline `DATA_FLOWS_TO` (`:pipeline`); cache schema 3.
- `scripts/code_graph_search.py`: reads `registry-wires`; a `data-flow :pipeline` stays out of `deps`, `rdeps` and `impact` (the next step depends on the previous one, not the reverse).
- `benchmarks/tocs/` (new): `run.sh`, `tocs_score.py`, `make_report.py`, `verify.py`, `scaffold_eval.py`, `SCAFFOLD.md`, `results/`, generated `README.md`.
- Docs: `references/sexp-notation.md`, `SKILL.md`, `README.md`, `CHANGELOG.md`.
- Tests: REQ-PY-001 to REQ-PY-005 (`tests/test_python_dynamic_loading.py`).

### Motivation
- On ToCS, 0.2.4 found every import and no other edge type (medium Dep F1 0.802): stages loaded by `importlib` from names in a configuration file, the runner's calls into them through the registry and the order of the pipeline were invisible. Plugin registries built the same way are common in real code, and `impact` of a plugin missed its loader.
- Direct calls of imported names were measured as a CALLS_API rule and rejected: of 407 such pairs in the 14 codebases, 273 are not CALLS_API in the ground truth (helpers, constructors, exceptions).

### Verification
- Full suite green on Python 3.12 and 3.11 (190 tests).
- ToCS, its own `edge_prf`: medium (paper's 3) 0.802 -> 0.953, precision 1.000; large 0.766 -> 0.869; 8 generated 0.790 -> 0.921. REGISTRY_WIRES 103 of 103 and DATA_FLOWS_TO 89 of 89 over the 14 codebases, none extra. `run.sh` reproduces the committed results (`verify.py`).
- Real code, 0.2.4 against this branch: fastapi gains one edge, a test that loads `docs_src/generate_clients/tutorial004.py` with a literal `importlib.import_module`; nanoGPT, minGPT and micrograd unchanged. The impact benchmark's import-statement reference does not hold that edge, so its edge test on fastapi will count it once.

## 2026-11-17 - Dart and Flutter language support

### Changes
- `scripts/lang/dart/` (new): a `dart` adapter with one result language. `parser.py` reads `.dart` files with tree-sitter (classes, mixins, `class A = B with M`, enums, extension types, extensions, type aliases, top-level functions and getters, the members of each type, and the `extends`/`with`/`implements`/`on` bases) and collects the `import`, `export`, `part` and `part of` URIs, conditional imports counted once; `imports.py` binds a library URI to a file.
- `imports.py` reads the top-level `name:` of every `pubspec.yaml` (no YAML parser: the first column-0 `name:` pair) and orders the packages deepest first. `package:<name>/path.dart` resolves to `lib/path.dart` of the nearest pubspec that declares `name`, then to any package of that name in the repository; a relative URI resolves next to the importing file; `dart:` and a `package:` name no local pubspec declares create no edge. `part` and `part of` follow the same three rules, and a URI whose file does not exist resolves to nothing without an error.
- `scripts/lang/discovery.py` (`DART_OUTPUT_DIRECTORIES`): `.dart_tool`, which `dart pub get` fills with the package config, kernel snapshots and generated sources, is excluded by its name everywhere. Flutter's compiled output goes to `build`, already a default skip folder like `vendor` or `dist`.
- Wiring: `DART_LANGUAGE` in the registry, `pubspec.yaml` as a project config, module IDs without the `.dart` extension, `dart language-server` as the LSP command.
- `requirements/dart.txt` (`tree-sitter==0.25.2`, `tree-sitter-dart==0.1.0`) and the package dependency; README and SKILL.md document the install line, the language, the resolution rules and the limitations; `benchmarks/dart/` holds the accuracy check.

### Decisions
- The grammar is `tree-sitter-dart==0.1.0`, the only Dart grammar on PyPI: no Dart SDK, analyzer or `dart pub get` is needed, and a build stays local and deterministic.
- Resolution follows the language's own rules rather than an import graph: Dart names libraries, not files, so `package:` URIs resolve through pubspec names and relative URIs through paths. The nearest pubspec wins, which is what makes a monorepo of packages link to itself and not to a lookalike name.
- A `pubspec.yaml` name is read with a regex instead of a YAML parser: the adapter needs one key, and PyYAML stays reserved for the infrastructure topology.
- A missing file behind a directive is silent, because `build_runner` generates parts (`*.g.dart`, `*.freezed.dart`) after a clone and a graph that needed the codegen step would fail on every fresh checkout. A generated part that exists on disk is indexed like any other file.
- An `import` or `export` with `if (...)` alternatives contributes its first URI only: the alternatives are platform variants of one library, not extra modules. A `part` into a `.../codegen/` folder of the macros proposal is left out, because on disk that folder holds a copy of the library rather than another module.
- Flutter: widgets are ordinary classes, so nothing in the adapter is Flutter-specific. The only Flutter knowledge is the `build/` folder it writes, which the default skip list already covers.
- Runtime wiring (a `get_it` registration, a `go_router` route table, a `retrofit` client) is not visible to static analysis and is documented as a limitation, not a defect: these bindings exist only after the application runs.

### Motivation
- The maintainer asked for Dart and Flutter support. A Flutter application is layered by feature folder rather than by package, so the question Wingbox answers there is what a change to a model, a widget or a repository breaks, and an import-only view of that is useful only if `package:` and relative imports both resolve.

### Verification
- Fixtures built in-process: package and relative imports, the nearest pubspec among several, part and part of in both directions, a `part of` with a package URI, generated parts on disk and missing, and discovery of `.dart_tool` and Flutter `build` output. `benchmarks/dart/` checks the accuracy of the graph.
- `python3 tools/sync_plugin.py` then `--check` exit 0; `tests.test_plugins` and `tests.test_local_only` pass.

## 2026-11-17 - CodeGraph in the comparison benchmark

### Changes
- `benchmarks/graphify`: CodeGraph 1.6.2 (npm `@colbymchenry/codegraph`) as a third tool on the same repositories, commits, questions, reference lists and scoring. New `tools/cg_graph.js` (CodeGraph's file graph and file-dependents walk through its own library API), `tools/cg_command_check.py` (test C), `pairs.py codegraph` (test B), a `cg` setup in `tools/agent_test.py` (test D), `tools/time_builds_same_machine.py` (three tools on one machine, cold and after a one-line edit; not run yet). `run.sh` installs CodeGraph from npm into the work directory and runs it with a private HOME, no daemon and no file watcher. `make_report.py` writes the three-tool tables.
- Results added: `command_check.codegraph.json`, `{karpathy,fastapi}.cg.{all,imports}.score.all.json`, `fastapi.cg.all.score.fastapi.json`, CodeGraph trials in `agent_summary.json`, `codegraph` lines in `versions.txt`. Wingbox and Graphify result files are unchanged except that `agent_summary.json` gains the 15 CodeGraph trials (the plain/wb/gf summary is recomputed equal).
- Test: REQ-BENCH-010 (same-machine build summary); REQ-BENCH-008 covers the regenerated README.

### Motivation
- The maintainer asked to add CodeGraph to the comparison: people ask how it compares.

### Verification
- `run.sh` from an empty work directory reproduces every CodeGraph result file byte for byte.
- `python -m unittest tests.test_benchmark_report`: 10 tests OK.
- The CodeGraph agent trials ran on 2026-11-17, a day after the other setups and not interleaved with them; 15 trials, 1.05 USD.
- Build time of CodeGraph not measured: the machine was under heavy load (1-minute load up to 225); to be run on a quiet machine.

### 2026-11-17, later: Wingbox 0.5.1 and the "why" sections
- The Wingbox column is now 0.5.1 (PyPI), measured by the same scripts: `run.sh` pins `wingbox==0.5.1` (it pinned 0.2.0 while the tools already read the 0.5.x layout, so it did not run), and 15 new Wingbox agent trials replace the 0.2.0 ones. Test C and test B result files are byte-identical to 0.2.0; test A moves (fastapi `analyze` mean 624 -> 527 tokens, `impact` mean 261 -> 141); agent 25,598 -> 23,768 tokens, 0.016 -> 0.021 USD per answer, F1 1.000 both. Test E (hosted runner) stays 0.2.0 and says so.
- `make_report.py` writes "Which tool for which job" (numbers from the results, documentation points labelled) and "Why the numbers differ" (each tool's design, from its own docs). Maintainer: the page answers why to choose Wingbox, without judging the other tools.
- Graphify and CodeGraph result files are unchanged by the 0.5.1 run (compared byte for byte).

## 2026-11-17 - .wingbox/ holds everything and marks the project; a graph is never refreshed into another folder

### Changes
- `scripts/build_code_graph.py`: `wingbox_directory`, `index_path`, `cache_path`; the build writes `.wingbox/index.sexp`, `index.uses.sexp`, `index.uses-cache.json`, `cache.json` and `.wingbox/.gitignore` (rewritten only while it is Wingbox's own text); `--output` removed; the snapshot records `root`; `keep_published` (set by the refresh) refuses to publish an empty graph over one with modules.
- `src/wingbox/freshness.py`: `graph_path`, `report_path`, `map_path`, `find_project` (nearest folder with `.wingbox/index.sexp`), `no_project_message`; `ensure_graph_fresh` returns `Unconfirmed(moved|no-snapshot)` and starts no refresh when the snapshot's root is not the folder; the hook gives the context with the note, MCP and queries print it.
- `scripts/code_graph_search.py`: finds `.wingbox/index.sexp` from the current folder or `--root`, a graph's root is the folder holding its `.wingbox/`; the no-graph error names the paths and `--map`/`--root`; `search` prints `(result-count N)`.
- `src/wingbox/cli.py`: `BUILD_USAGE` for `wingbox build --help`; `wingbox open`; REPORT.md and map.html in `.wingbox/`. `src/wingbox/update.py`: old-layout warnings (`! WARNING: ...`, yellow on a terminal without NO_COLOR, `(update-warning ...)` with --sexp).
- hook.py, mcp_server.py, install.py block, SKILL.md and plugin copy, README, references, store docs, Dockerfile, .dockerignore, .gitignore, map-request workflow, benchmark readers: the new paths.
- Tests: REQ-FRESH-029 to 034, REQ-CLI-008, 009, REQ-CS-074, REQ-UPDATE-008, 009; REQ-FRESH-027, REQ-INSTALL-019, REQ-CS-026 rewritten for the new contract; fixtures moved under `.wingbox/`.

### Motivation
- A real session: `wingbox build --root P --output O/graph.sexp`, the graph copied to `O/.code_graph.sexp`, `wingbox impact` from O answered `(error no-match ...)` and the refresh had replaced the graph by an empty build of O. Also `--output` still wrote the cache and `wingbox-out/` into P, `wingbox build --help` printed the builder script's usage, `wingbox search` printed nothing on no match, and the agent had to guess where the graph lives.
- Maintainer: everything under `.wingbox/`, `.wingbox/` is the project marker, no compatibility and nothing deleted; `wingbox update` warns about old files.

### Verification
- The repro before: graph 544 -> 210 bytes, `refreshes_build 1, files 0`; after: the moved graph answers as it is with the rebuild note, its bytes unchanged, no refresh log.
- Suite on 3.12 and 3.11; plugin validation; sync_plugin --check.

## 2026-11-16 - PHP 8 and Laravel (Blade, Livewire) language support

### Changes
- `scripts/lang/php/` (new): one `php` adapter with two result languages. `parser.py` reads PHP 8 with tree-sitter (classes, interfaces, traits, enums, methods, attributes, `use` aliases and grouped uses, `extends`/`implements`/trait use, Eloquent associations, `event(new X)`, `dispatch(new X)`, `X::dispatch()`, `view()`), `blade.py` scans `@extends`, `@include*`, `@component`, `@each`, `<x-*>` and `<livewire:*>`/`@livewire(...)`, and `imports.py` with `psr4.py` binds PSR-4 classes, views and Livewire aliases to files.
- `psr4.py` merges `autoload.psr-4` and `autoload-dev.psr-4` from every `composer.json`, the root one and the nested module ones, longest prefix first. View namespaces come from `loadViewsFrom`/`View::addNamespace` and the module `resources/views` convention; Livewire aliases from `Livewire::component()` and flat and dotted conventions.
- Wiring: `PHP_LANGUAGE` after Ruby, `.blade.php` before `.php` in the node-ID and search extension lists, `index.blade.php` as a folder index, `BUILDER_CONFIG_VERSION` `2026-11-16-php`, PHP/Blade purpose rules and layer patterns, PHPUnit/Pest test-file names, `.blade` stripped by `detect_naming`.
- `requirements/php.txt` (`tree-sitter==0.25.2`, `tree-sitter-php==0.24.1`) and the package dependency; README and SKILL.md document the install line, the language, and the PSR-4, Blade, Livewire and Eloquent edges; `references/sexp-notation.md` gains the `php` and `blade` `:lang` values and the new relation flags.

### Decisions
- Conservative resolution: an unresolved or ambiguous class name, alias, view or Livewire component creates no edge. When several files declare one fully qualified name, PSR-4 decides only if exactly one of those files is the file Composer would autoload; otherwise the name stays unlinked.
- Layers: `app/Models` domain; controllers, middleware and requests under `app/Http` api; `app/Livewire` and `resources/views` ui; `app/Jobs`, `app/Listeners` and `app/Policies` service; `app/Events` domain; tests test.
- Blade is scanned rather than parsed with a grammar (none is on PyPI); the scanner skips `{{-- ... --}}` comments and `@verbatim` blocks.
- Nothing boots Laravel, runs Composer or opens a network connection: the graph comes from source text and `composer.json`.

### Motivation
- The maintainer asked for TALL stack support (Tailwind, Alpine, Laravel, Livewire). Laravel code names classes through Composer, layouts through Blade and components through Livewire, so an import-only graph would miss most dependencies of a feature.
- IDE-helper finding: `_ide_helper_models.php` repeats model declarations from `app/Models`, which made every repeated class name ambiguous in a first index. Resolving duplicates through the PSR-4 file that Composer would load keeps the real model and lets the stub shadow nothing; that rule is now the documented default for any duplicate declaration.

### Verification
- A Laravel-shaped fixture built in-process: controller to model, job, event, `view()` and namespaced `admin::nav`; Blade to layout, include, component and Livewire; Livewire class to its template; `Livewire::component()` registration and a dotted alias.
- `python3 tools/sync_plugin.py` then `--check` exit 0; `tests.test_plugins` and `tests.test_install` pass.

## 2026-11-16 - the graph follows the code: dirty marks, outside-change check, partial update

### Changes
- `src/wingbox/freshness.py`: `ensure_graph_fresh(root)`, used by the search hook, every MCP tool and the query commands. State in `.wingbox/graph-state.json` (schema, dirty, generation, changed files with the generation of their last mark, the last failure, counters), written atomically under an `fcntl.flock` lock (released by the kernel when its process dies). Changes made outside Claude Code: one stat per source, resolution config and folder against `.wingbox/graph-snapshot.json`, which the build writes during discovery; a folder whose time changed is listed for new files. `graph_path(root)` is the one place that names the graph.
- `wingbox hook dirty` (PostToolUse on Edit, Write, MultiEdit): marks the file and returns; a failed tool call marks only when the file differs from the snapshot.
- `src/wingbox/refresh.py` (`python -m wingbox.refresh ROOT`, detached, output in `.wingbox/refresh.log`): under the refresh lock and the builder's own lock, records outside changes, patches the graph or runs the incremental build, then clears the marks up to the generation it started with; a failure keeps them and is not retried until a new mark.
- `scripts/graph_update.py`: the partial update. `scripts/build_code_graph.py`: `build_graph` split into `graph_context`, `module_fragment` (one module's node and edges, its resolved uses, and what it read: modules, module names, provider names, unresolved method names) and `assemble_graph`; the fragment store `.wingbox/graph-fragments.json`; modules resolved in forked processes (`jobs`). `scripts/lang/symbols.py`: the symbol index records what each memoised answer read. `scripts/lang/typescript/imports.py`: one walk for the three config loaders. `scripts/git_ignore.py`: `git_ignored_among` for created files.
- `src/wingbox/install.py`, the plugin manifest: the PostToolUse group; the search hook timeout 5 -> 10 s.
- `scripts/build_code_graph.py`: `.wingbox/` and `wingbox-out/` join SKIP_DIRS: a refresh log the repository ignores (`*.log`) entered the ignore set, changed the cache fingerprint and made the next build parse every file again (seen on the monorepo: 19 s -> 38 s).
- Tests: REQ-FRESH-001 to REQ-FRESH-028.

### Motivation
- Maintainer's spec (16/11): after an edit the hook and MCP kept answering from the old graph; never present a stale graph as fresh. Then: "can it be faster, with a partial build?": a real partial update, byte for byte the graph of a build, and a parallel resolve phase.

### Verification
- Byte for byte: a partial update after an edit that adds an import publishes the same `.code_graph.sexp` and `.code_graph.uses.sexp` as a full build, on fastapi (1,227 files, 24 modules redone) and on a 6,834-file TypeScript monorepo (12 redone); the tests compare graph, use file, cache and fragment store for edit, create, delete, rename, export change through a barrel, ignored created file.
- Wall time, interleaved before/after, load average 20 to 55 (a shared machine; absolute numbers are inflated, the ratios hold): fastapi search hook without change 0.20 -> 0.24 s, edit hook 0.21 s, outside-change check 0.02 s for 1,666 entries, one-file patch 1.0 s (hook including it 1.9 s), incremental build after one edit 11.2 -> 4.4 s, without changes 11.1 -> 3.3 s, cold 24.1 -> 7.0 s. Monorepo: search hook 0.30 -> 0.38 to 0.52 s (0.17 s stat check of 9,748 entries), edit hook 0.26 s, one-file patch 9 to 13 s, incremental after one edit 23.0 -> 25.9 s, without changes 20 to 23 -> 20 s, cold 44 -> 47 s.
- Profile of the monorepo: the resolve phase is 3.0 s single / 1.4 s with 16 processes (6.8 / 5.3 s with uses) of a 20 s build; the build is spent in discovery, Git ignore rules, hashing and rendering, so parallel resolution does not move it. The patch is spent reading and writing the 35 MB cache and 39 MB fragment store and walking the TypeScript configs.
- N (MAX_CHANGED_FILES 200, MAX_AFFECTED_SHARE 0.25): fastapi patch 0.58 / 1.07 / 1.37 / 3.34 s for 1 / 10 / 50 / 150 files (1 / 30 / 73 / 234 modules redone) against 3.5 / 3.5 / 4.1 / 5.6 s for the incremental build; the patch stays ahead up to about a fifth of the modules.
- Headless Claude Code 2.1.325 with the plugin from this branch (`--plugin-dir`): Edit adds `from shop.notes import note`, the PostToolUse hook marks the file, the Grep context then says `(module shop/notes :dependents 1)` (0 before), refresh log "partial in 0.03s".
- Suite on 3.12 and 3.11.

## 2026-11-16 - usages after a change: walk and resolve only what changed

### Changes
- `scripts/build_code_graph.py`: `.code_graph.uses-cache.json` next to the graph keeps, per file, its content hash, its full use walk (as JSON text, decoded only when the file is resolved again) and its rendered lines of the use file, with a digest of the surface (every path, and the definitions and bindings of the files with a use index) and the import lookups. `write_use_positions` walks only files whose hash is not in the cache (in this process when they are few), and while the surface is the same resolves only those and reuses the rendered lines of the others; it returns how many files it walked, reused and resolved. A file whose modification time is the one the build saw is not hashed again. Resolvers (tsconfig, packages, ignore rules) are created only on an import lookup the cache does not hold. `wingbox build --full` removes the walk cache.
- `scripts/code_graph_search.py`: the first pass of the loader stops once the path section is read; `CodeGraph.load(..., focus=name)` reads only the entity and edge lines that mention the name (besides paths and nodes); `usages` and `entity` with a literal name use it.
- `references/sexp-notation.md` (and the plugin copy), README, `.gitignore`: the walk cache.
- Tests: REQ-CS-070 to REQ-CS-073.

### Motivation
- Maintainer: the first `usages` on a large repository took about a minute and later calls about 10 s; target a few seconds after a build without changes or with one changed file, and find the share of graph loading in later calls.

### Verification
- Same machine, 0.3.0 (PyPI) and this tree one after the other, load 3 to 7 (`wingbox build`, then `usages`; one file changed by appending a comment):
  - univer: later call 3.93 s -> 0.88 s (graph load alone 3.23 s, 82 % of it -> 1.56 s full, 0.27 s focused); first call after a build without changes 4.14 -> 0.88 s; after a one-file change 26.14 -> 3.76 s; first fill of all positions 22.9 -> 24.9 s (it also writes the walk cache).
  - archestra: later call 5.42 -> 0.75 s (graph load 4.98 s -> 2.48 full, 0.34 focused); after a build without changes 5.36 -> 0.70 s; after a one-file change 39.13 -> 5.07 s; first fill 39.3 -> 48.3 s.
  - Walk cache on univer: 51.6 MB.
- Refactoring benchmark: entity 40/40, 40/40, 20/20 exact; usages files 100/100, positions as before (Python 234/234, Rust 587/587, TypeScript 741/741 plus the 106 `new C()` sites); impact lists unchanged; 711-symbol check unchanged. Suite on 3.12 and 3.11.

## 2026-11-16 - the use index at the cost of a build without it

### Changes
- Measured first (wall and CPU, 3 rounds, variants interleaved so that each round sees the same load, load average beside each build), then profiled. The cost was in three places: the TypeScript use walk (half the parse time: every node visited in Python), a build that resolved every use again with no change, and positions and re-export refs in `.code_graph.sexp` (2.3x on immich-server).
- `scripts/lang/*/symbols.py`, `parser.py`: two levels of use extraction. A build keeps only the uses that can name another file (import and export statements, chains and paths headed by an imported module or namespace, names a glob import may provide); `python_use_positions`, `typescript_use_positions`, `rust_use_positions` give the full list. The TypeScript walk is a tree-sitter query (names and method calls found in C); only names the module binds get scope work, and scopes are read lazily from the ancestors: a `let`, `const` or class shadows an import only in its block, a `var` in its function.
- `scripts/build_code_graph.py`: `.code_graph.uses.sexp` next to the graph holds what only entity and usages read: `var` definitions (Python module-level variables, kind `variable`), the (file, definition) pairs, and after the first query that needs them every use position (`write_use_positions`: the full walk in parallel from the build cache, then resolution). `:reexport` refs and variables leave `.code_graph.sexp`. A build whose inputs (each file's content hash, the builder, the mode) match the use file's key takes the refs edges from it and resolves nothing.
- `scripts/lang/symbols.py`: lookups that start afresh are memoised; `scripts/lang/*/imports.py`: `resolve_import` answers each (specifier, file) pair once per build (immich-server asked 91,191 times for 707 files; 0.2.6 did the same).
- `scripts/code_graph_search.py`: the loader reads the use file only when a query needs it, ignores it with a warning when it does not belong to the graph, has the builder fill positions on first need, and answers `usages` from the lines that mention the name. `usages <file>::X` follows a file that only re-exports X to the definition.
- Tests: REQ-CS-068, REQ-CS-069, REQ-TS-SYM-004.

### Motivation
- Maintainer: the price of usages is too high (graph about 2x, build 1.5 to 2.2x slower); build time and the main graph within about 10 to 15 % of 0.2.6, usages still 100/100, impact unchanged; also the two open gaps (TS block scope, re-exporting file as the target).

### Verification
- Interleaved, medians of 3, load 13 to 30 (`perf2.py`): immich-server cold 6.64 s wall / 25.9 s CPU (0.2.6) -> 4.79 / 25.6 (branch before: 10.29 / 39.6), warm 4.65 / 4.53 -> 2.55 / 2.51 (6.29 / 6.22); fastapi cold 24.95 / 42.0 -> 11.71 / 42.0 (30.62 / 63.7), warm 14.55 / 14.20 -> 6.51 / 6.41 (18.09 / 17.62).
- `.code_graph.sexp`: immich-server 612,199 -> 628,101 bytes (+2.6 %), fastapi 542,272 -> 553,527 (+2.1 %); the branch before had 1,381,206 and 955,943. `.code_graph.uses.sexp` after the build: 198,512 and 91,971 bytes.
- First `usages` query (fills positions): immich-server 6.4 s wall, fastapi 5.2 s; the next one 1.2 s and 0.5 s.
- Refactoring benchmark: entity 40/40, 40/40, 20/20 exact; usages files 100/100, positions as before (Python 234/234, Rust 587/587, TypeScript 741 of 741 plus the 106 `new C()` sites); impact lists unchanged; 711-symbol check unchanged. Suite on 3.12 and 3.11.

## 2026-11-16 - TypeScript imports with a Vite query suffix

### Changes
- `scripts/lang/typescript/imports.py` (`_without_query`): a specifier loses a `?query` or `#hash` suffix before it is resolved (relative, aliased or a package); a leading `#` (a package subpath import) is kept. A stripped path that does not exist stays unresolved.
- Tests: REQ-TS-VITE-001.

### Motivation
- Found on a public Vite repository against an independent resolver: `import("./codeHighlight.worker?worker&inline")` and `./annotate.ts?api-404-guard` in two tests made no edge.

### Verification
- Refactoring benchmark TypeScript 40/40; impact lists unchanged; suite on 3.12 and 3.11.

## 2026-11-16 - wingbox usages: every use of one symbol, with positions and kinds

### Changes
- `scripts/code_graph_search.py`: `cmd_usages` and the `usages` command (`Name`, `<file or module>::Name`, `<...>::Class.method`; `--sexp`, `--all`, 200 uses by default); facts `usages-target`, `usage`, `usage-file` (a language without use positions), `usages-more`, `usages-summary`, `usages-ambiguous`, `usages-candidate`, the text being their pretty print. The loader keeps `use` and `unresolved-call` lines unparsed until a query reads them; a position may carry a kind letter.
- `scripts/lang/*/symbols.py`: the kind of each use: import, re-export (Rust `pub use`, TypeScript `export ... from` and `export { k }`, an import in a Python package `__init__.py`), call (also `new C()`), type, extends (extends, implements, `impl Trait for`); a resolved method call is a call. Rust: the `if` guard of a match arm is an expression, not a pattern (it hid three uses of a function and shadowed it for the rest of the function).
- `scripts/build_code_graph.py`: `(use ... <line>:<col><kind letter> ...)`; `(unresolved-call <module> <method> <line>:<col> ...)` for unresolved method calls whose method name a class of the repository defines.
- `src/wingbox/mcp_server.py`: the `usages` tool (text).
- README, SKILL.md, `references/sexp-notation.md`: the command, the tool and the facts.
- Tests: REQ-CS-062 to REQ-CS-067, REQ-MCP-011, REQ-RUST-019.

### Motivation
- Maintainer: a separate `usages` command instead of a symbol-level `impact`, which stays file-level and unchanged.

### Verification
- Refactoring benchmark through `usages <file>::<S> --sexp --all`: files exact in 40/40 TypeScript, 40/40 Rust, 20/20 Python questions. Positions in other files against the SCIP index of the same engines: Python 234 of 234, Rust 587 of 587 (584 before the match-guard fix), TypeScript 741 of 741 found plus 106 more, all `new C(...)` sites of a class with its own constructor, which SCIP files under the constructor symbol and an editor's find usages on the class lists.
- `impact` lists of the five graphify questions unchanged; 711-symbol check: no new file outside the reference.
- Graph size with kinds and unresolved call sites: immich-server 614,320 -> 1,383,327 bytes (the `unresolved-call` lines are 193,532 of it), fastapi 537,364 -> 951,035 (35,134).
- Suite on 3.12 and 3.11.

## 2026-11-16 - method calls: resolved only with the receiver's type in sight (step 5 of the symbol index)

### Changes
- `scripts/lang/interface.py`: `UsePayload.member`, a method call with the receiver's type path (empty when no type is in sight).
- `scripts/lang/python/symbols.py`, `scripts/lang/typescript/symbols.py`, `scripts/lang/rust/symbols.py`: method call sites; the receiver's type from `self`/`this` of a class at module level (Rust: the type of the impl), a parameter or variable annotation, a constructor (`C()`, `new C()`, `C { .. }`, `C(..)`, a unit struct). A name bound in two ways has no type. Calls on module-level names and namespaces stay paths; calls on literals are not counted.
- `scripts/lang/symbols.py`: `resolve_member` resolves the type, then the method in the class or, through its bases, an ancestor.
- `scripts/build_code_graph.py`: resolved calls are `use` facts of `<Class>.<method>` (no refs edge: the class has its own); `(unresolved <module> :method-calls N)` per module.
- `scripts/code_graph_search.py`: the loader reads `unresolved`; `entity C --positions` also lists the resolved calls of C's methods.
- Tests: REQ-LANG-011, REQ-LANG-012.

### Motivation
- Maintainer, step 5: a method call whose receiver type is not visible is recorded as unresolved, never guessed; report how many such sites each repository has.

### Verification
- Method calls, resolved / unresolved: agent-browser 641 / 30,992 (2 %), chrome-devtools-mcp 890 / 9,546 (9 %), claudes-c-compiler 5,202 / 37,960 (12 %), fastapi 985 / 7,509 (12 %), immich-server 2,674 / 32,817 (8 %). Most unresolved calls are on values of the standard library or of dependencies (`.unwrap()`, `.map()`, `this.repository.find()`) or on fields whose type the walk does not follow.
- Graph size: immich-server 1,169,369 bytes, fastapi 906,640.
- Refactoring benchmark 40/40, 40/40, 20/20 exact; impact unchanged; suite on 3.12 and 3.11.

## 2026-11-16 - positions of every use (step 4 of the symbol index)

### Changes
- `scripts/build_code_graph.py`: the resolved use sites of each file are kept on its node and written as `(use <module> <defining-module>::<S> <line>:<col> ...)`, one line per file and definition, in a `; --- uses ---` section.
- `scripts/code_graph_search.py`: `entity S --positions` prints `(use <module> <line> <col> <defining-module>::<S> :file <path>)` for every use, sorted by file, line and column; the loader keeps the use lines unparsed until a query asks for them.
- `scripts/lang/python/symbols.py`: binding a name (`x = ...`, `del x`) is not a use of another definition.
- `references/sexp-notation.md`, `SKILL.md`: the `use` fact and the `--positions` flag.
- Tests: REQ-CS-060, REQ-CS-061.

### Motivation
- Maintainer, step 4: each resolved use stored with line and column in the graph, `entity S` printing every line:column with a flag, the graph size kept in check.

### Verification
- Graph size, 0.2.5 -> this tree, all uses (also inside the defining file): immich-server 614,320 -> 1,086,605 bytes (1.77x), fastapi 537,364 -> 866,642 (1.61x), agent-browser 327,336 -> 694,854 (2.12x), chrome-devtools-mcp 203,123 -> 355,817 (1.75x), claudes-c-compiler 271,685 -> 915,376 (3.37x; glob imports of encoder modules). One line per use was 1,836,359 bytes on immich-server (2.99x) and 1,138,566 on fastapi (2.12x).
- Graph load in process (median of 7, machine load 10 to 30): immich-server 700 -> 732 ms and 809 -> 810 ms in two rounds, claudes-c-compiler 314 -> 466 ms and 508 -> 631 ms. Parsing the use lines at load cost about 0.5 s per query before they were kept unparsed.
- Refactoring benchmark: 40/40, 40/40, 20/20 exact; impact unchanged; suite on 3.12 and 3.11.

## 2026-11-16 - use sites through scopes (step 3 of the symbol index)

### Changes
- `scripts/lang/typescript/parser.py`: use sites of every name the file binds at module level (its declarations and in-scope imports), from the scoped walk of `scripts/lang/typescript/symbols.py`: identifiers, member chains (`ns.S`, `Enum.A`), qualified types, JSX element names, shorthand properties and local `export { k }`. Parameters, variables, catch parameters, type parameters and declarations inside a function shadow a module-level name for the whole function. Python and Rust have had their scoped walks since the first change of this branch.
- Tests: REQ-TS-SYM-002, REQ-TS-SYM-003.

### Motivation
- Maintainer, step 3: every identifier and qualified path resolved through scopes; a local binding with the same name shadows the import.

### Verification
- Refactoring benchmark: TypeScript 40/40, Rust 40/40, Python 20/20 exact, no file outside the lists; impact unchanged; the 711-symbol check gives the same single pre-existing TypeScript file. New refs edges: immich-server 5,890 -> 5,914, chrome-devtools-mcp 1,364 -> 1,397; a sample read against the source was right each time (generator functions newly in the symbol table, `export { ConfigSmtpDto as SystemConfigSmtpDto }`, `export type {TestScenario}` re-exported from another file).

## 2026-11-16 - name resolution to the defining file (step 2 of the symbol index)

### Changes
- `scripts/lang/symbols.py`: TypeScript, TSX and JavaScript files take part; a TypeScript module has no child modules (`ns.X` is a name, never a file); a binding with module "" names the file itself (`export { k as K }`, `export default k`); a name in the file's scope comes only from bindings with `scope` (a re-export binds nothing locally).
- `scripts/build_code_graph.py`: the resolver maps a result language (tsx, javascript) to its bound language.
- `scripts/lang/typescript/parser.py`: use sites of the names in import and export statements.
- Tests: REQ-TS-SYM-001, REQ-LANG-010 (cycles in TypeScript, Python and Rust).

### Motivation
- Maintainer, step 2: resolve names to the defining file through named and aliased imports, globs, namespace imports and re-export chains, with cycle protection.

### Verification
- Refactoring benchmark: TypeScript 40/40, Rust 40/40, Python 20/20 exact, no file outside the lists; impact lists of the five graphify questions unchanged; over the 711 symbols the reference was asked, one file outside its list (TypeScript, already named by 0.2.5: a script that imports `../out/Default/gen/src/...`, which Wingbox maps to the source on purpose and the unbuilt reference does not resolve).

## 2026-11-16 - symbol table per file (step 1 of the symbol index)

### Changes
- `scripts/lang/interface.py`: `BindingPayload.scope` tells a binding in the file's scope from a re-export that binds nothing locally (TypeScript `export ... from`); module "" names the file itself.
- `scripts/lang/typescript/parser.py`: entities for `abstract class`, `function*`, `var`, `let`/`const` without a value, `declare` statements (unwrapped) and namespaces (kind `module`); enum members listed as the enum's methods. An abstract class has no bases in its entity, so interface edges and module dependencies stay as they were.
- `scripts/lang/typescript/symbols.py` (new): bindings of named, aliased, default, namespace and `import x = require()` imports, and of `export { X as Y } from`, `export * from`, `export * as ns from`, `export default <name>`.
- Tests: REQ-LANG-008, REQ-LANG-009.

### Motivation
- Maintainer: build the full symbol index on the tree-sitter and `ast` walk Wingbox already does; step 1 is a symbol table per file with every top-level definition and what the file re-exports.

### Verification
- Refactoring benchmark: TypeScript 40/40 exact, Rust 40/40, Python 20/20 (resolution unchanged for Python and Rust); suite on 3.12 and 3.11.

## 2026-11-16 - entity S: every file that uses S in Python and Rust

### Changes
- `scripts/lang/interface.py`: `FileResult.bindings` (module-level import bindings, with `*` for glob imports) and `FileResult.uses` (use sites: a name or a qualified path as written, with the line and column of each segment); both are kept in the parse cache.
- `scripts/lang/python/symbols.py`: module-level variables (assignment, annotated assignment with a value, `Final`, `TypeAlias`, `type X = ...`, tuple targets, also inside module-level `if`/`try`/`with`) that are public or listed in `__all__` become `function` entities, the kind TypeScript consts and Rust const/static already have; import bindings; use sites with Python scoping (parameters, locals, comprehension targets and class bodies shadow module-level names; `global` is honoured).
- `scripts/lang/rust/symbols.py`: `use` bindings (glob, alias, `pub use` marked as export) and use sites in code, types, patterns and macro arguments; pattern bindings, generic parameters and items defined inside functions or inline modules shadow a name.
- `scripts/lang/rust/parser.py`: a glob `use` is an imported name `*` (as Python's `from m import *`).
- `scripts/lang/symbols.py` (new): resolves a use site to the defining module: own definitions, import bindings, child modules, glob imports and re-export chains, with cycle protection and no answer when two definitions compete.
- `scripts/build_code_graph.py` (`_symbol_usage_edges`): a refs edge from each file to each definition it uses; `:reexport` when the file has no other edge to the defining module.
- `scripts/code_graph_search.py` (`CodeGraph.load`): `:reexport` refs stay out of the module adjacency, so `impact`, `deps`, `rdeps` and `analyze` are unchanged.
- Tests: REQ-PY-001 to REQ-PY-005, REQ-RUST-015 to REQ-RUST-018.

### Motivation
- The refactoring benchmark (`benchmarks/refactor`, 100 questions "which files use exported symbol S") measured `entity S` at 8 of 40 exact for Rust and 7 of 20 for Python: Python module-level variables were not entities, and Rust uses through glob imports, re-exports and qualified paths made no refs edge.

### Verification
- Refactoring benchmark, Wingbox 0.2.5 against this tree, reference lists of rust-analyzer, pyright and the TypeScript language service: TypeScript 40/40 exact before and after; Rust 8/40 exact, mean recall 0.33 -> 40/40, recall 1.00; Python 7/20, recall 0.40 -> 20/20, recall 1.00; no file outside a reference list. Over all 711 symbols the reference was asked (587 of them used by no other file), no Python or Rust file is named outside the reference.
- `impact <file> 2` on the five questions of `benchmarks/graphify`: the same file lists as 0.2.5.
- Test suite on Python 3.12 and 3.11.

## 2026-11-16 - Plugins found by purpose; MCP tool annotations; SECURITY.md

### Changes
- `SKILL.md` description (copied into the plugin): leads with "Code graph for large repositories and monorepos" and the questions an agent hears: what a change will break, who uses a file, function, type or package, where a feature is implemented, which files to read first, how packages depend on each other; before grep or reading files, before a refactor or a cross-package change. 630 characters, within the 1,024 of the Agent Skills format.
- Manifests: both descriptions and the Claude Code marketplace entry start with the same purpose; keywords monorepo, large-codebase, codebase-map, call-graph, refactoring, code-navigation added; Codex subtitle "Code graph for big monorepos", long description, capabilities and starter prompts by purpose. `termsOfServiceUrl` (the LICENSE) in the Claude Code manifest, asked by the directory's listing step. Plugin README, OpenAI package README and the docs/store listing follow.
- `src/wingbox/mcp_server.py`: every tool in `tools/list` carries MCP `annotations` (`title`, `readOnlyHint` true except `build`, `destructiveHint` false, `idempotentHint` true, `openWorldHint` false), as policy 5E of Anthropic's Software Directory Policy requires.
- `SECURITY.md` (new): private reports through GitHub's "Report a vulnerability" (enabled on the repository), acknowledgement within 7 days, latest release supported, scope; linked from the plugin README and docs/store, as the Software Directory Terms require.
- `plugins/wingbox/evals/` (new): `claude plugin eval` suite, a scaffolded TypeScript and Python monorepo, six purpose requests that must invoke the skill and three unrelated ones that must not.
- Tests: REQ-PLUGIN-014 and 015 (purpose wording, keywords, skill description length), REQ-MCP-011 (annotations), REQ-MCP-012 (tool names under 64 characters); REQ-PLUGIN-001 checks `termsOfServiceUrl`.

### Motivation
- The maintainer asked to check that the plugins are found by their purpose; Wingbox is built for large repositories and monorepos, and "monorepo" appeared in no description or keyword. The coordinator added the directory policy and terms requirements.

### Verification
- `claude plugin eval` with only the plugin loaded (isolated home and configuration), 3 runs per request: before (main 4bf2242 wording) the skill was invoked in 11 of 18 purpose runs (blast radius 3/3, refactor plan 3/3, cross-package change 2/3, who depends 2/3, files to read first 1/3, where implemented 0/3); after, 18 of 18. Unrelated requests: 0 invocations in 9 runs, before and after. A first rewording reached 16 of 18 (where implemented 1/3); "before grep or reading files" fixed it.
- Codex 0.169.0, static (its model has no tokens): `plugin/read` returns the new subtitle, capabilities and prompts and the new skill description; the MCP `impact` call still answers.
- `claude plugin validate --strict` on the plugin and the marketplace; the full test suite.

## 2026-11-16 - Codex plugin and the OpenAI directory packages

### Changes
- `plugins/wingbox/.codex-plugin/plugin.json` (new): name, version of pyproject, `skills: ./skills/`, `mcpServers: ./.mcp.json`, `requires_local_executor: true` and the `interface` listing (display name, 30-character subtitle, long description, developer, category Developer Tools, capabilities, four HTTPS URLs, three starter prompts, icon). `plugins/wingbox/.mcp.json` (new): `wingbox mcp` from PATH. The plugin README describes both clients.
- `.agents/plugins/marketplace.json` (new, added with `git add -f` because a global gitignore of the maintainer's machine ignores `.agents`): marketplace `aileron-tech`, local entry `./plugins/wingbox`, policy AVAILABLE / ON_INSTALL, category Developer Tools.
- `tools/sync_plugin.py` stamps the version in the Codex manifest too.
- `tools/build_openai_plugin.py` (new): `dist/wingbox-openai-plugin-<version>.zip` (skills only, Desktop only, for the public submission) and `...-local-mcp.zip` (with `.mcp.json`, for a local MCP submission after allowlisting); Codex layout, fixed timestamps (same files, same bytes), README from `docs/store/openai-package-README.md`; it refuses a stale plugin folder.
- `docs/store/README.md`: the OpenAI section (build command and paths, the submission checks with their results, what the maintainer does). `README.md`: Codex in "Install as a plugin". Tests: REQ-PLUGIN-011 to REQ-PLUGIN-013, and REQ-PLUGIN-004 covers the Codex manifest.

### Decisions
- Codex layout `.codex-plugin/plugin.json` + `.mcp.json`, not the portable root `plugin.json` + `mcp.json`: Codex 0.169.0 ignores the portable `mcp.json` (`plugin/read` lists no MCP server).
- In `.mcp.json`, `wingbox` from PATH without `cwd`. Codex does not expand `${PLUGIN_ROOT}` there, a relative command needs `cwd: "."`, and then `wingbox mcp` serves the plugin cache folder; Codex sends no MCP roots and no project folder in the environment. Without `cwd` the server runs in the session folder. So the Codex plugin needs the installed command; the launcher stays Claude Code only.
- No `hooks/hooks.json` in the plugin: Codex loads it by default. The Claude Code hook stays inline in `.claude-plugin/plugin.json`. Claude Code also reads `.mcp.json`; its inline server of the same name replaces it (checked: `claude mcp list` shows one server, through the launcher).
- The OpenAI packages leave out the hook (refused by the directory) and the launcher; their README describes only what they hold.

### Motivation
- The maintainer asked for plugins for the Claude and the OpenAI stores; this is the second part.

### Verification
- Codex 0.169.0 in a temporary `CODEX_HOME`, without a model call: `codex plugin marketplace add`, `codex plugin list`, `codex plugin add wingbox@aileron-tech` (installed under `plugins/cache/aileron-tech/wingbox/0.2.7`), `codex mcp list` shows `wingbox wingbox mcp`. Through `codex app-server`: `skills/list` lists `wingbox:wingbox` from the plugin cache; `mcpServerStatus/list` shows server wingbox 0.2.7 and its tools; `thread/start` then `mcpServer/tool/call impact` returned the three dependents of a test file in the session folder.
- Both ZIPs unpacked into a local marketplace and installed the same way: skills only gives the skill and no MCP server; local MCP gives the skill and the working server.
- `claude plugin validate --strict` on the plugin folder and the repository still passes with no warning.
- Tests: the full suite.

## 2026-11-16 - Plugin for Claude Code and its marketplace

### Changes
- `plugins/wingbox/` (new). `.claude-plugin/plugin.json`: name `wingbox`, version of pyproject, listing fields (`icon`, `documentationUrl`, `supportUrl`, `privacyPolicyUrl`), the PreToolUse hook on `Grep|Glob|Read` (timeout 5 s) and the MCP server `wingbox`, both inline and both through `scripts/wingbox-run`. `skills/wingbox/` and `LICENSE` are copies; `assets/icon.png` (512 px, source `docs/store/icon.svg`); `README.md`.
- `scripts/wingbox-run`: the installed `wingbox`, else `uvx --quiet --from wingbox==<version> wingbox`, else exit 127 with "pipx install wingbox". The hook (`hook ...`) uses uvx only `--offline`, hides stderr and always exits 0: uv exits 2 on errors, and exit 2 of a PreToolUse hook blocks the tool call.
- `.claude-plugin/marketplace.json` (new): marketplace `aileron-tech` with the entry `./plugins/wingbox`.
- `tools/sync_plugin.py` (new): copies SKILL.md, references/ and LICENSE into the plugin and stamps the version in the manifest and the launcher; `--check` lists what is stale and exits 1. Run it after a change to SKILL.md or the version.
- `docs/store/` (new): listing, test prompts, requirements and status of Anthropic's directory (README.md), PRIVACY.md, icon.svg, three screenshots.
- `README.md`: section "Install as a plugin". Tests: REQ-PLUGIN-001 to REQ-PLUGIN-010 in `tests/test_plugins.py`.

### Decisions
- Plugin folder `plugins/wingbox`, not the repository root: what the directory installs and scans is the plugin folder only (512 files, 256 KiB per text file, no symlink, no CLAUDE.md), so the skill is copied by `tools/sync_plugin.py` and a test keeps the copy identical.
- Hook and MCP server inline in `.claude-plugin/plugin.json` rather than `hooks/hooks.json` and `.mcp.json`, which other assistants' plugin formats also read.
- Launcher with an uvx fallback: the directory holds such a plugin for a reviewer ("Runs a pinned npx or uvx package"); it cannot be avoided, the plugin needs the Python package and its parsers.

### Motivation
- The maintainer asked for plugins for the Claude and the OpenAI stores; the Claude Code plugin comes first.

### Verification
- `claude plugin validate --strict` on `plugins/wingbox` and on the repository (marketplace): passed with no warning (Claude Code 2.1.325).
- In a temporary `CLAUDE_CONFIG_DIR`: `claude plugin marketplace add`, `claude plugin install wingbox@aileron-tech`, `claude plugin list` (0.2.7, enabled), `claude mcp list` shows `plugin:wingbox:wingbox ... wingbox-run mcp - Connected`.
- A headless `claude -p` with `--plugin-dir` and no session persistence, in a test repository with a graph: the session lists the skill `wingbox:wingbox` and the connected server; it called `mcp__plugin_wingbox_wingbox__impact` and got the three dependents; the Grep hook added the `wingbox-match` context.
- The uvx fallback with only uv on PATH: `wingbox-run --version` printed `wingbox 0.2.7`; the hook answered from the uv cache.
- Tests: the full suite.

## 2026-11-16 - wingbox install, uninstall, the search hook and wingbox update

### Changes
- `src/wingbox/install.py`: `wingbox install [--global|--project] [--assistant NAME ...] [--dry-run] [--sexp]` and `wingbox uninstall`. For Claude Code, Codex, Gemini CLI, Cursor, GitHub Copilot and the shared `~/.agents/skills` folder it copies the skill (SKILL.md, references/, stamp `.wingbox-version`), writes the always-on block between `<!-- wingbox:begin -->` and `<!-- wingbox:end -->` into CLAUDE.md, AGENTS.md, GEMINI.md or copilot-instructions.md, and for Claude Code merges a PreToolUse hook (matcher `Grep|Glob|Read`, timeout 5 s) into settings.json after a one-time backup `settings.json.wingbox-bak`. Assistants are detected by their folders in the home directory; folders that install itself created are recorded in `~/.wingbox/installed.sexp` and do not count. Uninstall removes stamped skill folders, the blocks, the hook entries and the empty folders install created; a folder still used by another installed assistant, or written by another installer, stays.
- `src/wingbox/hook.py`: `wingbox hook pre-search` reads the Claude Code hook input on stdin and, when the project has `.wingbox/graph.sexp` or `.code_graph.sexp`, answers with `hookSpecificOutput.additionalContext`: the modules and definitions that match a Grep or Glob pattern with their direct dependents, or for Read the dependents of the file and the `wingbox impact` hint; at most 1,500 characters; always exit 0; nothing without a graph. It reads the graph as text (section offsets, one regular expression for the dependents) instead of the query engine, which takes 17 s to load a graph of 8,000 files.
- `src/wingbox/update.py`: `wingbox update [--check] [--dry-run] [--sexp]` runs `pipx upgrade wingbox`, `uv tool upgrade wingbox` or `<python> -m pip install --upgrade wingbox` as a subprocess, then reruns `wingbox install` of the new version for every recorded install. pipx or uv is chosen only when it lists wingbox and Wingbox runs from that installer's wingbox environment, so a second copy elsewhere is not upgraded by mistake. Docker, editable and local installs print the command to use instead.
- `src/wingbox/facts.py`: facts as S-expressions (`--sexp`) and their pretty print, the output of the three commands.
- `src/wingbox/cli.py`: the new commands; `importlib.metadata` is imported only when the version is needed (it cost about 0.2 s of the hook's start).
- `pyproject.toml`, `Dockerfile`: the wheel ships SKILL.md and references/ as `wingbox/_skill`.
- `SKILL.md`: section `/wingbox` (no argument: build when missing or stale, then the report's key findings and the map; `<file>`: `impact <file> 2`; `<command>`: that command), how to run the commands when the skill has no `scripts/`, table of install, uninstall, update and the hook.
- `README.md`: "Install into your coding assistants" (paths per assistant, block, hook, record, `/wingbox`), "Update", the local-only sentence on `wingbox update`, FAQ on offline use and assistants. `AGENTS.md`: archive contents, layout, prefixes INSTALL and UPDATE.
- Tests: REQ-INSTALL-001 to REQ-INSTALL-022, REQ-UPDATE-001 to REQ-UPDATE-007.

### Motivation
- The maintainer asked for `wingbox install` on the scheme of `graphify install`: the skill in every assistant, a block in CLAUDE.md and AGENTS.md, a hook before search, and `/wingbox`; then for `wingbox update` when there is internet.

### Verification
- Hook schema: Claude Code hooks reference (code.claude.com/docs/en/hooks, read 2026-11-16): PreToolUse input `tool_name`, `tool_input` (Grep `pattern`/`path`/`-i`, Glob `pattern`/`path`, Read `file_path`), `cwd`; output `hookSpecificOutput.hookEventName` and `additionalContext`; exit 0 never blocks, stderr is not shown to the model; strings over 10,000 characters are moved to a file.
- Destinations from the assistants' documentation, read 2026-11-16: Codex skills `$HOME/.agents/skills`, `~/.codex/AGENTS.md`; Gemini CLI `~/.gemini/skills` (alias `~/.agents/skills`), `~/.gemini/GEMINI.md`; Cursor `~/.cursor/skills` (also reads `.agents`, `.claude`, `.codex`), project AGENTS.md; GitHub Copilot `~/.copilot/skills`, `.github/skills`, `~/.copilot/copilot-instructions.md`, project AGENTS.md.

## 2026-11-16 - `wingbox mcp`: an MCP server on stdio

### Changes
- `src/wingbox/mcp_server.py` (new): newline-delimited JSON-RPC 2.0 on stdin and stdout, written over the standard library. Protocol revision 2026-07-28 (per-request `_meta`, `server/discover`, `resultType`, `ttlMs`/`cacheScope`, -32022 for an unsupported version) and the `initialize` handshake of 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05, chosen per request. `ping` is answered for handshake-era clients.
- Tools: `build`, `impact` (text only: the maintainer chose it, the S-expression form doubled the reply), `analyze`, `quick`, `boundary`, `boundaries`, `layers`, `deps`, `rdeps`, `entity`, `find_function`, `find_type`, `find_callers`, `find_calls`, `find_callchain`, `diff_functions`, `query`. Each calls the same function as the `wingbox` command and is validated by the same argument check; answers without their own limit stop at 200 lines unless `all` is true. Left out: `search`, `module`, `flow`, `invariants`, `grep_functions` (overlap with the tools above or with the agent's own search) and `repl`.
- The loaded graph is kept between calls and reloaded when `.code_graph.sexp` changes: on a library of 1,227 files the first `impact` takes about 1 s, the next calls 5 to 35 ms.
- File descriptor 1 is pointed at stderr while the server runs and the protocol is written to a duplicate, so a stray print or a child process cannot corrupt the stream.
- `src/wingbox/cli.py`: `wingbox mcp` dispatches to the new module. `src/wingbox/__main__.py`: `python -m wingbox`.
- `README.md`: section "Use from an MCP client" with the configuration of Claude Code, Codex, Cursor and Claude Desktop, checked against their documentation on 2026-11-16; FAQ entry on agents. `SKILL.md`: command table row.
- Tests: REQ-MCP-001 to REQ-MCP-010 (`tests/test_mcp_server.py`), which run `python -m wingbox mcp` as a subprocess.

### Motivation
- The maintainer asked that `wingbox mcp` start an MCP server. MCP clients that do not load Agent Skills can then use the code graph.
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

## 2026-11-16 - `wingbox update` and a broken pipx neighbour

### Changes
- `src/wingbox/update.py`: pipx is chosen when `pipx list --short` names wingbox, whatever its exit status.

### Motivation
- On the maintainer's machine `pipx list --short` exits 1 (another package has a missing interpreter) while listing wingbox 0.2.6, so `update --check` reported pip.

### Verification
- REQ-UPDATE-008; full suite on 3.11 and 3.12; `choose_method` on that machine now returns `pipx upgrade wingbox`.

## 2026-11-16 - Release 0.2.7

### Changes
- Version 0.2.7: the README of main reaches the PyPI page (install with `pipx install wingbox && wingbox install`), and `wingbox update` picks pipx when `pipx list` exits 1. The map-request workflow pins the released version.

### Motivation
- The maintainer saw the PyPI page still showing the 0.2.5-era install line and chose a release now, with init and usages to follow as 0.3.0.

## 2026-11-16 - Release 0.3.0

### Changes
- Version 0.3.0: `wingbox usages` (every use of a symbol with position and kind), faster builds (each import resolved once), the plugins and their purpose wording, MCP tool annotations, SECURITY.md. The map-request workflow builds with 0.3.0.

### Motivation
- The maintainer asked when usages ships and chose to release it now, the architecture commands (init, place, align, learn) following as 0.4.0 after an independent check of learn.

## 2026-11-16 - Release 0.4.0

### Changes
- Version 0.4.0: the graph keeps itself fresh through hooks (edit marks, outside-change check, partial update), the first `usages` after a change reads only the changed files, PHP 8 and Laravel (#42). The map-request workflow builds with 0.4.0.

### Motivation
- The maintainer asked for the release.

## 2026-11-17 - Release 0.5.0

### Changes
- Version 0.5.0: everything in `.wingbox/` (index.sexp, caches, REPORT.md, map.html), `.wingbox/` as the project marker, no refresh into another folder (a copied graph was overwritten in 0.4.0), `--output` removed, `wingbox open`, `wingbox update` warns about old files; no compatibility with the old layout. The map-request workflow builds with 0.5.0.

### Motivation
- Errors seen in a real session on 0.4.0; the maintainer decided: the graph moves to `.wingbox/index.sexp`, every file lives in `.wingbox/`, nothing is deleted, no compatibility with the old layout, and the version is released at once.

## 2026-11-17 - Release 0.5.1

### Changes
- Version 0.5.1: Dart and Flutter (#47), the grammar as the opt-in extra `wingbox[dart]`. The map-request workflow builds with 0.5.1.

### Motivation
- The maintainer agreed to release Dart as 0.5.1.

## 2026-11-17 - OntoIndex as the fourth tool of the Graphify comparison (branch ontoindex-comparison, not merged)

### Changes
- OntoIndex 3.1.9 measured with the same corpus, reference lists and scoring; its lines in the title, "Which tool for which job", "Summary", "Why the numbers differ" and its own section come from `tools/other_tools.py` (the tool name is a parameter). Scripts: `tools/oi_run.sh`, `oi.Dockerfile`, `oi_edges.py`, `oi_command_check.py`, `oi_agent_test.py`, `time_builds_tools.py`, `../impact/tools/oi_impact100.py`.
- Results: FastAPI edges precision 0.987, recall 0.935 (Wingbox 1.000 / 1.000); exact dependent lists 1 of 5 with `--include-tests` (Wingbox 5 of 5); complete lists in 20 of the 100 questions of `benchmarks/impact` (Wingbox 100); agent test 78,070 tokens, F1 0.977, 0.075 USD per answer (Wingbox 23,768, 1.000, 0.021); 15 trials cost 1.13 USD.
- Not measured: build time (the machine is shared; the script is in place).

### Motivation
- The maintainer asked to study OntoIndex and add it next to Graphify and CodeGraph, ideas only (AGPL), then "finish everything".
