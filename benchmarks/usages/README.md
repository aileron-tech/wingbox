# Find usages: Wingbox, CodeGraph and Graphify on 100 questions

Run of 2026-11-17. Wingbox 0.5.1 from PyPI, CodeGraph 1.6.2 from npm (`@colbymchenry/codegraph`), Graphify 0.9.110 from PyPI (`graphifyy`, no model backend). Every number below is computed by `make_report.py` from the files in `results/`, and `run.sh` reproduces them from nothing.

The question of a rename or a signature change: "I will change exported symbol S of file X. Where is S used?" 100 questions on five public repositories in three languages. Each tool is asked with its own command for the users of one symbol, and every answer is checked against the language tooling: the files against its find usages (TypeScript language service, pyright, rust-analyzer), the use sites against the SCIP index of the same engines (scip-typescript, scip-python, rust-analyzer scip).

## Which tool for which job

- **Wingbox, to change a symbol and every place that uses it.** `wingbox usages <file>::<S>` names exactly the files that use S in 100 of 100 questions (TypeScript 40 of 40, Rust 40 of 40, Python 20 of 20) and 1,660 of the 1,660 use sites of SCIP (100.0 %), with 0 sites outside that list and the column of SCIP at 1,668 of 1,668 sites. It runs on the local machine, with no model and no key; the median answer is 62 tokens.
- **CodeGraph `callers`** lists the functions and files that call, reference, import or instantiate S. It names no file outside the reference lists (0 in 100 answers) and the exact files in 79 of 100 questions (TypeScript 40 of 40, Rust 22 of 40, Python 17 of 20); 48 files are missing, 45 of them in Rust. It names 680 of the 1,660 use sites (41.0 %), because it keeps one entry per calling function and kind of use; median answer 73 tokens. Per its documentation, its main tool `codegraph explore` returns the source of the relevant symbols with the call paths between them in one call, which this benchmark does not measure.
- **Graphify** has no command that lists the uses of a symbol. The nearest, `graphify affected`, is a reverse traversal from one node. It answers 91 of 100 questions (no node for the other 9: Python module variables). At depth 1 it names the exact files in 76 of 100 questions and 604 of the 1,660 use sites; at its default depth 2, the exact files in 34 of 100, with 1,899 files outside the reference lists. Per its documentation, it maps documents, PDFs, images and videos into the same graph as the code, which this benchmark does not measure.

## Results

100 questions; 362 reference files; 1,668 SCIP use sites on 1,660 distinct lines. A file or site outside the reference is one the tool names and the language tooling does not.

| Tool | Answered | Files exact | All files found | Files recall (mean) | Files precision (mean) | Files outside | Use sites found | Sites outside | Tokens (median) | Tokens (all answers) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Wingbox `usages` | 100 | 100 | 100 | 1.000 | 1.000 | 0 | 1,660 of 1,660 | 0 | 62 | 13,608 |
| CodeGraph `callers` | 100 | 79 | 79 | 0.850 | 0.890 | 0 | 680 of 1,660 | 0 | 73 | 17,149 |
| Graphify `affected --depth 1` | 91 | 76 | 78 | 0.832 | 0.858 | 6 | 604 of 1,660 | 339 | 91 | 22,630 |
| Graphify `affected` (depth 2, its default) | 91 | 34 | 81 | 0.848 | 0.552 | 1,899 | 608 of 1,660 | 3,822 | 148 | 98,172 |

Answered: the tool found S in X and gave an answer about it. Use sites are counted by file and line, because Graphify prints no column; of the SCIP sites on the lines a tool names, Wingbox gives the same column at 1,668 of 1,668, CodeGraph at 582 of 682. Tokens: characters of the text answer / 4, the measure of the other benchmarks of this repository; every tool is asked for its whole list (`--all`, `--limit 1000000`).

### By language

| Tool | Language | Questions | Files exact | Use sites found | Sites outside | Tokens (median) |
|---|---|---:|---:|---:|---:|---:|
| Wingbox `usages` | TypeScript | 40 | 40 | 843 of 843 | 0 | 64 |
| Wingbox `usages` | Rust | 40 | 40 | 583 of 583 | 0 | 59 |
| Wingbox `usages` | Python | 20 | 20 | 234 of 234 | 0 | 62 |
| CodeGraph `callers` | TypeScript | 40 | 40 | 340 of 843 | 0 | 86 |
| CodeGraph `callers` | Rust | 40 | 22 | 208 of 583 | 0 | 50 |
| CodeGraph `callers` | Python | 20 | 17 | 132 of 234 | 0 | 80 |
| Graphify `affected --depth 1` | TypeScript | 40 | 39 | 264 of 843 | 70 | 97 |
| Graphify `affected --depth 1` | Rust | 40 | 27 | 220 of 583 | 259 | 88 |
| Graphify `affected --depth 1` | Python | 20 | 10 | 120 of 234 | 10 | 82 |
| Graphify `affected` (depth 2, its default) | TypeScript | 40 | 13 | 265 of 843 | 1,382 | 218 |
| Graphify `affected` (depth 2, its default) | Rust | 40 | 15 | 222 of 583 | 875 | 140 |
| Graphify `affected` (depth 2, its default) | Python | 20 | 6 | 121 of 234 | 1,565 | 90 |

Wingbox counts a method call whose receiver type it cannot see as an unresolved call, apart from the uses: 0 in these 100 answers.

## Why the numbers differ

The three tools answer different questions with the command that comes closest to this one; the design points below are taken from each tool's own documentation and code.

**Wingbox** is built for this question: "`wingbox usages` lists every place that uses one symbol, through re-exports and aliases, grouped by file, with the line, the column and the kind of use" (Wingbox README). Its language adapters record, per file, which names an import binds and where each bound name is used, and a resolver follows re-exports, aliases and glob imports back to the defining symbol: the same model as the language tooling the references come from. For a method, "calls whose receiver type is not in sight are listed as `unresolved-call`, never guessed" (Wingbox README).

**CodeGraph** stores "nodes (functions, classes, methods) and edges (calls, imports, extends, implements)" and resolves references after extraction ("function calls -> definitions, imports -> source files", CodeGraph README, How It Works). `codegraph callers <symbol>` is documented as "Find all functions/methods that call a specific symbol"; in 1.6.2 it follows `calls`, `references`, `imports`, `instantiates` and `navigates` edges into the symbol (`getCallers`, lib/dist/graph/traversal.js) and prints each caller once per kind of edge. A function that uses S five times is one entry with the line of one use, so it names fewer sites than there are uses. A use that its resolver does not link to the definition is not an edge, and the file is not in the answer. In this run that happens in Rust: a glob import of a module that re-exports S (`use super::elf::*` reaching `w32`), a call through a module path (`screenshot::take_screenshot(...)`), a constant read as a value, a trait named in `impl S for T`. What it links, it links to the right definition: no file in its answers is outside the reference lists.

**Graphify** builds a knowledge graph of "code, docs, PDFs, images, videos" (Graphify README); its code nodes are files, classes, functions and methods, linked by `calls`, `imports`, `inherits` and more relations. It has no find-usages command. `graphify affected "X"` is documented as "reverse traversal to find nodes impacted by X" over fifteen relations by default, depth 2. The comment above its relation list says that in Python "every other relation (calls, references, imports) targets the *file* node, not the symbol node", and that the walk "follows `defines` from the symbol back to its defining file and then continues through `imports` to consumers" (graphify/affected.py). At depth 1 an answer therefore holds the direct callers it resolved; at depth 2 it also holds every file that imports the file of S and every caller of a caller, which is an impact answer rather than a list of uses. Its walk starts from S and from the methods of S ("seed the reverse walk with the root's own member nodes", graphify/affected.py), so for a class or struct it also lists the calls of its methods (`self.fresh_value()` in an `impl Lowerer` block for `Lowerer`), which SCIP files under each method rather than under S; these calls are most of its sites outside the SCIP list at depth 1. A hit carries the line where its edge starts: the first line of a multi-line `import {` statement rather than the line of the name, and the definition line of the calling function when the edge has no position of its own ("honest fallback: the node's own def line", graphify/affected.py). A module-level variable has no node in its graph, so `affected` exits with "No unique node match" for it.

## Method

Sampling, per repository: the exported top-level symbols found by the language's own parser (TypeScript compiler API, Python `ast`, a Rust item scanner in `tools/`) are shuffled with seed 20261116; the reference tool is asked for each in that order, and the first 20 symbols with a reference in another file are the questions. This is a uniform draw among the exported symbols that another file uses.

References: the files of a question are every file other than X in which the language tooling's find usages reports S (`results/gold/<id>.json`, `files`). The use sites are every occurrence of S in another file that is not a definition, from the SCIP index (`scip.sites`, 1-based line and column); for a TypeScript class, `new S(...)` is filed under the class's constructor symbol in SCIP and counted as a site of S.

The commands, run in a clean copy of each repository with the tool's own index built first (`wingbox build`, `codegraph init`, `graphify extract . --code-only`):

- `wingbox usages <X>::<S> --all` (and `--sexp --all` for the positions of the same answer);
- `codegraph callers <S> --file <X> --limit 1000000` (and `--json`);
- `graphify affected "<X>::<S>" --depth 1`, and with its default depth 2.

Scoring: files exact when the set equals the reference list; a site counts as found when the tool names its file and line. Every answer, with the files it named outside the list and the files it missed, is in `results/rows.json`.

| Repository | Language | Source files | Exported symbols | Drawn until 20 used | Reference tool |
|---|---|---:|---:|---:|---|
| chrome-devtools-mcp | typescript | 296 | 639 | 26 | typescript 6.0.3 |
| immich-server | typescript | 707 | 1,720 | 29 | typescript 6.0.3 |
| agent-browser | rust | 122 | 756 | 25 | rust-analyzer 2026-11-02 |
| claudes-c-compiler | rust | 351 | 1,479 | 24 | rust-analyzer 2026-11-02 |
| fastapi | python | 1,223 | 6,716 | 607 | pyright 1.1.414 |

## Scope

Public code only, at pinned commits. Third-party code is not executed: dependencies for the language tooling are installed with lifecycle scripts off, Rust crates are only fetched, rust-analyzer runs with build scripts and proc macros off. After the run, the copies hold 0 build output folders and 0 bytecode caches (`results/safety.json`). No model and no API key are used by any tool. The benchmark measures one question, the uses of one exported symbol; it does not measure flows, impact across files, agent sessions or build time.

## Versions and commits

- wingbox: 0.5.1
- codegraph: 1.6.2
- graphify: 0.9.110
- typescript: 6.0.3
- scip-typescript: 0.6.0
- scip-python: 0.6.8
- pyright: 1.1.414
- rust-analyzer: rust-analyzer 0.3.3105-standalone (b98172d80f 2026-11-01)
- rust toolchain: rustc 1.99.0 (b940084d7 2026-09-28)
- date: 2026-11-17

- chrome-devtools-mcp: 8ba2ac945ebaf1adb92208c29f3a30e73a920956
- immich: e5d6eb7be67834f59d90e4b7a86f5dec7ac9af86
- agent-browser: 53384771fbafc261ba67608ac806dd5aa70b84f0
- claudes-c-compiler: 6f1b99acb2f4ec2414592136c2009fe7713deec3
- fastapi: 2a1bd230b454a51644c7e2087f525b2dd245edbf

## Reproduce

```sh
benchmarks/usages/run.sh /path/to/work     # needs git, uv, node 20+, rustup, curl; downloads the five repositories
```

`run.sh` installs the pinned tools into the work folder, draws the questions, asks the language tooling and SCIP, builds each tool's index, asks every question, writes `out/results/` and `out/REPORT.md`, and compares the results with the committed ones (`verify.py`). `make_report.py results README.md` rewrites this file from the committed results.
