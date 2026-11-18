# Wingbox benchmark: impact questions on public code

Wingbox 0.10.0 from PyPI, run of 2026-11-18. This file is written by `make_report.py` from the files in `results/`. `Dockerfile.benchmark` at the root of the repository runs the tests that need no model in a clean container and compares every result file with the committed one.

## Summary

- 100 questions drawn at random with a fixed seed in 5 public repositories (TypeScript, Rust, Python): `wingbox impact` names the complete list of dependent files, with nothing outside it, in 100 of 100 (95 % interval 96 to 100 %). A required file is missing in 0 of 100.
- A coding agent on 55 of these questions, with Wingbox against its built-in search alone: tokens -32 % (-47 % to -12 %), cost -38 % (-49 % to -25 %); complete answers 55 of 55 against 48 of 55.
- Every module dependency of the graph against the import statements of the code, in 7 corpora (3,795 dependencies in the largest): 0 wrong, 1 missing.
- Building the graph, the report and the map of fastapi (1,227 source files) from a fresh checkout takes 2.0 s on a hosted runner.

## The question

For a file X: every file that depends on X directly or through one other file, where "A depends on B" means that A imports B. It is what a developer or an agent asks before changing X.

Reference lists come from resolvers that share no code with Wingbox, all in `tools/`: Python's `ast` (`py_edges.py`; on FastAPI the pairs of its import statements equal the import graph of grimp, `results/fastapi.grimp_check.json`; it also counts a module loaded by a literal name, `importlib.import_module("a.b")` or `__import__("a.b")`, which grimp does not read: 1 pair on FastAPI), an own TypeScript resolver (`ts_resolve.py`) and an own Rust resolver (`rust_resolve.py`). Since Wingbox 0.10.0 (2026-11-18) the Rust resolver also counts, for a path that names an item, the file that defines the item (followed through `pub use` re-exports), the items a module names after a glob import (`use x::*`), found by scanning the module text, and a member named through a type (`Type::member`, `Self::member`), which leads to the file whose inherent `impl` block defines it. A file that uses an item depends on the file that defines it; the earlier reference stopped at the module facade that re-exports the item. The questions were drawn before this change and stay the same.

Check of the Rust reference: `tools/rust_compiler_check.py` reads what rust-analyzer resolves every name of the source to (`results/<repo>.rust_compiler_check.json`). agent-browser: 453 of 456 required pairs confirmed, 0 contradicted, 3 in a file the compiler did not read; claudes-c-compiler: 2,084 of 2,084 required pairs confirmed, 0 contradicted; it also names 18 pairs the reference accepts and does not require (`use crate::<exported macro>`, to the crate root file).

Three kinds of file are accepted when an answer names them and are not required: in Python a package `__init__.py` executed on the way to the imported module; in TypeScript a file that imports the compiled output of X, which `tsconfig.json` maps back to X through `outDir` and `rootDir`; in Rust a file whose path passes through the module of X to reach a deeper module (`crate::x::y`). In each case a change of X can reach the file, and a reader of the import statements may or may not count it.

Sampling: per repository, 20 files drawn without replacement (seed 20261116) from the source files that at least one other file imports. Nothing else is filtered. The reference lists hold 1 to 503 files, median 11.

## Test 1. The impact command, 100 questions

`wingbox impact <file> 2 --all`, no agent. "Complete and nothing extra": every required file is named and no named file is outside the accepted list.

| Repository | Language | Questions | Complete and nothing extra | With a missing file | Median answer, tokens |
|---|---|---|---|---|---|
| chrome-devtools-mcp | typescript | 20 | 20 | 0 | 417 |
| immich-server | typescript | 20 | 20 | 0 | 333 |
| agent-browser | rust | 20 | 20 | 0 | 360 |
| claudes-c-compiler | rust | 20 | 20 | 0 | 180 |
| fastapi | python | 20 | 20 | 0 | 93 |
| All | | 100 | 100 (96 to 100 %) | 0 | 330 |

The interval is a 95 % Wilson interval. Mean recall 1.000, mean precision 1.000. In 90 of the 100 answers the list equals the required list file for file; in the other 10 Wingbox also names accepted files (19 in all). Tokens are characters of the output divided by four; the largest answer is 19,896 tokens for a list of 503 files.

## Test 2. A coding agent with and without Wingbox, 55 questions

Claude Code, headless, model `claude-opus-5-5`, no user settings, skills or connectors. Two setups: built-in search only (Read, Grep, Glob), and the same with the `wingbox` command added; the graph is built beforehand and the prompt names `wingbox impact <file> 2` in one sentence. One trial per question and setup. The agent subset is drawn at random among the questions whose required list holds 3 to 120 files (up to 12 per repository). Tokens: everything the model read and wrote, cache included.

The question states, for each language, what the reference counts (since 2026-11-18). Before, its Rust clause named only a `mod` declaration, a `use` or a path of the module; on that text the agents left out files that use an item through a re-export or a glob import, which the reference counts. Both setups ran on 2026-11-18. The search-alone setup ran before the reference followed members of a type, and its answers are scored on the current lists; the Wingbox setup ran again with the release build of Wingbox 0.10.0, which follows them too and whose `impact` output says why each file is on the list.

| Setup | Trials | Complete answers (95 % interval) | F1, mean (95 % interval) | Tokens, mean (95 % interval) | USD per answer | Turns |
|---|---|---|---|---|---|---|
| With Wingbox | 55 | 55 (93 to 100 %) | 1.000 (1.000 to 1.000) | 67,579 (57,562 to 79,037) | 0.110 | 6.0 |
| Built-in search only | 55 | 48 (76 to 94 %) | 0.993 (0.986 to 0.998) | 98,800 (74,688 to 128,062) | 0.179 | 7.9 |

- Tokens with Wingbox against search alone: -32 % (-47 % to -12 %); cost -38 % (-49 % to -25 %). Fewer tokens on 30 of the 55 questions, more on 25.
- Complete answer only with Wingbox on 7 questions, only without it on 0; exact sign test on these questions: p = 0.016.

| Repository | Questions | Tokens with Wingbox, mean | Tokens with search alone, mean | Change |
|---|---|---|---|---|
| chrome-devtools-mcp | 12 | 50,303 | 44,460 | +13 % |
| immich-server | 12 | 47,090 | 37,334 | +26 % |
| agent-browser | 12 | 63,828 | 193,995 | -67 % |
| claudes-c-compiler | 12 | 95,197 | 139,768 | -32 % |
| fastapi | 7 | 91,406 | 63,905 | +43 % |

Ratios are totals over the questions; their intervals are 95 % paired bootstrap intervals over the questions (10,000 resamples). The 110 trials cost 15.89 USD in total and ran with Wingbox 0.10.0; every trial with its scores is in `results/agent.json`.

## Test 3. Are the edges in the code?

Every module dependency of the Wingbox graph against every import statement of the code. Wrong: an edge the reference neither requires nor accepts. Missing: a required edge the graph does not have.

| Corpus | Language | Dependencies in the code | Edges in the graph | Wrong | Missing |
|---|---|---|---|---|---|
| immich, server | TypeScript | 3,795 | 3,795 | 0 | 0 |
| chrome-devtools-mcp | TypeScript | 901 | 919 | 0 | 1 |
| agent-browser, Rust sources | Rust | 456 | 456 | 0 | 0 |
| claudes-c-compiler | Rust | 2,084 | 2,084 | 0 | 0 |
| FastAPI, whole tree | Python | 1,802 | 1,819 | 0 | 0 |
| FastAPI, the fastapi/ package | Python | 124 | 130 | 0 | 0 |
| nanoGPT, minGPT, micrograd | Python | 15 | 15 | 0 | 0 |

- chrome-devtools-mcp, missing: `src/third_party/index.ts` -> `src/third_party/lighthouse-devtools-mcp-bundle.d.ts`

The graph holds more edges than the code requires where it also records accepted ones (see "The question").

## Test 4. Building the graph

Seconds from a fresh checkout to the graph, the report and the map: every run starts from a new export of the repository, with no earlier output and no cache. Median of 3 runs on a GitHub-hosted runner (Linux, AMD EPYC 9V45 96-Core Processor, 4 logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`, wingbox 0.2.3.

| Corpus | Source files | Build, s | Modules | Import edges |
|---|---|---|---|---|
| karpathy | 29 | 0.20 | 29 | 15 |
| chrome-devtools-mcp | 296 | 0.58 | 293 | 919 |
| agent-browser | 363 | 1.24 | 359 | 751 |
| claudes-c-compiler | 351 | 1.23 | 351 | 1,462 |
| immich-server | 707 | 1.34 | 707 | 3,795 |
| fastapi | 1,227 | 2.05 | 1,227 | 1,818 |

The size of the graph is read from the file each build wrote; the summary stops when two runs differ.

## Scope

- One kind of question: which files import this file, directly or through one other file. Other questions are not measured here.
- Questions are drawn inside five repositories, so they are not independent in the strict sense; the per-repository rows show where a result comes from.
- Tests 1 and 3 are deterministic and need no model. Test 2 uses one agent and one model, one trial per question; a second run will differ in the details.

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | 0.10.0 (PyPI `wingbox`) |
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


## Context size (the `graphify benchmark` measure)

`graphify benchmark` reports "Nx fewer tokens per query": its estimate of the corpus tokens divided by the average size of its answers. `tools/context_ratio.py` divides the same corpus estimate, taken from `graphify benchmark` 0.9.110 on the same commits (`results/graphify_benchmark.json`, code-only graphs), by the average size of the Wingbox impact answers above. Results in `results/context_ratio.json`:

| Repository | Corpus (Graphify's estimate) | Wingbox | Graphify | Wingbox answers complete |
|---|---|---|---|---|
| agent-browser | 701,066 | 1,813x | 391.9x | 20 of 20 |
| fastapi | 2,046,400 | 1,372x | 1,399.7x | 20 of 20 |
| claudes-c-compiler | 627,533 | 1,019x | 402.3x | 20 of 20 |
| chrome-devtools-mcp | 320,266 | 420x | 134.6x | 20 of 20 |
| immich-server | 539,466 | 251x | 415.0x | 20 of 20 |

The two tools answer different questions: Graphify five fixed questions per repository, not graded; Wingbox the impact of 20 files per repository, each answer checked against the import statements. So this compares answer size on one corpus count, not answer quality. The script also keeps an independent corpus estimate (characters / 4 over the source files, without vendored bundles and minified files) for reference.

    python3 tools/context_ratio.py <folder with the checkouts> results/graphify_benchmark.json
