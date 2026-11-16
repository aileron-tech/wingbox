# Wingbox and Graphify on the same public tests

Run of 2026-11-16. Wingbox 0.2.0 from PyPI. Graphify 0.9.110 from PyPI (`graphifyy`), `GRAPHIFY_BACKEND=none` (no model, no key). Every number below is produced by the scripts in `tools/` from the files in `results/`; this file is written by `tools/make_report.py`, and `run.sh` reproduces tests A, B and C from nothing.

## Summary

- Graphify's "71.5x fewer tokens per query" is the size of the whole corpus divided by the size of one graph answer. No answer is checked. On the code of its own example corpus Graphify publishes 8.8x; Graphify 0.9.110 gives 3.5x and 5.0x today, and the same formula gives Wingbox 78.1x (test A).
- Asked which files depend on a file, `wingbox impact` names the exact list in 5 of 5 questions, `graphify affected` in 1 of 5 (test C).
- A coding agent answers the same questions correctly in all three setups. It spends 25,598 tokens with Wingbox, 59,367 with its built-in search alone and 109,071 with Graphify: Wingbox -57 % against plain search, Graphify +84 % (test D).
- Building the graph of fastapi (1,227 source files) takes Wingbox 2.2 s and Graphify 17.9 s on a hosted runner; over the five corpora Graphify's extract takes 3.5 to 19.2 times as long as the whole Wingbox build (test E).

## What Graphify publishes in its repository

Read at Graphify-Labs/graphify, branch v8, commit 18cd78f (2026-11-15).

| Published number | Where it comes from | Reproducible from the repository |
|---|---|---|
| 71.5x fewer tokens per query; 8.8x on code only | `graphify benchmark` (`graphify/benchmark.py`) on `worked/karpathy-repos`: nanoGPT, minGPT, micrograd, 5 papers, 4 images | yes: formula, corpus list and five questions are published |
| 12 of 12 inferred cross-file edges correct | manual audit under `fastapi/` of the FastAPI repository (BENCHMARKS.md) | yes: the repository is public |
| ERPNext 82.0 % against 70.8 %; LOCOMO; LongMemEval | BENCHMARKS.md names `crosstool/run.py` and `memory/runner.py` | no: neither folder, nor the questions, nor the gold facts are in the repository |

The formula of `graphify benchmark`: corpus tokens = words x 100 / 75 (every file in the context); answer tokens = characters / 4; reduction = corpus tokens / mean answer tokens over the questions that matched something in the graph.

## Test A. Graphify's token formula, Graphify's corpus

Corpus: the code of karpathy/nanoGPT, minGPT and micrograd: 29 Python files, 12,748 words, 16,997 tokens. Graphify's review states ~16,997 tokens for the same 29 files.

| Tool and query set | Queries answered | Mean answer, tokens | Reduction |
|---|---|---|---|
| Graphify, published in `worked/karpathy-repos/review.md` (code only) | not stated | ~1,929 | 8.8x |
| Graphify 0.9.110, its five default questions, corpus counted by its own word counter | 1 of 5 | 4,909 | 3.5x |
| Graphify 0.9.110, the five questions of its review | 2 of 5 | 3,413 | 5.0x |
| Wingbox `analyze`, every module of the corpus | 29 of 29 | 218 | 78.1x |
| Wingbox `impact <file> 2`, every file of the corpus | 29 of 29 | 66 | 259.2x |
| Wingbox, the five questions of Graphify's review, one `quick <keyword>` per keyword | 2 of 5 | 424 | 40.1x |

The literal command `graphify benchmark`, run in the corpus folder after `graphify extract . --code-only`, estimates the corpus from the node count and prints 3.3x (`results/graphify_benchmark_literal.txt`).

Wingbox takes a module or a keyword, not a sentence, so its main row is every module of the corpus instead of five sentences. Largest single `analyze` answer: 617 tokens (27.5x). The whole Wingbox graph file is 1,834 tokens.

Graphify's published graph of the full corpus (`worked/karpathy-repos/graph.json`) holds 177 nodes and 246 links; its review states 285 and 340. Graphify 0.9.110 run on that file with the published 92,616 words and five questions gives 45.0x with 2 of 5 questions matched (published: 71.5x). Papers and images are outside Wingbox, so the full-corpus row has no Wingbox counterpart.

Same formula on FastAPI (1,223 Python files, 321,676 words, 428,901 tokens):

| Tool and query set | Queries answered | Mean answer, tokens | Largest answer, tokens | Reduction |
|---|---|---|---|---|
| Graphify 0.9.110, five default questions | 5 of 5 | 1,369 | 1,634 | 313.3x |
| Wingbox `analyze`, every module | 1,223 of 1,223 | 624 | 55,131 | 687.5x |
| Wingbox `impact <file> 2`, every file | 1,223 of 1,223 | 261 | 27,622 | 1645.1x |
| Wingbox, Graphify's five default questions by keyword | 5 of 5 | 1,078 | | 397.9x |

## Test B. Are the edges in the code?

The formula above does not check a single answer. This test does: every file-to-file dependency edge of each tool against the import statements of the code.

Reference: `tools/py_edges.py` reads every `import` with Python's own parser (`ast`) and resolves it to a file. Check of the reference itself: the import graph built by grimp 3.20 (the engine of import-linter) on FastAPI gives 1,801 file pairs; 1,801 of them are the reference's 1,801 pairs; 0 only in grimp, 0 only in the reference.

| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |
|---|---|---|---|---|---|---|---|
| Karpathy repositories | 15 | Wingbox | 15 | 0 | 0 | 1.000 | 1.000 |
| Karpathy repositories | 15 | Graphify, module-level imports | 15 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, whole tree | 1,801 | Wingbox | 1,818 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, whole tree | 1,801 | Graphify, module-level imports | 1,812 | 22 | 32 | 0.988 | 0.982 |
| FastAPI, `fastapi/` package | 124 | Wingbox | 130 | 0 | 0 | 1.000 | 1.000 |
| FastAPI, `fastapi/` package | 124 | Graphify, module-level imports | 122 | 0 | 7 | 1.000 | 0.944 |

An edge to a package `__init__.py` that Python executes on the way to the named module counts as right and is not required. Graphify also links an import to the file that defines the imported name; with those links accepted its FastAPI figures are precision 0.994 and recall 0.991 (19 wrong, 16 missing).

## Test C. The impact command without an agent

Question: every file that depends on file X directly or through one other file. Five files in four public repositories. `wingbox impact <file> 2` and `graphify affected "<file>" --depth 2` are run as they are and the files they name are compared with the reference list.

Reference lists (`gold/`), none taken from either tool: Q1 from an own TypeScript resolver (`tools/ts_resolve.py`); Q2 and Q3 from an own Rust resolver (`tools/rust_resolve.py`), every file of the two lists read against the source; Q4 and Q5 from `tools/py_edges.py`, equal to grimp. `run.sh` computes the five lists again and stops when one differs from the committed list.

| Question | Reference files | Wingbox: right / wrong / missing | Wingbox output, tokens | Graphify: right / wrong / missing | Graphify output, tokens |
|---|---|---|---|---|---|
| Q1 `src/collectors/PageCollector.ts` (chrome-devtools-mcp) | 30 | 30 / 0 / 0 | 585 | 30 / 0 / 0 | 1,324 |
| Q2 `cli/src/validation.rs` (agent-browser) | 30 | 30 / 0 / 0 | 440 | 17 / 2 / 13 | 2,550 |
| Q3 `src/backend/elf/constants.rs` (claudes-c-compiler) | 31 | 31 / 0 / 0 | 498 | 21 / 9 / 10 | 625 |
| Q4 `fastapi/openapi/models.py` (fastapi) | 29 | 29 / 0 / 0 | 614 | 29 / 366 / 0 | 32,188 |
| Q5 `fastapi/sse.py` (fastapi) | 99 | 99 / 0 / 0 | 1,997 | 98 / 0 / 1 | 2,791 |

Exact list: Wingbox 5 of 5, Graphify 1 of 5. `graphify affected` works on functions and methods and follows calls and references as well as imports; restricted to its import relations (`--relation imports_from --relation imports --relation re_exports --relation dynamic_import`) it gives: Q1 28 / 0 / 2; Q2 5 / 2 / 25; Q3 21 / 9 / 10; Q4 28 / 3 / 1; Q5 98 / 0 / 1.

## Test D. A coding agent answers the same five questions

Claude Code, headless, model alias `sonnet` (claude-sonnet-5-5), no user settings, skills or connectors. Three setups: built-in search only; search plus the `wingbox` command; search plus the `graphify` command. Graphs are built beforehand. The prompt names the tool's impact command in one sentence of the same shape for both tools (`tools/agent_test.py`). 3 repeats per question and setup, four trials at a time, setups interleaved. Score: F1 of the answer list against the reference list. Tokens: everything the model read and wrote, cache included. Seconds: wall clock of one trial while four run.

| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | Seconds | USD per trial | Refused shell commands |
|---|---|---|---|---|---|---|---|---|
| Plain search (Read, Grep, Glob) | 15 | 0.999 | 0.984 | 59,367 | 5.2 | 25.2 | 0.075 | 1 |
| Wingbox | 15 | 1.000 | 1.000 | 25,598 | 2.5 | 11.2 | 0.016 | 3 |
| Graphify | 15 | 0.997 | 0.975 | 109,071 | 7.8 | 34.9 | 0.095 | 12 |

- Wingbox against Graphify: tokens -77 %, time -68 %, cost -83 %.
- Wingbox against plain search: tokens -57 %, time -56 %, cost -79 %.
- Graphify against plain search: tokens +84 %, time +39 %, cost +27 %.

| Question | Plain: F1 / tokens / s | Wingbox: F1 / tokens / s | Graphify: F1 / tokens / s |
|---|---|---|---|
| Q1 (chrome-devtools-mcp, 30 files) | 0.995 / 39,364 / 16.8 | 1.000 / 26,416 / 11.4 | 1.000 / 83,439 / 26.1 |
| Q2 (agent-browser, 30 files) | 1.000 / 69,193 / 25.5 | 1.000 / 24,274 / 9.8 | 1.000 / 158,897 / 40.4 |
| Q3 (claudes-c-compiler, 31 files) | 1.000 / 63,529 / 26.2 | 1.000 / 19,832 / 10.5 | 1.000 / 81,091 / 33.0 |
| Q4 (fastapi, 29 files) | 1.000 / 57,892 / 26.4 | 1.000 / 19,181 / 8.1 | 1.000 / 65,159 / 25.8 |
| Q5 (fastapi, 99 files) | 1.000 / 66,858 / 31.1 | 1.000 / 38,287 / 16.0 | 0.983 / 156,768 / 49.1 |

Every trial with its answer list is in `results/agent_summary.json`. The 45 trials cost 2.79 USD in total.

## Test E. Building the graph

Seconds from a fresh checkout to a graph: every run starts from a new export of the repository, with no earlier output and no cache, and the two tools alternate. Median of 3 runs on a GitHub-hosted runner (Linux, AMD EPYC 9V45 96-Core Processor, 4 logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`. wingbox 0.2.0; graphify 0.9.110 with `GRAPHIFY_BACKEND=none`.

`wingbox build .` writes the graph, the report and the map. `graphify extract . --code-only` writes the graph; `graphify cluster-only . --no-label --no-viz` is the step this benchmark runs before `graphify affected`.

| Corpus | Source files | Wingbox build, s | Graphify extract, s | Graphify cluster, s | Extract / build | Wingbox graph | Graphify graph |
|---|---|---|---|---|---|---|---|
| karpathy | 29 | 0.23 | 0.80 | 0.30 | 3.5x | 29 modules, 15 import edges | 241 nodes, 363 links |
| chrome-devtools-mcp | 296 | 0.59 | 11.34 | 2.18 | 19.2x | 293 modules, 918 import edges | 4,804 nodes, 11,240 links |
| agent-browser | 363 | 1.25 | 16.68 | 5.31 | 13.3x | 359 modules, 751 import edges | 10,516 nodes, 27,103 links |
| claudes-c-compiler | 351 | 1.27 | 7.56 | 5.24 | 6.0x | 351 modules, 1,462 import edges | 9,413 nodes, 28,384 links |
| fastapi | 1,227 | 2.17 | 17.85 | 6.37 | 8.2x | 1,227 modules, 1,818 import edges | 12,897 nodes, 26,044 links |

The size of the graph is read from the file each build wrote, in every run; the summary stops when two runs of one tool differ. CPU seconds per step and every single run are in `results/build_times.json`.

## Scope

- Code only. Graphify also reads documents, papers and images with a model; that part is not tested here and Wingbox has no counterpart.
- One kind of question in tests C and D: which files depend on this file. Five questions, three languages (TypeScript, Rust, Python), one agent, one model.
- Tests A, B and C are deterministic and need no model. Test D depends on the model and varies from run to run.

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | 0.2.0 (PyPI `wingbox`) |
| Graphify | 0.9.110 (PyPI `graphifyy`); repository read at commit 18cd78f |
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
benchmarks/graphify/run.sh ~/wingbox-bench    # tests A, B, C; needs git, curl and uv; no model, no key
python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench run      # test D: paid Claude Code sessions
python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench report
python3 -I benchmarks/graphify/tools/time_builds.py ~/wingbox-bench             # test E: build time on this machine
```

`run.sh` installs both tools from PyPI into its own environments, fetches the seven repositories at the commits above, builds both graphs, computes the reference lists and writes `out/REPORT.md` in the work directory. `WINGBOX_SPEC=<path or requirement>` runs the same tests on another Wingbox.

| Folder | Content |
|---|---|
| `tools/` | every script of the run; the references use Python's `ast`, grimp and two own resolvers, never Wingbox or Graphify |
| `gold/` | the five reference lists |
| `results/` | the result files of the run this report is written from |

