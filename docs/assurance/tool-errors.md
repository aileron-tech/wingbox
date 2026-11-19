# Tool errors: which wrong answer can hide a defect, and what catches it

Wingbox is used to decide what to read, test and review before a change. A wrong answer matters in one direction above all: when it leaves out something that the change reaches, a defect in that place can pass unseen. This page takes the six ways an answer can be wrong and says for each one, in plain words:

- whether it can hide a defect of the user's code;
- how Wingbox reports the condition today: the fact it prints, where, and the exit status, with the command;
- which check that does not depend on Wingbox catches it.

The lines "how it is reported" were read from the README and the tests and run once on a small project with this checkout (0.14.0); the test that keeps each one true is named. What the graph misses per language is in [known-limits.md](known-limits.md).

## Summary

| Wrong answer | Can it hide a defect? | Does Wingbox say so? | Does the exit status say so? |
|---|---|---|---|
| 1. A missing edge | Yes | No, except for the cases it knows: an unresolved method call, a file it does not know | Only for a file it does not know (1) |
| 2. An extra edge | No: it adds a file to read or a violation to look at | Each row says why it is listed | No |
| 3. A stale graph | Yes | Yes: `wingbox-graph-stale` and `:stale N` | No: 0 |
| 4. A skipped language | Yes | Yes for a language whose parser is missing; no for a language Wingbox does not parse | No: 0 |
| 5. A part that was not checked | Yes | Yes: `boundaries-not-checked`, `boundaries-partial` | With `--strict` always (2); without it only when declared rules were not read (2) |
| 6. A wrong layer | Yes, when the wrong layer makes a rule not apply | The layer of every module is printed with the rule that placed it; nothing marks one as wrong | No |

A script or a gate that must not act on an incomplete answer reads the facts of the third column, not only the exit status: rows 3 and 4 exit 0.

## 1. A missing edge

**What it is.** File A depends on file B and the graph has no edge from A to B. `wingbox impact B` does not list A; `wingbox usages` does not list the use in A.

**Can it hide a defect?** Yes. This is the error that matters most: A is not read, not tested and not reviewed for the change of B. In a boundary check a missing edge is a violation that is not stated.

**How it is reported today.** In general it is not: the tool cannot name an edge it did not find. Three cases are reported:

| Condition | What is printed | Exit status | Command | Test |
|---|---|---|---|---|
| A method call whose receiver type is not in sight | `unresolved-call` rows, apart from the uses | 0 | `wingbox usages <file>::<Class.method>` | `tests/test_usages_command.py::UsagesCommandTests` |
| The file asked about is not in the graph | `(error no-match <file> :suggestions ...)` in place of the answer | 1 | `wingbox impact <file>` | `tests/test_code_graph_search.py::CodeGraphImpactTest`, `tests/test_output_formats.py::ImpactSeveralFilesTest` |
| A whole language was left out | see section 4 | | | |

**Independent checks.**

- The compiler or type checker of the language: a removed or renamed name that A still uses does not compile (TypeScript, Rust, C#, Java, Go, Dart), or fails pyright or mypy in Python.
- The test suite of the project, run in full and not only on the files Wingbox names, before a merge.
- A second resolver. The benchmarks of this repository compare the graph with resolvers that share no code with Wingbox (`benchmarks/impact/tools/`: Python's `ast`, an own TypeScript resolver, an own Rust resolver, checked against rust-analyzer; `benchmarks/php/tools/php_reference.php`), and the uses of a symbol with the language tooling (the TypeScript language service, pyright, rust-analyzer). The same tools answer for one project: "find usages" of the language server for the changed name.
- A text search for the name (`grep`), which finds a use in a string, a configuration file or a language Wingbox does not parse.
- Review of the change by a person who knows which parts are wired at run time.

## 2. An extra edge

**What it is.** The graph has an edge from A to B that the code does not have, or one a reader would not count: `impact B` lists A although a change of B cannot reach A.

**Can it hide a defect?** Not by itself. It costs time: one more file to read. Two indirect effects: a long list is read less carefully, and in a boundary check an extra edge can state a violation that is not one (it fails a gate in the open; it hides nothing).

**How it is reported today.** Every row carries its reason, so a reader can check it against the source:

| What | Printed | Command | Test |
|---|---|---|---|
| Why a file is on the list | by depth, `(:through <file> ...)` for a file reached through another one; `--human` adds "imports it" | `wingbox impact <file>`, `wingbox impact <file> --human` | `tests/test_impact_evidence.py::ImpactEvidenceTypeScriptTest`, `tests/test_output_formats.py::ImpactDefaultFormTest` |
| The line of the import behind an edge | `(imports <module> <module> :line N)` | `wingbox module <id> --lines` | `tests/test_edge_lines.py::EdgeLinesTest` |
| An edge that is a candidate, not a certainty (Rust: a glob import whose body names the item) | `:item :candidate` on the `imports` fact ([references/sexp-notation.md](../../references/sexp-notation.md)) | the facts of the graph, `.wingbox/index.sexp` | `tests/test_rust_reexports.py::RustReexportTest`, `tests/test_impact_evidence.py::ImpactEvidenceRustTest` |

The exit status does not change. The kinds of file an answer may name beyond the import statements (a package `__init__.py` on the way, an importer of compiled output, a Rust path through a module) are listed in [known-limits.md](known-limits.md).

**Independent checks.** Open the named line. The edge tests of the benchmarks count every edge the reference neither requires nor accepts as wrong (`benchmarks/impact/README.md`, Test 3; `benchmarks/php/README.md`, Test 2).

## 3. A stale graph

**What it is.** Files changed after the graph was built and the change is not in it: a new importer is missing from an answer, a deleted one is still listed.

**Can it hide a defect?** Yes, in the same way as a missing edge, for everything that changed since the build.

**How it is reported today.** Before a query command, an MCP tool or the search hook reads the graph, the changed files are brought in; the condition below remains when that is switched off (`WINGBOX_AUTO_REFRESH=0`), fails, or takes longer than the wait.

| Where | What is printed | Exit status | Test |
|---|---|---|---|
| Query commands, on standard error | `[wingbox] WARNING: (wingbox-graph-stale :reason <reason> :changed-files N)`; the answer from the old graph follows on standard output | 0 | `tests/test_freshness.py::ClaudeCodeEditsTest`, `tests/test_architecture_freshness.py::ArchitectureCommandsRefreshTest` |
| `wingbox impact a b` (several files), first line | `(graph :built <time> :files N :stale N)`; `:stale 0` when the graph misses no changed file | 0 | `tests/test_output_formats_cli.py::WingboxImpactCommandTest` |
| `--json` | `"graph": {"built": ..., "files": N, "stale": N}` | 0 | `tests/test_output_formats.py::ImpactSeveralFilesTest`, `tests/test_json_errors.py::CodeModeRecipesTest` |
| MCP answers | start with the `wingbox-graph-stale` line ([README.md](../../README.md), "Keeping the graph fresh") | | a test of this line in an MCP answer: not found |
| The search hook | gives no context from the graph, only `(wingbox-graph-stale :reason ...)` | | `tests/test_freshness.py::ClaudeCodeEditsTest` |

The exit status stays 0: a script reads `graph.stale` (the README shows the three lines of Python) or standard error.

**Independent checks.** `wingbox build .` right before the question, in the same checkout, as a gate in CI does on a fresh clone. `git status` and `git diff --stat <commit>` show what changed since a known point. The checks of section 1 hold here too.

## 4. A skipped language

**What it is.** The files of one language are not in the graph at all. Two different cases:

- a language Wingbox parses, whose parser is not installed where the build ran (a broken install; Dart on Windows on arm64);
- a language Wingbox does not parse.

**Can it hide a defect?** Yes: no file of that language is ever listed as a dependent, however it uses the changed file.

**How it is reported today.**

| Case | What is printed | Exit status | Test |
|---|---|---|---|
| Parser missing, at the build | `[code-graph] WARNING: Dart files are skipped (N files): ... To include them: pip install "wingbox[dart]"`; the fact is written to `.wingbox/graph-skipped.sexp` | 0 | `tests/test_language_parsers.py::SkippedLanguageTest` |
| Parser missing, at every later answer | `(skipped-language dart :files N :add "pip install \"wingbox[dart]\"")`: on standard error of each query command, as the first line of each MCP answer, in the context of the search hook, under the key findings of `REPORT.md` | 0 | `tests/test_skipped_language_notice.py::SkippedLanguageNoticeTest` |
| Parser missing and no other source in the project | no graph is published; the build stops with the install command | 2 | `tests/test_language_parsers.py::SkippedLanguageTest` |
| A language Wingbox does not parse | the build prints the suffixes it reads (`Supported source: ...`) and a count per supported language; nothing counts the other source files. A question about such a file: `(error no-match ...)`, exit 1 | 0 for the build | `tests/test_audit_regressions.py::AuditRegressionTest` (the language of a file is known or explicitly none) |

A gate can test for the file: `test ! -e .wingbox/graph-skipped.sexp` after `wingbox build .`. Whether `boundaries --strict` counts a skipped language as "not checked": not assessed.

**Independent checks.** Compare the languages of the repository with the list the build prints (`git ls-files | sed 's/.*\.//' | sort | uniq -c` gives the suffixes). For the files of a language outside the graph, the checks of section 1: its compiler, its tests, a text search.

## 5. A part that was not checked

**What it is.** `wingbox boundaries` judged some parts of the repository and not others: a part whose own rule file could not be read, a part without a determined style, a part with too few files, files that belong to no part.

**Can it hide a defect?** Yes: an import that breaks a layer rule inside such a part is not stated, and a reader who sees no violation may take that for "checked and clean".

**How it is reported today.**

| Command | What is printed | Exit status | Test |
|---|---|---|---|
| `wingbox boundaries all` | `(boundaries-not-checked :part <part> :reason <reason> ...)` first; `(boundaries-partial :checked N :not-checked M)` in place of `boundaries-ok` | 2 when a part's declared rules were not read; 1 for a violation; otherwise 0, also when a part was not checked for another reason | `tests/test_boundaries_coverage.py::BoundariesCoverageTest` |
| `wingbox boundaries all --strict` | the same lines, one `(boundaries-checked :part P :style S :modules N)` per checked part, and last `(boundaries-result :status pass\|fail\|incomplete\|error ...)` | 0 pass, 1 violation, 2 not fully checked (never a pass), 3 no readable graph | `tests/test_boundaries_strict.py::BoundariesStrictTest` |

`REPORT.md`, `wingbox advice` and the MCP `boundaries` tool say the same ([README.md](../../README.md), "Common code searches"). For a gate use `--strict`: without it a part that is too small or has no determined style leaves the exit status at 0.

**Independent checks.** The rule tool the project already runs (dependency-cruiser, import-linter, ArchUnit and the like), which Wingbox reads rules from but does not replace. Review of the `not-checked` list: each line names the part and the reason.

## 6. A wrong layer

**What it is.** A module is placed in a layer it does not belong to, or in none. The placement comes from the style Wingbox detected for the part and from folder names, unless the project declares its layers.

**Can it hide a defect?** Yes, in one direction: when the wrong layer makes a rule not apply, an import that breaks the architecture is not stated. In the other direction it states a violation that is not one, which is seen and corrected. How often folder names give the right layer is measured: 76.2 % of 240 held-out folders, with 1.2 % matched wrongly ([benchmarks/architecture/README.md](../../benchmarks/architecture/README.md)).

**How it is reported today.** Nothing marks a layer as wrong; the tool prints what it decided and why, so that a person or the coding agent can correct it:

| What | Printed | Command | Test |
|---|---|---|---|
| The layer of every module, with the part, the style and the rule that placed it | `(layer-member <layer> <module> :lang L :part P :style S :rule "..." :match M)` | `wingbox layers` | `tests/test_cli.py::WingboxBuildArchitectureTest` |
| The style of each part, its score and the folders it was recognised by; a part that is "not determined" | text, or facts with `--sexp` | `wingbox init` | `tests/test_architecture.py::PartsAndDetectionTest`, `tests/test_architecture.py::InitOutputTest` |
| Each violation with the part, the style and the rule | fact lines | `wingbox boundaries all` | `tests/test_cli.py::WingboxBuildArchitectureTest`, `tests/test_cli.py::WingboxDeclaredRulesTest` |
| The folders a style could not place, for a decision | the open points | `wingbox learn`, `wingbox align --learn` | `tests/test_learn.py::LearnTest` |

The exit status does not change.

**Independent checks.** The team reads `.wingbox/config.yaml` once and corrects it (`wingbox learn`); the decision is then a reviewed file in the repository. A project that declares its layers in its own rule tool is judged by those rules first ([README.md](../../README.md), "The architecture of each part: `wingbox init`"), and that tool checks them without Wingbox.
