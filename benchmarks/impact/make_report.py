"""Write benchmarks/impact/README.md from the result files; no number is typed by hand.

Usage: python3 -I make_report.py <results dir> <out README.md>
The agent test and the build time are included when their result files exist (agent.json, build_times.json).
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import bootstrap_mean, bootstrap_ratio, sign_test, wilson  # noqa: E402

R, out_path = sys.argv[1:3]
exists = lambda name: os.path.exists(os.path.join(R, name))
load = lambda name: json.load(open(os.path.join(R, name)))
meta, rows, versions, edges = load("questions.json"), load("command.json"), load("versions.json"), load("edges.json")
agent = load("agent.json") if exists("agent.json") else []
build = load("build_times.json") if exists("build_times.json") else None
commits = dict(line.split()[:2] for line in open(os.path.join(R, "commits.txt")).read().splitlines() if line.strip())
version = versions["wingbox"].split()[-1]
lines: list[str] = []
add = lines.append
interval = lambda low, high: f"{low * 100:.0f} to {high * 100:.0f} %"
signed = lambda value: f"{value * 100:+.0f} %"

n = len(rows)
repos = list(dict.fromkeys(row["repo"] for row in rows))
sizes = sorted(row["reference_files"] for row in rows)
exact = sum(row["wingbox"]["exact"] for row in rows)
file_for_file = sum(row["wingbox"]["exact_strict"] for row in rows)
with_missing = sum(row["wingbox"]["missing"] > 0 for row in rows)
low, high = wilson(exact, n)

add("# Wingbox benchmark: impact questions on public code")
add("")
add(f"Wingbox {version} from PyPI, run of {versions['date']}. This file is written by `make_report.py` from the files in `results/`. "
    "`Dockerfile.benchmark` at the root of the repository runs the tests that need no model in a clean container and compares every "
    "result file with the committed one.")
add("")
add("## Summary")
add("")
add(f"- {n} questions drawn at random with a fixed seed in {len(repos)} public repositories (TypeScript, Rust, Python): `wingbox impact` "
    f"names the complete list of dependent files, with nothing outside it, in {exact} of {n} (95 % interval {interval(low, high)}). "
    f"A required file is missing in {with_missing} of {n}.")
if agent:
    by = {setup: {t["id"]: t for t in agent if t["setup"] == setup} for setup in ("plain", "wb")}
    ids = sorted(set(by["plain"]) & set(by["wb"]))
    tokens = bootstrap_ratio([by["wb"][i]["tokens"] for i in ids], [by["plain"][i]["tokens"] for i in ids])
    cost = bootstrap_ratio([by["wb"][i]["cost_usd"] or 0 for i in ids], [by["plain"][i]["cost_usd"] or 0 for i in ids])
    hits = {setup: sum(by[setup][i]["exact"] for i in ids) for setup in ("plain", "wb")}
    add(f"- A coding agent on {len(ids)} of these questions, with Wingbox against its built-in search alone: tokens {signed(tokens[0])} "
        f"({signed(tokens[1])} to {signed(tokens[2])}), cost {signed(cost[0])} ({signed(cost[1])} to {signed(cost[2])}); "
        f"complete answers {hits['wb']} of {len(ids)} against {hits['plain']} of {len(ids)}.")
wrong_total, missing_total = sum(row["wrong"] for row in edges), sum(row["missing"] for row in edges)
largest = max(edges, key=lambda row: row["pairs"])
add(f"- Every module dependency of the graph against the import statements of the code, in {len(edges)} corpora "
    f"({largest['pairs']:,} dependencies in the largest): {wrong_total} wrong, {missing_total} missing.")
if build:
    big = max(build["summary"], key=lambda row: row["source_files"])
    add(f"- Building the graph, the report and the map of {big['corpus']} ({big['source_files']:,} source files) from a fresh checkout takes "
        f"{big['build_wall_s']:.1f} s on a hosted runner.")
add("")
add("## The question")
add("")
add("For a file X: every file that depends on X directly or through one other file, where \"A depends on B\" means that A imports B. "
    "It is what a developer or an agent asks before changing X.")
add("")
grimp = load("fastapi.grimp_check.json")
loaded = grimp.get("reference_only_loaded_by_name", [])
add("Reference lists come from resolvers that share no code with Wingbox, all in `tools/`: Python's `ast` (`py_edges.py`; on FastAPI the "
    "pairs of its import statements equal the import graph of grimp, `results/fastapi.grimp_check.json`"
    + (f"; it also counts a module loaded by a literal name, `importlib.import_module(\"a.b\")` or `__import__(\"a.b\")`, which grimp does not "
       f"read: {len(loaded)} pair on FastAPI" if loaded else "")
    + "), an own TypeScript resolver (`ts_resolve.py`) and an own Rust resolver (`rust_resolve.py`). "
    "Since 2026-11-17 (Wingbox 0.10.0) the Rust resolver also counts, for a path that names an item, the file that defines the item (followed through `pub use` re-exports), and the items a module names after a glob import (`use x::*`), found by scanning the module text. A file that uses an item depends on the file that defines it; the earlier reference stopped at the module facade that re-exports the item. The questions were drawn before this change and stay the same.")
add("")
add("Three kinds of file are accepted when an answer names them and are not required: in Python a package `__init__.py` executed on the way "
    "to the imported module; in TypeScript a file that imports the compiled output of X, which `tsconfig.json` maps back to X through `outDir` "
    "and `rootDir`; in Rust a file whose path passes through the module of X to reach a deeper module (`crate::x::y`). In each case a change of "
    "X can reach the file, and a reader of the import statements may or may not count it.")
add("")
add(f"Sampling: per repository, {meta['per_repo']} files drawn without replacement (seed {meta['seed']}) from the source files that at least one "
    f"other file imports. Nothing else is filtered. The reference lists hold {sizes[0]} to {sizes[-1]} files, median {statistics.median(sizes):.0f}.")
add("")
add(f"## Test 1. The impact command, {n} questions")
add("")
add("`wingbox impact <file> 2 --all`, no agent. \"Complete and nothing extra\": every required file is named and no named file is outside the "
    "accepted list.")
add("")
add("| Repository | Language | Questions | Complete and nothing extra | With a missing file | Median answer, tokens |")
add("|---|---|---|---|---|---|")
for repo in repos:
    subset = [row for row in rows if row["repo"] == repo]
    add(f"| {repo} | {subset[0]['language']} | {len(subset)} | {sum(r['wingbox']['exact'] for r in subset)} | "
        f"{sum(r['wingbox']['missing'] > 0 for r in subset)} | {statistics.median(r['wingbox']['tokens'] for r in subset):,.0f} |")
add(f"| All | | {n} | {exact} ({interval(low, high)}) | {with_missing} | {statistics.median(r['wingbox']['tokens'] for r in rows):,.0f} |")
add("")
recall = bootstrap_mean([row["wingbox"]["recall"] for row in rows])
precision = bootstrap_mean([row["wingbox"]["precision"] for row in rows])
add(f"The interval is a 95 % Wilson interval. Mean recall {recall[0]:.3f}, mean precision {precision[0]:.3f}. In {file_for_file} of the {n} answers "
    f"the list equals the required list file for file; in the other {n - file_for_file} Wingbox also names accepted files "
    f"({sum(row['wingbox']['accepted_extra'] for row in rows)} in all). Tokens are characters of the output divided by four; the largest answer "
    f"is {max(row['wingbox']['tokens'] for row in rows):,} tokens for a list of {sizes[-1]} files.")
add("")
if agent:
    add(f"## Test 2. A coding agent with and without Wingbox, {len(ids)} questions")
    add("")
    add("Claude Code, headless, model `claude-opus-5-5`, no user settings, skills or connectors. Two setups: built-in search only (Read, Grep, Glob), "
        "and the same with the `wingbox` command added; the graph is built beforehand and the prompt names `wingbox impact <file> 2` in one "
        f"sentence. One trial per question and setup. The agent subset is drawn at random among the questions whose required list holds 3 to "
        f"{meta['agent_max_files']} files (up to {meta['agent_per_repo']} per repository). Tokens: everything the model read and wrote, cache included.")
    add("")
    add("| Setup | Trials | Complete answers (95 % interval) | F1, mean (95 % interval) | Tokens, mean (95 % interval) | USD per answer | Turns |")
    add("|---|---|---|---|---|---|---|")
    for setup, label in (("wb", "With Wingbox"), ("plain", "Built-in search only")):
        trials = [by[setup][i] for i in ids]
        f1 = bootstrap_mean([t["f1"] for t in trials])
        mean_tokens = bootstrap_mean([t["tokens"] for t in trials])
        e_low, e_high = wilson(hits[setup], len(trials))
        add(f"| {label} | {len(trials)} | {hits[setup]} ({interval(e_low, e_high)}) | {f1[0]:.3f} ({f1[1]:.3f} to {f1[2]:.3f}) | "
            f"{mean_tokens[0]:,.0f} ({mean_tokens[1]:,.0f} to {mean_tokens[2]:,.0f}) | {statistics.mean(t['cost_usd'] or 0 for t in trials):.3f} | "
            f"{statistics.mean(t['turns'] or 0 for t in trials):.1f} |")
    add("")
    fewer = sum(by["wb"][i]["tokens"] < by["plain"][i]["tokens"] for i in ids)
    only_wb = sum(by["wb"][i]["exact"] and not by["plain"][i]["exact"] for i in ids)
    only_plain = sum(by["plain"][i]["exact"] and not by["wb"][i]["exact"] for i in ids)
    add(f"- Tokens with Wingbox against search alone: {signed(tokens[0])} ({signed(tokens[1])} to {signed(tokens[2])}); cost {signed(cost[0])} "
        f"({signed(cost[1])} to {signed(cost[2])}). Fewer tokens on {fewer} of the {len(ids)} questions, more on {len(ids) - fewer}.")
    add(f"- Complete answer only with Wingbox on {only_wb} questions, only without it on {only_plain}; exact sign test on these questions: "
        f"p = {sign_test(only_wb, only_plain):.3f}.")
    add("")
    add("| Repository | Questions | Tokens with Wingbox, mean | Tokens with search alone, mean | Change |")
    add("|---|---|---|---|---|")
    for repo in repos:
        mine = [i for i in ids if by["plain"][i]["repo"] == repo]
        if mine:
            with_w = statistics.mean(by["wb"][i]["tokens"] for i in mine)
            without = statistics.mean(by["plain"][i]["tokens"] for i in mine)
            add(f"| {repo} | {len(mine)} | {with_w:,.0f} | {without:,.0f} | {signed(with_w / without - 1)} |")
    add("")
    agent_version = versions.get("agent_wingbox", version)
    same = "" if agent_version == version else f" ({versions['agent_note']})"
    add("Ratios are totals over the questions; their intervals are 95 % paired bootstrap intervals over the questions (10,000 resamples). "
        f"The {len(agent)} trials cost {sum(t['cost_usd'] or 0 for t in agent):.2f} USD in total and ran with Wingbox {agent_version}{same}; "
        "every trial with its scores is in `results/agent.json`.")
    add("")

add("## Test 3. Are the edges in the code?")
add("")
add("Every module dependency of the Wingbox graph against every import statement of the code. Wrong: an edge the reference neither requires "
    "nor accepts. Missing: a required edge the graph does not have.")
add("")
add("| Corpus | Language | Dependencies in the code | Edges in the graph | Wrong | Missing |")
add("|---|---|---|---|---|---|")
for row in edges:
    add(f"| {row['corpus']} | {row['language']} | {row['pairs']:,} | {row['edges']:,} | {row['wrong']} | {row['missing']} |")
add("")
for row in edges:
    for kind in ("wrong", "missing"):
        if row[kind]:
            add(f"- {row['corpus']}, {kind}: " + "; ".join(f"`{a}` -> `{b}`" for a, b in row[f"{kind}_edges"]))
if any(row["wrong"] or row["missing"] for row in edges):
    add("")
add("The graph holds more edges than the code requires where it also records accepted ones (see \"The question\").")
if versions.get("edge_test_python"):
    add("")
    add(f"The Python rows were {versions['edge_test_python']}.")
add("")
if build:
    info = build["machine"]
    add("## Test 4. Building the graph")
    add("")
    add(f"Seconds from a fresh checkout to the graph, the report and the map: every run starts from a new export of the repository, with no "
        f"earlier output and no cache. Median of {build['rounds']} runs on a GitHub-hosted runner ({info['system']}, {info['cpu']}, "
        f"{info['logical_cpus']} logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`, {build['version']}.")
    add("")
    add("| Corpus | Source files | Build, s | Modules | Import edges |")
    add("|---|---|---|---|---|")
    for row in build["summary"]:
        add(f"| {row['corpus']} | {row['source_files']:,} | {row['build_wall_s']:.2f} | {row['modules']:,} | {row['import_edges']:,} |")
    add("")
    add("The size of the graph is read from the file each build wrote; the summary stops when two runs differ.")
    add("")
add("## Scope")
add("")
add("- One kind of question: which files import this file, directly or through one other file. Other questions are not measured here.")
add("- Questions are drawn inside five repositories, so they are not independent in the strict sense; the per-repository rows show where a result comes from.")
add("- Tests 1 and 3 are deterministic and need no model. Test 2 uses one agent and one model, one trial per question; a second run will differ in the details.")
add("")
add("## Versions and commits")
add("")
add("| Item | Version or commit |")
add("|---|---|")
add(f"| Wingbox | {version} (PyPI `wingbox`) |")
add("| grimp | 3.20 |")
for repo, sha in commits.items():
    add(f"| {repo} | {sha[:7]} |")
add("")
add("## Reproduce")
add("")
add("In a clean container (the repositories are fetched at the commits above; no model, no key):")
add("")
add("```sh")
add("git clone https://github.com/aileron-tech/wingbox && cd wingbox")
add("docker build -f Dockerfile.benchmark -t wingbox-benchmark .")
add("docker run --rm wingbox-benchmark        # ends with REPRODUCED, or lists the result files that differ")
add("```")
add("")
add("`results/docker_run.log` is the output of that run for the committed results. Without a container:")
add("")
add("```sh")
add("benchmarks/impact/run.sh ~/wingbox-bench                                    # tests 1 and 3; needs git and uv")
add("python3 -I benchmarks/impact/impact100.py ~/wingbox-bench agent             # test 2: paid Claude Code sessions")
add("python3 -I benchmarks/impact/tools/time_builds.py ~/wingbox-bench           # test 4 on this machine")
add("```")
add("")
add("| Path | Content |")
add("|---|---|")
add("| `run.sh`, `impact100.py`, `edge_test.py`, `verify.py`, `stats.py` | the run, the sampling and the tests, the comparison with the committed results, the intervals |")
add("| `tools/` | the reference resolvers and the build timing; none of them uses Wingbox code |")
add("| `results/` | the questions, the reference lists (`gold/`), every row of the command test, every trial of the agent test, the edge test, the build times |")
add("")
if os.path.exists(os.path.join(R, "context_ratio.json")):
    ctx = load("context_ratio.json")
    rows = sorted(ctx["repos"], key=lambda r: -r["ratio"])
    add("")
    add("## Context size (the `graphify benchmark` measure)")
    add("")
    add("`graphify benchmark` reports \"Nx fewer tokens per query\": its estimate of the corpus tokens divided by the average size of its answers. "
        "`tools/context_ratio.py` divides the same corpus estimate, taken from `graphify benchmark` 0.9.110 on the same commits "
        "(`results/graphify_benchmark.json`, code-only graphs), by the average size of the Wingbox impact answers above. "
        "Results in `results/context_ratio.json`:")
    add("")
    add("| Repository | Corpus (Graphify's estimate) | Wingbox | Graphify | Wingbox answers complete |")
    add("|---|---|---|---|---|")
    for r in rows:
        add(f"| {r['repo']} | {r['graphify_corpus_tokens']:,} | {r['ratio']:,}x | {r['graphify_ratio']:,}x | {r['complete_answers']} of {r['questions']} |")
    add("")
    add("The two tools answer different questions: Graphify five fixed questions per repository, not graded; Wingbox the impact of 20 files per repository, "
        "each answer checked against the import statements. So this compares answer size on one corpus count, not answer quality. "
        "The script also keeps an independent corpus estimate (characters / 4 over the source files, without vendored bundles and minified files) for reference.")
    add("")
    add("    python3 tools/context_ratio.py <folder with the checkouts> results/graphify_benchmark.json")
open(out_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("written", out_path, len(lines), "lines")
