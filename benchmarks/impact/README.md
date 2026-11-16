# Wingbox benchmark: impact questions on public code

Wingbox 0.2.2 from PyPI, run of 2026-11-16. This file is written by `make_report.py` from the files in `results/`. `Dockerfile.benchmark` at the root of the repository runs the tests that need no model in a clean container and compares every result file with the committed one.

## Summary

- 100 questions drawn at random with a fixed seed in 5 public repositories (TypeScript, Rust, Python): `wingbox impact` names the complete list of dependent files, with nothing outside it, in 100 of 100 (95 % interval 96 to 100 %). A required file is missing in 0 of 100.
- A coding agent on 55 of these questions, with Wingbox against its built-in search alone: tokens -35 % (-47 % to -20 %), cost -51 % (-58 % to -43 %); complete answers 55 of 55 against 48 of 55.
- Every module dependency of the graph against the import statements of the code, in 7 corpora (3,795 dependencies in the largest): 0 wrong, 1 missing.
- Building the graph, the report and the map of fastapi (1,227 source files) from a fresh checkout takes 3.2 s on a hosted runner.

## The question

For a file X: every file that depends on X directly or through one other file, where "A depends on B" means that A imports B. It is what a developer or an agent asks before changing X.

Reference lists come from resolvers that share no code with Wingbox, all in `tools/`: Python's `ast` (`py_edges.py`; on FastAPI its pairs equal the import graph of grimp, `results/fastapi.grimp_check.json`), an own TypeScript resolver (`ts_resolve.py`) and an own Rust resolver (`rust_resolve.py`).

Three kinds of file are accepted when an answer names them and are not required: in Python a package `__init__.py` executed on the way to the imported module; in TypeScript a file that imports the compiled output of X, which `tsconfig.json` maps back to X through `outDir` and `rootDir`; in Rust a file whose path passes through the module of X to reach a deeper module (`crate::x::y`). In each case a change of X can reach the file, and a reader of the import statements may or may not count it.

Sampling: per repository, 20 files drawn without replacement (seed 20261116) from the source files that at least one other file imports. Nothing else is filtered. The reference lists hold 1 to 503 files, median 9.

## Test 1. The impact command, 100 questions

`wingbox impact <file> 2 --all`, no agent. "Complete and nothing extra": every required file is named and no named file is outside the accepted list.

| Repository | Language | Questions | Complete and nothing extra | With a missing file | Median answer, tokens |
|---|---|---|---|---|---|
| chrome-devtools-mcp | typescript | 20 | 20 | 0 | 306 |
| immich-server | typescript | 20 | 20 | 0 | 232 |
| agent-browser | rust | 20 | 20 | 0 | 159 |
| claudes-c-compiler | rust | 20 | 20 | 0 | 100 |
| fastapi | python | 20 | 20 | 0 | 86 |
| All | | 100 | 100 (96 to 100 %) | 0 | 166 |

The interval is a 95 % Wilson interval. Mean recall 1.000, mean precision 1.000. In 90 of the 100 answers the list equals the required list file for file; in the other 10 Wingbox also names accepted files (19 in all). Tokens are characters of the output divided by four; the largest answer is 19,462 tokens for a list of 503 files.

## Test 2. A coding agent with and without Wingbox, 55 questions

Claude Code, headless, model alias `sonnet`, no user settings, skills or connectors. Two setups: built-in search only (Read, Grep, Glob), and the same with the `wingbox` command added; the graph is built beforehand and the prompt names `wingbox impact <file> 2` in one sentence. One trial per question and setup. The agent subset is drawn at random among the questions whose required list holds 3 to 120 files (up to 12 per repository). Tokens: everything the model read and wrote, cache included.

| Setup | Trials | Complete answers (95 % interval) | F1, mean (95 % interval) | Tokens, mean (95 % interval) | USD per answer | Turns |
|---|---|---|---|---|---|---|
| With Wingbox | 55 | 55 (93 to 100 %) | 1.000 (1.000 to 1.000) | 32,183 (28,229 to 36,523) | 0.031 | 3.0 |
| Built-in search only | 55 | 48 (76 to 94 %) | 0.979 (0.955 to 0.996) | 49,292 (42,863 to 56,703) | 0.063 | 5.0 |

- Tokens with Wingbox against search alone: -35 % (-47 % to -20 %); cost -51 % (-58 % to -43 %). Fewer tokens on 39 of the 55 questions, more on 16.
- Complete answer only with Wingbox on 7 questions, only without it on 0; exact sign test on these questions: p = 0.016.

| Repository | Questions | Tokens with Wingbox, mean | Tokens with search alone, mean | Change |
|---|---|---|---|---|
| chrome-devtools-mcp | 12 | 39,146 | 42,406 | -8 % |
| immich-server | 12 | 35,867 | 31,950 | +12 % |
| agent-browser | 12 | 35,158 | 78,529 | -55 % |
| claudes-c-compiler | 12 | 24,270 | 50,428 | -52 % |
| fastapi | 7 | 22,396 | 38,753 | -42 % |

Ratios are totals over the questions; their intervals are 95 % paired bootstrap intervals over the questions (10,000 resamples). The 110 trials cost 5.15 USD in total and ran with Wingbox 0.2.1 (the 100 answers of `wingbox impact` are identical in 0.2.1 and 0.2.2); every trial with its scores is in `results/agent.json`.

## Test 3. Are the edges in the code?

Every module dependency of the Wingbox graph against every import statement of the code. Wrong: an edge the reference neither requires nor accepts. Missing: a required edge the graph does not have.

| Corpus | Language | Dependencies in the code | Edges in the graph | Wrong | Missing |
|---|---|---|---|---|---|
| immich, server | TypeScript | 3,795 | 3,795 | 0 | 0 |
| chrome-devtools-mcp | TypeScript | 901 | 919 | 0 | 1 |
| agent-browser, Rust sources | Rust | 391 | 391 | 0 | 0 |
| claudes-c-compiler | Rust | 1,462 | 1,462 | 0 | 0 |
| FastAPI, whole tree | Python | 1,801 | 1,818 | 0 | 0 |
| FastAPI, the fastapi/ package | Python | 124 | 130 | 0 | 0 |
| nanoGPT, minGPT, micrograd | Python | 15 | 15 | 0 | 0 |

- chrome-devtools-mcp, missing: `src/third_party/index.ts` -> `src/third_party/lighthouse-devtools-mcp-bundle.d.ts`

The graph holds more edges than the code requires where it also records accepted ones (see "The question").

## Test 4. Building the graph

Seconds from a fresh checkout to the graph, the report and the map: every run starts from a new export of the repository, with no earlier output and no cache. Median of 3 runs on a GitHub-hosted runner (Linux, AMD EPYC 9V74 80-Core Processor, 4 logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`, wingbox 0.2.2.

| Corpus | Source files | Build, s | Modules | Import edges |
|---|---|---|---|---|
| karpathy | 29 | 0.28 | 29 | 15 |
| chrome-devtools-mcp | 296 | 0.79 | 293 | 919 |
| agent-browser | 363 | 2.06 | 359 | 751 |
| claudes-c-compiler | 351 | 2.23 | 351 | 1,462 |
| immich-server | 707 | 1.84 | 707 | 3,795 |
| fastapi | 1,227 | 3.23 | 1,227 | 1,818 |

The size of the graph is read from the file each build wrote; the summary stops when two runs differ.

## Scope

- One kind of question: which files import this file, directly or through one other file. Other questions are not measured here.
- Questions are drawn inside five repositories, so they are not independent in the strict sense; the per-repository rows show where a result comes from.
- Tests 1 and 3 are deterministic and need no model. Test 2 uses one agent and one model, one trial per question; a second run will differ in the details.

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | 0.2.2 (PyPI `wingbox`) |
| grimp | 3.20 |
| nanoGPT | 3adf61e |
| minGPT | 37baab7 |
| micrograd | 7bc720e |
| fastapi | 2a1bd23 |
| chrome-devtools-mcp | 8ba2ac9 |
| agent-browser | 5338477 |
| claudes-c-compiler | 6f1b99a |
| immich | e5d6eb7 |

## Reproduce

In a clean container (the repositories are fetched at the commits above; no model, no key):

```sh
git clone https://github.com/aileron-tech/wingbox && cd wingbox
docker build -f Dockerfile.benchmark -t wingbox-benchmark .
docker run --rm wingbox-benchmark        # ends with REPRODUCED, or lists the result files that differ
```

`results/docker_run.log` is the output of that run for the committed results. Without a container:

```sh
benchmarks/impact/run.sh ~/wingbox-bench                                    # tests 1 and 3; needs git and uv
python3 -I benchmarks/impact/impact100.py ~/wingbox-bench agent             # test 2: paid Claude Code sessions
python3 -I benchmarks/impact/tools/time_builds.py ~/wingbox-bench           # test 4 on this machine
```

| Path | Content |
|---|---|
| `run.sh`, `impact100.py`, `edge_test.py`, `verify.py`, `stats.py` | the run, the sampling and the tests, the comparison with the committed results, the intervals |
| `tools/` | the reference resolvers and the build timing; none of them uses Wingbox code |
| `results/` | the questions, the reference lists (`gold/`), every row of the command test, every trial of the agent test, the edge test, the build times |

