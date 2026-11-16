"""Write benchmarks/php/README.md from the result files; no number is typed by hand.

Usage: python3 -I make_report.py <results dir> <out README.md>

The result files are `questions.json` (the sampled questions and their reference
lists), `command.json` (the scored answers of `wingbox impact`), `edges.json`
(the whole-graph edge audit), `versions.json` and `commits.txt`. A result set
whose `questions.json` says `"synthetic": true` comes from the small fixture in
`benchmarks/php/fixture/`, not from the public corpora; the report says so.
"""
from __future__ import annotations

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "impact"))
from stats import wilson  # noqa: E402

RESULTS, out_path = sys.argv[1:3]
load = lambda name: json.load(open(os.path.join(RESULTS, name), encoding="utf-8"))
meta = load("questions.json")
rows = load("command.json")
edges = load("edges.json")
versions = load("versions.json")
synthetic = bool(meta.get("synthetic"))
questions = meta["questions"]
by_id = {row["id"]: row for row in rows}
corpora = sorted({question["corpus"] for question in questions})
corpus_meta = {entry["name"]: entry for entry in meta.get("corpora", [])}
commits = []
commits_path = os.path.join(RESULTS, "commits.txt")
if os.path.exists(commits_path):
    for line in open(commits_path, encoding="utf-8").read().splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split()
        if len(parts) >= 3:
            commits.append((parts[0], parts[2], parts[3] if len(parts) > 3 else ""))

lines: list[str] = []
add = lines.append
interval = lambda low, high: f"{low * 100:.0f} to {high * 100:.0f} %"
mean = lambda values: sum(values) / len(values) if values else 0.0

n = len(questions)
sizes = sorted(question["reference_files"] for question in questions)
exact = sum(by_id[question["id"]]["wingbox"]["exact"] for question in questions)
file_for_file = sum(by_id[question["id"]]["wingbox"]["exact_strict"] for question in questions)
with_missing = sum(by_id[question["id"]]["wingbox"]["missing"] > 0 for question in questions)
low, high = wilson(exact, n)
recall = mean([by_id[question["id"]]["wingbox"]["recall"] for question in questions])
precision = mean([by_id[question["id"]]["wingbox"]["precision"] for question in questions])
named_outside = sum(by_id[question["id"]]["wingbox"]["not_on_list"] for question in questions)
wrong_total = sum(row["wrong"] for row in edges)
missing_total = sum(row["missing"] for row in edges)

add("# Wingbox benchmark: PHP and Laravel impact questions on public code")
add("")
version = versions["wingbox"].split()[-1] if versions.get("wingbox") else "unknown"
spec = versions.get("spec", "")
version_note = " from this checkout" if spec == "checkout" else (f" (`{spec}`)" if spec else "")
where = "the synthetic Laravel fixture in `benchmarks/php/fixture/`" if synthetic else "the corpora below"
corpus_word = "corpus" if len(corpora) == 1 else "corpora"
add(f"Wingbox {version}{version_note}, run of {versions.get('date', 'unknown')}, on {where}. This file is written by "
    "`make_report.py` from the files in `results/`; no number is typed by hand. "
    "`benchmarks/php/Dockerfile.php` reruns the benchmark in a clean container and compares every result "
    "file with the committed one.")
add("")
if spec == "checkout":
    add("The results are of the code in this checkout, at the commit that holds `results/`. "
        "`WINGBOX_SPEC=wingbox==X.Y.Z` runs the same benchmark against a release once one ships the PHP adapter.")
    add("")
if synthetic:
    add("## Status")
    add("")
    add("The committed result files come from the synthetic fixture, not from the public corpora: the PHP "
        "adapter is new, and the full run has not been recorded in this tree yet. The fixture is a Laravel "
        "project small enough to read by hand (two PSR-4 prefixes, a nested `composer.json`, Blade layouts, "
        "anonymous and Livewire components, one ambiguous view and one ambiguous class). It shows that the "
        "harness runs end to end and what a result file looks like; the numbers below are a smoke test, not "
        "a measurement of Wingbox on real projects. The corpora are pinned in `results/commits.txt` and "
        "`benchmarks/php/run.sh <work dir>` produces their results.")
    add("")
add("## Summary")
add("")
add(f"- {n} questions drawn with a fixed seed from {len(corpora)} PHP/Blade {corpus_word}: `wingbox impact <file> 2 --all` "
    f"names the complete list of dependent files, with nothing outside it, in {exact} of {n} "
    f"(95 % interval {interval(low, high)}). A required file is missing in {with_missing} of {n}.")
add(f"- Every IMPORTS edge of the graph against the reference: {len(edges)} {corpus_word}, {sum(row['pairs'] for row in edges):,} "
    f"reference dependencies in all; {wrong_total} wrong, {missing_total} missing.")
if synthetic:
    add("- Read the status above before the numbers: this is a harness check on a synthetic project, not a claim "
        "about the corpora.")
else:
    add(f"- The reference lists hold {sizes[0]} to {sizes[-1]} files, median {statistics.median(sizes):.0f}. The "
        "answer is read from the text the command prints, without a model.")
add("")
add("## The question")
add("")
add("For a file X: every file that depends on X directly or through one other file, where \"A depends on B\" "
    "means that A names B. It is what a developer or an agent asks before changing X.")
add("")
add("In PHP, A names B when A's source contains an import that resolves to B's file, or a class reference "
    "that resolves to B: `new B`, `B::member`, `B::class`, `extends`, `implements`, a trait `use`, a type "
    "hint (parameter, property, return type, union, intersection, nullable), `catch`, `instanceof` or an "
    "attribute. In Blade, A names B through `@extends`, `@include*`, `@component`, `@each`, `<x-*>` or "
    "`<livewire:*>` when the view or component resolves to B's file. In PHP the helper `view('a.b')`, "
    "`View::make('a.b')`, `view()->make('a.b')`, a `->view('a.b')` call (a response) and `Route::view(uri, 'a.b')` "
    "are the same kind of edge.")
add("")
add("The reference lists and the edge audit come from `tools/php_reference.php`, a resolver written in PHP "
    "that shares no code with Wingbox: it uses only `token_get_all`, `json_decode` and the standard library, "
    "has no Composer package and no autoloader, and never imports or calls Wingbox. It resolves class names "
    "through the declarations of the checkout and through the PSR-4 maps of every `composer.json` in the tree "
    "(root and nested, `autoload` and `autoload-dev`, the longest prefix wins), and Blade views through every "
    "`resources/views` directory plus `loadViewsFrom` and `View::addNamespace` registrations (an unregistered "
    "namespace is the directory of that name under a default root, as the published error views are); Livewire "
    "aliases follow `Livewire::component` registrations, then `App\\Livewire\\<Studly>` and the view "
    "`livewire.<alias>`.")
add("")
add("Two rules keep the reference honest about names it cannot decide. When a view resolves in several roots, "
    "the name is ambiguous and resolves to nothing. When the same class is declared in several files, the file "
    "the PSR-4 map names wins (a generated IDE-helper file declaring stub classes for every model is the usual "
    "case); if that leaves zero or several files, the name is ambiguous and resolves to nothing. An edge to a "
    "candidate of an ambiguous name is accepted when it is in the graph and is never required.")
add("")
add(f"Sampling: per corpus, {meta['per_repo']} files drawn without replacement with seed {meta['seed']} from the "
    f"source files that at least one other file depends on. Nothing else is filtered. The reference lists hold "
    f"{sizes[0]} to {sizes[-1]} files, median {statistics.median(sizes):.0f}.")
add("")
add(f"## Test 1. The impact command, {n} questions")
add("")
add("`wingbox impact <file> 2 --all`, no model. \"Complete and nothing extra\": every required file is named and "
    "no named file is outside the accepted list. A file counts as missing when the required list names it and "
    "the answer does not.")
add("")
add("| Corpus | Questions | Complete and nothing extra | With a missing file | Median answer, tokens |")
add("|---|---|---|---|---|")
for corpus in corpora:
    mine = [question for question in questions if question["corpus"] == corpus]
    add(f"| {corpus} | {len(mine)} | {sum(by_id[q['id']]['wingbox']['exact'] for q in mine)} | "
        f"{sum(by_id[q['id']]['wingbox']['missing'] > 0 for q in mine)} | "
        f"{statistics.median(by_id[q['id']]['wingbox']['tokens'] for q in mine):,.0f} |")
add(f"| All | {n} | {exact} ({interval(low, high)}) | {with_missing} | "
    f"{statistics.median(by_id[q['id']]['wingbox']['tokens'] for q in questions):,.0f} |")
add("")
if file_for_file == n:
    add(f"The interval is a 95 % Wilson interval. Mean recall {recall:.3f}, mean precision {precision:.3f}. In all "
        f"{n} answers the list equals the reference list file for file.")
else:
    add(f"The interval is a 95 % Wilson interval. Mean recall {recall:.3f}, mean precision {precision:.3f}. In "
        f"{file_for_file} of the {n} answers the list equals the reference list file for file; the other "
        f"{n - file_for_file} name {named_outside} file(s) outside it.")
add(f"Tokens are characters of the output divided by four; the largest answer is "
    f"{max(by_id[q['id']]['wingbox']['tokens'] for q in questions):,} tokens for a list of {sizes[-1]} files.")
imperfect = [row for row in rows if not row["wingbox"]["exact"]]
if imperfect:
    add("")
    add("The questions whose answer is not complete and nothing extra:")
    add("")
    for row in imperfect:
        state = row["wingbox"]
        outside = ", ".join(f"`{path}`" for path in state.get("outside_files", []))
        missing_files = ", ".join(f"`{path}`" for path in state.get("missing_files", []))
        detail = f"- `{row['id']}` (`{row['file']}`): {state['not_on_list']} file(s) outside the reference"
        if outside:
            detail += f" ({outside})"
        detail += f", {state['missing']} missing"
        if missing_files:
            detail += f" ({missing_files})"
        add(detail + ".")
    add("")
add("")
add("## Test 2. Are the edges in the code?")
add("")
add("Every IMPORTS edge of the Wingbox graph against every reference edge of the same checkout, compared by "
    "module ID (`app/Models/User.php` is module `app/Models/User`; a `.php` and a `.blade.php` file of one stem "
    "are one module, as in the graph). Wrong: an edge the reference neither requires nor accepts. Missing: a "
    "required edge the graph does not have.")
add("")
add("| Corpus | Reference dependencies (module pairs) | Graph edges (PHP only) | Wrong | Missing | Accepted (not required) | Outside PHP |")
add("|---|---|---|---|---|---|---|")
for row in edges:
    add(f"| {row['corpus']} | {row['pairs']:,} | {row['edges']:,} | {row['wrong']} | {row['missing']} | "
        f"{row.get('accepted', 0)} | {row.get('unmapped_edges', 0):,} |")
add("")
unmapped = sum(row.get("unmapped_edges", 0) for row in edges)
causes: dict[str, int] = {}
for row in edges:
    for cause, count in row.get("unmapped_by_cause", {}).items():
        causes[cause] = causes.get(cause, 0) + count
shared = sum(len(row.get("shared_modules", {})) for row in edges)
languages = sorted({language for cause in causes for language in ("TypeScript", "JavaScript") if language in cause})
if causes and any("PHP file not in the reference list" in cause for cause in causes):
    add("WARNING: the reference file list and the builder's PHP/Blade discovery disagree; the audit is not complete.")
    add("")
if unmapped:
    what = ", ".join(languages) if languages else "non-PHP"
    unmapped_word = "edge" if unmapped == 1 else "edges"
    add(f"{unmapped:,} IMPORTS {unmapped_word} have an endpoint outside the PHP/Blade sources ({what} modules and the like). "
        "They are counted in the Outside PHP column, not dropped silently: the audit covers every IMPORTS edge whose "
        "two endpoints are PHP/Blade files; the breakdown by cause is in `results/edges.json`.")
    add("")
else:
    add("Every IMPORTS edge of every graph has two PHP/Blade endpoints, so the audit covers the whole graph.")
    add("")
if shared:
    add(f"{shared} module(s) are shared by a `.php` and a `.blade.php` file (single-file Livewire components). The "
        "graph has one node for both, and the audit compares modules, so an edge to either file is the same edge.")
    add("")
examples = [(row["corpus"], kind, pair) for row in edges for kind in ("wrong", "missing") for pair in row.get(f"{kind}_edges", [])]
if examples:
    for corpus, kind, (source, target) in examples[:20]:
        add(f"- {corpus}, {kind}: `{source}` -> `{target}`")
    if len(examples) > 20:
        add(f"- ... {len(examples) - 20} more edge(s) are in `results/edges.json`.")
else:
    add("Every IMPORTS edge of every graph is in the reference and every reference edge is in a graph.")
add("")
add("The graph is allowed to hold edges the reference only accepts: a candidate of an ambiguous name, and the "
    "conventional template `livewire.<alias>` of a component class (the class and its template name each other in a "
    "running application; the reference requires the class edge, not both). Accepted edges are neither wrong nor "
    "required.")
add("")
add("## Corpora and commits")
add("")
add("| Corpus | Source files | Questions | Commit |")
add("|---|---|---|---|")
commit_of = {name: (commit, tag) for name, commit, tag in commits}
for corpus in corpora:
    entry = corpus_meta.get(corpus, {})
    commit, tag = commit_of.get(corpus, ("", ""))
    pinned = f"`{commit[:7]}`" + (f" ({tag})" if tag else "") if commit else "the fixture"
    add(f"| {corpus} | {entry.get('source_files', '')} | {entry.get('questions', '')} | {pinned} |")
add("")
if synthetic:
    add("The fixture lives in `benchmarks/php/fixture/`; the three commits are the pins that `run.sh` clones for "
        "the full run. They are not the source of the committed numbers.")
    add("")
else:
    add("Each corpus is exported with `git archive`, so a path the repository marks `export-ignore` (tests and "
        "documentation in these projects) is not part of the run.")
    add("")
add("## What to conclude")
add("")
if exact == n:
    answer = (f"`wingbox impact <file> 2 --all` returned the complete reference list with nothing outside it in all "
              f"{n} questions.")
else:
    answer = (f"`wingbox impact <file> 2 --all` returned the complete reference list with nothing outside it in "
              f"{exact} of {n} questions ({interval(low, high)}); the missed questions are in `results/command.json`.")
if wrong_total == 0 and missing_total == 0:
    graph = ("Every IMPORTS edge of every graph is a dependency of the code, and every dependency the reference finds "
             "is in the graph.")
else:
    wrong_word = "edge" if wrong_total == 1 else "edges"
    missing_word = "dependency" if missing_total == 1 else "dependencies"
    graph = (f"The graph holds {wrong_total} IMPORTS {wrong_word} the reference rejects and misses {missing_total} "
             f"{missing_word} the reference requires; the corpus table and `results/edges.json` name them.")
add(answer)
add("")
add(graph)
add("")
add("Read Test 1 as the share of \"what depends on this file\" questions the command answers completely, and Test 2 "
    "as the precision and recall of the graph's IMPORTS edges against a static reading of the same source. The "
    "reference is a reader of names, not a runtime: a dependency it cannot see (a class name built in a string, a "
    "view chosen at run time) is outside both lists, and a convention the graph records but the reference does not "
    "require (a Livewire component's template) is counted as accepted, not wrong.")
add("")
add("## Scope")
add("")
add("- One kind of question: which files name this file, directly or through one other file. Other questions are "
    "not measured here.")
add("- The reference is static. It reads the text of the checkout, never executes it; a view name built at run "
    "time or a class name in a string cannot become an edge.")
add("- A name that resolves nowhere in the checkout is external (a Composer package, a framework class) and is "
    "not an edge; the reference does not know the packages.")
if synthetic:
    add("- The committed results are a smoke test on the fixture. They say nothing about the corpora.")
else:
    add("- Questions are drawn inside the corpora, so they are not independent in the strict sense; the "
        "per-corpus rows show where a result comes from.")
add("")
add("## Versions and commits")
add("")
add("| Item | Version or commit |")
add("|---|---|")
add(f"| Wingbox | {version}{version_note} (`WINGBOX_SPEC` of the run) |")
add(f"| PHP | {versions.get('php', 'unknown')} |")
for name, commit, tag in commits:
    add(f"| {name} | {commit[:7]}" + (f" ({tag})" if tag else "") + " |")
add("")
add("## Reproduce")
add("")
add("In a clean container (the corpora are fetched at the commits above; no model, no key):")
add("")
add("```sh")
add("git clone https://github.com/aileron-tech/wingbox && cd wingbox")
add("docker build -f benchmarks/php/Dockerfile.php -t wingbox-php-benchmark .")
add("docker run --rm wingbox-php-benchmark        # ends with REPRODUCED, or lists the result files that differ")
add("```")
add("")
add("Without a container (needs `git`, `uv`, Python 3.11+ and PHP 8.4 with `token_get_all`):")
add("")
add("```sh")
add("benchmarks/php/run.sh ~/wingbox-php-bench                         # the corpora and every result")
add("WINGBOX_SPEC=/path/to/a/checkout benchmarks/php/run.sh ~/other    # the Wingbox under test")
add("```")
add("")
add("By default `WINGBOX_SPEC` is the checkout that holds the script, so the run tests this commit. "
    "`WINGBOX_SPEC=wingbox==X.Y.Z` tests a released version, and `WINGBOX_SPEC=/path/to/checkout` another "
    "checkout. With `VERIFY=0` the run does not compare its results with the committed ones.")
add("")
add("| Path | Content |")
add("|---|---|")
add("| `run.sh`, `tools/questions.py`, `tools/verify.py`, `make_report.py` | the run, the sampling, the comparison "
    "with `wingbox impact` and the edge audit, this report |")
add("| `tools/php_reference.php`, `tools/list_files.py`, `tools/graph_edges.py` | the reference resolver (PHP), the "
    "file list it reads, the graph edges the audit reads; none of them uses Wingbox code |")
add("| `fixture/`, `results/` | the synthetic Laravel project, and the questions, answers, edge audit, versions "
    "and commits of one run |")
add("")
open(out_path, "w", encoding="utf-8").write("\n".join(lines).rstrip("\n") + "\n")
print("written", out_path, len(lines), "lines")