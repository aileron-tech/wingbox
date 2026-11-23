# Completion Log

## 2026-11-23 - Build targets and tests of CMake as facts; the ones a change reaches (#188)

### Why
- Issue #188, point 5: for C and C++ the reporter asked for build targets and test targets as nodes, so that "what does this change affect" names the tests and the binaries, not only files. A file list of 300 test sources is not what is given to `ctest`.

### Decisions
- Facts, not nodes: a target is not a file, and every query that walks modules (impact, deps, layers, cycles) would have to learn to skip it. The facts stand in their own section of the graph file, `; --- build ---`.
- Read literally, as the generation links are: no CMake is run and no variable is evaluated beyond the four folder variables. What a function or a macro of the project adds is not seen, and the documents say so.
- Linking is not followed. On the repository of #177 nearly every test executable links the one engine library, so a link rule would name every test for every change. The rule is the compile one: a target is affected when the file or a listed dependent is among its sources.
- No new option: the answer is part of `impact --only tests`, the question "which tests", and appears only in a graph that has such facts.

### Changes
- `scripts/generation.py`: `cmake_targets(root, cmake_files, read_text)` -> `BuildTarget` and `BuildTest`; `_cmake_source` resolves a source from the folder of the list file that includes the CMake file, then from the folder of the file.
- `scripts/build_code_graph.py`: the facts `(target <name> :kind ... :in "<file>" <module> ...)` and `(ctest <name> :target <target> | :runs ... <module> ...)`.
- `scripts/code_graph_search.py`: the loader (`build_targets`, `build_tests`), `_affected_build`, `impact_answer` with `only` `tests`. `scripts/impact_forms.py`: `Affected`; the two facts in the default form, `targets` and `tests` in JSON, two lines in the text. The flat form of `--sexp` is unchanged.
- `src/wingbox/mcp_server.py`: the description of the parameter `only` of `analyze_impact`.
- `README.md`, `SKILL.md`, `references/sexp-notation.md`.
- Tests written first, `tests/test_build_targets.py`: REQ-CS-164, REQ-CS-165.

### On the repository of #177
- 767 targets, and 1,671 of the 1,706 names of `add_test`; of the other 35, 8 names are written with a variable and 27 tests run a command kept in a variable.
- `impact <central header> 2 --only tests`: 308 test files, 307 targets, 1,019 tests. Its source file at depth 1: 146 test files, 148 targets.
- `impact --diff --only tests` after a change inside one function of that source file: the file 308 test files and 1,019 tests; the changed function 11 test files, 12 targets, 16 tests. This is the list to run, and the reason each `(changed ...)` fact got `:targets` and `:tests` of its own (`Changed.affected`): the file's own targets and those of the files that use the function.
- Build time unchanged within the noise (60 to 64 s, one worker). A change of a CMake file is seen by the next query, which refreshes the graph.

### Not done
- No reference checks these numbers: the build of that repository was not run here, so CMake's own list of tests (`ctest -N`) was not compared.
- `target_link_libraries`, build systems other than CMake.

## 2026-11-23 - wingbox cycles: structure without a layer style (#188)

### Why
- Issue #188, point 3, the third of the reporter's conditions: a repository that fits no style of the library gets `style none`, "boundary violations: not checked" and `place-none`, so half of the product says nothing on the first run. The report asks for checks that work without a style (cycles, hubs, edges between top-level folders) and for a draft of the layers.
- The maintainer left the name to the implementer: `wingbox cycles`, after its first and plainest finding.

### Changes
- `scripts/code_graph_search.py`: `cmd_cycles(g, scope)`. `_strong_groups`: strongly connected components, Tarjan without recursion. Facts: `(cycle N :files K <file> ...)` for each group of more than one file over the import edges (type-only imports left out), largest first, at most 30 files named and `:more`; `(hub <file> :dependents N :dependencies M)` for a file with at least ten of each, at most 20, by the product of the two; `(folder-cycle a b :forward N :back M)` for two top-level folders with file pairs both ways; `(folder-order (...) ...)`, the groups of folders in the order in which a group stands before those it depends on, ties by name; `(cycles-summary ...)`. With a folder as the argument: its files, its subfolders as the folders.
- `README.md` ("Without a layer style: `wingbox cycles`"), `SKILL.md` (the command table).
- Tests written first, `tests/test_cycles_command.py`: REQ-CS-162, REQ-CS-163.

### On the repository of #177
- Four groups of files that depend on each other (ten files, all Python), six hubs (headers of the engine), no two top-level folders that depend on each other, and an order of the seven top-level folders. Below the source folder of the engine: its subsystems depend on each other in pairs, with counts each way, for example 122 file pairs one way and 11 back.

### Seen while writing, not changed
- A Python file named `index.py` beside an `__init__.py` gets the module ID of its folder (the rule for TypeScript index files is applied to Python), and commands that print a file path name `__init__.py` for it.

### Not done
- A command that writes the layers of `.wingbox/config.yaml` from `folder-order`: the order is printed, the choice of what is a layer stays with a person or with `wingbox learn`.
- The MCP server has no tool for it: its tool list is kept as it is.

## 2026-11-23 - The first usages after a build reads the files that hold the name (#188)

### Why
- Issue #188, point 4, the second of the reporter's three conditions: the first `wingbox usages` on C++ took 10.2 s on their machine, the next ones 0.18 s; an agent that waits ten seconds goes back to a text search. Measured here on the repository of #177 with one worker: 73.6 s for the first call after a build of 71.9 s, 0.58 s for the second.
- Where the time went (profile of `write_use_positions`): three quarters in walking all 2,281 files again for their positions (the 719 Python files 56 %, the 1,558 C and C++ files 18 %), 12 % in resolving the uses, 5 % in reading the build cache. An answer about one name needs about twenty of those files.

### Changes
- `scripts/build_code_graph.py`: `use_position_lines(map_path, root, name)` walks and resolves only the files whose text holds the name (a file that uses a name holds it), refuses when such a file changed since the build, writes nothing, and returns the lines the full fill writes for those files. `start_position_fill` starts `write_use_positions` in a process of its own (`--fill-positions`, not an option of a build), under `.wingbox/positions.lock`; a lock older than fifteen minutes is taken over; `WINGBOX_POSITIONS_FILL=0` starts nothing. `_position_inputs`: what both fillers read. C and C++ use sites are taken from the build cache in both (the build already reads every one of them): no second walk of those files.
- `scripts/code_graph_search.py`: `CodeGraph._positions_of(name)` and `unresolved_calls_of(member)`; `uses_named` and the unresolved calls of `usages` read through them. `entity --positions` still fills the whole file: it lists every use of a file. A graph of up to 300 files fills the whole use file at the first question, as before (`_FILL_AT_ONCE_FILES`): that takes less than a second there, and the tests of the repository, all on small trees, start no process.
- `README.md`: what the first `usages` does, and the environment variable.
- Tests written first, `tests/test_usages_first_query.py`: REQ-CS-159 (the first answer equals the answer of the filled use file, for ten symbols of Python and C++), REQ-CS-160 (only the files that hold the name are read; a changed file is refused), REQ-CS-161 (the fill runs in a process of its own, once).

### Measured on the repository of #177 (one worker)
- First `usages` after a build: 5.3 to 6.3 s (73.6 s before), the same 33 uses in 20 files. About 3 s of it is reading the build cache, 59 MB, most of it the use sites of the C and C++ files.
- The fill in its own process: done after 69 s; then 0.58 s a call, as before.

### Not done
- A build that fills the positions itself (the reporter's other proposal): it would double the time of a cold build, the number the build benchmark publishes.
- The build cache is read whole for one question; splitting the use sites out of it would take most of the remaining seconds.

## 2026-11-23 - impact of an implementation file, of tests only, of a diff (#188)

### Why
- Issue #188, points 1 and 2, the first of the reporter's three conditions for adoption. `wingbox impact <file>.cpp` answered 0 direct and 0 total, because nothing includes a source file; the text for people said "No other module depends on it". Most edits of a C or C++ repository are in source files, and an agent reads that answer as "safe". On a central header the answer held 440 files, 308 of them tests, with no way to ask for the tests alone.
- The maintainer left the names and the defaults to the implementer. Chosen: the header is followed by default, without a flag, because a flag would leave the empty answer in place; `--only tests` and `--diff` are options of `impact`, not a new command.

### Changes
- `scripts/code_graph_search.py`: `paired_headers` (a header of the stem of the source file among its includes, then one of that stem in its folder); `_impact_levels` and `_impact_rows` start from the file and its headers, and a row of depth 1 reached through a header has the basis `paired-header`. `_source_file_note`: `(impact-paired-header <file> <header> ...)`, or `(impact-no-includers <file>)` for a source file without a header and without rows; the three forms and the flat form print it. `_only_rows`: with `--only tests` the rows whose layer is `test`. `impact_options` reads `--only tests` and `--diff [ref]`. `cmd_diff_functions` is split into `_diff_ranges` and `_diff_functions` (its output is unchanged; a deleted file no longer keeps the name of the file before it); `diff_changes` gives the changed files and, per changed function, the files that use it from the use lines of the graph (`use_pairs`: no positions are needed), through the declaration in the header as well.
- `scripts/impact_forms.py`: `ImpactFound.only`, `Changed` and `changed_fact`; the compact form skips a depth without rows; the text says "No test file depends on it" and leaves the sentence out when a note explains the empty list.
- `src/wingbox/mcp_server.py`: `analyze_impact` takes `only` (`all`, `tests`); the tool list is unchanged.
- `README.md`, `SKILL.md`, `references/sexp-notation.md`, `docs/assurance/known-limits.md`.
- Tests written first, `tests/test_impact_implementation_files.py`: REQ-CPP-014, REQ-CPP-015, REQ-CS-157, REQ-CS-158.

### On the repository of the issue (the commit of #177)
- The source file of the central header: 192 direct dependents and 439 within two steps, where it had 0 and 0 (the header has 193 and 440: the source file itself is not a row).
- `--only tests` on that header: 146 direct, 308 within two steps.

### Not done
- A changed function is found by the lines of the definitions around it, as `diff_functions` always did: lines added after the last definition of a file count for that definition.
- Mapping the test files to runner targets (CTest, pytest): with the build targets of point 5.

## 2026-11-23 - After the release 0.17.0: the pull request check, the site, the topics

### What was done (AGENTS.md sections 6.2, 6.4 and 6.5)
- 6.4, the action: `aileron-tech/wingbox-action` 1.6.1 sets the default Wingbox release to 0.17.0 (`scripts/bump_version.py`). Two Windows jobs of its pull request ran before PyPI served 0.17.0 and failed at the install; restarted, all 15 checks passed. The `test` workflow of the release commit: 14 jobs green, the fixture job on Linux, macOS and Windows with the new default. Then the tag `v1.6.1`, the tag `v1` moved to it, and the release.
- 6.4, the example pull request the site links: its `wingbox` run was started again; the one comment was updated in place and its review plan says `:wingbox "0.17.0"`.
- 6.4, the Marketplace page of the action names v1.6.1.
- 6.4, this repository: `.github/workflows/wingbox.yml` names the action at the commit of `v1.6.1` (it was at `v1.4.2`); the run of that workflow on the pull request of this entry is the check.
- 6.5, the site: the home page and the FAQ name C and C++ ("Sixteen languages and frameworks"), and the note that Dart needs an extra is removed; type check and build clean, deployed, the live page holds the new text, no horizontal overflow at 1440 and 390 px.
- 6.2, GitHub topics: the seven topics with the fewest repositories on GitHub (163 to 1,366: `graphify`, `blast-radius`, `impact-analysis`, `code-graph`, `call-graph`, `code-search`, `dependency-graph`) were replaced by `c`, `cpp`, `mcp`, `mcp-server`, `python`, `typescript` and `rust` (93,000 to 1,058,000), at the maintainer's word.

### Not done
- 6.4, GitLab: the pipeline of the private test project with the released template. Its host and credentials are not on this machine.
- 6.5: the benchmark pages of the site do not show `benchmarks/cpp` and `benchmarks/collisions`.

## 2026-11-23 - After the release 0.17.0

### Checks (AGENTS.md section 6)
- 6.1 PyPI: `wingbox` and `wingbox-http` 0.17.0 list two files each. `uvx --refresh --from "wingbox[http]==0.17.0" wingbox --version` answers `wingbox 0.17.0`; `wingbox mcp --http --help` runs. For some minutes after the upload an index without the new version was served: `uvx` without `--refresh` and one job of the pull request check on Windows did not find 0.17.0.
- 6.1 GitHub release `v0.17.0`: assets `SHA256SUMS`, `wingbox-0.17.0.cdx.json`, `wingbox-0.17.0.mcpb`, `wingbox.mcpb`; the two digests PyPI answers for `wingbox` (wheel `eead7fe9...`, sdist `4ac59b63...`) are lines of `SHA256SUMS`; the extension link answers 200.
- 6.1 Workflows: `publish-mcp` succeeded; the registry answers 0.17.0, `active`. `tests` of the commit of #183 on `main` had one macOS job cancelled without steps (no runner): restarted with `gh run rerun --failed`.
- 6.1 Plugin pin: #185. Homebrew: `bump.yml` started for the release; the package has one more dependency (`tree-sitter-cpp`), which the formula must name as a resource.
- 6.1 Clean install, before the upload: the built wheel in a new environment gave every number below on the repository of issue #177; after it, the same with `uvx --from wingbox==0.17.0`.
- 6.2 Descriptions: the README and the store texts name C and C++ (#181). GitHub "About": the description names C and C++; the topics `c` and `cpp` were not added, the repository has the twenty topics GitHub allows. `pyproject.toml` `description` names no language.
- 6.3 Listings: the tool list and the tool descriptions did not change; not applicable.
- 6.4 The pull request check: `aileron-tech/wingbox-action` pull request 38 sets the default to 0.17.0 (its unit tests pass; its tag follows the green fixture job).
- 6.5 The sites: not done. The benchmark pages of this change hold new numbers, which are shown to the maintainer before a site names them.

### The repository of issue #177 with 0.17.0 from PyPI (commit of the issue, built from nothing, one worker)
- Python: 1,296 required import edges, 0 wrong, 0 missing; the module of the issue has 31 direct dependents and 66 within two steps (0.16.0: 292).
- C and C++: the header of the issue has 193 direct dependents, the 193 files a search of the include lines finds; 6,043 reference include edges, 0 missing, 1 more in the graph, confirmed by the compile command in that file. Doxygen 1.11.0 on the same tree: 6,042 edges, 6,038 of them in the graph; its other 4 are `<time.h>` and `<math.h>` taken for files of the repository.
- Generation: the generator has 2 direct dependents and 33 within two steps, the schema 3 and 34, with no configuration. 43 launch links.
- Coverage: 686 CMake, 9 Shell, 4 PowerShell and 2 Lua files counted as not parsed; a CMake file answers `not-in-graph`; the report says "not checked" for the boundaries.
- Build: 69 s (2,281 files).

## 2026-11-23 - Benchmarks: C and C++ includes, colliding Python names (#177)

### Why
- Issue #177 asks for independent benchmarks with mixed languages, generated files and package and module name collisions; the maintainer asked for the benchmarks to be taken after the fixes, and proposed Doxygen as a further check for C and C++.

### Changes
- `benchmarks/cpp/`: `run.sh` fetches fmt 11.0.2, leveldb 1.23 and libuv v1.48.0 and runs four tests. `tools/cpp_reference.py`: a reference of the include edges from the text of the files, with the include roots of each project given by hand. `tools/clang_check.py`: every file of the repository that `clang -MM -MG` reads for a translation unit must be reached over the reference edges. `questions.py`: the edges of the graph against the reference, and 20 impact questions per repository. `tools/doxygen_check.py`: the includes Doxygen resolves (`GENERATE_XML`) against the edges of the graph. `make_report.py` writes the page from `results/`.
- `benchmarks/collisions/run.py`: a corpus of seven small Python projects with names that collide; the reference is the interpreter (a hook on `__import__` records the file each statement of a file loads, the functions of the file are called so that their imports run); `--report` writes the page from `results/`.
- `tests/test_benchmark_cpp_collisions.py`: REQ-BENCH-021 to REQ-BENCH-024 (the reference on a small tree, the compiler check with an edge taken out, the corpus against this checkout, the two pages equal to their results).
- `docs/assurance/verification-plan.md`, `known-limits.md`, `README.md`: the rows and links of the two pages; the limit of C++20 module imports.

### Results (Wingbox 0.17.0 from PyPI)
- C and C++: 60 of 60 questions complete with nothing extra; 1,345 reference edges, 0 wrong, 0 missing; 3,699 files read by the compiler for 435 translation units, all reached by the reference (2 translation units stopped by the compiler). Doxygen: 1,316 of its 1,376 edges are in the graph; 59 of the others are system headers it took for a file of the repository of the same name, 1 is a C++20 module import, which Wingbox does not read.
- Colliding names: 51 required edges from 64 loads; 0.16.0 has 8 wrong and 11 missing, 0.16.1 and 0.17.0 none.

### What the benchmarks changed before the release
- Three defects of the new code were found by these runs and fixed in #183: a generation marker read from a sentence that only contains the words, interface-call edges from C++ base class names, and `not-in-graph` for the path of a file that is a module of the graph.

### Not measured
- The uses of C and C++ names (`usages`): Doxygen's cross references attribute a call by its name (of 60 sampled functions of the repository of the issue it agreed with Wingbox on the files of 5; in the cases read by hand it named a function of another header with the same name, or passed over a function the file defines itself), so they are no reference. A compiler index (clangd) would be one.
- C and C++ with a compilation database on a public repository: the benchmark gives Wingbox none.

## 2026-11-23 - Release 0.17.0

### Changes
- Version 0.17.0, core and `packages/wingbox-http` (REQ-HTTP-021): C and C++ (#181); what the graph does not cover (#182); generated files, launched scripts and the basis of an edge (#183). The map-request workflow builds with 0.17.0.
- `README.md`: installing only the parsers a repository needs (run in a clean environment: the package without dependencies and PyYAML build a Python graph and skip C and C++ files with the notice; with `tree-sitter` and `tree-sitter-cpp` added they are in the graph; a TypeScript file without its parser stops the build with the package name), and the configuration for OpenCode (run with OpenCode 1.2.10 in a small project: `opencode mcp list` answers "wingbox connected" and the server builds the graph; the form `mcp.servers` of the current online documentation is refused by that version).

### Motivation
- Issue #177: the maintainer asked for every point of the report to be closed and released. Point 5 of the report (lower the cost of adoption): the parsers stay dependencies of the package, as decided for 0.13, and the README says how to install fewer.

## 2026-11-23 - Generation and launch links, and the basis of every edge (#177)

### Why
- Issue #177, point 3: the links that matter in a mixed repository are schema -> generator -> generated Python and C++ files; `wingbox impact` of the generator answered 0 dependents and the schema was not in the graph. The report asks for declared inputs and outputs first, then CMake and `subprocess`, and for declared, inferred and unresolved links to be told apart.

### Changes
- `scripts/generation.py` (new): the links and their sources. Declared: `generators:` of `.wingbox/config.yaml`, entries of `run`, `inputs`, `outputs` (globs allowed; any other key is an error that declares nothing). Inferred: `read_markers` (a line among the first ten that starts with `generated by|with|using <path>`; a first version took the words anywhere in the line and linked a file of a public repository to another one over the comment "Decodes the blocks generated by ...", with `edit|generated from|source:|input: <path>` for inputs), `cmake_links` (`add_custom_command(OUTPUT ... COMMAND ... DEPENDS ...)` with the CMake variables for the current and the top folder). `merged`: a declared link stands for an inferred one of the same pair.
- `scripts/lang/interface.py`: `FileResult.generation` (the markers, read in `parse_file` for every language) and `FileResult.launches`. `scripts/lang/python/dynamic.py`: `find_launches` reads `subprocess.run|call|check_call|check_output|Popen` and `os.system|popen` from the one walk of the tree; a script path is a literal, a word of a command line, or the literal tail of a `join` call or of a `/` chain. Parse cache schema 8.
- `scripts/build_code_graph.py`: `_generation_edges` in the assembly of the graph: `(generated <output> <generator|input> :by|:from :declared|:inferred)`, `(runs <launcher> <script> :inferred :lines ...)`, `(generated-file <output> "<path>" ...)` for an end that is a file of the repository outside the graph, `(generation-unresolved "<path>" :role ... :stated "...")` with a warning of the build. An import edge all of whose includes were found by inference carries `:inferred` (`BoundCppLanguage.import_basis`).
- `scripts/code_graph_search.py`, `scripts/impact_forms.py`: `generated` and `runs` are edge kinds that `impact`, `rdeps` and `analyze` walk; layer rules read imports and data flow only, as before. `ImpactRow.basis` and `ImpactLine.basis`; `basis` in a JSON row and `:basis` in the compact and the flat form only when the edge is declared or inferred: the rows of every other edge are unchanged (REQ-CS-111 keeps its six keys). `impact` of a generator input outside the graph is answered from the `generated-file` facts.
- `src/wingbox/overview.py`: one key finding with the counts per basis and the unresolved paths.
- `README.md` ("Generated files and launched scripts"), `SKILL.md`, `references/sexp-notation.md`, `references/code-mode.md`.
- Tests written first: `tests/test_generation_links.py`, REQ-CS-151 to REQ-CS-156, REQ-PY-024, REQ-CPP-012.

### Verification on the repository of the issue, with no configuration
- The two generated files carry the line "Generated by <generator>; edit <schema>". `wingbox impact <generator> 2`: 2 direct dependents (the generated Python module and the generated header, `generated`, `inferred`), 33 within two steps. `wingbox impact <schema> 2`: 3 direct (the two files and the generator, `reads`), 34 within two steps. A word search for the name of the generated files finds 37 files: the 31 of depth 2, the two generated files, the generator, and 5 files that hold the path in a list of strings; no file is missing.
- 43 launch links from `subprocess` calls; 5,374 of 6,044 include edges marked `inferred` (the repository has no compilation database in its tree), 0 unresolved paths.

### Found by the benchmarks run for this change, and fixed here
- `deps`, `rdeps`, `analyze` and `module` take a module ID; asked with the path of a Python file of the graph they answered `not-in-graph :reason excluded` since the change before this one, where the answer is `no-match` with the module among the suggestions. `outside_graph` now asks first whether the file is a module of the graph (REQ-CS-148).
- C and C++: a file with a derived class got an interface-call edge to a file found by the bare name of the base class, which is not always the header it includes: 5 of 60 impact questions on three public repositories listed files that no include reaches. The dependencies of C and C++ files are now their includes only: no `calls-api` edge from heritage, and an abstract class is not reported as a provider (REQ-CPP-013; REQ-CPP-001 no longer expects the abstract class in `exports_abstract`).

### Not done
- A launch from a CMake file (`add_test`, `add_custom_target`) and from a shell script: the launcher is not a module of the graph.
- Other languages than Python for launch calls.

## 2026-11-23 - What the graph does not cover is said (#177)

### Why
- Issue #177, point 4: keep "not checked" apart from "no violations", and show the languages that are not covered in the answers, apart from a missing parser. On the repository of the issue `wingbox impact` of a header (before C and C++) and of a CMake file answered `no-match` with five Python files of similar names, which reads as "you misspelled it"; the report wrote "0 boundary violations" beside "not checked: 723 files". Recorded as a known gap in the log entry of `docs/assurance` (2026-11-19): nothing counted the source files of a language Wingbox does not parse.

### Changes
- `scripts/source_kinds.py` (new, standard library): the extensions of the parsed languages with their display names (a copy of the registry, compared by a test) and a table of languages without a parser (build files, shells, shaders, programming languages), by extension or by file name.
- `scripts/build_code_graph.py`: `discover_files` counts the files of the second table; the build prints `Not in the graph: ...` and writes `.wingbox/graph-unparsed.sexp` (removed when there are none). `*.cmake` and, through the C and C++ language, `CMakeLists.txt` are resolution configs: a change of one rebuilds the graph, because the include folders may differ.
- `scripts/code_graph_search.py`: `outside_graph` says why an existing file of the repository is not in the graph; `impact` (the three forms) and the no-match answer of `analyze`, `deps`, `rdeps`, `module` use it. The flat `--sexp` form of `impact` keeps `no-match`. The graph fact carries `:not-parsed` and stands before a single `impact` answer when the record is not empty. `scripts/impact_forms.py`: the fields and the three printers.
- `src/wingbox/overview.py`, `cli.py`: the key finding "Not in this graph: ..." and "not checked" for boundary violations when the unchecked parts hold every module.
- `README.md` ("What the graph does not cover"), `SKILL.md` (what to do on `not-in-graph`; a text search for code outside the graph), `docs/assurance/known-limits.md` and `tool-errors.md` (C and C++ are no longer listed as not parsed; the rows of an unparsed language and of a file outside the graph).
- Tests written first, `tests/test_coverage_limits.py`: REQ-CS-146 to REQ-CS-150, REQ-OUT-020.

### Verification on the repository of the issue
- Build line: 686 CMake, 9 Shell, 4 PowerShell and 2 Lua files not in the graph. `impact` of a CMake file: `not-in-graph`, `not-parsed-language`, exit 1; of the schema file of the generator: `not-in-graph`, `not-source`. The report: "Boundary violations: not checked", the table row "not checked".

### Not done
- Notices on standard error and at the start of every MCP answer, as for a missing parser: a missing parser can be installed, a language without a parser cannot, so the fact is in the graph fact, the report and the answer about such a file, not in every answer.

## 2026-11-23 - C and C++ (#177)

### Why
- Issue #177: in a mixed C++ and Python repository the engine was outside the graph. `wingbox impact` of a header answered `no-match` where a search of the include lines finds 193 files, so a change in the central code had no blast radius. The maintainer asked for every point of the report to be closed and for the numbers the reporter states on the reporter's repository.

### Changes
- `scripts/lang/cpp/` (new): the language `cpp` with the result languages `cpp` and `c` (a `.c` file), one grammar for both (`tree-sitter-cpp==0.23.4`). `parser.py` reads the includes with a query (anywhere in the file, quoted names plain, angle names in their brackets, a macro name skipped), and the entities: classes, structs and unions with their methods and base classes, enums with their members, typedefs and `using` aliases, function definitions and prototypes, macros with a value; a method defined in a class body or outside it (`Painter::paint`) is the entity `Painter.paint` with its own line. A module ID is the path with its extension.
- `scripts/lang/cpp/imports.py`: the file of an include. Beside the including file for a quoted name; then the include folders of the compilation database (`compile_commands.json` at the root, in a folder of the root or one level below; `-I`, `-isystem`, `-iquote`, `-idirafter`, `--include-directory`, `-external:I`, `/I`, joined or as the next argument, in `arguments` or `command`; a header takes the folders of the entries of the nearest folder); without a database the literal folders of `target_include_directories` and `include_directories` in the CMake files; last, for a quoted name that is not a standard header, the one file whose path ends with the name. Nothing else is tried.
- `scripts/lang/cpp/symbols.py`: one glob binding per include and the use sites of a file (calls, types, base classes, names with a scope, other names that are not locals of the function around them, method calls as unresolved member uses), read with one query of the grammar. `scripts/lang/symbols.py`: `cpp` and `c` are languages of the symbol index; a name behind includes is the one file among those the includes reach that defines it (`_from_includes`), not a walk of every include. `scripts/build_code_graph.py`: the use positions of C and C++ are read with `cpp_use_positions`.
- `src/wingbox/hook.py`: the source extensions of the hook. `pyproject.toml`, `requirements/cpp.txt`: the pin, the extra `cpp`, `all`. `README.md`, `SKILL.md`, the plugin copies and the store texts name the language; `README.md` has the section "C and C++ dependency search"; `docs/assurance/known-limits.md` and `verification-plan.md` have their rows.
- Tests written first, `tests/test_cpp_language.py`: REQ-CPP-001 to REQ-CPP-011. `tests/test_language_registry.py`, `tests/test_language_parsers.py`: the new language in the registry, the pins and the skipped-language notice.

### Verification on the repository of the issue (commit of the issue; 1,558 C and C++ files, 719 Python, 4 JavaScript; one worker)
- `wingbox impact <the header named in the issue> 2 --json`: 193 direct dependents, the same 193 files as a search of the include lines (`rg -l` of `#include` with that file name); 440 within two steps.
- Every include edge of the graph against a reference written for this change (the text of the files without comments; a quoted name beside the file, then the one include root of the engine): 6,043 edges in the reference, 0 missing in the graph, 1 edge more in the graph. That edge is right: the file states its own compile command in a comment, with `-I` of the folder that holds the header, and the graph finds the header as the one file of that name. A first run had 4 wrong edges, `<time.h>` and `<math.h>` taken for files of the repository of the same name; since then an angle name and a standard header are never found by the end of a path.
- `wingbox usages <that header>::<one of its functions>`: 33 uses in 20 files. A word search finds 25 files: the 20, the declaration, the definition, and 3 files that name the function only in a comment.
- The Python numbers are unchanged: the module named in the issue has 31 direct dependents, 66 within two steps; 1,296 required import edges, 0 wrong, 0 missing.
- Build time, `wingbox build .` from nothing: 32 s for the Python and JavaScript files alone (0.16.1), 67 s with the C and C++ files. The first version of the use sites took 133 s: 51 s walking the syntax trees in Python (now one query, 10 s) and about 45 s asking every include on the way for every name (now the set of reached files).

### Not done here
- No compilation database of that repository was available (its build was not run): the database path is covered by the tests, the numbers above come from the CMake files and the tree.
- No benchmark on public C and C++ repositories yet; `docs/assurance/known-limits.md` says "not assessed".
- A name that two reachable headers declare is attributed to neither (REQ-CPP-011); with the walk of every include the nearest one had answered: 92 of 23,172 reference edges of that repository.

## 2026-11-23 - After the release 0.16.1

### Checks (AGENTS.md section 6)
- 6.1 PyPI: `curl -s https://pypi.org/pypi/wingbox/0.16.1/json` and the same for `wingbox-http` list two files each (the sdist and the wheel). `uvx --refresh --from "wingbox[http]==0.16.1" wingbox --version` answers `wingbox 0.16.1`; `wingbox mcp --http --help` prints the options of the HTTP transport.
- 6.1 GitHub release `v0.16.1`: assets `SHA256SUMS`, `wingbox-0.16.1.cdx.json`, `wingbox-0.16.1.mcpb`, `wingbox.mcpb`. The two lines PyPI answers for `wingbox` (wheel `c0450b96...`, sdist `3981e71e...`) are lines of `SHA256SUMS`. `curl -sIL .../releases/latest/download/wingbox.mcpb`: 200.
- 6.1 Workflows: `publish-mcp` succeeded for the release; the registry answers version 0.16.1, status `active`. `tests` and `docs` of the release commit: see the pull request of this entry.
- 6.1 Plugin pin: `python3 tools/sync_plugin.py` changed the four manifests to the commit of `v0.16.1` (this change); `tests.test_plugins` passes.
- 6.1 Homebrew tap: `gh workflow run bump.yml -R aileron-tech/homebrew-wingbox` started; the dependencies of the package did not change.
- 6.1 Clean install: in a project with a module file beside a package of the same name, `wingbox build .` and `wingbox impact <module> 2 --json` from PyPI list the tool as the only dependent, and `analyze` names the package `__init__.py`, not the tool.
- 6.2 and 6.3: not applicable. The release changes no command, option, tool, description or install line.
- 6.4 and 6.5 (the pull request check and the sites): not done for 0.16.1. They are carried to the release that follows in the same working session (C and C++), so that the default version of the action and the site move once.

## 2026-11-23 - Release 0.16.1

### Changes
- Version 0.16.1, core and `packages/wingbox-http` (REQ-HTTP-021): a Python package folder is taken before the module file of the same name (#177, #178). The map-request workflow builds with 0.16.1.

### Motivation
- Issue #177: the wrong dependencies made the impact of a module four times too large in a repository that has a command line tool beside the package it drives. The maintainer asked for the release.

## 2026-11-23 - Python: a package folder wins over the module file of the same name (#177)

### Why
- Issue #177: in a repository with a command line tool `<name>.py` beside the package `<name>/` it drives, `wingbox impact` of a module of the package listed 292 files within two steps; a check of the static imports gave 62. The files of the package were listed as importers of the tool.

### Cause
- `BoundPythonLanguage._resolve_import` (`scripts/lang/python/imports.py`) tried `<base>/<parts>.py` before `<base>/<parts>/__init__.py`. For `from . import x` the parts are empty and the base is the folder of the package, so the first candidate was the module file beside that folder under its name. Every file of the package with such an import got an import edge to the tool and, at depth 2, to everything the tool imports. `import <name>` from any other file took the module file too; Python's finder takes the package folder first.

### Changes
- `_resolve_import`: in each folder the package `__init__.py` is tried before the module file; a name without parts (`from . import x`, `from .. import x`) has the package `__init__.py` as its only candidate, so a folder without `__init__.py` gives no dependency instead of the file beside it.
- Tests written first, `tests/test_python_package_and_module.py`: REQ-PY-021 (relative imports at the top of a file, inside a function and inside a method, `from .module import Name`, and the impact within two steps), REQ-PY-022 (`import <name>` and `from <name> import module` mean the package), REQ-PY-023 (a folder without `__init__.py`). All three fail on 0.16.0. `docs/assurance/requirements.md` regenerated.

### Verification
- The repository and commit of the issue (719 Python files), one worker: 0.16.0 gives 31 direct dependents and 292 within two steps, 226 of them through the tool; the branch gives 31 and 66, the same 66 files as a check written for this change (Python `ast`, package before module file, two steps of reverse imports), none missing and none extra. That check counts 66 where the issue counts 62: its rules for `import a.b` and for names imported from a package are not those of the issue's script, which was not available. The graph has one import edge more (1,590 -> 1,591): the tool now imports the package.
- Not changed: `from package import name` still looks for `name.py` only, not for a sub-package `name/__init__.py`; `from <name> import module` for a folder without `__init__.py` beside `<name>.py` still names `<name>/module.py`, where Python would read an attribute of `<name>.py`.

## 2026-11-19 - Release 0.16.0

### Changes
- Version 0.16.0, core and `packages/wingbox-http` (REQ-HTTP-021): the Code Mode text as an instruction (#171); `wingbox grep` and the MCP tool `search_source`, and an error for a malformed `wingbox query` expression (#172); the build of a large Python repository linear in its size, with the same graph byte for byte (#157, #174); the search hook waits for its timer thread, which ended the hook process with a segmentation fault on macOS (#175, merged into this branch after its own eleven checks were green). The map-request workflow builds with 0.16.0.

### Motivation
- The instruction form is what measured sessions in Code Mode answered from in two turns; the maintainer asked for it and for the MCP grade.

## 2026-11-19 - The build of a large Python repository is linear (#157)

### Why
- Issue #157: release 0.14.0 needs 752 s and about 5 GB for a repository of 5,182 Python files and 1.86 million lines (one worker), while 353 files of the same tree take 21 s and 102 MB. Two causes were named: a slow constant in the Python parser and a growth that was not found.

### What grew
- The repository keeps its public names behind a facade of three levels of `from .x import *` (package `__init__.py` -> `models/__init__.py` -> one `__init__.py` per model folder -> the files), and its tests import them from the facade (`from package import Name`).
- `SymbolIndex._from_globs` answered such a name by asking every glob import in turn, down to every file behind the facade: all of them for every distinct name. Every answer kept the set of modules and module names it had read (`_deps`), one set per memoised answer, so per importing file and name; every use site merged that set again; and `module_fragment` wrote it for every importing file into `.wingbox/graph-fragments.json`. Work, memory and the store grew with (files importing through the facade) x (modules behind it).
- Evidence, 0.14.0 on subsets of the repository with their tests (N model folders, the top level, the tests of those models): in the store of the 60-folder subset 138 of 832 modules list 200 to 300 modules read (the facade's whole closure of 245), in the 30-folder subset 73 of 573 list about 115. The subsets cut earlier (188, 353, 896 files) had the model folders flattened into one folder, so the facade led nowhere and they showed nothing.

### Changes
- `scripts/lang/symbols.py`: a search behind glob imports follows only the glob imports that reach a module holding the name (`_holding`, `_reach`, `_glob_routes`), plus those behind which the search would stop at a re-export cycle or at the depth limit (`_height`, `_tallest_globs`): such a stop decides which answers the index memoises, so the answers and the memo are those of the exhaustive search, question by question. What an answer read behind the globs of a module is one item, `("c", module)`; `glob_record` gives the glob targets and module names of one module.
- `scripts/build_code_graph.py`: `ModuleFragment.closures` (the modules whose glob imports a module searched) and `.globs` (its own glob record); the fragment store writes both (schema 2). `scripts/graph_update.py`: `glob_readers` finds, backwards over the stored glob imports, the modules from which a changed or deleted module, or a glob import a created path could answer, is reached; a module that searched one of them is redone. The condition is the one the old lists expressed, stored once per facade instead of once per reader.
- `scripts/lang/python/tree.py` (new): `index_tree` walks a tree once and keeps the nodes in preorder with depth and subtree end; the `ast.walk` order of any subtree is the slice sorted by depth. `parser.py` (imports), `dynamic.py` (`find_module_loads`, `find_registry_calls`) and the use collector of `symbols.py` (the names a function or class body binds, for every scope in one sweep) read it. Before: one `ast.walk` of the tree for the imports, one for the loads, two per function for the registry calls and one per loop body, and three walks of every function body in the use collector (`_stored_names`, `_declared`, `_local_types`; the last one only serves the full list of a query and is no longer run for a build).
- `scripts/lang/purpose.py`: the suffix tables are module constants; `_entity_role` and `_stem_role` keep the role per name (exact suffix first, then one edit away); "one edit away" is decided by `_within_one_edit` (common start, compare the rest) instead of a full edit-distance table.
- `scripts/lang/python/imports.py`: the folders a module name is looked for in are kept per importing folder and first segment (`_bases`); they were worked out, with `is_dir` calls up to the root, for every import of every file.

### Measurements (one worker, `WINGBOX_JOBS=1`, a shared machine under other load; seconds are wall time of `wingbox build .`, memory is the peak resident size sampled every 2 s)

| Tree | Files | Lines | 0.14.0: s / MB / store MB | Branch: s / MB / store MB |
|---|---|---|---|---|
| 30 model folders + tests | 573 | 276,756 | 33.7 / 134 / 3.96 | 13.0 / 124 / 2.93 |
| 60 model folders + tests | 832 | 349,893 | 77.0 (machine busy) / 222 / 7.33 | 16.5 / 148 / 4.04 |
| 120 model folders + tests | 1,325 | 517,874 | 68.3 / 514 / 17.69 | 24.8 / 184 / 6.42 |
| 240 model folders + tests | 2,285 | 837,080 | 120.1 / 1,701 / 49.84 | 39.2 / 265 / 11.40 |
| the whole repository | 5,182 | 1,861,171 | 752 / about 5,000 / 247 (issue; not rerun: over the 2 GB limit of this machine) | 102.1 / 588 / 27.49 |

- Branch, per 1,000 lines: 47, 47, 48, 47 and 55 ms; store per file 5.1, 4.9, 4.8, 5.0 and 5.3 KB. 0.14.0 from 120 to 240 folders: lines x1.6, memory x3.3, store x2.8.
- The generated project of the new test (40 and 160 model folders, 163 and 643 files): 0.14.0 asks the index 9,882 and 154,722 times (x15.7), its answers keep 81,613 and 1,286,413 items (x15.8), the store is 0.33 and 4.30 MB (x13.0), a test file is read from 254 and 974 items; the branch: 482 and 1,922 questions (x4.0), 3,010 and 12,010 items (x4.0), 0.10 and 0.39 MB (x4.0), 20 and 20 items.
- Profile of the 353-file tree (cProfile, whole build): 73.6 s -> 28.1 s. `parse_python` 53.5 -> 17.2 s: `use_sites` 19.1 -> 5.2, `infer_purpose` 15.3 -> 0.9, `find_registry_calls` 10.9 and `find_module_loads` 5.9 -> inside `index_tree` 7.7 (the one walk), `_candidate_bases` 6.6 -> `_bases` 0.6; `module_fragments` 15.1 -> 3.6. What is left on top: the one walk, the use collector, `capabilities.detect` (3.8), `ast.parse` (2.3).

### Verification
- Graph bytes, release 0.14.0 against the branch run in the same Python environment (the `built-from` line names the installed version): `index.sexp` is equal byte for byte on nine trees: the four subsets with tests above, four subsets of the package that the issue measured (188 files: 13.3 -> 6.0 s; 353 files: 21.3 -> 9.5 s; 382 files: 24.5 -> 9.3 s; 896 files: about 55 -> 19.9 s), and fastapi at commit 2a1bd23 (1,227 files), the repository of the build-time benchmark. `index.uses.sexp` is equal except the `:key` of its header line (a digest that includes the builder's own source files, so it changes with every change of the builder). Every parse result in `cache.json` is equal (all fields but `mtime`), except the order of the two use sites of one JavaScript file of fastapi, which also differs between two runs of 0.14.0 itself (the TypeScript parser, not touched here; the graph sorts them).
- The whole repository of the issue was built by the branch only: 0.14.0 needs more memory for it than this machine may give one build (2 GB). Its node and edge counts are those the issue reports for 0.14.0 (5,182 and 88,211) and its graph has the same size (7.9 MB).
- Tests written first, `tests/test_issue_157_build_growth.py`: REQ-PY-015..020, REQ-LANG-014..017, REQ-FRESH-054..056. On 0.14.0 the growth guard fails on the store size, the questions asked, the items kept and the walks per file (numbers above). `docs/assurance/requirements.md` regenerated.
- Not measured: a build with several workers (every measurement here ran with one). Each chunk of 64 modules still builds a symbol index of its own in its worker, as before.

## 2026-11-19 - The search hook waits for its timer thread before it returns

### Changes
- `src/wingbox/hook.py` `main`: after `out_of_time.cancel()` the timer thread is joined. Test REQ-INSTALL-047 (`tests/test_hook.py`): after `hook.main(["pre-search"])` returns, with an answer, without one and after an error, no thread but the caller's is alive; it failed before the change with the daemon timer still listed.

### Motivation
- Three failures of the test matrix on macOS in two days, each a `hook pre-search` child process that returned -11 (SIGSEGV) with nothing on stderr: REQ-FRESH-025 on Python 3.12 once, then REQ-INSTALL-020 on 3.12 and a freshness test on 3.13 in one run. The common part is the daemon `threading.Timer` added in 0.10.2 for the hook's time limit; a daemon thread that is still running during interpreter shutdown is a known way for CPython to crash. Not reproduced locally in 400 runs, so the proof is the deterministic test plus the matrix.

### Not covered
- Whether the crash is gone can only be seen over many runs of the macOS cells.

## 2026-11-19 - Code Mode text: an instruction first

### Changes
- `references/code-mode.md` (and its two plugin copies): for each question an instruction (what the call returns, what to do with it) followed by a short example whose last expression is the value of the block; the shared helper is gone, each example is one call. For `impact` the instruction is the text measured in Code Mode sessions: the three facts of the answer (each dependent once and never the file itself, the list not cut, read from every source file of the graph with test files included) and "let the value of that block be the object that maps each file to the list of its rows' files".
- `src/wingbox/install.py`: the line of the always-on block says the same in one sentence (the block stays under its 1,600 characters). `SKILL.md`: the pointer says "one call in one block of code whose value is the answer".
- `tests/test_json_errors.py` REQ-INSTALL-046: the expectation follows the new form on purpose (instruction sentences present, five examples, none ends with an assignment, the block's sentence). `docs/assurance/requirements.md` regenerated.

### Motivation
- Sessions in Code Mode with the earlier text (a helper and recipes that ended with `var x = ...`) took an extra model turn to print the result and re-checked the answer by text search in two of five sessions; with a plain instruction that states where the answer comes from and that the block's value is the answer, they answered in two turns and did not re-check. The maintainer: an instruction, not code.

## 2026-11-19 - Text search in the source files, and an error for a malformed query

### Changes
- `wingbox grep <text> [path-pattern]` (`cmd_grep`) and the MCP tool `search_source`: the lines of the source files of the graph that hold a text, with file and line; files are read only through `_source_file`, as `source` reads them; 300 characters a line, 200 matches and 100,000 characters an answer, `--first N` goes on (REQ-CS-145, asked in review: an answer must not put 700,000 characters into a model's context). `parse_search_pattern` takes `text=True`, in which every character but `.*`, `^` first, `$` last and the backslash is literal; the other commands keep the strict form.
- `wingbox query`: `_query_problem` refuses an expression whose parentheses do not match, whose string is not closed or that is empty, and an unknown function is an `(error invalid-query ...)` line instead of a comment and an empty result.
- Descriptions of `analyze_impact`, `list_layers`, `check_boundaries` and `query_graph` rewritten; `query_graph` names the tools to prefer; the server has fourteen tools.

### Motivation
- The tool directory graded the thirteen tools of 0.15.0 A 4.4 of 5: names 5, count 5, and two remarks: no tool searches text inside the source files, and `query_graph` does not say how a malformed expression is reported and names no tool to prefer. The maintainer asked to bring the grade to 5 of 5 while there are no clients to break.

### Verification
- Tests first: REQ-CS-135 to REQ-CS-144 (`tests/test_grep.py`), REQ-MCP-036 and REQ-MCP-037, REQ-MCP-029 and REQ-MCP-021 reworded for fourteen tools and for `query_graph` naming its neighbours. Eight of the nine first tests failed before the code and pass after it.
- `wingbox impact scripts/code_graph_search.py src/wingbox/mcp_server.py 2` before the edit named 12 and 18 test files; those were run.


## 2026-11-19 - Release 0.15.0

### Changes
- Version 0.15.0, core and `packages/wingbox-http` (REQ-HTTP-021): the MCP tools named verb_noun with `list_files` and `read_source`, the commands `wingbox files` and `wingbox source` (#155); workflow actions pinned to commits, `tools/release_files.py` for the checksum file and the SBOM of a release (#154); `docs/assurance/` (#156); the roadmap item for the Evidence Assurance Layer (#159). The map-request workflow builds with 0.15.0.

### Motivation
- The maintainer asked for the MCP tool names while there are no clients to break, and for the assurance material; released together.

## 2026-11-19 - Roadmap: an Evidence Assurance Layer

### Changes
- `ROADMAP.md`, first item of "Next": a deterministic Evidence Assurance Layer above the code graph and the pull request check, as three libraries (`wingbox-evidence`, `wingbox-acceptance`, `wingbox-controls`), with no language model and with independent tests; the task is issue #158.

### Motivation
- The maintainer set the task and decided against a separate compliance agent.

## 2026-11-19 - docs/assurance: requirements, verification plan, known limits, tool errors (#153, part 2)

### Why
- Issue #153: the material a team needs to justify the tool exists in pieces. Every test names a requirement, and no document lists them; limits are spread over the README, the changelog and the log; nothing says which wrong answer could hide a defect and what catches it. This change puts that in one folder, from the repository alone: no benchmark was run and no number is new.

### Changes
- `tools/requirements_index.py` (new, standard library): reads the docstring `/* REQ-<AREA>-<n>: text */` of every function under `tests/` with `ast` (nothing is imported or run) and writes `docs/assurance/requirements.md`: a summary table, then one table per area (the id without its number) with the id, the text on one line and the test as `file::Class::method`; rows by number, then test. Markup is escaped outside code, every pipe everywhere. A test without an id is named at the top. `--check` exits 1 when the file is stale and writes nothing.
- `docs/assurance/requirements.md` (generated): 764 tests, 751 ids, 43 areas; every test carries an id.
- `docs/assurance/verification-plan.md`: the claims of the README's first screen and of the seven benchmark pages, each with the file its number comes from, the test class or benchmark that answers it and the command that reruns it; what keeps a published number from drifting; the missed-edge assessment per language as planned work, in three lines.
- `docs/assurance/known-limits.md`: per language (and for every language, layers, installing), what the graph does not see and what that means for an answer, each line with its source: README sections, `docs/edge-model.md`, the benchmark pages, the changelog, the two "Not covered" entries of this log, and test docstrings.
- `docs/assurance/tool-errors.md`: six kinds of wrong answer (a missing edge, an extra edge, a stale graph, a skipped language, a part that was not checked, a wrong layer): whether each can hide a defect, the fact and exit status Wingbox prints today with the command and the test, and the independent check.
- `docs/assurance/README.md`: what the folder is, how to regenerate the generated page, and what the material is not.
- `tests/test_assurance_docs.py` (new): REQ-DOC-004 (the committed index equals the generator's output), REQ-DOC-005 (tables, rows, order and escaping on a stand-in tree), REQ-DOC-006 (`--check` names a stale index, a run repairs it), REQ-DOC-007 (every relative link and every `tests/<file>.py::<Class>` of the folder exists).
- `.github/workflows/docs.yml`: the documentation job also runs `tests.test_assurance_docs`, so a change of a page alone is checked. `AGENTS.md`, section 2: run the generator after a test changes. `README.md`, "Project resources": one line. `CHANGELOG.md`: one entry.

### Written "not assessed", because the sources do not say
- Verification plan: "no missed files" for C#, Java, Go, Ruby, Dart and Pascal (no benchmark result for them) and for questions other than the importers of a file and the uses of a symbol; the result file and the rule behind the line "Up to 5x faster, up to 8.8x fewer tokens, up to $484 saved per 1,000 questions" of the README; a session inside each assistant; a rerun of the time "about a second on a 1,200-file project".
- Known limits: how complete the call commands are without `--lsp`; other limits of the Python and TypeScript graphs; Rust code that build scripts and procedural macros generate; what else the C#, Java and Go graphs miss; how often the Ruby and the Pascal limits occur; Pascal beyond "no LSP enrichment"; a public Dart project against its imports; the infrastructure map.
- Tool errors: whether `boundaries --strict` counts a skipped language as not checked. A test of the `wingbox-graph-stale` line at the start of an MCP answer was not found.

### Seen while writing, not changed here
- A stale graph and a skipped language leave the exit status of a query at 0; a script has to read `graph.stale` or standard error, and a gate the file `.wingbox/graph-skipped.sexp`.
- For a language Wingbox does not parse, the build prints the suffixes it reads and no count of the other source files.
- Twelve requirement ids are carried by more than one test, each test with its own text (REQ-CLI-008, REQ-CLI-009, REQ-CS-060, REQ-CS-077, REQ-MCP-011, REQ-PY-001 to REQ-PY-005, REQ-RUST-031, REQ-UPDATE-008); the index lists one row per test.
- One list of the defects that changed an answer, across versions, is not written; `known-limits.md` says where they are recorded.

### Verification
- The statements "how it is reported" of `tool-errors.md` were run once on two small projects with this checkout: `impact` of an unknown file (`(error no-match ...)`, exit 1), a changed file with `WINGBOX_AUTO_REFRESH=0` (the warning on standard error, `"stale": 1` with `--json`, exit 0), a build without the Dart parser (the warning, `.wingbox/graph-skipped.sexp`, exit 0, the note at the next query), `boundaries all` and `--strict` on a part with too few files (exit 0 and 2), `layers`, `module --lines`, a `.swift` file.
- `tests.test_assurance_docs`, `tests.test_english_only`, `tests.test_readme_numbers`: pass. The whole suite: see the pull request.

## 2026-11-19 - Release integrity: actions at a commit, checksums and SBOM, a build that repeats (#153, part 1)

### Why
- Issue #153: a team that relies on Wingbox as an engineering control has to justify the tool itself. Its table for 0.14.0: 11 of 13 `uses:` lines of the workflows named a tag, the release assets carried no checksum, there was no SBOM, and whether two builds of a tag give the same bytes was not tested. This change closes those four rows. Publishing through trusted publishing needs a setting in the maintainer's PyPI account, so it is written down as a next step and nothing about how a release is published changes.

### Changes
- `.github/workflows/*.yml`: all 13 `uses:` lines name a full commit with the release tag as a trailing comment. `actions/checkout` v4.4.0 `11d5960a326750d5838078e36cf38b85af677262` (7 lines), `actions/upload-artifact` v4.6.2 `ea165f8d65b6e75b540449e92b4886f43607fa02` (4 lines; the line of `map-request.yml` already named this commit with the comment `v4`), `aileron-tech/wingbox-action` v1.4.2 `ef3733e3c0394c1e5ecf44d6c369c5a213b5d9d9`, `sigstore/cosign-installer` v4.1.2 `6f9f17788090df1f26f669e9d70d6ae9567deba6` (unchanged; v4.1.3 exists and is not taken here). Each commit was read with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`; all four tags are lightweight and point at a commit, and the commits of `v4`, `v4` and `v1` are the same as those of the three release tags, so no workflow runs other code than before.
- `tools/release_files.py` (new, standard library): for a release folder writes `SHA256SUMS` (every file of the folder, the SBOM included, hidden files left out: `uv build` puts a `.gitignore` there) and `<name>-<version>.cdx.json`, a CycloneDX 1.5 document with the package, its built files with their SHA-256 and the dependencies `pyproject.toml` pins (those only an extra names are `optional`; an environment marker is kept as a property). No timestamp and a serial number derived from the content: the same input gives the same bytes. A dependency without `==`, or a folder without a built file of the package, is exit 1 and nothing written.
- `tests/test_release_integrity.py` (new): REQ-REL-001 (the pins), REQ-REL-002 (the checksum file), REQ-REL-003 (the SBOM on a stand-in folder and `pyproject.toml`), REQ-REL-004 (the refusals), REQ-REL-005 (the SBOM of this repository names exactly the pinned dependencies; standard library only), REQ-REL-006 (`uv build` twice with `SOURCE_DATE_EPOCH`, the source archive and the wheel compared; skipped without `uv`).
- `AGENTS.md`: the prefix `REL` and the rule for `uses:` lines in section 2; step 7 of section 3 (the two commands that write and attach the files); the row "Checksums and SBOM" in 6.1; in 6.4 the note that `wingbox.yml` follows a release of the action only when its commit is changed.
- `docs/assurance/release-integrity.md` (new): what each file shows and how to check it, how to rebuild a release from its tag, what is not shown (no signature, no provenance on PyPI), and the trusted publishing steps with the proposed workflow, marked not active.
- `CHANGELOG.md`: three lines under "Unreleased".

### The build
- No change of the build configuration was needed: two builds of the same tree gave the same bytes from the first run of the test. The backend stamps every file with a fixed date (2020-02-02 without `SOURCE_DATE_EPOCH`, the given date with it).
- A shallow clone of the tag `v0.14.0`, built with `uv build` and `hatchling==1.33.0` (the version the wheel on PyPI names in its `WHEEL` file) under Python 3.11, 3.12, 3.13 and 3.14 on macOS, gave each time the two files PyPI serves: wheel `0bf88e2f...c74107`, source archive `1080db73...4b7b71`. With `SOURCE_DATE_EPOCH` set the digests differ (other dates in the archives), so a rebuild that is compared with a release leaves it unset.
- Not assessed: the same bytes between operating systems; `wingbox-http`. `pyproject.toml` does not pin the build backend; whether to pin it is the maintainer's decision.

### Verification
- The five tests of the pins and the release files failed before the workflows were changed and the tool was written, then passed; the build test passed at once.
- The SBOM of this checkout and the one of the stand-in folder validate against the CycloneDX 1.5 JSON schema of the specification repository (`bom-1.5.schema.json`, checked with `jsonschema`, 0 errors); the schema is not vendored.
- The commands of step 7 and of the new 6.1 row were run on a folder built from this checkout: `shasum -a 256 -c SHA256SUMS` answered OK for the six files (the two of `wingbox`, the two of `wingbox-http`, the extension, the SBOM).
- The whole suite: see the pull request.

## 2026-11-19 - MCP tools named verb_noun; `files` and `source`; parameters explained

### Why
- The directory scored release 0.14.0: A, 4.6 of 5 across 11 tools (0.13.0: 4.0). What it still named: Naming Consistency 4 of 5 ("'graph' and 'facts' lean action/verb-ish while the rest are nouns"); Completeness 4 of 5 ("no direct file listing or raw source retrieval"); per tool, "Parameters" 3 on four tools and 4 on the rest (the description added nothing to the schema about the parameters), "Usage Guidelines" 4 on two. The maintainer asked to rename again while the server has no clients, and to fix what is named.

### Decisions
- Names: verb_noun for every tool. A tool that builds has no good plural noun, and the one-word pattern was read as broken three times (`build`/`query`, `module`, then `graph`/`facts`); verb_noun is the form the directory's own examples use.
- Completeness: two tools for the two gaps named, each over a new command, so that the rule "a tool returns the output of the command behind it" holds: `files` and `source`. `source` is useful where the agent cannot open the file itself: over the HTTP transport.
- `source` reads only what the graph holds: the target must be a module of the graph, a file of one, or a definition in one; a path with `..`, an absolute path outside the root and a file that is not source are refused with `(error no-match ...)`. 400 lines a call (`SOURCE_LINE_LIMIT`), with `:more` and `:next`.
- Hardening from the security review of the pull request, each with a test (REQ-CS-131..134): the file is read only when its real path equals the root's real path joined with the path the graph holds (`_source_file`), so a source file replaced by a symbolic link after the build is refused wherever the link points. The review's probe by file path was refused by accident; by module ID and by definition name the first version printed the link's target, a `.env` of the repository: found by writing the test for every form of the target. Without a root nothing is read. One answer holds at most 100,000 characters and a line is cut at 2,000 (`:cut N`). `cmd_source` checks `first` and `last` itself, and a read error prints the reason without the server's folder.

### Changes
- `scripts/code_graph_search.py`: `cmd_files`, `cmd_source` (`_source_module`, `_source_target`), their validation, dispatch and usage lines.
- `src/wingbox/mcp_server.py`: thirteen tools `build_graph`, `analyze_impact`, `describe_module`, `list_files`, `read_source`, `find_definitions`, `find_usages`, `list_dependencies`, `trace_calls`, `list_layers`, `check_boundaries`, `list_changes`, `query_graph`; every description names its parameters in backticks; `build_graph` says when it is not needed, `list_changes` what it does not compare.
- Tests: `tests/test_files_source.py` (REQ-CS-124..130, written first); `tests/test_mcp_tools.py`: REQ-MCP-029 (thirteen verb_noun names; the old expectation, eleven one-word nouns, is what this change replaces on the maintainer's word), REQ-MCP-021 (also: every parameter named; 900 characters), REQ-MCP-030 and 032 for the names, new REQ-MCP-034 (files and source through the server, refusals included) and REQ-MCP-035 (one line limit). Every other test that calls a tool uses the new name; assertions unchanged except the quoted tool names.
- README (tool table, "Common code searches", what a token holder can read over HTTP), `packages/wingbox-http/README.md`, `SKILL.md` and its copies, `references/sexp-notation.md` (`files`, `source`), the plugin README, `AGENTS.md` section 5.

### Verification
- 104 tests of the MCP server, the HTTP transport and the new commands pass; the directory's linter `npx mcp-tdqs lint` on the branch: see the pull request.
- The graded score is the directory's and is read after the release.

## 2026-11-19 - Release 0.14.0

### Changes
- Version 0.14.0, core and `packages/wingbox-http` (REQ-HTTP-021): S-expressions as the default output of the query commands with `--human` and `--json`, `impact` for several files, one error object for a failed `--json` call, the code for Code Mode in the skill (#150); the MCP tool names `graph`, `modules`, `facts` and the new descriptions (#148); the installed block that puts the graph before text search (#149). The map-request workflow builds with 0.14.0. The module text of `mcp_server.py` names the tools as they are now.

### Motivation
- The maintainer decided the default output and asked for ready code for Code Mode; a second breaking change for MCP clients goes out together with the first of this release, under one note.

## 2026-11-19 - The always-on block: the graph before text search, tests from the impact

### Why
- The owner's words: an instruction he found good in his own agents' work ("Prefer Wingbox to grep, rg, find and opening files for research, search and reading code: ask the graph first, then read only the files and lines it names. A text search is the fallback...") belongs in the block Wingbox installs, together with the rule for validating tests. A field report of a team of about thirty agents the same day said the same from the other side: one worker tested only the edited file, a dependent's test failed and the main branch went red.

### Changes
- `src/wingbox/install.py`, `ALWAYS_ON_BLOCK`: the first bullet is now the preference for the graph over `grep`, `rg`, `find` and opening files, with the fallback named; a new bullet ties tests to the impact analysis and asks, for every changed function, for a test that reaches it, and says that this proves reach, not the strength of the assertions. 1,147 characters (was 545).
- `tests/test_install.py`, REQ-INSTALL-044: the phrases, that every `wingbox <command>` the block names exists, ASCII, at most 1,600 characters.

### Verification
- `tests.test_install`, `tests.test_plugins`, `tests.test_english_only`: pass. An installed block is replaced between its markers by `wingbox install` and `wingbox update` (REQ-INSTALL-012, unchanged).

## 2026-11-19 - S-expressions by default, `--human` and `--json`; `impact` for several files; `--sexp` kept as 0.13 printed it

### Why
- The maintainer decided that the default output is S-expressions, with `--human` for people and `--json` for code, and that one `impact` call should answer for several files. Coding agents pay for every token they read; in a measurement of an agent that works through scripts, the agent called Wingbox one file at a time and then did not trust the answer. So the answer is compact, complete, says whether the graph is current, and one call covers the files a change touches.
- The pull request check (`aileron-tech/wingbox-action`, used with any Wingbox version) calls `impact <file> <hops> --sexp --all`, `usages <target> --sexp --all`, `module <id>`, `build` and `boundaries` and parses the flat facts of 0.13. Those outputs are a contract: they stay byte for byte.

### Changes
- `scripts/code_graph_search.py`: one form option per call, read by `answer_form`: none (S-expressions, complete), `--human` (the text for people, with its list limits unless `--all`), `--json` (one JSON object) and `--sexp` (form "flat": what 0.13 printed with it). Two at once are an invalid argument. `main` reads the form and calls `_answer`; with `--json` the standard output of the command is captured and turned into `{"facts": [...]}`, but for `impact` and `usages`, which build their own document. `impact` takes several targets (`impact_arguments`: of several arguments a last whole number is the depth), builds one `impact_answer` per target (the advice of the graph worked out once for a call with several files) and prints them through `scripts/impact_forms.py`; the answer for several files opens with `graph_header` (time the graph file was written, number of files, number of changed files it is known to miss). The flat facts are printed by `_print_impact_flat` and `_print_usages_flat`, the code of 0.13 moved as it was, with exit status 0 for a file that is not in the graph. In every other form `impact` exits 1 when a file is not in the graph. An option `impact` or `usages` does not know is an invalid argument instead of being read as a depth or dropped. `main(args, graph_stale=None)`: the `wingbox` command passes the number of changed files the freshness check found.
- `scripts/impact_forms.py`, `scripts/usages_forms.py` (new): the answer as one value (`ImpactFound`, `ImpactMissing`, `GraphHeader`; `UsagesFound`, `UsagesAmbiguous`, `UsagesMissing`) and its printers: the compact form, JSON and the text for people (the text is the code of 0.13 moved here). `scripts/fact_json.py` (new): one fact line as a JSON object with stable keys.
- `src/wingbox/cli.py`: `advice`, `align`, `place` and `compare` print their facts unless `--human` is given, take `--json` through `_answer_command`, and still take `--sexp` (their facts, as before). `align --learn` runs the questionnaire for a person only when neither `--sexp` nor `--json` asks for facts. `_refresh_before_query` returns `freshness.known_stale` of the check. `init`, `learn`, `install`, `uninstall` and `update` are unchanged.
- `src/wingbox/mcp_server.py`: the `impact` tool takes `target` or `targets` and returns the default form with the graph fact for several files; `GraphReady.stale` carries the number of changed files. `all` is still accepted there and changes nothing. `usages` still answers in text; the other tools are unchanged.
- `src/wingbox/install.py`: one line more in the always-on block (the call for several files from a script). `SKILL.md`: the command table follows the new default, a short section "Use from code", and `--sexp` is named only for the commands that talk to a person and where the text says what it still prints. Plugin copies synced.
- Benchmarks: `benchmarks/impact/impact100.py`, `benchmarks/php/tools/verify.py`, `benchmarks/usages/usages.py`, `benchmarks/graphify/tools/command_check.py` and `benchmarks/graphify/tools/formula_test.py` ask the Wingbox under test for the form they read. `text_form` gives `--human` when the help of that Wingbox names `--human|--json` and nothing for an earlier release, so a run with a pinned release (0.2.3, 0.5.1) sends the same command as before and a run with this checkout reads the same text as before. No benchmark was run; no result file, report or published number was touched.
- Documents: `README.md` (the examples that show output name the option that prints it, the default form of `impact` and `usages`, several files in one call, the table of MCP tools, the paragraph on forms and limits), `references/sexp-notation.md` (forms of an answer; `impact` and `usages` with their flat facts under `--sexp`), `docs/layer-control.md`, `AGENTS.md` (the rule on output formats and the compatibility promise of `--sexp`), `CHANGELOG.md`.

### Which default outputs changed
- Changed: `impact`, `usages` (text before, the compact S-expression now), `advice`, `align`, `place`, `compare` (text before, their facts now); `analyze` and `quick` print the same lines without the 200-dependent limit; `impact <file> <not a number>` reads the second argument as a file (it was a bad depth, exit 2); `impact` of a file that is not in the graph exits 1 (0 with `--sexp`).
- Not changed: every other query command without an option (`module`, `deps`, `rdeps`, `boundary`, `entity`, `flow`, `search`, `layers`, `boundaries`, `boundaries --strict`, `invariants`, the `find_*` commands, `grep_functions`, `diff_functions`, `query`), everything with `--sexp`, `build`, `init`, `learn`, `install`, `uninstall`, `update`, the hooks.

### Tests
- New: `tests/test_output_formats.py` (REQ-CS-104..123), `tests/test_output_formats_cli.py` (REQ-CLI-039..043, REQ-MCP-033, REQ-BENCH-020, REQ-INSTALL-045), `tests/output_trees.py` and `tests/data/outputs_0_13_0.json`: what the code of the tag v0.13.0 prints for 90 calls (49 without a form option and 23 with `--sexp` on four fixture trees through the search script, 18 through the `wingbox` command in a layered project), written before any code changed. `--human` is compared with the kept default byte for byte (REQ-CS-115, REQ-CLI-042), `--sexp` with the kept `--sexp` (REQ-CS-122, REQ-CS-123, REQ-CLI-042), `module`, `boundaries` and `boundaries --strict` with the kept output and exit status (REQ-CS-116, REQ-CLI-042, REQ-CLI-043).
- Expectation changed: REQ-CS-043 (`impact <file> many` is one more file, exit 1; a depth out of range is still exit 2), REQ-MCP-003 (the `impact` schema has no required argument: `target` or `targets`), REQ-MCP-004 (the MCP `impact` answer is the default form, not the text), REQ-CLI-024 and REQ-BCOV-006 (`align`, `place` and `advice` print text with `--human`).
- Call adapted, expectation unchanged: the tests that read the text of `usages` ask for `--human` (REQ-CS-062, 064, 065, 066, 067, 068, 069, 070, 072, REQ-TS-SYM-004); the tests that call `cmd_impact` name the form, "human" or "flat" (REQ-CS-038..042, 044, 048, 053, 057, 076, 077, 082, REQ-PY-005); the stand-in script of REQ-CLI-007 takes `graph_stale`.

### Sizes measured (for the maintainer; not published anywhere else)
Characters of `wingbox impact <file> 2 --all` for ten files of each repository (a sample with seed 20261119 among the files of the graph that another file depends on), one process at a time, this checkout:

| Repository (files in the graph) | `--human` (the default of 0.13.0) | `--sexp` (flat facts) | default now | one call for the ten files | ten calls, default |
|---|---|---|---|---|---|
| wingbox, this checkout (293) | 9,378 | 12,584 | 5,563 | 5,619 | 5,563 |
| fastapi `2a1bd23` (1,227) | 89,963 | 94,329 | 36,327 | 36,384 | 36,327 |
| immich server `e5d6eb7` (707) | 78,388 | 99,866 | 38,923 | 38,979 | 38,923 |

The call for ten files is the ten answers plus the graph fact (56 or 57 characters): it saves nine commands, not characters. The `read-first` line repeats the files of depth 1 and is 34 %, 5 % and 10 % of the default form in the three samples.

### Verification
- The two new test files failed on `main` before the change (import of `scripts.fact_json`), then passed. The whole suite: see the pull request.
- Run by hand in this checkout after `wingbox build .`: `wingbox impact scripts/lang/treesitter.py` (one form, 13 direct, 25 in all), `wingbox impact scripts/lang/treesitter.py scripts/impact_forms.py nowhere.py 1` (the graph fact with `:stale 0`, two forms, `(error no-match nowhere.py)`, exit 1), `--json` of the same, `wingbox usages scripts/fact_json.py::atom` and its `--json`.
### The JSON form for code: one error object
- `scripts/fact_json.py`: `with_error(document, fallback)` puts `error: {code, message}` first in a document whose call failed as a whole (the first error fact, the error of a `usages` answer, or the fallback for an empty answer); `facts_json` keeps a fact whose quoted text holds line breaks as one object.
- `scripts/code_graph_search.py` `main`: in the JSON form a missing graph (the search stops the process) is caught and answered as `{"error": {"code": "no-graph", ...}}` on standard output with the same exit status; `src/wingbox/cli.py` `_answer_command` does the same for the architecture commands.
- `references/code-mode.md` (new, with its two plugin copies): the helper and five recipes for Code Mode, in the form the model writes by itself in a Code Mode session (`.nothrow()` then `.json()`, an array inside `` $`...` `` as separate arguments); run as written in a Code Mode session on a small project before it was added. `SKILL.md` and the always-on block point to it.
- Tests REQ-INSTALL-046 (every call of the recipes answers on a real project with the keys the recipes read) and REQ-CLI-047 (the rows of `impact --json` hold each dependent once, never the file, and `total` is their number). REQ-INSTALL-015 expects the third reference file of the skill: the packaged skill grew by it on purpose.
- Tests `tests/test_json_errors.py`, REQ-CLI-044..046; all three failed before the change. Found by calling every query command with `--json` in its failing cases while writing the code a script uses: without a graph nothing was printed on standard output, and the message of a failed git command became one object per line.

## 2026-11-19 - MCP tools: three names become nouns, every description rewritten

### Why
- The directory that grades the server scored release 0.13.0 on 2026-11-19: A, 4 of 5 across 11 tools (it was B, 3.4, on 18 tools). What kept it from 5: Naming Consistency 4 of 5 ("two verbs (query, build) among noun-like names, and module is singular"); Disambiguation 4 of 5 (overlap of impact and dependencies, of usages and calls; query as a catch-all); Completeness 4 of 5 ("no name-pattern/symbol search", which `definitions` has and did not say). Per tool the average was 4.1: `calls`, `changes`, `dependencies` and `layers` got 3.6, each for the same reasons: no verb at the start, nothing about the shape of the answer, the 200-item cap or an unknown name, and the telegraphic "take X". The maintainer asked to go for 5 of 5 and chose to rename the three tools now.

### Changes
- `src/wingbox/mcp_server.py`: `build` -> `graph`, `module` -> `modules`, `query` -> `facts` (no aliases); the eleven descriptions rewritten in one form (a verb and what comes back, "Use it ...", the neighbour as "use `name`", the cap, the unknown name), with the two shared sentences `_CAP` and `_MISS`; the instructions and the "graph is missing" answers name `graph`. Titles and the read-only hint follow the names.
- `scripts/code_graph_search.py`, `cmd_query`: `(help)` prints the functions of the query language. The description of the tool promised it; only `wingbox repl` did it.
- Tests: `tests/test_mcp_tools.py` REQ-MCP-021 rewritten for the new form of a description (its old expectation, "take <name>" and 400 characters, was the form this change replaces), REQ-MCP-029 and 030 for the new names, new REQ-MCP-031 (`(help)`) and REQ-MCP-032 (the three tools under their new names, the old names unknown). The other tests that call the three tools use the new names; their assertions are unchanged except the quoted answer "call the `graph` tool first".
- README table, `SKILL.md` (and the plugin copies through `tools/sync_plugin.py`), the plugin README, `AGENTS.md` section 5 (names are nouns; the form of a description; the directory's linter before a pull request; how to get a new score).

### Verification
- `python -m unittest tests.test_mcp_tools tests.test_mcp_server tests.test_skipped_language_notice tests.test_http_transport tests.test_mcpb tests.test_mcp_registry`: 63 tests pass.
- `npx mcp-tdqs lint --command "python -m wingbox mcp"` on the branch: see the pull request.
- The graded score is the directory's and is read after the release.

## 2026-11-19 - AGENTS.md: what follows every release

### Why
- The maintainer asked for the steps after each release to be written down as a pipeline with a checklist: update the site, check the README, update the GitHub description, update the description where the MCP server is listed when needed, and everything to check right after a release, for GitHub Actions and GitLab too. Until now these steps lived in the heads of the sessions that release.

### Changes
- `AGENTS.md`, new section 6 "After every release": 6.1 the release exists everywhere (PyPI for both distributions, the GitHub release and its assets, the workflows, the MCP registry, the plugin pin, the Homebrew tap, a clean install), each with its command and the expected answer; 6.2 the descriptions (README, GitHub "About", the PyPI text, plugin and store texts); 6.3 the places that list the MCP server, with where each takes its text from; 6.4 the pull request check in GitHub Actions and GitLab CI (default version, live reruns, Marketplace, our own workflow); 6.5 the sites; 6.6 the report. Section 3 ends with a pointer to it.

### Verification
- Every command of 6.1 was run against release 0.12.2 on 2026-11-19: the registry query with `version=latest` answered 0.12.2 `active`; `gh release view` listed `wingbox-0.12.2.mcpb` and `wingbox.mcpb`; `gh repo view --json description,homepageUrl,repositoryTopics` answered the description, `https://wingbox.dev` and 20 topics; `scripts/bump_version.py` and the two GitLab templates exist at `v1.4.0` of the action.
- `python -m unittest tests.test_english_only`: pass.

## 2026-11-19 - Release 0.13.0

### Changes
- Version 0.13.0, core and `packages/wingbox-http` (version, its `wingbox==` pin and the `http` extra of the core, REQ-HTTP-021): the parsers of every language back in the package and the download removed (#142), the MCP server over HTTP as the package `wingbox-http` (#129), eleven MCP tools (#130), `boundaries --strict` (#123), baseline entries with owner, reason and end date (#125), the source line on edges and the `built-from` fact of the graph (#124), Python imports of a package kept under `src/` (#136). The map-request workflow builds with 0.13.0.

### Motivation
- The maintainer asked to bring every open pull request in and to close the open issues.

## 2026-11-19 - Edges carry their source line; the graph names what it was built from (issue #124)

### Changes
- `scripts/lang/interface.py`: two optional fields of the parse result, so a parser that fills neither keeps working: `FileResult.import_lines` (an entry of `imports`, the module as written, to the 1-based lines of the statements that import it, ascending) and `line` on an imported name (`ImportedName.line`, left out of the payload when 0 and of the identity of the name). `FileResult.text_hash` is the sha256 of the text the parser read. `scripts/lang/source.py`: `add_import_line`.
- The ten parsers fill them from the position they already hold: Python (`ast` node line of `import` and `from ... import`), TypeScript and JavaScript (start of the import or export statement, of an `import()`, `require()` or mock call with a literal name; one tree walk now serves specifiers, forms and lines where two served the first two), Go (import spec), Java (import declaration, and the types a wildcard import brings in), C# (using directive, and the types it brings in), Ruby (`require`, `require_relative`), PHP (`use` declaration), Dart (import, export, part and part of directives), Rust (`use` and `mod` declarations), Pascal (the name in a `uses`, `requires` or `contains` clause, an include directive). `additional_import_lines` on the bound Python and Go languages gives the lines for the targets of `resolve_additional_imports` (`from package import module`; every file of an imported Go package); the Python one answers once per file, since the build asks for the targets and then for their lines.
- `scripts/build_code_graph.py`: `_module_facts` puts `lines` on an IMPORTS edge (the union over the import strings that resolve to the target) and on a module-level REFERENCES edge of an imported name; `render_sexp` writes them as the last mark, ` :line 7` or ` :lines 3 9`, before the lists of names of an imports fact (`_edge_lines_sexp`), so a fact starts as it did. No line is written for a fact no single statement states: an import followed through a Rust facade, a type of the file's own package or namespace, a constant, class or view found by a convention, a Python name passed to `importlib.import_module`, a Blade template, a REFERENCES edge from a class naming its base or from a resolved use (their positions are in the use file), DATA_FLOWS_TO and CALLS_API. The cache stores both new fields and its schema goes from 6 to 7. The fragment store keeps the edges as they are, lines included, so a partial update still publishes the bytes of a build.
- `scripts/graph_snapshot.py` (new): `Snapshot(tree, commit, version)`, `tree_digest` (sha256 over the sorted pairs of path relative to the root and text hash, 32 hex digits), `snapshot_fact`, `read_snapshot`, and `clean_commit`, the one function that asks Git (`rev-parse --verify HEAD`, then `status --porcelain -- .` with `GIT_OPTIONAL_LOCKS=0` and a timeout; no commit with any change or untracked file, outside a repository, or without Git). `build_code_graph.built_from` makes the snapshot from the parse results; `render_sexp` writes `(built-from :tree <digest> [:commit <sha>] [:wingbox <version>])` after the header when it is given one. The build and `graph_update.update_graph` both pass it.
- The digest is of the text as the parsers read it (decoded, without a byte order mark, every line ending `\n`), not of the raw bytes: REQ-LANG-013 and REQ-PORT-004 require the graph of a project saved with a BOM or with CRLF to be byte for byte the graph of the same project without, and a Windows checkout of a commit must give the digest a Linux checkout gives. The digest is always written; the commit is added when the tree is clean. The issue proposed one or the other; writing both keeps two builds of the same files identical whatever Git says and never leaves a reader to guess which of the two a value is.
- `scripts/code_graph_search.py`: `Edge.lines`; the loader takes `:line` / `:lines` of imports and refs out of the marks, so every output that prints the marks is unchanged; `CodeGraph.built_from`. `module <id> --lines` prints the edges with their lines, `search <pattern> --lines` the facts as stored. Without the option `search` and the fallback of `quick`, which read the file as text, see each fact as it was before (`_fact_without_lines`): no line mark, no `built-from` fact. A graph without lines and without the fact loads as before.
- `README.md`, `SKILL.md`, `references/sexp-notation.md`, the plugin's copy of the skill, `CHANGELOG.md`.
- Tests `tests/test_edge_lines.py`, REQ-CS-097..103; with the changed sources taken from `main` six of the seven fail (the seventh tests the new pure module): Python lines (`:line 7`, `:lines 3 9`, the refs of an imported name, none on the refs from a base class); TypeScript lines (static, type, `import()`, re-export); twelve commands print the same bytes from the graph with lines and from the same graph without them, and only `module --lines` and `search --lines` differ; the eight other parsers give the line of each import statement and none for an import nothing states; the same files give the same graph and digest (incremental, `--full`, another folder, CRLF with a BOM) and a changed character another digest; the commit is named only for a clean tree; the snapshot fact is a pure rendering.
- Existing expectations changed on purpose, because the stored fact now ends with its line: `tests/test_install_list.py` (REQ-FRESH-050, REQ-FRESH-051: `(imports pr1 pr2 :line 1)`), `tests/test_issue_105_bom.py` (REQ-PY-006, REQ-LANG-013: the refs and imports facts it looks for carry `:line`; its byte for byte comparison of the two graphs is unchanged and passes), `tests/test_php_language.py` (REQ-PHP-002: each imported name carries the line of its `use` statement).
- Measured on this repository (280 files, the same tree built by `main` at 0.12.2 and by this change, `WINGBOX_JOBS=1`, one run at a time, on a shared machine): `.wingbox/index.sexp` 393,421 bytes before, 404,301 after (+10,880, 2.8 %); 1,124 of its 1,325 imports and refs facts carry a line. `wingbox build . --full`: 10.44, 8.99, 9.50 s before; 9.28, 9.35, 9.19 s after. `wingbox impact src/wingbox/cli.py`: 0.37, 0.33, 0.30 s before; 0.39, 0.32, 0.31 s after, with identical output. A first version asked the Python resolver twice per file and measured 10.64, 10.29, 10.39 s for the build; the answer is now kept per file.

### Motivation
- Issue #124: an edge named two modules and nothing else, so a reader who wanted the line that produced it had to search the file again, and a fact taken out of the graph could not be tied to a state of the repository. For a review, or for evidence that must point at its source, the module pair is not enough. The graph file is read by every command and, as text, by the search hook, so the change had to leave old graphs readable, every existing output as it was, and the size and the query time where they were.

## 2026-11-19 - The Wingbox pull request check on this repository's own pull requests

### Changes
- `.github/workflows/wingbox.yml`: on every pull request the released action (`aileron-tech/wingbox-action@v1`) writes its one comment: who uses what the pull request changes, the tests that use it, the review plan. Default settings; it fails no job.

### Motivation
- The maintainer asked for the check on our own builds.

## 2026-11-19 - Baseline entries with owner, reason and end date; fix advice is not hidden by inaction (issue #125)

### Changes
- `scripts/architecture/baseline.py` (new, pure): `Acceptance(owner, reason, expires)` with `stated()` and `expired(today)`, `terms_of` (the three keywords among the tokens of a fact), `terms_sexp`, `expired_sexp`, `read_baseline_terms` (the entries of `.wingbox/baseline.sexp` that state a term, keyed by `(baseline | baseline-capability, part, source or module, target or capability)`). An entry is expired from the day after `:expires` and when the value is not a `YYYY-MM-DD` date: a date nobody can check never hides a finding.
- `scripts/architecture/facts.py`, `scripts/build_code_graph.py`: the build reads the terms and writes them on the facts of the graph, `(arch-known <part> <src> <tgt> "<file>" [:owner ..] [:reason ..] [:expires ..])` and the same on `capability-known`. Facts of entries without terms are byte for byte what they were. The build does not decide expiry: the graph of yesterday must not keep hiding a finding today.
- `scripts/code_graph_search.py`: `CodeGraph.load` compares `:expires` with the current day. An expired pair is left out of `known` (and an expired capability out of `capability_known`) and kept in `known_expired` / `capability_expired`; the stated terms of the others are in `known_terms` / `capability_terms`. Everything that reads `known` follows without a change: `boundaries` (a `violation` again, exit status 1), `advice` (the fix-violation item returns), `align`, REPORT.md and the map at the next build. `boundaries` prints ` :owner .. :reason .. :expires ..` after `:known <file>` and ` :expired <date> [:owner ..]` on the violation of an expired entry.
- `src/wingbox/cli.py`: `align --learn` reads the terms of the present baseline and writes them back on the entries it writes again (`_baseline_terms`), so the fields a person added survive and an expired entry stays expired; new entries are written as before. The header of the file gains two comment lines that name the fields. `wingbox advice reject <id>` on fix advice without `--reason` is refused (exit status 2, nothing written).
- `scripts/architecture/decisions.py`: `read_store` counts a `(build-run ...)` line as a run only when its graph hash differs from the one of the line before (`Store.last_graph`, `_counts`), and `reconcile` numbers the current run the same way; the run line itself is still written by every build. Advice of the kinds in `NEVER_HIDDEN` (`fix-violation`, `fix-capability`, `fix-pattern`) is never given the `:ignored` outcome, and `visible` shows such an item even when an older store holds `:ignored` for it. `needs_reason` tells the command line which advice needs a reason to be rejected.
- `README.md`, `SKILL.md`, `references/sexp-notation.md`, the plugin's copy of the skill, `CHANGELOG.md`.
- Tests `tests/test_baseline_terms.py`, REQ-ARCH-046..056, failing on `main`: the same graph built six times hides nothing and the count resumes with the next graph; fix advice of the three kinds survives six builds of changed code while a missing-folder item is hidden at the third; an `:ignored` outcome from an older store is shown again and a rejection hides it; the terms are read and written back; a known break prints its owner, reason and end date; an entry past its date, or with a date that is no date, is a violation again and its advice returns; an entry without terms gives the same lines as before; `align --learn` keeps the terms; the capability entry behaves the same; four builds after `init` leave the fix visible; `advice reject` needs a reason for fix advice.
- Existing expectations changed on purpose, in `tests/test_architecture.py` (REQ-ARCH-027) and `tests/test_review_fixes.py` (REQ-ARCH-032, REQ-ARCH-033): the three tests passed the same graph hash to every simulated build. With the new rule such builds are one run, so each simulated build now passes its own hash; what the tests assert (hidden after 3 builds, logged once, an accepted item never ignored) is unchanged.

### Motivation
- Issue #125: two mechanisms let a rule break stop asking for a decision without anyone deciding. `align --learn`, a command an agent can run, wrote the current breaks into the baseline with no owner, reason or end date; and an open recommendation was hidden after three builds, counting rebuilds of an unchanged graph, while `boundaries` still reported the violation and exited 1. A reader who takes the default outputs as "no open violations" was misled both ways. The capability and pattern fixes are included with `fix-violation` because they are the same case: the finding still fails `boundaries` while its advice would be gone.

## 2026-11-19 - boundaries --strict: a result line and an exit status for gates (issue #123)

### Changes
- `scripts/code_graph_search.py`: `cmd_boundaries(g, module_id, strict=False)`. With `strict` the plain output is followed by `(boundaries-checked :part P :style S :modules N)` for each checked part of the scope and by one `(boundaries-result :status pass|fail|incomplete|error :checked N :not-checked N :violations N :known N [:reason R])` line (`boundaries_result_line`, `_strict_result`); the exit status is `STRICT_EXIT`: pass 0, fail 1, incomplete 2, error 3.
  - pass: every part with files was checked, at least one file was checked, no violation. fail: a boundary violation, a capability violation or a pattern break; it outranks incomplete, and the line still carries `:not-checked`. incomplete: any part of `not_checked_parts` whatever its reason (plain mode exits 2 only for `declared-rules-not-read`), files whose part has no `arch-part` fact (printed in strict mode as `boundaries-not-checked ... :reason in-no-part`), a scope that matches no module, or a graph without files (`:reason part-not-checked` or `no-files-checked`). error: no `.wingbox/` (`no-graph`), a graph that `CodeGraph.load` refuses (`invalid-graph`), `--map`/`--root` without a usable value (`invalid-argument`).
  - `:violations` counts the pairs printed as `violation` plus capability violations and pattern breaks; `:known` counts `violation-known` pairs plus `capability-violation-known`. Known breaks do not change the status.
  - `main` recognises `boundaries ... --strict` before anything else (`_is_strict_boundaries`) so that every way out prints the result line. `find_sexp_file` is split into `_search_sexp_file` (returns None with the paths looked at) and `_print_no_project`; its own behaviour (message on standard error, exit 1) is the same.
  - `print_capability_findings` keeps its signature and result; the counts come from `_print_capability_findings`.
  - The usage text lists `boundaries [scope] [--strict]`.
- `src/wingbox/cli.py` is not changed: the query arguments pass through to the script.
- `README.md` (the contract table next to `boundaries`, one row in the command table), `SKILL.md` and `references/sexp-notation.md` (the two facts and the exit statuses), the plugin's copy of the skill (`tools/sync_plugin.py`), `CHANGELOG.md`.
- Tests `tests/test_boundaries_strict.py`, REQ-BCOV-007..019, every command run as a process; all fail on `main` (`--strict` was read as nothing: no result line, the plain exit status). One of them fixes a case the issue does not list: source files outside every part of a configured architecture, which plain mode counts as checked (`boundaries-ok`), are `in-no-part` and incomplete in strict mode. `tests/test_boundaries_coverage.py` (REQ-BCOV-001..006) is unchanged and passes.

### Motivation
- Issue #123: a script that gates on `wingbox boundaries` could not tell "checked and clean" from "nothing was checked" (an undetermined style and a project without source files both exit 0), nor a violation from a tool error (no graph and a damaged graph both exit 1, as a violation does). The interactive behaviour is deliberate (REQ-BCOV-004), so the contract is opt-in: plain mode keeps its lines and exit statuses, strict mode never lets a part that was not checked read as a pass and gives the four outcomes four exit statuses.

## 2026-11-19 - The language parsers are dependencies of the package again; the download is removed

### Changes
- `pyproject.toml`: the dependencies are tree-sitter, PyYAML and the grammars of TypeScript, Rust, C#, Java, Go, Ruby, PHP and Dart, with the pins they had. The Dart grammar alone carries a marker, `sys_platform != "win32" or platform_machine != "ARM64"`: it has no wheel for Windows arm64 (checked with `packaging`: false for win32/ARM64 only, true for win32/AMD64, darwin and linux). The extras `csharp`, `java`, `go`, `ruby`, `php`, `dart` and `all` stay with the same pins, so that `pip install "wingbox[all]"` and `"wingbox[csharp]"`, written for 0.12, keep working.
- Removed with the download: `src/wingbox/fetch.py`, `src/wingbox/assets/parsers.json`, `tools/update_parsers.py`, `tools/check_add.py` and the job `add` of `.github/workflows/tests.yml` that ran it against the package index.
- Removed with the per-user folder: `scripts/lang/ondemand.py` (`~/.wingbox/parsers`, `WINGBOX_PARSERS_DIR`, the platform folders) and its two calls, at the import of `scripts/lang` and at the start of every build. A grammar is imported from the environment that runs Wingbox and from nowhere else. A folder that 0.12 filled is not read and not deleted.
- `src/wingbox/add.py` is 124 lines instead of 642 and imports `wingbox.facts` only (`importlib.metadata` and the language registry for `--list`). `wingbox add` takes every form 0.12 took, and any other argument: it prints one line, or the fact `(nothing-to-add :reason "..." :next "wingbox build")` with `--sexp`, and exits 0. `--list` puts one fact per language before it, with the heads 0.12 used: `(parser-installed csharp :package tree-sitter-c-sharp==0.23.5 :from environment)`, `(parser-installed python :from built-in)`, `(parser-missing dart :package ... :reason ... :instead "pip install \"wingbox[dart]\"")`; the check is the one a build makes (the pinned version of every package of the parser). It writes nothing: the marking of a graph that had skipped a language (`graph-marked`, `graph-next`, the counter `parser_marks`) is gone, and the line names `wingbox build` instead.
- `scripts/lang/__init__.py`: `PIP_EXTRA_LANGUAGES` (the six languages that have an extra) replaces `ON_DEMAND_LANGUAGES`. `scripts/build_code_graph.py`: `install_command` replaces `add_command`. A language whose parser is missing all the same (a broken install, Dart on Windows arm64) is skipped as in 0.12, and a project of that language alone still publishes nothing. Only the hint changed, to the pip extra: the warning ends `To include them: pip install "wingbox[csharp]"` (plus the requirements file in a clone), and the record keeps its shape, `(skipped-language csharp :files 2 :add "pip install \"wingbox[csharp]\"")`, the quotes inside the string escaped with a backslash. `src/wingbox/cli.py` (the last lines of a build, the help: one line says the command is kept for compatibility) and `src/wingbox/overview.py` (REPORT.md) name the same command.
- `Dockerfile`: `pip install /src`, without the extra. `.github/workflows/map-request.yml` and the five benchmark scripts keep `[all]`: they install released versions, the 0.12 releases need it and later versions accept it (comments and the docstring of REQ-BENCH-019 say so; its assertions are unchanged).
- Documents: `README.md` (the install lines, a section "Language parsers" in place of "Language parsers on demand", the local-only section and its table of write places, plugin, Claude Desktop, Docker, clone, the Ruby, PHP and Dart sections, FAQ), `SKILL.md` and its plugin copies (`tools/sync_plugin.py`), `plugins/wingbox/README.md`, `docs/store/PRIVACY.md`, `SECURITY.md` (the scope names any network connection opened by Wingbox itself), `AGENTS.md`, `CHANGELOG.md`.
- Tests removed with the code they tested: REQ-CLI-026..036 (`tests/test_add_command.py`: the arguments, `--from`, the sha256 refusal, the pinned download, the error texts, unsafe archive members, the lock, the wheel choice per platform, `--list` by place, the usage errors, the marking of the graph), REQ-SEC-003 (the download is reachable from `wingbox add` alone), REQ-SEC-005 (the download takes only https files of the package index), REQ-DEPS-010 (the folder and its override), REQ-DEPS-011 (import from the per-user folder after the environment), REQ-DEPS-013 (a parser added while a process runs).
- Expectations changed on purpose, because the requirement changed: REQ-SEC-001 is back to its form before 0.12, no file of `src/` or `scripts/` may import a network module, with no exception. REQ-DEPS-012 now demands every parser among the dependencies with exact pins, the Dart marker and nothing else with a marker, the extras with the same pins, and the pins equal to the adapters and the requirements files; the comparison with the catalog went with the catalog. REQ-DEPS-007, 008, 009, REQ-FRESH-052, 053 and REQ-OUT-019 expect the pip extra where they expected `wingbox add <language>` (the record with the escaped quotes); REQ-FRESH-053 and REQ-OUT-019 look for the line `Not in this graph` instead of the words `wingbox add`. REQ-SEC-004 no longer names the removed modules: a build that skips a language loads none of `urllib.request`, `urllib.error`, `http.client`, `ssl`, and names the pip extra. REQ-DART-015: the docstring says Dart is installed with the package except on Windows arm64; its assertions are unchanged. `tests/test_dynamic_dependencies.py` loses the `WINGBOX_PARSERS_DIR` line 0.12 added. `tests/test_parsers_on_demand.py` is renamed `tests/test_language_parsers.py`.
- New tests: REQ-CLI-037 (every form of `wingbox add` exits 0 with the one line, one fact with `--sexp`, nothing on stderr; the help keeps one line), REQ-CLI-038 (`--list`: installed, built-in, missing, another version, the command for a language with and without an extra), REQ-SEC-011 (every form run in a project with a temporary home folder that holds a parser folder of 0.12 changes no file there and loads none of `urllib.request`, `urllib.error`, `http.client`, `ssl`, `ftplib`, `smtplib`), REQ-DEPS-014 (a parser in `~/.wingbox/parsers` or in the folder `WINGBOX_PARSERS_DIR` names is not importable and no folder under it is on the module search path). On `main` these fail, as do the changed expectations.
- Whole suite, the way CI runs it (every requirements file through uv, `python -m unittest discover -s tests`): 657 tests, OK, 1 skipped (CPython 3.12, macOS). pyright on the touched files: the same 90 messages as on `main` (84 in `cli.py`, 6 in `build_code_graph.py`), none in the new code.
- Clean install, CPython 3.12, macOS x86-64, the wheel built from this change in a fresh environment with a temporary home folder: a project with one file each of C#, Java, Go, Ruby, PHP, Dart, TypeScript, Rust and Python builds with all nine files in the graph (`Found 9 source files (1 py, 1 ts, 1 rs, 1 cs, 1 java, 1 go, 1 rb, 1 php, 0 pas, 1 dart)`, nine nodes) and no `graph-skipped.sexp`; `wingbox add`, `add csharp`, `add --list` and `add x --from FILE` exit 0 and leave the home folder empty; the wheel's metadata lists the ten dependencies and the seven extras.
- Sizes, site-packages of a fresh environment after the install, sum of the file sizes: `wingbox==0.12.1` from PyPI 7,074,403 bytes (198 files), this change 18,525,651 bytes (286 files); an empty environment holds 4,360 bytes.

### Motivation
- The maintainer decided that the languages come back into the package: 15 to 30 MB is small by today's standards, and installing parsers on demand caused problems. The download is removed, so the core has no network access again, and `wingbox add` stays as a command that says every language is already in the package.

### Not covered
- On Linux with musl on aarch64, tree-sitter-php 0.24.1 has neither a wheel nor a source package, so `pip install wingbox` fails there, as it did up to 0.11.0. No environment marker tells musl from glibc.
- The extras `dart` and `all` carry no marker: on Windows arm64 they build the Dart grammar from its source package.
- A graph that 0.12 built without a parser keeps its record, with the old command, until `wingbox build` runs in that project; `wingbox add` now answers with that command.

## 2026-11-18 - MCP server: eleven tools named after their answers

### Why
- A tool directory grades an MCP server by four measures: one job per tool, one naming pattern, 3 to 15 tools, the whole life of the domain covered. The server had eighteen tools with overlapping jobs (`entity`, `usages`, `find_function`, `find_type`; `boundary` and `boundaries`; `analyze` and `quick`) and three naming styles. The owner decided to clean it up now, in one breaking change without aliases, while the server has few users.

### Changes
- `src/wingbox/mcp_server.py`: `TOOLS` is `build`, `impact`, `module`, `definitions`, `usages`, `dependencies`, `calls`, `layers`, `boundaries`, `changes`, `query`. Tools that differed only by direction or kind are one tool with a parameter (`dependencies` `direction` out or in; `calls` `direction` in or out and `to` for a call path; `definitions` `kind` any, function or type); `module` analyzes a module or the best match of a keyword; `boundary` is removed, `impact` lists the files to read first. Every description says what the answer holds and when to take the neighbouring tool; every parameter is described; `enum` values are validated. The instructions given at `initialize` name the new tools.
- The `wingbox` command keeps its command names.
- `AGENTS.md`, section 5 "Changing the MCP server": the default is to keep the tool list; the checklist for a change and the tests that hold it.
- README table "Use from an MCP client" and the changelog name the old tool for each new one.

### Verification
- `tests/test_mcp_tools.py` (REQ-MCP-021..030) and `tests/test_mcp_server.py`: pass.
- The Windows cells of the first CI run failed on REQ-MCP-027 (`changes`): `(diff-functions-no-match :files 1)`. `cmd_diff_functions` compared the path git prints (`shop/pricing.py`) with the graph's path (`shop\pricing.py`); the diff path is now normalized to the system's separator before the comparison. A defect of the command on Windows since it exists; no test ran it there before.

## 2026-11-18 - Streamable HTTP transport for the MCP server, as the package wingbox-http

### Why
- The owner asked for the MCP server to be reachable from another machine: stdio needs the client and the repository on one host. His two decisions: access for Claude Code and Codex with a secret token in the Authorization header (not OAuth), and the transport as a separate package so that the core keeps its promise of holding no listening code.

### Changes
- New distribution `packages/wingbox-http` (module `wingbox_http`, console script `wingbox-http`, standard library only, requires exactly its own version of `wingbox`):
  - `transport.py`, pure: `admit` (path `/mcp`, Origin, token, method, Content-Type, Accept) and `decide` (size, one JSON object, `MCP-Protocol-Version`, and for revision 2026-07-28 the `Mcp-Method` and `Mcp-Name` headers against the body) return `Refuse(status, headers, body)` or `Forward(line, method, modern)`; `reply_status` maps the server's reply to 202, 200, 400 or 404.
  - `settings.py`: arguments, `WINGBOX_HTTP_TOKEN` and `--token-file` into `Settings` or `Problem`; a non-loopback address without a token is a `Problem`.
  - `listener.py`: `ThreadingHTTPServer`, optional TLS (handshake in the connection's thread), one worker thread for the calls, and the event-stream answer with keep-alive comments for a `tools/call` slower than one second.
  - `__init__.py`: `main(args, cwd, env)` builds `mcp_server.Server` and passes `mcp_server.handle_line` to the listener; `run()` is the console script and turns SIGTERM into a clean stop.
- `src/wingbox/mcp_server.py`: `wingbox mcp --http ...` loads `wingbox_http` by name and hands over the remaining arguments; without the package it prints the install command and exits 2. No network module is imported by the core.
- `pyproject.toml`: the extra `http = ["wingbox-http==<version>"]`.
- README section "Over HTTP, from another machine", the package README, the changelog entry, the `HTTP` test prefix in AGENTS.md.

### Protocol choices
- Read from the specification pages of 2025-11-25 and 2026-07-28: one endpoint, POST; a notification is answered 202; the server may answer a request with JSON or an event stream; GET and DELETE may be answered 405 when no server stream and no session exist; an invalid Origin is 403; an unsupported `MCP-Protocol-Version` is 400; under 2026-07-28 a header that differs from the body is 400 with -32020 and an unknown method is 404.
- Not implemented, by design: sessions (`Mcp-Session-Id`), resumable streams (`Last-Event-ID`), the GET stream, OAuth. The token is a shared secret; the specification's authorization chapter describes OAuth and says nothing of a static token, which is the owner's choice for agents that send a fixed header.

### Verification
- `python -m unittest tests.test_http_transport`: 23 tests (REQ-HTTP-001..022, REQ-SEC-010), among them a real listener on a loopback port with the real server (initialize, 202 for the notification, tools/list, build, impact, 401 without the token, 404 for an unknown method under 2026-07-28), the event stream of a slow call, one call at a time, 411 and 413 before the body is read, and HTTPS with a certificate made by openssl.
- `tests.test_local_only`, `tests.test_mcp_server`, `tests.test_english_only`, `tests.test_mcp_registry`: pass; REQ-SEC-001 is unchanged and still scans `src/` and `scripts/`.
- Run for real in a clean environment (wheel of the package built with `uv build packages/wingbox-http`, installed beside the core): `wingbox mcp --http --host 0.0.0.0` without a token refuses to start; with a token file, curl got 401 without the token, 405 for GET, 403 for a foreign Origin, the `initialize` result, the `build` answer as an event stream, and `impact` over HTTPS with `--cacert`. Claude Code, with the server added by `--transport http` and the Authorization header, called `impact` and reported the 14 direct dependents the tool printed.

## 2026-11-19 - Python: imports of a package kept under `src/` are resolved

### Why
- Found while using Wingbox to pick the tests of a change in this repository: `wingbox impact src/wingbox/mcp_server.py` named no test and `wingbox usages TOOLS_BY_NAME` found 1 use in 1 file, although two test files use it. A test that called a renamed MCP tool was therefore missed before a push and failed in CI. Cause: an absolute import was looked up from the root of the repository, from the folder of the importing file and from its parent folders that hold the first segment of the name; a file in `tests/` that imports `wingbox` never looked into `src/`.

### Changes
- `scripts/lang/python/imports.py`, `_candidate_bases`: after the existing folders, the folder `src` of every parent folder of the importing file, nearest first. Resolution still needs the file to exist in the graph, so a name that no `src/` holds stays unresolved.
- `tests/test_python_src_layout.py` (REQ-PY-012..014): the tests of a src-layout project are dependents of the modules they import and appear in `impact` and `usages`; two parts of a monorepo with a package of the same name each reach their own `src/`; a package at the root still wins and an unknown name is no dependency.

### Result
- This repository, before and after: `usages TOOLS_BY_NAME` 1 use in 1 file -> 7 uses in 3 files (`tests/test_freshness.py`, `tests/test_skipped_language_notice.py`); `impact src/wingbox/mcp_server.py 1` 1 direct dependent -> 4.
- Not changed: the Python reference of the benchmarks (`benchmarks/*/tools/py_edges.py`) resolves an absolute name from the folder of the file and from the repository roots only. On a benchmark repository with a src layout Wingbox now reports dependencies the reference does not count; the reference needs the same rule before those benchmarks are run again, and no published figure changes until then.

## 2026-11-19 - Release 0.12.2

### Changes
- Version 0.12.2: every Python that Wingbox starts itself gets `-P`, so a folder of the project is never imported in place of an installed module (#134). The map-request workflow builds with 0.12.2.

### Motivation
- A fix of what Wingbox promises (code is read, never run) goes out on its own, at once.

## 2026-11-19 - A Python started by Wingbox never imports from the project folder (issue #134)

### Changes
- `src/wingbox/launch.py` (new, imports `os` only): `module_command(python, module)` = `(python, "-P", "-m", module)`, `code_command(python, code)` = `(python, "-P", "-c", code)`, and `without_starting_folder(path, folder, package_parent)`, pure.
- Every own start goes through it: `freshness.start_refresh` (`wingbox.refresh`, working folder = the project), the first build of `mcp_server`, `cli.global_hook_program` (the hook form of an install without the `wingbox` program), and in `update.py` the pip method, the version probe and the `wingbox install` of the upgraded package.
- `cli.main` called without arguments (a real command start) takes the starting folder and the empty entry off `sys.path` before `portable`, a script or a parser is loaded; the folder stays when the package itself lies in it. Calls with an argument list (tests, the MCP server in process) leave the path alone.
- Tests `tests/test_project_code_never_runs.py`, REQ-SEC-006..009: a scan of the source for `-m`/`-c` without `-P` or `-I`; a refresh and a build started in a project that holds `wingbox/`, `pip/`, `yaml.py`, `json.py`, `tree_sitter.py` run none of them; the path function; a build started as `python -m wingbox` without `-P` runs no project file named like a script or a parser.
- `tests/test_update.py` REQ-UPDATE (the pip method): the expected command gains `-P`. This is the changed behaviour itself, not a check bent to pass.

### Motivation
- A review of the pull request check found that it could run a module of the checked repository; the same start form was in the core. Reproduced on 0.12.1 with a stand-in folder.

### Not covered
- A person who types `python -m wingbox` in a project that holds a folder `wingbox/` starts that folder: nothing inside Wingbox runs before it. The `wingbox` program and `python -P -m wingbox` are not affected.

## 2026-11-18 - The layer guess is stored when the graph is built (issue #106)

### Changes
- `scripts/layer_guess.py` (new, pure, standard library and `architecture/words.py` only): the keyword table `PURPOSE_TO_LAYER`, the matching `classify` and `within` (moved from `scripts/code_graph_search.py`), `guess_key` (a hash of the table in its order and of `ALGORITHM_VERSION`), and the content of the stored file: `guesses`, `serialise`, `parse`. The builder and the search both import it; neither imports the other for it.
- The file is `.wingbox/cache.layer-guess.json`: `{"guesses": {"<purpose>": "<layer>", ...}, "key": "<16 hex digits>"}`, keys sorted, one line, the guess of every distinct purpose of the published graph. The `cache*` line of `.wingbox/.gitignore` already covers it. `index.sexp` does not change.
- `scripts/build_code_graph.py`: `publish_layer_guesses(root, nodes, again)` writes the file by atomic replacement right after the graph and the use file, in the build (`_run_build`) and, through `scripts/graph_update.py`, in the partial update. What a file with the current key already holds is kept, so a rebuild or a partial update matches only the purposes that are new and leaves an unchanged file alone; `--full` works every purpose out again. A file that cannot be written is a warning, not a failed build.
- `scripts/code_graph_search.py`: `CodeGraph.layer_guess(purpose)` reads the file once per loaded graph, at the first guess asked for, and falls back to `_classify_layer_by_purpose` for a purpose the file does not hold. A missing or unreadable file, text that is not JSON, another shape, another key or a layer the table cannot give are all read as "nothing stored", without a message. No query writes the file. `_PURPOSE_TO_LAYER`, `_within` and `_classify_layer_by_purpose` keep their names and their behaviour.
- The matching itself: in the fuzzy step the closest keyword word of a purpose word is remembered (`_closest`), and a keyword word that an earlier keyword already carries is compared once. The result is the same (the closest pair decides, the first in the table among equals); it is there because the build now runs the matcher for every purpose of the graph, also in a project that has no layered part, and purposes share their words. `ALGORITHM_VERSION` stays 1.
- `README.md`: one row in the table of the files of `.wingbox/`. `CHANGELOG.md`: one entry under Unreleased.
- Tests `tests/test_layer_guess_file.py`, REQ-CS-088..096, all failing on `main`: the build stores every purpose under the current key, atomically, ignored by git; the partial update adds a new purpose, runs the matcher for it alone and leaves the bytes a full build writes; a rebuild matches nothing and `--full` everything; `impact` (text and `--sexp`), `layers` and `boundaries` of a layered fixture print the same bytes with and without the file, with the matcher patched to fail while the file is there; ten kinds of unusable file and a folder in its place give the same answers with an empty stderr; a file that lacks one purpose has only that one matched; no query creates or changes the file; the key changes with the table, its order and the version, and a file under an old key is not read and is rewritten by the next build; the builder starts without the search module; 600 generated purposes get the guess of the comparison written out in full.
- Checked on a copy of this repository (274 modules) with a `.wingbox/config.yaml` that declares the root part layered: 27 of 27 answers (`impact` of 12 files as text and `--sexp`, `layers`, `boundaries`, `advice --sexp`; stdout, stderr and exit status) are byte-identical between `main`, this change with the file and this change with the file moved away, and no query brought the file back. Timings and call counts are in the pull request. pyright on the touched files: the same seven messages as on `main`.

### Motivation
- Issue #106, the reader's second measurement: with 0.10.2 a repository with a layered part still ran the fuzzy layer guess in every `impact` call, through `module_advice`, `architecture_inputs`, `module_label` and `_classify_layer_by_purpose`, because the cache of 0.10.2 lives only as long as the process. The rule set there is that a query does no work that depends only on the graph; the guess depends only on the purpose text, so it belongs to the build. It is stored as a cache of a pure function, with the key of that function, rather than as a fact of the graph: the graph and every output stay as they are, and an upgrade that changes the table or the matching simply stops reading old files.

## 2026-11-18 - Validation mark from the syntax tree, with its evidence (issues #121, #122)

### Changes
- The eight text detectors that set `FileResult.has_validation` are replaced by one module per language, `scripts/lang/<language>/validation.py`, each a pure function over the tree the parser already has; it returns the evidence as text, "" when the file defines no validator. A name counts only when the file binds it by an import of the validation library or writes it in full.
  - Python (`ast`): a base class `pydantic.BaseModel`, `RootModel`, `BaseSettings`, `pydantic_settings.BaseSettings` (and the `pydantic.v1` forms); a decorator `validator`, `field_validator`, `model_validator`, `root_validator`, `validate_call`, `validate_arguments`, `pydantic.dataclasses.dataclass`; a call `TypeAdapter`, `create_model`; a call of `attr.ib`, `attr.field`, `attrs.field` with `validator=`. A local name that anything else in the file also binds (a parameter, an assignment, a definition, another import) is left out. `TypedDict`, `NamedTuple`, `Annotated[`, dataclasses (also frozen, also with `__post_init__`) and the word "validator" are no longer evidence.
  - TypeScript and JavaScript: a call of a name bound by an import, or a top-level `require`, of `zod`, `joi`, `@hapi/joi`, `yup` or `ajv` (`z.object(...)`, `object(...)`, `new Ajv()`), read through the existing use collector, so a parameter or local variable of the same name does not count. `.parse(`, `.safeParse` and `validateSchema` by name are no longer evidence.
  - Go: a struct field tag with the key `validate` or `binding`; a call `New` of an imported `github.com/go-playground/validator` package. The import alone is not evidence.
  - Java: an annotation or supertype of `jakarta.validation`, `javax.validation` or `org.springframework.validation`, through a single-type import or written in full; with a wildcard import, a known Bean Validation name, and the evidence then reads `annotation NotBlank with import jakarta.validation.constraints.*`.
  - C#: an attribute of `System.ComponentModel.DataAnnotations` that states a rule (`Required`, `Range`, `StringLength`, ...), a base `ValidationAttribute`, `IValidatableObject` or FluentValidation's `AbstractValidator`, written in full or through a using alias, or beside the using directive of the namespace (`attribute Required with using System.ComponentModel.DataAnnotations`).
  - PHP: a class that extends `Illuminate\Foundation\Http\FormRequest`, `make` on `Illuminate\Support\Facades\Validator`, `validate` on a parameter typed `Illuminate\Http\Request`, the attribute `Livewire\Attributes\Validate`, each resolved through the `use` statements. `$request->validate(` on an untyped variable, `request()->validate(` and the root alias `Validator` are not reported: the file does not say which class they are.
  - Ruby: a call of `validates`, `validate` or `validates_...` without a receiver in the body of a class or module, or in the `included do` block of a concern. Ruby binds no library names, so the evidence is the construct and names it (`call validates in User`).
  - Dart: a call that passes the named argument `validator` (`argument validator of TextFormField`). `FormState`, `TextFormField` without a validator, input formatters and the names `RegisterValidators`, `YupValidator`, `FieldValidator` are no longer evidence.
  - Where a file cannot hold evidence the tree is not walked: Go, Java, C#, PHP, Ruby and Dart first test the text for a word every form of evidence needs (`.validation`, `validate`, ...). The text can only say no; every mark comes from the tree.
- `scripts/lang/interface.py`: one field with a default, `FileResult.validation_evidence: str = ""`. `has_validation` stays a boolean and is true exactly when the evidence is set, so no other language module and no caller changes.
- `scripts/build_code_graph.py`: the edge carries `validation_evidence` instead of `validated`, written as `(data-flow <src> <tgt> :validation-module :evidence "<kind> <name>")`; a result that claims validation without evidence gives no fact. `CACHE_SCHEMA_VERSION` 5 -> 6; the cache stores and checks the new field. Module docstring updated.
- `scripts/code_graph_search.py`: a data-flow edge keeps the text of `:evidence`; `module`, `entity` and `analyze` print the marks of the stored fact (`_edge_marks`). `analyze` no longer prints a constant mark: one `analyze-dataflow` line per different set of marks of a pair, `:dataflows` counts target modules as before. `:validated` of graphs written up to 0.11.0 is read as no mark.
- The spelling follows `references/sexp-notation.md`: `:evidence "<text>"` is the key that already carries the ground of a `capability` fact, and `:by` there is always a bare word for who or which method (`:by style`, `:by user`). The mark names the target (`:validation-module`), not the data, because an import does not show that the importer calls the validator.
- `references/sexp-notation.md` (and both plugin copies through `tools/sync_plugin.py`), `CHANGELOG.md`.
- Tests: `tests/test_validation_mark.py`, REQ-PY-007..011, REQ-TS-VALID-001..002, REQ-GO-005, REQ-JAVA-006, REQ-RUBY-011, REQ-PHP-024, REQ-CSHARP-006, REQ-DART-016, REQ-CS-084..087; `tests/test_mcp_server.py`, REQ-MCP-020. All of them fail on `main`.
- Checked with the wheel in a clean environment: the four fixtures of issue #121 (comment, string, `TypedDict`, the TypeScript "quiz." file) and an unused pydantic import give no `data-flow` fact and no `analyze-dataflow` line; a pydantic model gives `:validation-module :evidence "base pydantic.BaseModel"`, a zod schema `:evidence "call z.object from zod"`, in `index.sexp` and in `analyze`. On this repository: 216 `data-flow` facts with `:validated` (53 target files) with 0.11.0, none with this change; the repository imports no validation library, and the three most marked targets read by hand hold only `TypedDict` classes or frozen dataclasses (`scripts/lang/interface.py`, `scripts/architecture/library.py`, `src/wingbox/facts.py`).
- Parse time of one generated file of 1,500 functions, best of five, 0.11.0 -> this change: Java 121 -> 138 ms, Go 113 -> 120, C# 161 -> 163, PHP 283 -> 268, Ruby 154 -> 139 (all within the spread of repeated runs, about 10 %: these files hold no word the evidence needs, so their tree is not walked again); a Python file that imports pydantic 610 -> 721 ms, a TypeScript file that imports zod 162 -> 200 ms. The second look at the tree happens only in a file that imports a validation library.
- Not changed: `benchmarks/tocs/README.md` and `benchmarks/tocs/make_report.py` still name `data-flow :validated` in their table of fact kinds; no benchmark was run. The ToCS score counts only `data-flow :pipeline`.

### Motivation
- Issue #121: the mark `:validated` was set when the first 4096 characters of the imported file matched a regular expression, so a comment, a string or a type declaration was enough ("quiz." matched `z\.`), and its name said more than the method can know. Issue #122: `analyze` printed the mark as a constant. A fact in the graph must state what was found and show its ground.

## 2026-11-18 - Language parsers on demand: `wingbox add`

### Changes
- `pyproject.toml`: the dependencies are `tree-sitter`, `tree-sitter-typescript`, `tree-sitter-rust` and `PyYAML`. The grammars of C#, Java, Go, Ruby and PHP move from the dependencies to extras of their own, beside the Dart extra that existed, and `all` holds the six; the pins are unchanged.
- `scripts/lang/ondemand.py` (new, standard library, no network, no writes): the six on-demand languages, the per-user folder `~/.wingbox/parsers` (`WINGBOX_PARSERS_DIR` overrides), its layout `<platform>/<distribution>-<version>/` (platform: `darwin-arm64`, `linux-x86_64`, `linux-x86_64-musl`, `windows-amd64`, so two processor types or C libraries sharing a home folder do not load each other's binaries), and `activate`, which appends the folders that exist to `sys.path` after everything else. A grammar of the environment therefore wins, and `importlib.metadata` reads the version from either place, so `_dependency_issues` and the cache fingerprint of the builder needed no change. `scripts/lang/__init__.py` activates when the languages are loaded (every build, worker and refresh process); the builder activates again at the start of each build, for a process that lives long (the MCP server builds in its own process).
- `scripts/build_code_graph.py`: `OPTIONAL_LANGUAGE_EXTRAS` holds the six languages (it held Dart). The warning of a skipped language gives the number of files and `wingbox add <language>`; the error of a project in such a language alone names the command first; the `pip install -r requirements/<language>.txt` line is printed only where that file exists (a clone), because the installed package has none. After publishing the graph the build writes `.wingbox/graph-skipped.sexp`, one `(skipped-language <name> :files N :add "wingbox add <name>")` per language, or removes the file when nothing was skipped. The name starts with `graph-`, which `.wingbox/.gitignore` already covers. The graph file itself is not touched, so graphs stay byte-identical and a partial update still reproduces a build.
- Where the fact is read: `freshness.skipped_languages_line` (one small file read) and `freshness.notes`; `cli._refresh_before_query` prints it on stderr for every query and architecture command; `mcp_server.Server.graph` puts it at the head of each answer after the stale line (`_query` now takes "is an error" from the command's own output, so an `(error ...)` answer stays marked as an error behind a note); `hook.context_for` adds it when it gives context (not when it has none, so a search that the graph cannot answer costs nothing more); `cli._build_outputs` ends `wingbox build` with one line per language (which the MCP `build` tool returns); `overview.render_report` lists it first under the key findings.
- `src/wingbox/add.py` (new): `wingbox add LANGUAGE [LANGUAGE ...] [--from PATH] [--sexp]` and `wingbox add --list`. The core is pure over a `Machine` (catalog, folders, interpreter, download function, environment lookup); `this_machine` builds the real one. It picks the wheel by the tags in its file name against the running interpreter (CPython, version, free-threaded or not) and platform (macOS version and processor, glibc or musl version and processor, Windows processor), takes it from the download or from `--from` (a wheel file or a folder), refuses it unless its sha256 is the pinned one, unpacks into a temporary folder inside the per-user folder (a member with a parent or absolute path, a drive, a backslash or a link is refused before anything is written), renames the folder into place with `portable.replace`, all under `portable.lock` on `~/.wingbox/parsers/.lock`. Leftovers of an interrupted run are removed under the lock. Then it marks the files the current project's last build skipped (`freshness.mark_changed`, the way the edit hook marks a file), so the next use of that graph rebuilds it.
- `src/wingbox/fetch.py` (new): the download, the one module that imports `urllib.request`. It takes only `https://files.pythonhosted.org/packages/...` (no other scheme, host, port, user, query or redirect) and exactly the pinned number of bytes.
- `src/wingbox/assets/parsers.json` (new) and `tools/update_parsers.py`: per language the package, the pinned version and every wheel of that version with file name, URL, sha256 and size, written from the JSON API of the package index. What the pinned versions ship: abi3 wheels for macOS x86_64 and arm64, manylinux x86_64 and aarch64, musllinux x86_64, Windows amd64; musllinux aarch64 only for C#, Go and Dart; Windows arm64 for all but Dart; no source package for PHP. Where no wheel fits, `wingbox add` says so and names the pip extra.
- Installs that need every language: `Dockerfile` (`/src[all]`; a container without network cannot add a parser), `.github/workflows/map-request.yml` (`wingbox[all]`), the five benchmark scripts that install Wingbox (`with_all`: `wingbox[all]==X.Y.Z` or `<path>[all]`; a release without the extra installs as before, uv prints a warning). No benchmark was run and no result file changed. CI installs every requirements file as before; `tools/check_add.py`, a job of its own in `tests.yml`, runs `wingbox add` against the real index on the three systems.
- `README.md` (install, a section "Language parsers on demand", the local-only section and its table of write places, plugin, Claude Desktop, Docker, clone, languages, FAQ), `SKILL.md` and its plugin copies, `plugins/wingbox/README.md`, `SECURITY.md`, `docs/store/PRIVACY.md`, `AGENTS.md`, `CHANGELOG.md`.
- Tests. New: REQ-DEPS-007..013 (skip with the count and the command, the error of a project in one such language, the record and its removal, the folder and its override, import from the per-user folder after the environment and never from another version, platform or unfinished folder, the catalog against the extras, adapters and requirements files, a parser added while the process runs), REQ-CLI-026..036 (arguments, `--from` with the hash check and idempotence, a wrong hash, the download through a stand-in, the errors, unsafe archive members and atomic placement, two runs at once, the wheel choice over the platforms of the real catalog, `--list`, the command end to end, marking the project), REQ-SEC-003..005 (the download reachable only from `add`, a build that skips a language loads no download code, the URL rules), REQ-FRESH-052..053 and REQ-OUT-019 (the fact in every reader, nothing without a skipped language), REQ-BENCH-019. Changed: REQ-SEC-001 now names the one file that may import a network module (`src/wingbox/fetch.py`) instead of allowing none; the dependency tests set `WINGBOX_PARSERS_DIR` to an empty place so a parser added on the machine that runs them does not change their result.
- Sizes, measured the same way in two clean environments on one machine (CPython 3.12, macOS x86-64, sum of the file sizes of site-packages minus an empty environment): `wingbox==0.11.0` from PyPI 17,211,725 bytes, this branch 7,023,433 bytes. The six parsers in the per-user folder take 11,503,434 bytes together, C# alone 5,337,375.
- Checked by hand with the built wheel run through `uvx --from <wheel>` in fresh home folders: a project with two `.cs` and two `.py` files builds with the warning, the record and the report line; a query and an MCP answer carry the fact; `wingbox add csharp` downloads from the index, verifies and unpacks; the next query rebuilds the graph by itself and the C# modules are in `index.sexp`; `wingbox add --list`; `wingbox add csharp --from <wheel>` and `wingbox add go --from <folder>` in a second fresh home; a wheel with one byte appended is refused and nothing is written; the same sequence for Go.
- Graphs: 61 projects (the PHP benchmark fixture and every project the language tests of C#, Java, Go, Ruby, PHP and Dart write, kept on disk for the comparison) built by `wingbox==0.11.0` from PyPI (with its Dart extra) and by this branch after `wingbox add` of the six: `index.sexp` byte-identical in all of them; `index.uses.sexp` identical apart from the `:key` of its header, the cache key that hashes the builder's source files and so changes with every change of the builder.

### Motivation
- The maintainer decided that parsers other than those of TypeScript, Python and Rust are installed on demand, by one command and never by Wingbox itself, so that the promise that nothing goes to the network on its own stays true while a default install gets smaller; the command has to work with every way Wingbox is installed, including a uv tool environment without pip and the pinned `uvx` of the plugin.

## 2026-11-18 - Size audit of what the package ships

### Changes
- Every file of the wheel and of the sdist was listed with its size and with what reads it (imports from the `wingbox` entry point, `python -m wingbox.refresh`, the hooks, the MCP server, `wingbox install` copying `wingbox/_skill`, the map template and its d3 build, the style library). After the word-vector table (entry below) two kinds of things were changed, one commit each.
- Code nothing calls (one occurrence in the repository each, its definition): `LspClient.prepare_call_hierarchy`, `outgoing_calls`, `incoming_calls` (the LSP pass sends these requests through the pipelined path), the `_languages` set of `_LazyBoundLanguages` and its `results` argument, `impact_levels` in `scripts/code_graph_search.py`, `Review` and `REVIEW_MIN_BROKEN` in `architecture/align.py`, `CONFIG_DIRECTORY` in `architecture/config.py`, `_has_macro_parts` in the Dart parser, `consume_comment` in the Pascal tokenizer.
- An asset that could be smaller with the same content: `plugins/wingbox/assets/icon.png` and its copy in `plugins/wingbox-skill` hold the same scanlines at the highest deflate level, 67,176 -> 59,362 bytes (identical after inflating; identical bitmap after conversion).
- Kept, with the reason: `assets/d3.min.js` (280,622 bytes; the map page uses force, selection, zoom, drag, polygon, ease and min/max of it; a smaller build would change every `map.html`, so it is a decision, not a cleanup); the README as the long description (81,641 bytes of `METADATA`); `scripts/architecture/styles/infra.toml` (15,351 bytes; no code opens it yet, it holds the rules of the planned infrastructure style); `scripts/git_descendants.py` (4,035 bytes; no command reaches it, `SKILL.md` lists it as a bundled script); functions only the tests call (`detect_source_language`, `write_sexp`, `tokenize_reference`, `forget_resolved_roots`, `hook_group`, `dirty_group`, two properties of the loaded graph); the `*_use_positions` functions (reached by name through `getattr`); fields of the style records that the TOML files fill and no code reads yet; the refusals of `--output` and `install --project`, which tests require.
- Sizes, `main` at 0.10.2 -> this branch: wheel 3,559,698 -> 597,908 bytes (117 -> 114 files, 5,238,260 -> 1,970,880 unpacked); sdist 3,547,014 -> 583,288 bytes; installed `wingbox` with its metadata, without dependencies, 5,239,013 -> 1,971,634 bytes; desktop extension bundle 66,235 -> 60,554 bytes; `plugins/wingbox` 146,364 -> 138,521 bytes, `plugins/wingbox-skill` 132,936 -> 125,093 bytes. The dependencies are unchanged: 15,239,644 bytes installed (CPython 3.12, macOS x86-64), of which `tree_sitter_c_sharp` 5,328,611.
- Checked with the final wheel in a clean environment against the wheel of 0.10.2: `wingbox init` on 9 folders (two real repositories, the layered fixture of the tests, the same with a `webhooks` folder, five path lists of `benchmarks/architecture/trees` as empty files), 135 output files byte-identical; `wingbox build` on four of them, `index.sexp` and `boundaries all` byte-identical. `--version`, `init`, `build`, `impact`, `install --dry-run`, the four hooks and the MCP server answer from that wheel.

### Motivation
- The maintainer asked to remove everything the installed program does not use and to make the distribution as small as it can be without removing a feature, a language, the offline map or the README on the package page.

## 2026-11-18 - The built-in word-vector table and `--synonyms` are removed

### Changes
- `scripts/architecture/vectors/` (`glove100.i8`, `words.txt`, `manifest.json`, 3.25 MB) is deleted, with the code of `scripts/architecture/words.py` that loaded and compared vectors (`VectorTable`, `load_vectors`, `split_words`, `name_vector`, the third step of `_match_cached`, the `vectors` field of `Dictionary`, `threshold` and `margin` of `MatchSettings`, `SYNONYM_SETTINGS`, `DETECTION_SETTINGS`). Matching a folder name keeps its two steps unchanged: exact normalised name, then a spelling variant one edit away.
- `--synonyms` is gone from `wingbox init` (`src/wingbox/cli.py`) and from the builder (`scripts/build_code_graph.py`: `BuilderOptions.synonyms`, the parameter of `project_architecture` and `architecture_plan`); both refuse it the way they refuse any unknown option. `detect.py`: `layer_dictionary(style)` and `_group_dictionaries(style)` take no flag, the unused `group_dictionary` is deleted; `plan.py`: `detect_plan` has one settings argument. `synonym` leaves the `:match` lists (`references/sexp-notation.md`, comments) and the places that tested for it.
- Benchmark `benchmarks/architecture`: the arm "+ word vectors" and `build_vectors.py` are removed; `evaluate.py` compares dictionaries and + Levenshtein. `results/` regenerated by `run.sh`: every remaining number is unchanged (`detection.tsv` and `roles.tsv` lose the `+vectors` column, the `step` column of 17 rows changes from `synonym` to `none`). The README keeps one sentence of history with the two numbers.
- `README.md`, `SKILL.md` and the plugin copies no longer describe the table or the option; `.gitattributes` loses `*.i8` and `*.npy`.
- Tests: REQ-ARCH-022 and REQ-CLI-016 (the option) are deleted, REQ-ARCH-003 loses its two word-vector assertions and keeps the dictionary ones, new REQ-CLI-025 (no vector table under `scripts/` or `src/`, `--synonyms` refused by `init` and by the builder).
- Checked twice. Against the wheel of 0.10.1, where the work started: `wingbox init` (text, `--sexp`, `--details`, kept config) on 31 folders (the layered fixture of the tests, the same with a `webhooks` folder, the 27 path lists of `benchmarks/architecture/trees` as empty files, two real repositories), 453 output files byte-identical, and `wingbox build` on four of them, `index.sexp` and `boundaries all` byte-identical. Against the wheel of 0.10.2, after the rebase: the same commands on 9 of the folders (135 output files) and the same four builds, byte-identical. Wheel 3,559,698 -> 598,266 bytes, sdist 3,547,014 -> 583,639 bytes.

### Motivation
- The maintainer decided to drop the word-vector dependency: on the held-out half of `benchmarks/architecture` the table did not beat the dictionaries (accuracy 75.4 % against 76.2 %), it was off by default, and it made up most of the package.

## 2026-11-18 - Search hook: no context for calls made from a script, and a time budget of its own

### Changes
- `src/wingbox/hook.py`: `is_call_from_code` (a tool_use_id that starts with `repl_`, as observed on Claude Code 2.1.327 with Code Mode; an absent id or any other form is a direct call). For such a call `context_for` returns nothing and does no graph work (no search for the graph, no freshness check or refresh, no read). `main` starts a timer of `BUDGET_SECONDS` (5 s, half of the 10 s Claude Code gives the hook) that ends the process with exit code 0 and nothing printed; it is cancelled before an answer is printed, so an answer is never cut.
- Tests `tests/test_hook_code_calls.py`, REQ-INSTALL-040..043: no context and zero graph work for a call from code (calls counted); context kept for `toolu_...`, an absent id, an unknown form, a non-text id; a stuck hook ends with exit code 0 and empty stdout; a ready answer of 5,000 characters is printed whole.

### Motivation
- Measured in three Code Mode sessions (Claude Code 2.1.327, claude-opus-5-5, a graph of 707 files): hooks run for every tool call a script makes (60 Grep calls: 60 PreToolUse and 60 PostToolUse runs); each context block is delivered to the model beside the script's result, at most 50 per run of code, 23,272 characters for the 60 calls; and a hook that does not finish within its timeout refuses the call ("the hook did not finish, so this call was not run"), which does not happen to a direct call. The answer once per script (from the code of the REPL call) comes with the batch `impact` in 0.11.0.
## 2026-11-18 - `impact` does less work per call (issue #106)

### Changes
- `scripts/code_graph_search.py`: `tokenize` reads a graph line with one pattern (quoted value, list without a quote or a list inside, bare word) and hands a line to the character-by-character reader only when it holds a backslash or a list that the pattern does not cover; that reader stays as `tokenize_reference`, the reference the test compares with. `_classify_layer_by_purpose` is cached and `_within` skips the letter-by-letter comparison of two words whose lengths already differ by more than the allowed distance. `_impact_file` works out the file of a module once per loaded graph.
- `scripts/portable.py`: `relative_posix` resolves the links of the project folder once (`_resolved_root`, 16 entries; `forget_resolved_roots` drops them).
- Tests `tests/test_issue_106_impact_speed.py`, REQ-CS-078..082: same tokens as the reference reader on the lines of two built graphs and 20,000 generated lines; fewer than 1 graph line in 10 takes the slow reader; the cached guess equals the full comparison and compares nothing the second time; words of too different length are not compared; one `impact` call resolves at most one path per file plus the folder, text unchanged.
- Checked on two repositories with the graph of 0.10.0, old scripts against these, 12 modules each, text and `--sexp`: 48 of 48 answers byte-identical. Work of one call, counted by the profiler: immich (707 files) 2,351,264 -> 1,353,050 function calls, 22,300 -> 1,835 lines read one character at a time, 1,502 -> 708 link resolutions; FastAPI (1,227 files) 1,670,505 -> 748,001 function calls, 20,679 -> 199 lines. Wall clock of the 24 calls: 25.2 s -> 22.2 s and 22.8 s -> 20.6 s (one machine, one run).

### Motivation
- Issue #106: a reader measured about 6 s per `impact` call on a repository of 400 Python files with 0.9.2 and traced it to repeated fuzzy matching; his two changes go in as he described them, and the reader of the graph file, the largest share on the repositories measured here, goes with them. What is left of a call is starting Python, importing the modules and reading the graph.

## 2026-11-18 - Version 0.10.0: release branch, benchmarks measured again on claude-opus-5-5

### Changes
- Branch `release-0-10-0`: the Rust item edges (the 2026-11-17 entry below), the Rust resolution audit (type members, module layout, Cargo targets; entry above) and the benchmark proposal, rebased on 0.9.2. Version 0.10.0 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`; the plugin manifests stay at 0.9.2 until they are pinned to the release.
- Free checks at this branch: edge test Rust rows agent-browser 456 required / 456 graph / 0 wrong / 0 missing and claudes-c-compiler 2,084 / 2,084 / 0 / 0; `wingbox impact` complete on 100 of 100; the 100 questions and the agent subset are the same draw; of the 100 reference lists only agent-browser-19 (11 -> 19, `impl Running` in `seed.rs`) and claudes-c-compiler-17 (3 -> 4) change; the five comparison lists do not.
- Agent tests (`benchmarks/impact/impact100.py`, `benchmarks/graphify/tools/agent_test.py`, `oi_agent_test.py`): the question states, for each language, what the reference counts (the old Rust clause named only a `mod`, a `use` or a path of the module, and agents on Opus left out files that use an item through a re-export or a glob import, quoting that text); the model is `claude-opus-5-5`. All arms ran on 2026-11-18 with the new text; the search-alone, Graphify, CodeGraph and OntoIndex arms ran before the type-member fix and are scored on the current lists (same question text, same tools); the Wingbox arms ran a third time with the release build, whose `impact` output says why each file is on the list (the second run, before that output, had 54 of 55: on agent-browser-14 the agent left out `flow/replay.rs` and `flow/send.rs`, which `wingbox impact` printed; the new output names them `uses HarEntry by name after \`use super::*\``). Impact: 55 of 55 complete with Wingbox, 48 of 55 with search alone, cost -38 % (-49 % to -25 %), tokens -32 % (-47 % to -12 %). Comparison: Wingbox F1 1.000, 15 of 15 perfect, 0.121 USD per trial; search alone 0.989, Graphify 0.984, CodeGraph 0.984, OntoIndex 0.984.
- Results: `benchmarks/impact/results` (`agent.json`, `agent_answers.json`, `command.json`, `edges.json`, two reference lists, `command.ontoindex.json` rows of the two changed lists, `<repo>.rust_compiler_check.json` from the audit), `benchmarks/graphify/results` (`agent_summary*.json`, versions); `context_ratio.json` and the comparison `command_check.json` and `*.formula.json` (the `impact` answers are longer now that each row carries its reason: mean 797 -> 1,081 tokens over the 100 answers); both READMEs generated; the README tables and the sentences that describe a measurement follow the new numbers; slogans and titles are left as they are.

### Motivation
- The maintainer: release 0.10.0 with the Rust fix, measure the headline agent numbers on Opus with a question that states what the reference counts, and publish only what the numbers say.
## 2026-11-18 - `impact` says why each file is on the list

### Changes
- `scripts/code_graph_search.py`: every row of `impact` carries its reason. Depth 1: the relation of the edge (`imports it`, `imports A, B from it`, `uses A, re-exported by crate::m`, `uses A by name after `use super::*``, `uses T::new, defined in an impl block there`, `uses the macro m, exported at the crate root`). Depth 2 and deeper: `through <file> (or N other files): <the relation of that hop>`. The facts are the source (`ImpactRow`, `impact_fact`): `:through <module>`, `:more <count>` and the name lists of the edge; the text is a printer over them (`impact_reason`). The MCP tool shares the command (REQ-CS-076, REQ-CS-077).
- `scripts/lang/rust/definers.py`, `scripts/build_code_graph.py`: the Rust resolver returns, with each module a file depends on, the names it followed there and the way they arrive (`ItemRoute`); an import edge of the graph records them as `(:use ..)`, `(:reexport "<facade>" ..)`, `(:glob "<module>" ..)`, `(:member "<type>" ..)`, `(:macro ..)`, three names at most and `:of <count>` (REQ-RUST-041). A printed name is always one the resolver followed for that edge.
- REQ-CS-039 and REQ-CS-040 (the exact text and facts of `impact`) and two assertions on the text of a Rust `:item` edge in `tests/test_graph_update.py` follow the new format.

### Numbers
- The 100 benchmark questions, this branch against 68fde76: the set of files is the same in all 100 (text and facts). Output of `wingbox impact <file> 2 --all`: 3,189 to 4,326 characters on average (+35.6 %), median 688 to 1,320; with `--sexp` 4,386 to 5,223 (+19.1 %). Three quarters of the growth is the `through <file>:` part of the deeper rows.
- `index.sexp`: byte-identical for FastAPI, chrome-devtools-mcp and immich (server); agent-browser 406,139 to 419,777 bytes, claudes-c-compiler 427,138 to 489,690 bytes (the name lists of the Rust edges). The edges themselves are the same. No cache schema changes: parse results are unchanged, and a builder change already invalidates the cache and the fragment store.

### Motivation
- Measured twice in the agent benchmark: the output listed the exact files, and the agent removed rows it could not verify (a file whose only line of interest is `use super::*`). The output gave a single word, "imports", for every row and no file through which a depth-2 row is reached.

## 2026-11-18 - Rust resolution audit against the compiler: type members, module layout, Cargo targets

### Changes
- Rules read from the sources of rustc (rustc_expand module.rs, rustc_resolve, rustc_hir_typeck method/probe.rs) and of Cargo (target discovery, workspace and manifest), one minimal fixture per rule (73 rules); Wingbox handled 41 of them before and 69 after; the four left need macro expansion or lie outside the benchmark question (a macro used by textual scope, a `#[macro_use] extern crate`, `include!`, an item a macro invocation defines in a module a glob re-exports). The reference resolver handles 68 (54 before).
- `scripts/lang/rust/definers.py` (new, the definer logic moved out of `imports.py`): a member named through a type path (`Type::member`, `Self::member`, `<Type>::member`, `Type::<T>::member`, the type named by a `use`, a facade, a glob import or a type alias) gives an `:item` edge to the file whose inherent impl block defines it (REQ-RUST-031). The compiler finds such a member by name among all inherent impl blocks of the type (`assemble_inherent_impl_candidates_for_type`), so no type inference is needed; `value.method()` and trait members need it and give no edge. Parse results record impl blocks and type aliases (`type_members`), cache schema 5.
- `scripts/lang/rust/cargo.py` (new): the root file of every Cargo target (library, binaries, tests, examples, benches, build script, with `[lib]`/`[[bin]]` paths), the crate name of the library (`[lib] name`), renamed dependencies, the edition (also inherited from the workspace). `scripts/lang/rust/modules.py` (new): the module layout of the compiler (a crate root, a `mod.rs` and a file loaded through `#[path]` own their folder; inline modules add their name; `#[path]` and `#[cfg_attr(.., path)]` name the file). `imports.py` resolves through both (REQ-RUST-032, 034, 035, 036), follows edition 2015 paths and `::name` (REQ-RUST-037) and `extern crate .. as ..;` (REQ-RUST-038).
- A named re-export of an item a macro invocation defines leads to the module the re-export names (REQ-RUST-033, the three `keys!` edges of agent-browser). After a glob import a local definition or an explicit import shadows the glob; variants re-exported with `pub use Kind::*;`, a `macro_rules!` macro re-exported as an item and a module re-exported under a name are followed (REQ-RUST-039). A glob re-export carries a `pub(super)` or `pub(in path)` item only into that scope (REQ-RUST-040).
- Reference (`benchmarks/*/tools/rust_resolve.py`, written independently of the product code): category `type-member` with the same rule (REQ-BENCH-016); `impact100.drawn_from` leaves it out, so the 100 questions and the agent subset are the same draw. Also, each shown by rust-analyzer on a minimal package (REQ-BENCH-018): crates named by `[lib] name`, a renamed dependency, `extern crate .. as ..;`, another package of the workspace or `::name`; `src/bin/<name>/main.rs`; edition 2015 paths; `#[cfg_attr(.., path)]` and `#[path = "dir"]` on an inline module; variants re-exported by a glob; an explicit import shadows a glob import; restricted visibility. These leave the required edges of the two benchmark repositories unchanged.
- `benchmarks/impact/tools/rust_compiler_check.py` (new, REQ-BENCH-017): the edges of the reference and of a graph against rust-analyzer (`scip`), with no dependency and without executing anything of the analysed repository (build scripts replaced by a no-op command, procedural macros off).
- REQ-RUST-024 no longer says that a name behind a `#[path]` module stays on the facade (REQ-RUST-034 follows it).

### Numbers
- Impact edge test, Rust rows (dependencies in the code / edges in the graph / wrong / missing). Before, with the reference of 2026-11-17: agent-browser 452 / 449 / 0 / 3, claudes-c-compiler 2,037 / 2,037 / 0 / 0. The earlier Wingbox against the new reference: agent-browser 456 / 449 / 0 / 7, claudes-c-compiler 2,084 / 2,037 / 0 / 47. Now: agent-browser 456 / 456 / 0 / 0, claudes-c-compiler 2,084 / 2,084 / 0 / 0.
- Reference lists that change with the type-member rule: agent-browser-19 (11 to 19 files) and claudes-c-compiler-17 (3 to 4); the other 38 Rust questions and the comparison questions Q2 and Q3 keep their lists. `wingbox impact` equals the new list on all 40 Rust questions.
- Compiler check (rust-analyzer 1.100.0): agent-browser, 456 required pairs, 453 confirmed, 0 contradicted, 3 not checkable on the host (`cfg(windows)`); claudes-c-compiler, 2,084 of 2,084 confirmed; a third project, 221 of 221. Graph edges the compiler does not have: 0 in the three. Compiler edges of the counted kinds that the reference and the graph lack: 18 on claudes-c-compiler, each the crate root named by `use crate::<exported macro>` (the root file is accepted, not required), 0 on the other two. Outside the question (method calls and fields of a value, trait members, visibility paths): 19, 517 and 6 file pairs.
- The graphs of FastAPI, chrome-devtools-mcp and immich (server) are byte-identical before and after (`index.sexp`).

### Motivation
- The maintainer asked for the Rust resolution to follow the compiler and Cargo sources and to be verified with the Rust toolchain. A paid benchmark run showed the type-member gap on agent-browser (`impl Running` in `seed.rs`, the type in `running.rs`): both Wingbox and the reference stopped at the file of the type.

## 2026-11-17 - Version 0.10.0: Rust item edges through module facades

### Changes
- `scripts/lang/rust/imports.py::resolve_named_imports` (the parked branch rust-reexport-names, rebased): a name imported through a module facade also gives an import edge to the file that defines it, marked `:item` (`:item :candidate` for a glob import whose body names the item, alone or as a path head); the facade edge stays. A use path that starts at a module alias (`use crate::m; use m::Name;`) is followed, and a descendant that glob-imports an ancestor sees the ancestor's private imports. `scripts/build_code_graph.py` adds these edges per module and counts the names it could not follow in the build log. Rust entities record `private` (`scripts/lang/interface.py`, `scripts/lang/rust/parser.py`), and a module reached through a re-export must make the item visible (found on a replay: a private `fn parse_rfc3339_to_unix_ms` in one child shadowed the pub(crate) one in another); cache schema 3 -> 4. Tests REQ-RUST-020 to 030 (chains through two facades, glob, renamed and crate-visible re-exports, items of the facade itself, names not followed, private items, alias-led paths, path heads, private imports of a parent, determinism), REQ-FRESH-040 and 044 (one changed Rust file: `wingbox build` equals a full build; two full builds are byte-identical). REQ-RUST-015 changed with the requirement: a use through a glob facade now also imports the defining file (it asserted the opposite).
- Checks: the graphs of FastAPI, karpathy, chrome-devtools-mcp and immich (server) are byte-identical between 0.9.0 and this version; the usages benchmark's 100 Wingbox rows are unchanged. The Rust benchmark numbers move (impact edge test, Rust impact questions, comparison Q3); they are regenerated on the branches rust-numbers (published reference) and rust-reference-definers (a reference that follows items to their definers) for the maintainer to decide.
- Version 0.10.0 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`; the `:item` edge in the S-expression notation reference, the edge model and the README language table.

### Motivation
- The maintainer's decision of 2026-11-17: fix the Rust facade edges, test first; 0.10.0 because the impact answers for Rust change materially.
## 2026-11-18 - `wingbox update` replaces its own running wingbox.exe on Windows

### Changes
- `src/wingbox/update.py`: `running_launchers(argv0, windows)` names the wingbox.exe the command was started through, and the file it points at when it is a link (pipx on a Windows account that may create links); `upgrade_past_launcher` renames each to `wingbox.exe.<pid>.old` for the time of the installer's upgrade, hands the old file to `start_removal` (a process without a window that deletes it once it is free) when a new launcher is in place, and renames it back when there is none. `Environment.upgrade` carries it into `update()`; `src/wingbox/cli.py` wires it.
- `close_archive_handles()`: Python runs the launcher as a zip archive, first on sys.path, and importlib.metadata (asked for the installed version) keeps that archive open in a cache; `importlib.invalidate_caches()` and a collection close it. Without this the rename itself failed on windows-latest with WinError 32 (run 44262394368), which is also why the installers reported os error 32 and not access denied.
- `tests/test_update.py`: REQ-UPDATE-013 to REQ-UPDATE-019; REQ-UPDATE-018 starts Python the way a launcher does, with the launcher file as the archive.
- `tools/check_update.py` and a step in `.github/workflows/tests.yml`: the checkout built as 98.0.0 and 99.0.0, installed with uv pip, `uv tool`, pip and pipx, and each one updated through its own `wingbox` program; the skill must be refreshed, the program must answer 99.0.0, a second update must pass, nothing may be left beside the program.
- `README.md` (Update), `CHANGELOG.md` (0.9.2).

### Motivation
- Issue #48, report of 2026-11-18 on 0.9.1 with Windows 11: Windows does not allow overwriting or deleting a running program. `uv pip` failed to remove `Scripts\wingbox.exe` (os error 32) and the version stayed; `uv tool upgrade` upgraded the package, failed to copy the new launcher over `bin\wingbox.exe`, and the refresh of skills and hooks was skipped.
- The check was pushed first, without the fix (run 44261657441): green on Linux and macOS, and on windows-latest all four installers failed, uv pip and `uv tool` with the two errors of the report, pip and pipx in the same place (`Check the permissions.`, pipx's upgrade command failed).
- With the fix (run 44264429813) the nine cells are green: on windows-latest uv pip, `uv tool`, pip and pipx each update 98.0.0 to 99.0.0 through their own wingbox.exe, refresh the skill and leave nothing beside the program. pipx there links the program into its bin folder, which is why the link and its target are both renamed.
- `README.md` has a Thanks section and the changelog a line for the reporter, at the maintainer's word.

## 2026-11-17 - Version 0.9.1: Homebrew installs, and a Python reference that counts modules loaded by name

### Changes
- `src/wingbox/update.py`: `homebrew_keg(prefix)`; inside a keg (`.../Cellar/wingbox/<version>/`) `choose_method` returns `homebrew-install` with `brew upgrade wingbox` and runs no installer (REQ-UPDATE-012, written first). Checked with the built wheel in a simulated keg; `wingbox install` started through the `bin/wingbox` link records that link in the hooks.
- `benchmarks/*/tools/py_edges.py`: literal `importlib.import_module("a.b")`, `import_module("a.b")` and `__import__("a.b")` count as pairs (REQ-BENCH-013); questions are still drawn from files reached by an import statement, so the 100 questions and their reference lists are identical (REQ-BENCH-014). FastAPI, whole tree: 1,801 pairs / 1,818 edges / 0 wrong / 0 missing becomes 1,802 / 1,819 / 0 / 0; the comparison's FastAPI rows of the other tools are scored again from their saved edges (one more missing pair each). The build-time tables keep 1,818: that is the edge count of the builds that were timed.
- Version 0.9.1 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`.

### Motivation
- Wingbox installs with Homebrew since today (tap `aileron-tech/wingbox`, which follows PyPI by itself); an upgrade inside the keg by another installer would change files Homebrew owns.
- The maintainer approved the reference change: the graph's extra FastAPI edge is a real dependency that the reference did not read.
- The Rust item edges prepared for this version are not in it: with them the impact benchmark's Rust answers no longer equal the present reference lists, and the maintainer decides on the reference before that is released.

## 2026-11-17 - Version 0.9.0: Wingbox writes only to .wingbox/ and your own settings; the install list; refresh after git

### Changes
- `tests/test_write_zones.py` (REQ-ZONE-001): every command (build, init, learn, align, place, advice, compare, boundaries, impact, usages, the four hooks, install, update --check, uninstall, the MCP server with --build-missing) runs in a temporary repository with a temporary home and temporary folder; any changed path outside `<project>/.wingbox/` and the home folder fails. It found the build lock and the MCP first-build lock in the system temporary folder: both moved into `.wingbox/` (`*.lock` ignored there).
- `src/wingbox/install.py`: `install --project` removed (usage error naming `wingbox init`, `wingbox install` and `wingbox uninstall --project`; REQ-INSTALL-010); install is global only. Items `skill`, `block`, `search-hook`, `edit-hook`, `session-hook`, `git-hook`; `--without ITEM|ASSISTANT:ITEM` (comma separated, repeatable, `none`), `--yes`; the choice is recorded as `:without` in `~/.wingbox/installed.sexp` and reused by a later install; `uninstall --without` keeps items; `uninstall --project` stays for what 0.2.6 to 0.8.x wrote (REQ-INSTALL-011 now builds such an install by hand and removes it). Hooks are handled per item (`hook_kind`, `item_group`, `merge_hook(keys)`, `strip_hook(keys)`).
- `src/wingbox/checklist.py` and `scripts/portable.py::RawKeys`: the list in a terminal, arrow keys and space (termios on POSIX, msvcrt on Windows) or typed numbers; asked only when stdin and stdout are terminals, no CI, no agent variable, no --yes/--dry-run/--sexp. Tests REQ-INSTALL-030 to 039 (REQ-INSTALL-039 drives a pseudo-terminal, skipped on Windows with its reason).
- `src/wingbox/hook.py` / `freshness.refresh_in_background`: `wingbox hook session-start` (SessionStart) and `wingbox hook git` (PostToolUse on Bash; acts on git switch, checkout, pull, merge, rebase, reset, stash pop): note the outside changes and start the background refresh, print nothing. REQ-FRESH-050, 051; REQ-FRESH-011 and REQ-FRESH-012 cover the four hooks in settings and in the plugin manifest; the plugin carries them through the pinned uvx (pin count 6, REQ-PLUGIN-004/005).
- `src/wingbox/update.py`: refreshes global installs only, with the recorded `--without`; one `update-project-files-left` fact per recorded project install (REQ-INSTALL-035).
- `wingbox init` writes `.wingbox/assistant-setup.md` (the always-on block and the four hook entries on `wingbox` from PATH, for a team to commit by hand) and prints it; REQ-CLI-008/010, REQ-FRESH-043 and the new-project test name the file.
- `tools/check_hooks.py`: the global install, the settings of `assistant-setup.md` and the plugin, four hook kinds each, run as Claude Code runs them (passed locally on macOS).
- Graph: a first-party `vendor` folder inside the sources is kept (REQ-CS-075); TypeScript `baseUrl` imports without an alias resolve (REQ-TS-BASEURL-001). Against dependency-cruiser 18 (edges of the 17/11 Angular check, same configuration), 0.8.2 -> this branch: thingsboard ui-ngx 14,837 -> 14,843 of 14,855 edges found, extra 0 -> 0; bitwarden/clients 40,890 -> 40,947 of 41,018, extra 90 -> 90 (42 jest.mock strings, 48 others: the workspace package `@bitwarden/desktop-napi` the resolver did not resolve and similar); angular/components 6,668 of 6,680 unchanged. What remains on the resolver's side is only non-code files and `.d.ts`, plus on angular/components the 4 edges of an alias to a folder (decided: not a bug, the nearest tsconfig maps the alias to `public-api.ts`). `jest.mock` edges stay imports until edge kinds (decided).
- Branch switch timing, angular/components 20.1.0 <-> 20.2.0 (256 changed sources), first search 5 s after the switch: fresh graph 13.6 to 13.7 s after the switch with the search hook only, 8.8 to 9.5 s with the git hook (0.21 to 0.36 s per hook call); the first search answers `wingbox-graph-stale :reason refresh-timeout` in both. FastAPI 0.115.0 <-> 0.118.0 refreshes in 1.7 to 1.8 s, inside the search hook's wait, so there both answer from a fresh graph.
- README: "Where Wingbox writes" table, the install items and flags, team setup through `assistant-setup.md`, moving from `install --project`, the two refresh hooks, the FAQ on branch switches; SKILL.md, SECURITY.md (any write outside the two places is in scope), CLI help.
- Version 0.9.0 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`.

### Motivation
- The maintainer's rule: Wingbox writes only into the project's `.wingbox/` and the user's own global settings, proved by a test over every command; no git hooks; a project is set up by `wingbox init`, assistants by `wingbox install`, with a list a person can untick.
- The FastAPI benchmark count (1,819 edges in the graph against 1,818 published) comes from `tests/test_tutorial/test_generate_clients/test_tutorial004.py` line 17, `importlib.import_module("docs_src.generate_clients.tutorial004")`, an edge added by module loads in 0.6.0 (#60); the edge is right and the reference `py_edges.py` reads import statements only. Proposed: count literal `importlib.import_module` and `__import__` calls as implied pairs in the reference; the published number is not regenerated until the maintainer approves.

## 2026-11-17 - Version 0.8.2: Windows console windows, concurrent refreshes, update without pip, a clean git status

### Changes
- `scripts/portable.py`: `detached_process_options` starts the refresh with `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` and a hidden STARTUPINFO instead of `DETACHED_PROCESS` (Microsoft's process creation flags: CREATE_NO_WINDOW is ignored with DETACHED_PROCESS or CREATE_NEW_CONSOLE; a detached process has no console, so each console child gets a new one); `hidden_process_options` for every captured child (git in git_ignore and diff_functions, the installers of update, the MCP server's first build, the language server). Test REQ-PORT-011 on windows-latest: with DETACHED_PROCESS the children report a console window that is visible (handle 197000, visible true), with the fix none has a window; REQ-PORT-012.
- `src/wingbox/refresh.py`: a refresh re-reads the state under the lock and ends at once when nothing is marked (another refresh published the marks while it waited). Cause of "build ... for 0 changed files": with nothing marked the refresh fell through to the build branch. Tests REQ-FRESH-041 (two hooks at once), REQ-FRESH-042 (no change: under 100 ms, no build).
- `src/wingbox/update.py`: `has_pip()`; without pip `uv pip install --python <python> --upgrade wingbox` (latest version from `uv pip install --dry-run --upgrade --reinstall-package wingbox`), without uv a `no-pip` message with the commands to run. REQ-UPDATE-010, 011.
- `.wingbox/.gitignore` lists itself; a build appends to `decisions.sexp` only when it or `config.yaml` exists. REQ-FRESH-043; REQ-FRESH-030 changed: the generated `.gitignore` is ignored and `decisions.sexp` is not created by a plain build (the requirement changed with this report).
- README: the `.wingbox/` table and one paragraph on which files appear in git; FAQ on `wingbox-graph-stale` after a branch switch, measured: FastAPI 0.115.0 to 0.118.0 (402 of 1,142 Python files) redoes 1,006 of 1,152 modules in a partial update without its limits, 4.6 s against 5.5 s for a full build and 5.6 s for the incremental one, so the limits stay.
- Version 0.8.2 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`.
- Why: the report of a test on Windows 11 in issue #48.
- Added after installing by clicks in Claude Desktop and the Codex app: `plugins/wingbox/codex.mcp.json` starts `uvx --quiet --from <pin> wingbox mcp --build-missing` (pin kept by `tools/sync_plugin.py`, REQ-PLUGIN-005/011); `wingbox mcp --build-missing` takes a first-build lock so that two servers build once, waits for a running `wingbox build`, and never builds outside a source repository (REQ-MCP-018, 019; the server tests' fixture is now a git work tree); one short description in every manifest (REQ-PLUGIN-014); `tests.yml` skips Markdown, docs/ and LICENSE changes and cancels a superseded run outside main, `docs.yml` runs the documentation tests on one cell for them.

## 2026-11-17 - Version 0.8.1: init on a small repository

### Changes
- `scripts/architecture/config.py`: `render_config` writes `parts: {}` when no part is judged (a repository below `min_source_files`); `parse_config` reads an empty file, an empty or missing `parts` as one undetermined part `.` with the note ".wingbox/config.yaml declares no parts: the whole repository is one undetermined part"; errors name the line of `parts` and of each part key (from `yaml.compose`).
- `scripts/build_code_graph.py`: `architecture_plan` prints the note once on stderr.
- Tests REQ-ARCH-042..045 (`tests/test_small_repository.py`): init then build on repositories with 1, 3 and 11 source files, and a new project with no source file (init with --language and --style); configs with empty or missing `parts` from 0.6.0 to 0.8.0; broken configs name the line; learn, align, advice, place, compare, boundaries and the search hook on a small repository.
- `pyproject.toml`, `server.json` and `WINGBOX_VERSION`: 0.8.1.
- Why: found while preparing the GitHub Action: on a fixture of eight files `wingbox init --yes` wrote a config that made the next build fail.

## 2026-11-17 - Search hook: a hint without a graph, smart case

### Changes
- `src/wingbox/hook.py`: in a source repository (a git work tree or a folder with a project manifest, holding files of a supported language within four levels and 5,000 entries) without `.wingbox/index.sexp`, the search hook answers `(wingbox-no-graph :root <root> :hint ...)` once per session and repository; the mark is a file under `~/.wingbox/hook-hints` (pruned after 7 days), nothing is written into the project and nothing is built. No hint in the home folder, outside a repository or in a repository of documents. REQ-INSTALL-025..027; REQ-FRESH-026 and REQ-FRESH-032 now expect the hint (they asserted the silence that was the reported bug), and the freshness and project-folder tests run with a temporary home.
- Grep patterns use smart case (no upper-case letter outside escapes: ignore case; Grep's `-i`: always ignore case); the longest-word retry follows the same case. REQ-INSTALL-024.
- Why: the maintainer's session read four files in a repository without a graph and the hook said nothing, so the agent never learned Wingbox was there; and `subscription` found nothing in the hook while `wingbox quick subscription` matched 17 modules.
- Measured on fastapi without a graph: the no-graph check takes 2.3 ms in the hook (first hint) and 1.4 ms when already told; the whole hook process 0.27 s, as before (Python start).

## 2026-11-17 - Version 0.8.0: Claude Desktop extension

### Changes
- `pyproject.toml`, `server.json` and `WINGBOX_VERSION`: 0.8.0 (a new command-line option, `wingbox mcp --build-missing`, and a new artefact, the `.mcpb` bundle).
- `CHANGELOG.md`: `## 0.8.0 - 2026-11-17`. README: section "Claude Desktop" (install, the repository setting, first build, what the `uv` server type needs; the uv requirement is stated as the MCPB specification gives it, since it was not checked inside Claude Desktop).
- `tools/sync_plugin.py`: the plugin follows the newest `vX.Y.Z` tag that is an ancestor of HEAD (`git describe --tags --abbrev=0`), with a "fetch the tags and the history" message when tags exist but none is reachable; REQ-PLUGIN-017. Why: the run of 350aa31 went red on macOS 3.11 when the v0.7.2 tag landed during the run.
- Release note for the maintainer: build the bundle with `python3 tools/build_mcpb.py` from the release commit and attach `dist/wingbox-0.8.0.mcpb` to the GitHub release after PyPI has 0.8.0 (the bundle pins `wingbox==0.8.0`).

## 2026-11-17 - Claude Desktop extension (MCP Bundle)

### Changes
- `tools/build_mcpb.py`: builds `dist/wingbox-<version>.mcpb` deterministically (sorted entries, fixed times): `manifest.json` (manifest 0.4, server type `uv`, tools generated from `mcp_server.TOOLS`, a required `repository` directory setting passed as `--root`, platforms darwin, win32 and linux), a `pyproject.toml` pinning `wingbox==<version>`, `src/server.py` (runs `wingbox mcp`), the plugin icon and LICENSE.
- `tools/mcpb/`: the MCPB manifest schema v0.4, copied unchanged from github.com/modelcontextprotocol/mcpb at commit 70fe3b3 (2026-04-22), with its source in SOURCE.txt.
- `src/wingbox/mcp_server.py`: `--build-missing` starts `wingbox build` in a child process when the root has no graph; tool calls wait up to `WINGBOX_FIRST_BUILD_WAIT` seconds (default 45) and otherwise answer that the first build is running; the child is stopped when the client closes the server.
- Tests: REQ-MCPB-001..004 (`tests/test_mcpb.py`: schema validation with a small draft-07 validator, tools equal to `tools/list` over stdio, version, archive contents and determinism); REQ-MCP-015..017.
- Why: the maintainer asked for a Claude Desktop extension due 20/11.
- Verification: `npx @anthropic-ai/mcpb@2.1.2 validate` and `pack` pass; the bundle started on macOS with the manifest's command (`uv run --directory <bundle> src/server.py --root <project> --build-missing`, the dependency pointed at this checkout) answered `tools/list` (18 tools) and `impact` after building the graph on first use.

## 2026-11-17 - Version 0.7.2: documents

- The maintainer found the README's title and several sections out of date (old tagline, the two-tool comparison of an early run, no find-usages section, no operating systems). The README was rewritten in #80; PyPI shows it only with a release, hence 0.7.2.
- New test REQ-DOC-003: every number in the README's comparison and find-usages tables is a number of the generated reports; checked by putting an old figure back, which fails the test.
- `pyproject.toml`, `server.json` and `WINGBOX_VERSION` say 0.7.2; no code change since 0.7.1.
- Verification: the nine CI cells on the pull request.

## 2026-11-17 - Version 0.7.1: hooks and MCP server without Git Bash on Windows

### Changes
- `plugins/wingbox/.claude-plugin/plugin.json`: both hooks run `uvx --offline --quiet --from git+https://github.com/aileron-tech/wingbox@<commit> wingbox hook ...` and the MCP server `uvx --quiet --from ... wingbox mcp`, in exec form with no shell; the hooks carry `"onFailure": "continue"`. `scripts/wingbox-run` and its four sh tests are removed.
- `tools/sync_plugin.py`: the pin lives in the Claude Code manifest (three literal 40-character commits, one per uvx command); the fallback without tags reads it there.
- `src/wingbox/install.py`, `cli.py`: one hook form on every system and scope, exec form with `onFailure: continue`; `Machine.hook_program` is the program and its leading arguments (`global_hook_program`), the project scope uses `("wingbox",)`; `is_wingbox_hook` still recognises the shell form of 0.7.0 and earlier.
- `tools/check_hooks.py` and a CI step on all nine cells: install the checkout into a virtual environment, build a project whose folder name holds a space and non-ASCII letters, run `wingbox install` and `wingbox install --project`, take the plugin manifest with the pin replaced by the checkout, and run every entry as Claude Code does (resolved with `shutil.which`, a real `.exe` required on Windows, `shell=False`, Git for Windows removed from `PATH` there), with a Grep and an Edit hook input; the search hooks must name the defining module.
- Docs: README, plugin README, AGENTS.md, SECURITY.md, docs/store/README.md; version 0.7.1 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`.
- Why: the maintainer relayed that the reporter of #48 needs the hooks to run under PowerShell or cmd; in 0.7.0 the plugin's hooks and MCP server needed the `sh` of Git for Windows.
- Measured on this Mac (immich graph, search hook, median of 5): installed `wingbox` through the old launcher 0.41 s, `uvx --offline` direct 0.80 s; with only uv installed the old launcher already ran the same `uvx --offline`. `uvx` with a full-commit pin still contacts api.github.com when online (uv 0.6.6), so the hooks keep `--offline`; the MCP server, which starts first in a session, fetches.

## 2026-11-17 - Version 0.7.0: native Windows support (issue #48)

### Changes
- `.github/workflows/tests.yml`: the suite on ubuntu, macos and windows with Python 3.11, 3.12 and 3.13 (fail-fast off, uv with every requirements file, `PYTHONUTF8=0`). The first Windows run gave the list of failures; the work followed it.
- `scripts/portable.py`: the one module of platform differences: file locks (fcntl or msvcrt), detached processes (new session, or `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`), `replace` with a short retry on `PermissionError`, directory fsync, the start method of worker processes (`WINGBOX_START_METHOD` overrides it), ANSI colours, UTF-8 streams, and the edge path functions `local_path` and `relative_posix`.
- Builder: no `fcntl`; the parse pool and the resolution workers use an explicit context with a single-process fallback; spawned resolution workers rebuild their context in an initializer; graph and side files written with LF; module paths and capability-cache keys use `/`.
- Freshness, refresh, hooks and queries: locks and detached refresh through the helper; paths from Claude Code hooks and from the command line normalised at the edge; no string prefix checks on paths.
- Install: on Windows the Claude Code hooks of a global install are written in exec form (`command` = `wingbox.exe`, `args` apart); a project install keeps `wingbox hook pre-search`; both forms are recognised by uninstall. Home folders from `Path.home()`; printed paths use `/` and `~`.
- Plugin: the Claude Code hooks in exec form (`sh` + `args`), as the hooks documentation advises for `${CLAUDE_PLUGIN_ROOT}` paths; the launcher stays one `sh` script because the plugin manifest has no per-system key.
- Update and learn: installers found with `shutil.which` (pipx.exe, uv.exe, .cmd shims); child output read as UTF-8; `$EDITOR` parsed for Windows paths, Notepad when none is set.
- `.gitattributes`: text files checked out with LF on every system (the plugin and fixture comparisons are byte comparisons).
- Tests made portable (no `sh` stubs, POSIX path expectations, `USERPROFILE`); skipped on Windows with a stated reason only where the feature cannot exist: the pty questionnaire test and the four tests that run the `sh` launcher. New: REQ-PORT-001..006, REQ-INSTALL-023, REQ-LEARN-014.
- README: PowerShell forms, Windows notes for the plugin and the hooks; classifiers Windows and Python 3.13; version 0.7.0 in `pyproject.toml`, `server.json` and `WINGBOX_VERSION`.
- Why: the maintainer asked for native Windows support as the one exception to the feature freeze.
- Verification: all nine CI cells green; immich built with main and with this branch on macOS, same `index.sexp` bytes, 4.5 s both (side files differ only by the builder fingerprint and an edge order that also varies between two runs of main).

## 2026-11-17 - Version 0.6.2: package page and documents

- The maintainer asked for the PyPI page to be brought up to date; PyPI shows what the last release carries, so the metadata and README changes of #66 go out as a release.
- `pyproject.toml`, `server.json` and `WINGBOX_VERSION` say 0.6.2; no code change since 0.6.1.
- New test REQ-DOC-002: the README has no repository-relative link (such links lead nowhere on the PyPI page).
- Feature work is paused by the maintainer after 0.6.1; the agreed design of the edge model is kept in `docs/edge-model.md`.
- Verification: full suite on Python 3.12 and 3.11; the built wheel's metadata read back.

## 2026-11-17 - version 0.6.1: boundaries never passes a part it did not check

### Changes
- `pyproject.toml` and `WINGBOX_VERSION` of `.github/workflows/map-request.yml`: 0.6.1; the plugin manifests follow the release tag later.
- `CHANGELOG.md`: `## 0.6.1 - 2026-11-17` with two fixes: boundaries no longer prints ok for parts whose declared rules were not read; init names the next step instead of "edit it".
- `README.md`: the lead of the top block says "Your agent sees every file a change touches before it edits, so it no longer forgets a fix. Half the cost of its own search", as the site does now.
- Why: the maintainer asked to ship the false-pass fix at once, with the init wording fix.

## 2026-11-17 - boundaries says which parts it did not check

### Changes
- `scripts/code_graph_search.py`: `not_checked_parts` (parts with files and no rule that was read or determined: declared-rules-not-read, style-undetermined, no-layer-style, too-few-files) and `not_checked_text`; `cmd_boundaries` prints `boundaries-not-checked` first, `boundaries-partial` instead of `boundaries-ok`, and returns the exit status (2 not checked because declared rules were not read, 1 violation, capability violation or pattern break, 0 otherwise); `advice_items` gives such parts the same reason.
- `scripts/architecture/facts.py`: `(arch-declared-unread <part> <file> :reason ...)` for every rule file of a part that was not read.
- `src/wingbox/overview.py`: REPORT.md names the parts that were not checked in Key findings and in Boundary violations; `src/wingbox/cli.py`: `wingbox advice` prints `not checked: part . - declared rules not read (...)`.
- Docs: README, SKILL.md, references/sexp-notation.md (facts and exit status).
- Tests: REQ-BCOV-001..006 (`tests/test_boundaries_coverage.py`); REQ-CLI-012 now expects not-checked for an undetermined part instead of ok.
- Why: on dirac (its own checker reports 73 violations), kopf and marumie, whose rule files 0.6.0 does not read, `boundaries` printed `(boundaries-ok :checked N)`: a false pass for audit evidence.

## 2026-11-17 - init names the next step instead of "edit it"

### Changes
- `scripts/architecture/init.py`: `NEXT_STEP`, printed after `wrote .wingbox/config.yaml` (also by the new-project init in `src/wingbox/cli.py`): `wingbox learn` settles the open points, then `wingbox build`.
- Wording of the same rule in the `wingbox init` help, the `config.py` docstring, the `index.toml` note and the README example.
- Tests: REQ-CLI-008, REQ-NEW-002 and the init printer test assert the next step and that no message says "edit it".
- Why: the maintainer's rule that people do not edit the layer configuration by hand; the agent settles it, a terminal user answers questions.

### Verification
- test_architecture, test_cli, test_new_project, test_learn, test_plugins: 88 OK (Python 3.12).

## 2026-11-17 - version 0.6.0: architecture styles, layer config through the agent, capability rules

### Changes
- `pyproject.toml` and `WINGBOX_VERSION` of `.github/workflows/map-request.yml`: 0.6.0. The plugin manifests follow the release tag later through `tools/sync_plugin.py`.
- `CHANGELOG.md`: `## Unreleased` becomes `## 0.6.0 - 2026-11-17`, with a release summary at the top of its Changes.
- `README.md`: a section with one real example of each architecture command (init, align, place, learn, advice, compare, boundaries) from immich `server`: 15 capability violations and 2 pattern breaks; the boundaries row of the commands table names the capability rules.
- `docs/layer-control.md`: stays a proposal; its Status line names what shipped in 0.6.0.
- Why: the maintainer decided to release what exists as 0.6.0 and to move `wingbox export coderabbit` to the next version.

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
