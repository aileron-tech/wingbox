"""Write benchmarks/usages/README.md from the result files.

Usage: python3 -I make_report.py <results dir> <out.md>
Every number of the report comes from <results dir>: questions.json, rows.json, versions.json, commits.txt,
safety.json and gold/. The test suite checks that README.md equals what this script writes.
"""
from __future__ import annotations

import json
import os
import statistics
import sys

TOOLS = [
    ("wingbox", "Wingbox `usages`"),
    ("codegraph", "CodeGraph `callers`"),
    ("graphify_depth1", "Graphify `affected --depth 1`"),
    ("graphify", "Graphify `affected` (depth 2, its default)"),
]
LANGUAGES = [("typescript", "TypeScript"), ("rust", "Rust"), ("python", "Python")]


def load(root: str, name: str):
    return json.load(open(os.path.join(root, name)))


def number(value: int) -> str:
    return f"{value:,}"


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "-"


def summary(rows: list[dict], tool: str) -> dict:
    mine = [row[tool] for row in rows]
    return {
        "questions": len(rows),
        "answered": sum(1 for r in mine if r["resolved"]),
        "files_exact": sum(1 for r in mine if r["files"]["exact"]),
        "files_complete": sum(1 for r in mine if r["files"]["complete"]),
        "files_recall": statistics.mean(r["files"]["recall"] for r in mine),
        "files_precision": statistics.mean(r["files"]["precision"] for r in mine),
        "files_outside": sum(r["files"]["outside"] for r in mine),
        "files_missing": sum(r["files"]["missing"] for r in mine),
        "lines_reference": sum(row["reference_lines"] for row in rows),
        "lines_found": sum(r["sites"]["right"] for r in mine),
        "lines_outside": sum(r["sites"]["outside"] for r in mine),
        "tokens_median": statistics.median(r["tokens"] for r in mine),
        "tokens_total": sum(r["tokens"] for r in mine),
    }


def of_language(rows: list[dict], language: str) -> list[dict]:
    return [row for row in rows if row["language"] == language]


def report(results: str) -> str:
    meta = load(results, "questions.json")
    rows = load(results, "rows.json")
    versions = load(results, "versions.json")
    safety = load(results, "safety.json")
    commits = open(os.path.join(results, "commits.txt")).read().split("\n")
    total = {tool: summary(rows, tool) for tool, _label in TOOLS}
    by_language = {(tool, language): summary(of_language(rows, language), tool)
                   for tool, _label in TOOLS for language, _name in LANGUAGES}
    wb, cg, g1, g2 = (total[tool] for tool, _label in TOOLS)
    n = len(rows)
    lines_reference = wb["lines_reference"]
    sites_reference = sum(row["reference_sites"] for row in rows)
    unresolved = sum(row["wingbox"]["unresolved_calls"] for row in rows)

    def per_language(tool: str, key: str) -> str:
        return ", ".join(f"{name} {by_language[(tool, language)][key]} of {by_language[(tool, language)]['questions']}"
                         for language, name in LANGUAGES)

    columns = {tool: (sum(row[tool]["columns"]["same_column"] for row in rows),
                      sum(row[tool]["columns"]["reference_sites_on_named_lines"] for row in rows))
               for tool in ("wingbox", "codegraph")}
    out: list[str] = []
    add = out.append
    add("# Find usages: Wingbox, CodeGraph and Graphify on 100 questions")
    add("")
    add(f"Run of {versions['date']}. Wingbox {versions['wingbox']} from PyPI, CodeGraph {versions['codegraph']} from npm "
        f"(`@colbymchenry/codegraph`), Graphify {versions['graphify']} from PyPI (`graphifyy`, no model backend). "
        "Every number below is computed by `make_report.py` from the files in `results/`, and `run.sh` reproduces "
        "them from nothing.")
    add("")
    add("The question of a rename or a signature change: \"I will change exported symbol S of file X. Where is S "
        f"used?\" {n} questions on five public repositories in three languages. Each tool is asked with its own "
        "command for the users of one symbol, and every answer is checked against the language tooling: the files "
        "against its find usages (TypeScript language service, pyright, rust-analyzer), the use sites against the "
        "SCIP index of the same engines (scip-typescript, scip-python, rust-analyzer scip).")
    add("")
    add("## Which tool for which job")
    add("")
    add(f"- **Wingbox, to change a symbol and every place that uses it.** `wingbox usages <file>::<S>` names exactly "
        f"the files that use S in {wb['files_exact']} of {n} questions ({per_language('wingbox', 'files_exact')}) "
        f"and {number(wb['lines_found'])} of the {number(lines_reference)} use sites of SCIP "
        f"({percent(wb['lines_found'], lines_reference)}), with {number(wb['lines_outside'])} sites outside that list "
        f"and the column of SCIP at {number(columns['wingbox'][0])} of {number(columns['wingbox'][1])} sites. "
        f"It runs on the local machine, with no model and no key; the median answer is {wb['tokens_median']:,.0f} tokens.")
    cg_missing = {language: sum(len(row["codegraph"]["files_missing"]) for row in of_language(rows, language))
                  for language, _name in LANGUAGES}
    add(f"- **CodeGraph `callers`** lists the functions and files that call, reference, import or instantiate S. "
        f"It names no file outside the reference lists ({number(cg['files_outside'])} in {n} answers) and the exact "
        f"files in {cg['files_exact']} of {n} questions ({per_language('codegraph', 'files_exact')}); "
        f"{number(cg['files_missing'])} files are missing, {cg_missing['rust']} of them in Rust. It names "
        f"{number(cg['lines_found'])} of the {number(lines_reference)} use sites "
        f"({percent(cg['lines_found'], lines_reference)}), because it keeps one entry per calling function and kind "
        f"of use; median answer {cg['tokens_median']:,.0f} tokens. Per its documentation, its main tool "
        "`codegraph explore` returns the source of the relevant symbols with the call paths between them in one "
        "call, which this benchmark does not measure.")
    unanswered = [row for row in rows if not row["graphify_depth1"]["resolved"]]
    names = dict(LANGUAGES)
    kinds = {"variable": "module variables"}
    unanswered_kinds = sorted({f"{names[row['language']]} {kinds.get(row['kind'], row['kind'] + 's')}" for row in unanswered})
    add(f"- **Graphify** has no command that lists the uses of a symbol. The nearest, `graphify affected`, is a "
        f"reverse traversal from one node. It answers {g1['answered']} of {n} questions (no node for the other "
        f"{len(unanswered)}: {', '.join(unanswered_kinds)}). At depth 1 it names the exact files "
        f"in {g1['files_exact']} of {n} questions and {number(g1['lines_found'])} of the {number(lines_reference)} "
        f"use sites; at its default depth 2, the exact files in {g2['files_exact']} of {n}, with "
        f"{number(g2['files_outside'])} files outside the reference lists. Per its documentation, it maps documents, "
        "PDFs, images and videos into the same graph as the code, which this benchmark does not measure.")
    add("")
    add("## Results")
    add("")
    add(f"{n} questions; {number(sum(row['reference_files'] for row in rows))} reference files; "
        f"{number(sites_reference)} SCIP use sites on {number(lines_reference)} distinct lines. A file or site "
        "outside the reference is one the tool names and the language tooling does not.")
    add("")
    add("| Tool | Answered | Files exact | All files found | Files recall (mean) | Files precision (mean) | "
        "Files outside | Use sites found | Sites outside | Tokens (median) | Tokens (all answers) |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tool, label in TOOLS:
        s = total[tool]
        add(f"| {label} | {s['answered']} | {s['files_exact']} | {s['files_complete']} | {s['files_recall']:.3f} | "
            f"{s['files_precision']:.3f} | {number(s['files_outside'])} | {number(s['lines_found'])} of "
            f"{number(s['lines_reference'])} | {number(s['lines_outside'])} | {s['tokens_median']:,.0f} | "
            f"{number(s['tokens_total'])} |")
    add("")
    add("Answered: the tool found S in X and gave an answer about it. Use sites are counted by file and line, because "
        "Graphify prints no column; of the SCIP sites on the lines a tool names, Wingbox gives the same column at "
        f"{number(columns['wingbox'][0])} of {number(columns['wingbox'][1])}, CodeGraph at {number(columns['codegraph'][0])} "
        f"of {number(columns['codegraph'][1])}. Tokens: characters of the text answer / 4, the measure of the other "
        "benchmarks of this repository; every tool is asked for its whole list (`--all`, `--limit 1000000`).")
    add("")
    add("### By language")
    add("")
    add("| Tool | Language | Questions | Files exact | Use sites found | Sites outside | Tokens (median) |")
    add("|---|---|---:|---:|---:|---:|---:|")
    for tool, label in TOOLS:
        for language, name in LANGUAGES:
            s = by_language[(tool, language)]
            add(f"| {label} | {name} | {s['questions']} | {s['files_exact']} | {number(s['lines_found'])} of "
                f"{number(s['lines_reference'])} | {number(s['lines_outside'])} | {s['tokens_median']:,.0f} |")
    add("")
    add(f"Wingbox counts a method call whose receiver type it cannot see as an unresolved call, apart from the "
        f"uses: {unresolved} in these {n} answers.")
    add("")
    not_exact = [row for row in rows if not row["wingbox"]["files"]["exact"]]
    if not_exact:
        add("Questions where Wingbox's file list differs from the reference:")
        add("")
        for row in not_exact:
            r = row["wingbox"]
            add(f"- {row['id']} `{row['name']}` ({row['file']}): missing {', '.join(r['files_missing']) or 'none'}; "
                f"outside {', '.join(r['files_outside']) or 'none'}")
        add("")
    add("## Why the numbers differ")
    add("")
    add(WHY)
    add("")
    add("## Method")
    add("")
    add(METHOD.format(seed=meta["seed"], per_repo=meta["per_repo"]))
    add("")
    add("| Repository | Language | Source files | Exported symbols | Drawn until 20 used | Reference tool |")
    add("|---|---|---:|---:|---:|---|")
    for repo in meta["repos"]:
        add(f"| {repo['repo']} | {repo['language']} | {number(repo['source_files'])} | {number(repo['exported_symbols'])} | "
            f"{repo['tried']} | {repo['reference_tool']} |")
    add("")
    add("## Scope")
    add("")
    add(SCOPE.format(build=len(safety["build_dirs"]), bytecode=len(safety["bytecode_dirs"])))
    add("")
    add("## Versions and commits")
    add("")
    for name, value in versions.items():
        add(f"- {name}: {value}")
    add("")
    for line in commits:
        if line.strip():
            add(f"- {line.split()[0]}: {line.split()[1]}")
    add("")
    add("## Reproduce")
    add("")
    add(REPRODUCE)
    add("")
    return "\n".join(out)


WHY = """The three tools answer different questions with the command that comes closest to this one; the design points below are taken from each tool's own documentation and code.

**Wingbox** is built for this question: "`wingbox usages` lists every place that uses one symbol, through re-exports and aliases, grouped by file, with the line, the column and the kind of use" (Wingbox README). Its language adapters record, per file, which names an import binds and where each bound name is used, and a resolver follows re-exports, aliases and glob imports back to the defining symbol: the same model as the language tooling the references come from. For a method, "calls whose receiver type is not in sight are listed as `unresolved-call`, never guessed" (Wingbox README).

**CodeGraph** stores "nodes (functions, classes, methods) and edges (calls, imports, extends, implements)" and resolves references after extraction ("function calls -> definitions, imports -> source files", CodeGraph README, How It Works). `codegraph callers <symbol>` is documented as "Find all functions/methods that call a specific symbol"; in 1.6.2 it follows `calls`, `references`, `imports`, `instantiates` and `navigates` edges into the symbol (`getCallers`, lib/dist/graph/traversal.js) and prints each caller once per kind of edge. A function that uses S five times is one entry with the line of one use, so it names fewer sites than there are uses. A use that its resolver does not link to the definition is not an edge, and the file is not in the answer. In this run that happens in Rust: a glob import of a module that re-exports S (`use super::elf::*` reaching `w32`), a call through a module path (`screenshot::take_screenshot(...)`), a constant read as a value, a trait named in `impl S for T`. What it links, it links to the right definition: no file in its answers is outside the reference lists.

**Graphify** builds a knowledge graph of "code, docs, PDFs, images, videos" (Graphify README); its code nodes are files, classes, functions and methods, linked by `calls`, `imports`, `inherits` and more relations. It has no find-usages command. `graphify affected "X"` is documented as "reverse traversal to find nodes impacted by X" over fifteen relations by default, depth 2. The comment above its relation list says that in Python "every other relation (calls, references, imports) targets the *file* node, not the symbol node", and that the walk "follows `defines` from the symbol back to its defining file and then continues through `imports` to consumers" (graphify/affected.py). At depth 1 an answer therefore holds the direct callers it resolved; at depth 2 it also holds every file that imports the file of S and every caller of a caller, which is an impact answer rather than a list of uses. Its walk starts from S and from the methods of S ("seed the reverse walk with the root's own member nodes", graphify/affected.py), so for a class or struct it also lists the calls of its methods (`self.fresh_value()` in an `impl Lowerer` block for `Lowerer`), which SCIP files under each method rather than under S; these calls are most of its sites outside the SCIP list at depth 1. A hit carries the line where its edge starts: the first line of a multi-line `import {` statement rather than the line of the name, and the definition line of the calling function when the edge has no position of its own (\"honest fallback: the node's own def line\", graphify/affected.py). A module-level variable has no node in its graph, so `affected` exits with \"No unique node match\" for it."""

METHOD = """Sampling, per repository: the exported top-level symbols found by the language's own parser (TypeScript compiler API, Python `ast`, a Rust item scanner in `tools/`) are shuffled with seed {seed}; the reference tool is asked for each in that order, and the first {per_repo} symbols with a reference in another file are the questions. This is a uniform draw among the exported symbols that another file uses.

References: the files of a question are every file other than X in which the language tooling's find usages reports S (`results/gold/<id>.json`, `files`). The use sites are every occurrence of S in another file that is not a definition, from the SCIP index (`scip.sites`, 1-based line and column); for a TypeScript class, `new S(...)` is filed under the class's constructor symbol in SCIP and counted as a site of S.

The commands, run in a clean copy of each repository with the tool's own index built first (`wingbox build`, `codegraph init`, `graphify extract . --code-only`):

- `wingbox usages <X>::<S> --all` (and `--sexp --all` for the positions of the same answer);
- `codegraph callers <S> --file <X> --limit 1000000` (and `--json`);
- `graphify affected "<X>::<S>" --depth 1`, and with its default depth 2.

Scoring: files exact when the set equals the reference list; a site counts as found when the tool names its file and line. Every answer, with the files it named outside the list and the files it missed, is in `results/rows.json`."""

SCOPE = """Public code only, at pinned commits. Third-party code is not executed: dependencies for the language tooling are installed with lifecycle scripts off, Rust crates are only fetched, rust-analyzer runs with build scripts and proc macros off. After the run, the copies hold {build} build output folders and {bytecode} bytecode caches (`results/safety.json`). No model and no API key are used by any tool. The benchmark measures one question, the uses of one exported symbol; it does not measure flows, impact across files, agent sessions or build time."""

REPRODUCE = """```sh
benchmarks/usages/run.sh /path/to/work     # needs git, uv, node 20+, rustup, curl; downloads the five repositories
```

`run.sh` installs the pinned tools into the work folder, draws the questions, asks the language tooling and SCIP, builds each tool's index, asks every question, writes `out/results/` and `out/REPORT.md`, and compares the results with the committed ones (`verify.py`). `make_report.py results README.md` rewrites this file from the committed results."""


if __name__ == "__main__":
    results_dir, out_path = sys.argv[1:3]
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(report(results_dir))
