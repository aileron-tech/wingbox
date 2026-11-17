# Wingbox, Graphify, CodeGraph and OntoIndex on the same public tests

Wingbox 0.5.1 from PyPI, run of 2026-11-17. Graphify 0.9.110 from PyPI (`graphifyy`), `GRAPHIFY_BACKEND=none` (no model, no key), run of 2026-11-16. Every number below is produced by the scripts in `tools/` from the files in `results/`; this file is written by `tools/make_report.py`, and `run.sh` reproduces tests A, B and C from nothing.

CodeGraph 1.6.2 from npm (`@colbymchenry/codegraph`), run of 2026-11-17. The three tools ran on the same commits, questions, reference lists and scoring.

Versions: Wingbox 0.5.1, Graphify 0.9.110, CodeGraph 1.6.2. Test E (build time on a hosted runner) is the only part still measured with an earlier Wingbox; its version is named there.

OntoIndex 3.1.9, run of 2026-11-17 in a Linux container (its indexing runs only on Linux), on the same commits, questions, reference lists and scoring; build time not yet measured; its own section is at the end.

## Which tool for which job

- Choose Wingbox for the exact list of files a change reaches: exact lists 5 of 5 (Graphify 1, CodeGraph 1); FastAPI file edges 0 wrong / 0 missing (Graphify 19 wrong / 17 missing, CodeGraph 106 wrong / 25 missing); agent cost 0.021 USD per answer at F1 0.895 (Graphify 0.095 at 0.892, CodeGraph 0.070 at 0.888, built-in search 0.075 at 0.894). Per its documentation it also draws an architecture map with layers and boundaries, and runs fully local.
- Where the others fall short for this job: `codegraph impact` named no file on Q2 and Q3 (Rust), and its file graph links imports to the files that define the names; `graphify affected` answers average 7.9 times the tokens of Wingbox's (7,896 against 1,003), and Graphify costs the most per answer in the agent test (0.095 USD).
- What the others do better: CodeGraph's answers are the shortest where it answers (1,000 tokens against 1,065 for Wingbox and 12,101 for Graphify on the same 3 questions). Per its documentation CodeGraph covers more languages (Swift, Kotlin, C, C++, Dart, Lua and others), keeps its index in sync with file changes automatically and answers symbol and call-flow questions; per its documentation Graphify also maps documents, PDFs, images and videos.
- OntoIndex 3.1.9: exact lists 1 of 5 with `--include-tests` (0 of 5 with its default, which leaves test files out); FastAPI file edges 22 wrong / 118 missing; no file on Q2 and Q3 (Rust); complete lists in 20 of the 100 questions of `benchmarks/impact` (Wingbox 100). Where it does better: on Q4 it names 3 files beyond the reference list against Graphify's 366, and on chrome-devtools-mcp (TypeScript) its list is exact. Per its documentation it records calls, inheritance, routes, execution flows and clusters, maps a git diff to the symbols it touches and serves a web UI; its indexing runs on Linux only. In the agent test it costs 0.075 USD per answer at F1 0.873 and 78,070 tokens, fewer tokens than Graphify (109,071) and CodeGraph (81,151), more than Wingbox (23,768).

## Summary

- Graphify's "71.5x fewer tokens per query" is the size of the whole corpus divided by the size of one graph answer. No answer is checked. On the code of its own example corpus Graphify publishes 8.8x; Graphify 0.9.110 gives 3.5x and 5.0x today, and the same formula gives Wingbox 88.4x (test A).
- Asked which files depend on a file, `wingbox impact` names the exact list in 5 of 5 questions, `graphify affected` in 1 of 5, `codegraph impact` in 1 of 5 (test C).
- File-to-file edges of FastAPI against its 1,802 import pairs, a link to the file that defines an imported name counted as right: Wingbox 0 wrong and 0 missing, Graphify 19 and 17, CodeGraph 106 and 25 (test B).
- A coding agent answers the same questions correctly in all three setups. It spends 23,768 tokens with Wingbox, 59,367 with its built-in search alone and 109,071 with Graphify: Wingbox -60 % against plain search, Graphify +84 % (test D).
- With CodeGraph the agent spends 81,151 tokens (F1 0.888): -26 % against Graphify, +37 % against plain search (test D).
- Building the graph of fastapi (1,227 source files) takes Wingbox 2.2 s (wingbox 0.2.0) and Graphify 17.9 s on a hosted runner; over the five corpora Graphify's extract takes 3.5 to 19.2 times as long as the whole Wingbox build (test E).
- OntoIndex 3.1.9, `ontoindex impact File:<file> --depth 2 --include-tests` names the exact list in 1 of 5 questions and the complete list in 20 of 100 questions of `benchmarks/impact`; FastAPI file edges 22 wrong and 118 missing (precision 0.987, recall 0.934); with OntoIndex the agent spends 78,070 tokens at F1 0.873 (test D).

## What Graphify publishes in its repository

Read at Graphify-Labs/graphify, branch v8, commit 18cd78f (2026-11-15).

| Published number | Where it comes from | Reproducible from the repository |
|---|---|---|
| 71.5x fewer tokens per query; 8.8x on code only | `graphify benchmark` (`graphify/benchmark.py`) on `worked/karpathy-repos`: nanoGPT, minGPT, micrograd, 5 papers, 4 images | yes: formula, corpus list and five questions are published |
| 12 of 12 inferred cross-file edges correct | manual audit under `fastapi/` of the FastAPI repository (BENCHMARKS.md) | yes: the repository is public |
| ERPNext 82.0 % against 70.8 %; LOCOMO; LongMemEval | BENCHMARKS.md names `crosstool/run.py` and `memory/runner.py` | no: neither folder, nor the questions, nor the gold facts are in the repository |

The formula of `graphify benchmark`: corpus tokens = words x 100 / 75 (every file in the context); answer tokens = characters / 4; reduction = corpus tokens / mean answer tokens over the questions that matched something in the graph.

## Test A. Graphify's token formula, Graphify's corpus

Test A checks a number Graphify publishes; it has no CodeGraph row. The size of CodeGraph's impact answers is in test C.

Corpus: the code of karpathy/nanoGPT, minGPT and micrograd: 29 Python files, 12,748 words, 16,997 tokens. Graphify's review states ~16,997 tokens for the same 29 files.

| Tool and query set | Queries answered | Mean answer, tokens | Reduction |
|---|---|---|---|
| Graphify, published in `worked/karpathy-repos/review.md` (code only) | not stated | ~1,929 | 8.8x |
| Graphify 0.9.110, its five default questions, corpus counted by its own word counter | 1 of 5 | 4,909 | 3.5x |
| Graphify 0.9.110, the five questions of its review | 2 of 5 | 3,413 | 5.0x |
| Wingbox `analyze`, every module of the corpus | 29 of 29 | 192 | 88.4x |
| Wingbox `impact <file> 2`, every file of the corpus | 29 of 29 | 66 | 259.2x |
| Wingbox, the five questions of Graphify's review, one `quick <keyword>` per keyword | 2 of 5 | 351 | 48.4x |

The literal command `graphify benchmark`, run in the corpus folder after `graphify extract . --code-only`, estimates the corpus from the node count and prints 3.3x (`results/graphify_benchmark_literal.txt`).

Wingbox takes a module or a keyword, not a sentence, so its main row is every module of the corpus instead of five sentences. Largest single `analyze` answer: 524 tokens (32.4x). The whole Wingbox graph file is 1,834 tokens.

Graphify's published graph of the full corpus (`worked/karpathy-repos/graph.json`) holds 177 nodes and 246 links; its review states 285 and 340. Graphify 0.9.110 run on that file with the published 92,616 words and five questions gives 45.0x with 2 of 5 questions matched (published: 71.5x). Papers and images are outside Wingbox, so the full-corpus row has no Wingbox counterpart.

Same formula on FastAPI (1,223 Python files, 321,676 words, 428,901 tokens):

| Tool and query set | Queries answered | Mean answer, tokens | Largest answer, tokens | Reduction |
|---|---|---|---|---|
| Graphify 0.9.110, five default questions | 5 of 5 | 1,369 | 1,634 | 313.3x |
| Wingbox `analyze`, every module | 1,223 of 1,223 | 527 | 8,769 | 813.7x |
| Wingbox `impact <file> 2`, every file | 1,223 of 1,223 | 141 | 5,255 | 3039.0x |
| Wingbox, Graphify's five default questions by keyword | 5 of 5 | 957 | | 448.2x |

## Test B. Are the edges in the code?

The formula above does not check a single answer. This test does: every file-to-file dependency edge of each tool against the import statements of the code.

Reference: `tools/py_edges.py` reads every `import` with Python's own parser (`ast`) and resolves it to a file, and counts a module loaded by a literal name (`importlib.import_module("a.b")`, `__import__("a.b")`) like an import. Check of the reference itself: the import graph built by grimp 3.20 (the engine of import-linter) on FastAPI gives 1,801 file pairs; 1,801 of them are the reference's 1,801 pairs of import statements; 0 only in grimp, 0 only in the reference; grimp reads import statements only, so the 1 pair of a module loaded by name is not in its graph. The Wingbox rows of this test were run again with Wingbox 0.9.1 on 2026-11-17, when the reference started to count modules loaded by a literal name; the other tools' saved edges were scored again against the same reference.

| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |
|---|---|---|---|---|---|---|---|
| Karpathy repositories | 15 | Wingbox | 15 | 0 | 0 | 1.000 | 1.000 |
| Karpathy repositories | 15 | Graphify, module-level imports | 15 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, whole tree | 1,802 | Wingbox | 1,819 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, whole tree | 1,802 | Graphify, module-level imports | 1,812 | 22 | 33 | 0.988 | 0.982 |
| FastAPI, `fastapi/` package | 124 | Wingbox | 130 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, `fastapi/` package | 124 | Graphify, module-level imports | 122 | 0 | 7 | 1.000 | 0.944 |
| Karpathy repositories | 15 | CodeGraph, file graph | 25 | 10 | 0 | 0.600 | 1.000 |
| FastAPI, whole tree | 1,802 | CodeGraph, file graph | 2,434 | 1,296 | 669 | 0.468 | 0.629 |
| FastAPI, `fastapi/` package | 124 | CodeGraph, file graph | 143 | 26 | 7 | 0.818 | 0.944 |

An edge to a package `__init__.py` that Python executes on the way to the named module counts as right and is not required. Graphify also links an import to the file that defines the imported name; with those links accepted its FastAPI figures are precision 0.994 and recall 0.991 (19 wrong, 17 missing).

CodeGraph's file graph is the one its file-level queries read (`getFileDependencyPairs`, exported by `tools/cg_graph.js`): two files are linked when a symbol of one has a resolved edge (import, call, instantiation, reference, inheritance, decorator) to a symbol of the other. An import is linked to the file that defines the imported name, so the strict rule above counts most re-exported imports as wrong. The same rule with those links accepted, for the three tools:

| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |
|---|---|---|---|---|---|---|---|
| Karpathy repositories | 15 | Wingbox | 15 | 0 | 0 | 1.000 | 1.000 |
| Karpathy repositories | 15 | CodeGraph, file graph | 25 | 10 | 0 | 0.600 | 1.000 |
| FastAPI, whole tree | 1,802 | Wingbox | 1,819 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, whole tree | 1,802 | Graphify, all import links | 2,978 | 19 | 17 | 0.994 | 0.991 |
| FastAPI, whole tree | 1,802 | CodeGraph, file graph | 2,434 | 106 | 25 | 0.957 | 0.986 |
| FastAPI, `fastapi/` package | 124 | Wingbox | 130 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, `fastapi/` package | 124 | CodeGraph, file graph | 143 | 14 | 5 | 0.902 | 0.960 |

In the Karpathy corpus 9 of CodeGraph's 10 wrong edges join two different repositories (for example a call in nanoGPT resolved to a class of the same name in minGPT); nothing in one repository imports the other. CodeGraph's import edges alone give 2,407 edges on FastAPI, 90 wrong and 34 missing under the same rule.

CodeGraph's README publishes a "measured cross-file coverage" per language (Python on psf/requests: 100 %): the share of source files that have at least one resolved dependent. It counts files, not edges, and asks only whether something links to a file: a file with ten real dependents of which one is found counts as covered, and so does a file whose only dependent is wrong. It cannot see a missing edge into a file that has another, or an edge that should not exist. The test above checks every edge against the import statements, so the two numbers measure different things and a 100 % coverage is compatible with wrong and missing edges.

## Test C. The impact command without an agent

Question: every file that depends on file X directly or through one other file. Five files in four public repositories. `wingbox impact <file> 2`, `graphify affected "<file>" --depth 2` and `codegraph impact <file name> --file <file> --depth 2` are run as they are and the files they name are compared with the reference list.

Reference lists (`gold/`), none taken from either tool: Q1 from an own TypeScript resolver (`tools/ts_resolve.py`); Q2 and Q3 from an own Rust resolver (`tools/rust_resolve.py`), every file of the two lists read against the source; Since 2026-11-17 (Wingbox 0.10.0) the Rust resolver also counts, for a path that names an item, the file that defines the item (followed through `pub use` re-exports), and the items a module names after a glob import (`use x::*`), found by scanning the module text. A file that uses an item depends on the file that defines it; the earlier reference stopped at the module facade that re-exports the item. The questions stay the same. Q4 and Q5 from `tools/py_edges.py`, whose import statements equal grimp. `run.sh` computes the five lists again and stops when one differs from the committed list.

| Question | Reference files | Wingbox: right / wrong / missing | Wingbox output, tokens | Graphify: right / wrong / missing | Graphify output, tokens | CodeGraph: right / wrong / missing | CodeGraph output, tokens |
|---|---|---|---|---|---|---|---|
| Q1 `src/collectors/PageCollector.ts` (chrome-devtools-mcp) | 30 | 30 / 0 / 0 | 585 | 30 / 0 / 0 | 1,324 | 30 / 0 / 0 | 525 |
| Q2 `cli/src/validation.rs` (agent-browser) | 32 | 32 / 0 / 0 | 464 | 17 / 2 / 15 | 2,550 | 0 / 0 / 32 | 43 |
| Q3 `src/backend/elf/constants.rs` (claudes-c-compiler) | 91 | 91 / 0 / 0 | 1,355 | 30 / 0 / 61 | 625 | 0 / 0 / 91 | 47 |
| Q4 `fastapi/openapi/models.py` (fastapi) | 29 | 29 / 0 / 0 | 614 | 29 / 366 / 0 | 32,188 | 26 / 2 / 3 | 550 |
| Q5 `fastapi/sse.py` (fastapi) | 99 | 99 / 0 / 0 | 1,997 | 98 / 0 / 1 | 2,791 | 96 / 0 / 3 | 1,925 |
| Mean output, tokens | | | 1,003 | | 7,896 | | 618 |

Exact list: Wingbox 5 of 5, Graphify 1 of 5, CodeGraph 1 of 5. `graphify affected` works on functions and methods and follows calls and references as well as imports; restricted to its import relations (`--relation imports_from --relation imports --relation re_exports --relation dynamic_import`) it gives: Q1 28 / 0 / 2; Q2 5 / 2 / 27; Q3 30 / 0 / 61; Q4 28 / 3 / 1; Q5 98 / 0 / 1.

`codegraph impact` takes a symbol; a file is a node of CodeGraph's graph, and `--file` picks the node of that path. From a file node it follows the edges that point at the file itself. In Q2 and Q3 (Rust) no edge points at the file node, so the answer names no file. On the 3 questions it answers, its output averages 1,000 tokens against 1,065 for Wingbox. CodeGraph's file-level graph, walked two steps back from the file as `codegraph affected` walks it before it keeps only test files (`tools/cg_graph.js`; the file count equals what that command reports as traversed), gives: Q1 30 / 30 / 0; Q2 31 / 17 / 1; Q3 0 / 0 / 91; Q4 28 / 674 / 1; Q5 98 / 34 / 1.

## Test D. A coding agent answers the same five questions

Claude Code, headless, model alias `sonnet` (claude-sonnet-5-5), no user settings, skills or connectors. Four setups: built-in search only; search plus the `wingbox` command; search plus the `graphify` command; search plus the `codegraph` command. Graphs are built beforehand. The prompt names the tool's impact command in one sentence of the same shape for every tool (`tools/agent_test.py`). 3 repeats per question and setup, four trials at a time. Score: F1 of the answer list against the reference list. Tokens: everything the model read and wrote, cache included. Seconds: wall clock of one trial while four run.

Plain search and Graphify ran interleaved on 2026-11-16. The Wingbox 0.5.1 trials ran on 2026-11-17 and the CodeGraph trials on 2026-11-17, each setup on its own, four trials at a time, with the same prompt, model and limits. Tokens, cost and F1 compare directly; seconds less so, since the other trials running at the same time differ.

| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | Seconds | USD per trial | Refused shell commands |
|---|---|---|---|---|---|---|---|---|
| Plain search (Read, Grep, Glob) | 15 | 0.894 | 0.508 | 59,367 | 5.2 | 25.2 | 0.075 | 1 |
| Wingbox | 15 | 0.895 | 0.508 | 23,768 | 2.3 | 9.9 | 0.021 | 0 |
| Graphify | 15 | 0.892 | 0.508 | 109,071 | 7.8 | 34.9 | 0.095 | 12 |
| CodeGraph | 15 | 0.888 | 0.508 | 81,151 | 6.2 | 27.0 | 0.070 | 11 |

- Wingbox against Graphify: tokens -78 %, time -72 %, cost -78 %.
- Wingbox against plain search: tokens -60 %, time -61 %, cost -72 %.
- Graphify against plain search: tokens +84 %, time +39 %, cost +27 %.
- CodeGraph against plain search: tokens +37 %, cost -7 %; against Graphify: tokens -26 %, cost -26 %; Wingbox against CodeGraph: tokens -71 %, cost -70 %.

| Question | Plain: F1 / tokens / s | Wingbox: F1 / tokens / s | Graphify: F1 / tokens / s | CodeGraph: F1 / tokens / s |
|---|---|---|---|---|
| Q1 (chrome-devtools-mcp, 30 files) | 0.995 / 39,364 / 16.8 | 1.000 / 26,403 / 7.6 | 1.000 / 83,439 / 26.1 | 1.000 / 31,575 / 10.7 |
| Q2 (agent-browser, 32 files) | 0.968 / 69,193 / 25.5 | 0.968 / 24,259 / 7.5 | 0.968 / 158,897 / 40.4 | 0.968 / 81,990 / 24.4 |
| Q3 (claudes-c-compiler, 91 files) | 0.508 / 63,529 / 26.2 | 0.508 / 19,988 / 9.2 | 0.508 / 81,091 / 33.0 | 0.508 / 90,432 / 28.9 |
| Q4 (fastapi, 29 files) | 1.000 / 57,892 / 26.4 | 1.000 / 28,656 / 11.1 | 1.000 / 65,159 / 25.8 | 0.971 / 106,160 / 37.2 |
| Q5 (fastapi, 99 files) | 1.000 / 66,858 / 31.1 | 1.000 / 19,535 / 14.3 | 0.983 / 156,768 / 49.1 | 0.995 / 95,597 / 34.0 |

Every trial with its answer list is in `results/agent_summary.json`. The 60 trials cost 3.91 USD in total.

## Test E. Building the graph

Seconds from a fresh checkout to a graph: every run starts from a new export of the repository, with no earlier output and no cache, and the two tools alternate. Median of 3 runs on a GitHub-hosted runner (Linux, AMD EPYC 9V45 96-Core Processor, 4 logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`. wingbox 0.2.0; graphify 0.9.110 with `GRAPHIFY_BACKEND=none`.

`wingbox build .` writes the graph, the report and the map. `graphify extract . --code-only` writes the graph; `graphify cluster-only . --no-label --no-viz` is the step this benchmark runs before `graphify affected`.

| Corpus | Source files | Wingbox build, s | Graphify extract, s | Graphify cluster, s | Extract / build | Wingbox graph | Graphify graph | CodeGraph build, s |
|---|---|---|---|---|---|---|---|---|
| karpathy | 29 | 0.23 | 0.80 | 0.30 | 3.5x | 29 modules, 15 import edges | 241 nodes, 363 links | not measured yet (machine under load) |
| chrome-devtools-mcp | 296 | 0.59 | 11.34 | 2.18 | 19.2x | 293 modules, 918 import edges | 4,804 nodes, 11,240 links | not measured yet (machine under load) |
| agent-browser | 363 | 1.25 | 16.68 | 5.31 | 13.3x | 359 modules, 751 import edges | 10,516 nodes, 27,103 links | not measured yet (machine under load) |
| claudes-c-compiler | 351 | 1.27 | 7.56 | 5.24 | 6.0x | 351 modules, 1,462 import edges | 9,413 nodes, 28,384 links | not measured yet (machine under load) |
| fastapi | 1,227 | 2.17 | 17.85 | 6.37 | 8.2x | 1,227 modules, 1,818 import edges | 12,897 nodes, 26,044 links | not measured yet (machine under load) |

The size of the graph is read from the file each build wrote, in every run; the summary stops when two runs of one tool differ. CPU seconds per step and every single run are in `results/build_times.json`.

CodeGraph was not part of the hosted-runner run. `tools/time_builds_same_machine.py` times the three tools on one machine, the tools taking turns and their order rotating from round to round, cold and after a one-line edit; it has not been run on a quiet machine yet.

## Why the numbers differ

The three tools are built for different questions. Tests B and C ask one question, which files depend on a file through its import statements, and the reference lists are built from import statements. Each tool's design explains its numbers on that question; the design points below are taken from each tool's own documentation.

**Wingbox** is built for this question. Its README describes the blast radius of a change as "everything that depends, directly or transitively, on the code you are about to edit", computed from the reverse dependencies of a file-level code graph, and `wingbox impact <file>` prints the dependents of a file by depth. Wingbox resolves each import statement to a file, which is also how the reference lists are made, so its answers match them. The benchmark measures the question Wingbox is built for; it does not measure symbol-level or flow questions.

**Graphify** builds a knowledge graph of a whole project: "code, docs, PDFs, images, videos" (Graphify README). In code it records functions, methods and classes and links them through `calls`, `imports` and `inherits`, and tags every edge `EXTRACTED` (read from the source) or `INFERRED` (resolved by Graphify). `graphify affected` is a "reverse traversal to find nodes impacted by X" over all these relations, a wider question than which files import X: it names files that call or reference something in X, which is why Q4 lists 366 files beyond the import statements, and 3 when the same command follows only its import relations (test C). Graphify also links an import to the file that defines the imported name and resolves some names across files; that adds and drops some file pairs (19 and 17 on FastAPI, test B). Its documents, papers and images are outside these tests.

**CodeGraph** builds a symbol-level graph: "nodes (functions, classes, methods) and edges (calls, imports, extends, implements)" stored in SQLite, with references resolved after extraction ("function calls -> definitions, imports -> source files", CodeGraph README, How It Works). Two files are linked when a symbol of one reaches a symbol of the other, and an import is linked to the file that defines the imported name. A file-to-file check against import statements therefore counts many of its links as extra although they are correct in its model; the second table of test B accepts those links. Its `impact` command takes a symbol ("Analyze what code is affected by changing a symbol"), so a file question needs `codegraph impact <file name> --file <path>`; from a file node it follows the edges into that node, and in the two Rust repositories no edge points at the file node, so Q2 and Q3 returned no file. Its file-level command `codegraph affected` is documented for test files ("Find test files affected by changed source files"). When it answers, its answers are the shortest of the three (test C). Its main agent tool, `codegraph_explore`, returns source code and call paths for symbols, which these tests do not measure.

**OntoIndex** is a fork of GitNexus (its NOTICE: "This product includes code originally developed as GitNexus"). It builds a symbol-level graph stored in LadybugDB, a fork of Kuzu: files, folders, functions, classes, methods, routes, plus execution-flow and cluster nodes, linked through one relation table whose type is IMPORTS, CALLS, EXTENDS, IMPLEMENTS and others, each edge with a confidence. `ontoindex impact` ("Blast radius analysis: what breaks if you change a symbol") walks these relations from a symbol; a file is the node `File:<path>`, and the walk follows the edges into that node, which for imports are file-to-file IMPORTS edges. In TypeScript and Python its answers are close to the import lists (Q1 exact, Q5 1 missing). In the two Rust repositories no edge from another file reaches the target file or its symbols, so Q2 and Q3 returned no file; in immich-server, whose imports go through the tsconfig alias `src/*` and name `.js` files, none of the 20 questions got a complete list. On FastAPI it links some standard-library imports to repository files of the same name (`import gzip` to `fastapi/middleware/gzip.py`) and leaves out imports of a submodule through its package (`from fastapi import routing`): 22 and 118 pairs (test B). By default `impact` leaves test files out; its README example adds `--include-tests`, which the main rows use. Its answers are JSON with the depth, relation, confidence and provenance of every node, 4.3 times the tokens of Wingbox's on the questions it answers. Its `analyze` takes a native file lock built only for Linux, so it ran in a Linux container.

## OntoIndex

OntoIndex 3.1.9: GitHub release asset `ontoindex-3.1.9.tgz` (github.com/ontograph/ontoindex, AGPL-3.0), checked against its sha256 and installed without lifecycle scripts in a Linux container (`tools/oi.Dockerfile`, `tools/oi_run.sh`); every OntoIndex step runs with `--network none`. Its `analyze` takes a native file lock that exists only on Linux, so it does not index on macOS. Not yet measured: build time (`tools/time_builds_tools.py`, Wingbox, Graphify and OntoIndex interleaved, cold and after a one-line edit); the script is in place.

Test B, edges against the import statements (same reference and scoring as above):

| Corpus | Pairs in the code | Edges | Wrong | Missing | Precision | Recall |
|---|---|---|---|---|---|---|
| Karpathy repositories | 15 | OntoIndex, IMPORTS edges between files: 15 | 0 | 0 | 1.000 | 1.000 |
| Karpathy repositories | 15 | OntoIndex, every relation its impact follows, lifted to files: 17 | 2 | 0 | 0.882 | 1.000 |
| FastAPI, whole tree | 1,802 | OntoIndex, IMPORTS edges between files: 1,718 | 22 | 118 | 0.987 | 0.934 |
| FastAPI, whole tree | 1,802 | OntoIndex, every relation its impact follows, lifted to files: 2,876 | 1174 | 112 | 0.592 | 0.938 |
| FastAPI, `fastapi/` package | 124 | OntoIndex, IMPORTS edges between files: 122 | 0 | 8 | 1.000 | 0.935 |
| FastAPI, `fastapi/` package | 124 | OntoIndex, every relation its impact follows, lifted to files: 134 | 9 | 5 | 0.933 | 0.960 |

Test C, the five questions: `ontoindex impact File:<file> --depth 2`, the files of the nodes it names against the reference list.

| Question | Reference files | default (test files left out): right / wrong / missing | with `--include-tests`, as in its README: right / wrong / missing | its file-level IMPORTS edges, two steps back (`ontoindex cypher`): right / wrong / missing | output, tokens (default (test files left out)) | output, tokens (with `--include-tests`, as in its README) |
|---|---|---|---|---|---|---|
| Q1 (chrome-devtools-mcp) | 30 | 12 / 0 / 18 | 30 / 0 / 0 | 30 / 0 / 0 | 1,346 | 2,714 |
| Q2 (agent-browser) | 32 | 0 / 0 / 32 | 0 / 0 / 32 | 0 / 0 / 32 | 468 | 468 |
| Q3 (claudes-c-compiler) | 91 | 0 / 0 / 91 | 0 / 0 / 91 | 0 / 0 / 91 | 482 | 482 |
| Q4 (fastapi) | 29 | 14 / 1 / 15 | 26 / 3 / 3 | 26 / 3 / 3 | 1,557 | 2,696 |
| Q5 (fastapi) | 99 | 63 / 0 / 36 | 98 / 0 / 1 | 98 / 0 / 1 | 5,446 | 8,338 |

Exact list: default (test files left out) 0 of 5; with `--include-tests`, as in its README 1 of 5; its file-level IMPORTS edges, two steps back (`ontoindex cypher`) 1 of 5.

The 100 questions of `benchmarks/impact` (`ontoindex impact File:<file> --depth 2` with `--include-tests --repo <repo> --read-mode committed`, scored as there): complete and nothing extra in 20 of 100 (95 % interval 13 to 29 %), a required file missing in 78, median answer 602 tokens; Wingbox 100 of 100, median 166 tokens. Every row is in `benchmarks/impact/results/command.ontoindex.json`.

| Repository | Questions | OntoIndex: complete | OntoIndex: a required file missing | OntoIndex: median answer, tokens | OntoIndex: context ratio | Wingbox: complete | Wingbox: median answer, tokens | Wingbox: context ratio |
|---|---|---|---|---|---|---|---|---|
| chrome-devtools-mcp | 20 | 14 | 5 | 1,852 | 114x | 20 | 306 | 537x |
| immich-server | 20 | 0 | 20 | 586 | 922x | 20 | 232 | 372x |
| agent-browser | 20 | 1 | 19 | 582 | 1,191x | 20 | 172 | 4,233x |
| claudes-c-compiler | 20 | 0 | 20 | 606 | 968x | 20 | 123 | 2,308x |
| fastapi | 20 | 5 | 14 | 690 | 529x | 20 | 86 | 1,449x |

Context ratio: Graphify's corpus estimate of the repository divided by the mean answer, as in `benchmarks/impact` (`tools/context_ratio.py`); a short answer that misses files gives a high ratio, so read it next to the complete answers.

Test D, the agent of test D with the `ontoindex` command (`tools/oi_agent_test.py`: same prompt, model alias, flags, budget, 15 trials, run of 2026-11-17; the other rows are the committed test D):

| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | Seconds | USD per trial | Refused shell commands |
|---|---|---|---|---|---|---|---|---|
| Plain search | 15 | 0.894 | 0.508 | 59,367 | 5.2 | 25.2 | 0.075 | 1 |
| Wingbox | 15 | 0.895 | 0.508 | 23,768 | 2.3 | 9.9 | 0.021 | 0 |
| Graphify | 15 | 0.892 | 0.508 | 109,071 | 7.8 | 34.9 | 0.095 | 12 |
| CodeGraph | 15 | 0.888 | 0.508 | 81,151 | 6.2 | 27.0 | 0.070 | 11 |
| OntoIndex | 15 | 0.873 | 0.496 | 78,070 | 6.0 | 28.3 | 0.075 | 10 |

Every trial is in `results/agent_summary.ontoindex.json`. Seconds include starting the container on every `ontoindex` call.

## Scope

- Code only. Graphify also reads documents, papers and images with a model; that part is not tested here and Wingbox has no counterpart.
- CodeGraph also answers questions about symbols and call flows (`codegraph explore`, `callers`, `impact <symbol>`); these tests ask about files only.
- One kind of question in tests C and D: which files depend on this file. Five questions, three languages (TypeScript, Rust, Python), one agent, one model.
- Tests A, B and C are deterministic and need no model. Test D depends on the model and varies from run to run.

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | 0.5.1 (PyPI `wingbox`) |
| Graphify | 0.9.110 (PyPI `graphifyy`); repository read at commit 18cd78f |
| CodeGraph | 1.6.2 (npm `@colbymchenry/codegraph`) |
| grimp | 3.20 |
| nanoGPT | 3adf61e |
| minGPT | 37baab7 |
| micrograd | 7bc720e |
| fastapi | 2a1bd23 |
| chrome-devtools-mcp | 8ba2ac9 |
| agent-browser | 5338477 |
| claudes-c-compiler | 6f1b99a |

## Reproduce

```sh
git clone https://github.com/aileron-tech/wingbox && cd wingbox
benchmarks/graphify/run.sh ~/wingbox-bench    # tests A, B, C; needs git, curl, uv and npm; no model, no key
python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench run      # test D: paid Claude Code sessions
python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench report
python3 -I benchmarks/graphify/tools/time_builds.py ~/wingbox-bench             # test E: build time on this machine
python3 -I benchmarks/graphify/tools/time_builds_same_machine.py ~/wingbox-bench # test F: the three tools, cold and after an edit
```

`run.sh` installs Wingbox and Graphify from PyPI into their own environments and CodeGraph from npm into its own folder, fetches the seven repositories at the commits above, builds the three graphs, computes the reference lists and writes `out/REPORT.md` in the work directory. CodeGraph runs with a private HOME, no daemon and no file watcher. `WINGBOX_SPEC=<path or requirement>` runs the same tests on another Wingbox.

| Folder | Content |
|---|---|
| `tools/` | every script of the run; the references use Python's `ast`, grimp and two own resolvers, never Wingbox, Graphify or CodeGraph |
| `gold/` | the five reference lists |
| `results/` | the result files of the run this report is written from |

