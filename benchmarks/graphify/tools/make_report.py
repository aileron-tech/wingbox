"""Assemble the benchmark report from the result files; no number is typed by hand.

Usage: python3 -I make_report.py <results dir> <out README.md>
<results dir> is <work dir>/out after run.sh, or the committed benchmarks/graphify/results.
Test D is included when <results dir>/agent_summary.json exists (tools/agent_test.py report).
"""
from __future__ import annotations

import json
import os
import re
import sys

R, out_path = sys.argv[1:3]
load = lambda name: json.load(open(os.path.join(R, name)))
text = lambda name: open(os.path.join(R, name), encoding="utf-8").read()
num = lambda value: f"{value:,}" if isinstance(value, int) else (f"{value:,.3f}" if value < 10 else f"{value:,.1f}")
pct = lambda new, old: f"{(new - old) / old * 100:+.0f} %"

versions = dict(line.split(None, 1) for line in text("versions.txt").splitlines() if line.strip())
commits = dict(line.split()[:2] for line in text("commits.txt").splitlines() if line.strip())
kf, ff = load("karpathy.formula.json"), load("fastapi.formula.json")
published = load("karpathy.gf_published.json")
cc = load("command_check.json")
agent = load("agent_summary.json") if os.path.exists(os.path.join(R, "agent_summary.json")) else None
gf = versions["graphify"]
lines: list[str] = []
add = lines.append

exact_w = sum(row["wingbox"]["wrong"] == 0 and row["wingbox"]["missing"] == 0 for row in cc)
exact_g = sum(row["graphify"]["wrong"] == 0 and row["graphify"]["missing"] == 0 for row in cc)
kw, kg = kf["wingbox"], kf["graphify"]

add("# Wingbox and Graphify on the same public tests")
add("")
add(f"Run of {versions['date']}. Wingbox {versions['wingbox']} from PyPI. Graphify {gf} from PyPI (`graphifyy`), "
    "`GRAPHIFY_BACKEND=none` (no model, no key). Every number below is produced by the scripts in `tools/` "
    "from the files in `results/`; this file is written by `tools/make_report.py`, and `run.sh` reproduces tests A, B and C from nothing.")
add("")
add("## Summary")
add("")
add(f"- Graphify's \"71.5x fewer tokens per query\" is the size of the whole corpus divided by the size of one graph answer. "
    f"No answer is checked. On the code of its own example corpus Graphify publishes 8.8x; Graphify {gf} gives "
    f"{kg['default_questions']['reduction_ratio']}x and {kg['published_questions']['reduction_ratio']}x today, and the same formula gives Wingbox "
    f"{kw['analyze_every_module']['reduction_by_mean']}x (test A).")
add(f"- Asked which files depend on a file, `wingbox impact` names the exact list in {exact_w} of {len(cc)} questions, "
    f"`graphify affected` in {exact_g} of {len(cc)} (test C).")
if agent:
    sm = agent["summary"]
    w, g, p = sm["all_wb"], sm["all_gf"], sm["all_plain"]
    add(f"- A coding agent answers the same questions correctly in all three setups. It spends {w['tokens']:,} tokens with Wingbox, "
        f"{p['tokens']:,} with its built-in search alone and {g['tokens']:,} with Graphify: Wingbox {pct(w['tokens'], p['tokens'])} "
        f"against plain search, Graphify {pct(g['tokens'], p['tokens'])} (test D).")
if os.path.exists(os.path.join(R, "build_times.json")):
    rows = load("build_times.json")["summary"]
    big = max(rows, key=lambda row: row["source_files"])
    ratios = [row["extract_to_build_wall_ratio"] for row in rows]
    add(f"- Building the graph of {big['corpus']} ({big['source_files']:,} source files) takes Wingbox {big['wingbox_build_wall_s']:.1f} s and Graphify "
        f"{big['graphify_extract_wall_s']:.1f} s on a hosted runner; over the five corpora Graphify's extract takes {min(ratios):.1f} to {max(ratios):.1f} times "
        f"as long as the whole Wingbox build (test E).")
add("")
add("## What Graphify publishes in its repository")
add("")
add("Read at Graphify-Labs/graphify, branch v8, commit 18cd78f (2026-11-15).")
add("")
add("| Published number | Where it comes from | Reproducible from the repository |")
add("|---|---|---|")
add("| 71.5x fewer tokens per query; 8.8x on code only | `graphify benchmark` (`graphify/benchmark.py`) on `worked/karpathy-repos`: nanoGPT, minGPT, micrograd, 5 papers, 4 images | yes: formula, corpus list and five questions are published |")
add("| 12 of 12 inferred cross-file edges correct | manual audit under `fastapi/` of the FastAPI repository (BENCHMARKS.md) | yes: the repository is public |")
add("| ERPNext 82.0 % against 70.8 %; LOCOMO; LongMemEval | BENCHMARKS.md names `crosstool/run.py` and `memory/runner.py` | no: neither folder, nor the questions, nor the gold facts are in the repository |")
add("")
add("The formula of `graphify benchmark`: corpus tokens = words x 100 / 75 (every file in the context); answer tokens = characters / 4; "
    "reduction = corpus tokens / mean answer tokens over the questions that matched something in the graph.")
add("")

add("## Test A. Graphify's token formula, Graphify's corpus")
add("")
k = kf["corpus"]
add(f"Corpus: the code of karpathy/nanoGPT, minGPT and micrograd: {k['code_files']} Python files, {num(k['words'])} words, {num(k['tokens'])} tokens. "
    "Graphify's review states ~16,997 tokens for the same 29 files.")
add("")
add("| Tool and query set | Queries answered | Mean answer, tokens | Reduction |")
add("|---|---|---|---|")
add("| Graphify, published in `worked/karpathy-repos/review.md` (code only) | not stated | ~1,929 | 8.8x |")
g = kg["default_questions"]
add(f"| Graphify {gf}, its five default questions, corpus counted by its own word counter | {len(g['per_question'])} of 5 | {num(g['avg_query_tokens'])} | {g['reduction_ratio']}x |")
g = kg["published_questions"]
add(f"| Graphify {gf}, the five questions of its review | {len(g['per_question'])} of 5 | {num(g['avg_query_tokens'])} | {g['reduction_ratio']}x |")
a = kw["analyze_every_module"]
add(f"| Wingbox `analyze`, every module of the corpus | {a['queries']} of {a['queries']} | {num(a['mean_tokens'])} | {a['reduction_by_mean']}x |")
a = kw["impact_every_file"]
add(f"| Wingbox `impact <file> 2`, every file of the corpus | {a['queries']} of {a['queries']} | {num(a['mean_tokens'])} | {a['reduction_by_mean']}x |")
q = kw["published_questions"]
add(f"| Wingbox, the five questions of Graphify's review, one `quick <keyword>` per keyword | {q['answered']} of {q['of']} | {num(q['mean_tokens'])} | {q['reduction']}x |")
add("")
literal = re.search(r"Reduction:\s+(\S+)", text("graphify_benchmark_literal.txt"))
if literal is None:
    raise SystemExit("no Reduction line in graphify_benchmark_literal.txt")
add("The literal command `graphify benchmark`, run in the corpus folder after `graphify extract . --code-only`, estimates the corpus from the node count and prints "
    + literal.group(1) + " (`results/graphify_benchmark_literal.txt`).")
add("")
add(f"Wingbox takes a module or a keyword, not a sentence, so its main row is every module of the corpus instead of five sentences. "
    f"Largest single `analyze` answer: {num(kw['analyze_every_module']['max_tokens'])} tokens ({kw['analyze_every_module']['reduction_by_max']}x). "
    f"The whole Wingbox graph file is {num(kw['whole_graph_file_tokens'])} tokens.")
add("")
add(f"Graphify's published graph of the full corpus (`worked/karpathy-repos/graph.json`) holds {published['graph_file']['nodes']} nodes and "
    f"{published['graph_file']['links']} links; its review states 285 and 340. Graphify {gf} run on that file with the published 92,616 words and "
    f"five questions gives {published['reduction_ratio']}x with {len(published['per_question'])} of {published['questions_asked']} questions matched "
    "(published: 71.5x). Papers and images are outside Wingbox, so the full-corpus row has no Wingbox counterpart.")
add("")
f = ff["corpus"]
add(f"Same formula on FastAPI ({f['code_files']:,} Python files, {num(f['words'])} words, {num(f['tokens'])} tokens):")
add("")
add("| Tool and query set | Queries answered | Mean answer, tokens | Largest answer, tokens | Reduction |")
add("|---|---|---|---|---|")
g = ff["graphify"]["default_questions"]
add(f"| Graphify {gf}, five default questions | {len(g['per_question'])} of 5 | {num(g['avg_query_tokens'])} | {num(max(p['query_tokens'] for p in g['per_question']))} | {g['reduction_ratio']}x |")
w = ff["wingbox"]
a = w["analyze_every_module"]
add(f"| Wingbox `analyze`, every module | {a['queries']:,} of {a['queries']:,} | {num(a['mean_tokens'])} | {num(a['max_tokens'])} | {a['reduction_by_mean']}x |")
a = w["impact_every_file"]
add(f"| Wingbox `impact <file> 2`, every file | {a['queries']:,} of {a['queries']:,} | {num(a['mean_tokens'])} | {num(a['max_tokens'])} | {a['reduction_by_mean']}x |")
q = w["default_questions"]
add(f"| Wingbox, Graphify's five default questions by keyword | {q['answered']} of {q['of']} | {num(q['mean_tokens'])} | | {q['reduction']}x |")
add("")

add("## Test B. Are the edges in the code?")
add("")
add("The formula above does not check a single answer. This test does: every file-to-file dependency edge of each tool against the import statements of the code.")
add("")
gr = load("fastapi.grimp_check.json")
add(f"Reference: `tools/py_edges.py` reads every `import` with Python's own parser (`ast`) and resolves it to a file. "
    f"Check of the reference itself: the import graph built by grimp {versions['grimp']} (the engine of import-linter) on FastAPI gives {gr['grimp_pairs']:,} file pairs; "
    f"{gr['in_both']:,} of them are the reference's {gr['reference_pairs_between_covered_files']:,} pairs; "
    f"{len(gr['grimp_only_unexplained'])} only in grimp, {len(gr['reference_only'])} only in the reference.")
add("")
add("| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |")
add("|---|---|---|---|---|---|---|---|")


def edge_row(corpus: str, label: str, name: str, suffix: str = "all") -> None:
    s = load(f"{name}.score.{suffix}.json")
    add(f"| {corpus} | {s['reference_pairs']:,} | {label} | {s['tool_edges']:,} | {len(s['tool_edges_wrong_strict'])} | "
        f"{len(s['code_pairs_missing_strict'])} | {s['strict']['precision']:.3f} | {s['strict']['recall']:.3f} |")


edge_row("Karpathy repositories", "Wingbox", "karpathy.wb.dependency")
edge_row("Karpathy repositories", "Graphify, module-level imports", "karpathy.gf.modules")
edge_row("FastAPI, whole tree", "Wingbox", "fastapi.wb.dependency")
edge_row("FastAPI, whole tree", "Graphify, module-level imports", "fastapi.gf.modules")
edge_row("FastAPI, `fastapi/` package", "Wingbox", "fastapi.wb.dependency", "fastapi")
edge_row("FastAPI, `fastapi/` package", "Graphify, module-level imports", "fastapi.gf.modules", "fastapi")
add("")
s = load("fastapi.gf.imports.score.all.json")
add(f"An edge to a package `__init__.py` that Python executes on the way to the named module counts as right and is not required. "
    f"Graphify also links an import to the file that defines the imported name; with those links accepted its FastAPI figures are "
    f"precision {s['either']['precision']:.3f} and recall {s['either']['recall']:.3f} "
    f"({len(s['tool_edges_not_in_code'])} wrong, {len(s['code_pairs_missing_either'])} missing).")
add("")

add("## Test C. The impact command without an agent")
add("")
add("Question: every file that depends on file X directly or through one other file. Five files in four public repositories. "
    "`wingbox impact <file> 2` and `graphify affected \"<file>\" --depth 2` are run as they are and the files they name are compared with the reference list.")
add("")
add("Reference lists (`gold/`), none taken from either tool: Q1 from an own TypeScript resolver (`tools/ts_resolve.py`); Q2 and Q3 from an own Rust resolver "
    "(`tools/rust_resolve.py`), every file of the two lists read against the source; Q4 and Q5 from `tools/py_edges.py`, equal to grimp. "
    "`run.sh` computes the five lists again and stops when one differs from the committed list.")
add("")
add("| Question | Reference files | Wingbox: right / wrong / missing | Wingbox output, tokens | Graphify: right / wrong / missing | Graphify output, tokens |")
add("|---|---|---|---|---|---|")
for row in cc:
    w, g = row["wingbox"], row["graphify"]
    add(f"| {row['question']} `{row['file']}` ({row['repo']}) | {row['reference_files']} | {w['right']} / {w['wrong']} / {w['missing']} | {num(w['tokens_chars_div_4'])} | "
        f"{g['right']} / {g['wrong']} / {g['missing']} | {num(g['tokens_chars_div_4'])} |")
add("")
add(f"Exact list: Wingbox {exact_w} of {len(cc)}, Graphify {exact_g} of {len(cc)}. `graphify affected` works on functions and methods and follows calls and references as well as imports; "
    "restricted to its import relations (`--relation imports_from --relation imports --relation re_exports --relation dynamic_import`) it gives: "
    + "; ".join(f"{r['question']} {r['graphify_import_relations']['right']} / {r['graphify_import_relations']['wrong']} / {r['graphify_import_relations']['missing']}" for r in cc) + ".")
add("")

if agent:
    sm, trials = agent["summary"], agent["trials"]
    names = {"plain": "Plain search (Read, Grep, Glob)", "wb": "Wingbox", "gf": "Graphify"}
    models = ", ".join(sorted({m for t in trials for m in t["models"]}))
    repeats = max(t["repeat"] for t in trials)
    add("## Test D. A coding agent answers the same five questions")
    add("")
    add(f"Claude Code, headless, model alias `sonnet` ({models}), no user settings, skills or connectors. Three setups: built-in search only; search plus the `wingbox` command; "
        "search plus the `graphify` command. Graphs are built beforehand. The prompt names the tool's impact command in one sentence of the same shape for both tools "
        f"(`tools/agent_test.py`). {repeats} repeats per question and setup, four trials at a time, setups interleaved. Score: F1 of the answer list against the reference list. "
        "Tokens: everything the model read and wrote, cache included. Seconds: wall clock of one trial while four run.")
    add("")
    add("| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | Seconds | USD per trial | Refused shell commands |")
    add("|---|---|---|---|---|---|---|---|---|")
    for setup in ("plain", "wb", "gf"):
        s = sm[f"all_{setup}"]
        add(f"| {names[setup]} | {s['trials']} | {s['f1']:.3f} | {s['f1_min']:.3f} | {s['tokens']:,} | {s['turns']:.1f} | {s['seconds']:.1f} | {s['cost_usd']:.3f} | {s['denials']} |")
    add("")
    w, g, p = sm["all_wb"], sm["all_gf"], sm["all_plain"]
    add(f"- Wingbox against Graphify: tokens {pct(w['tokens'], g['tokens'])}, time {pct(w['seconds'], g['seconds'])}, cost {pct(w['cost_usd'], g['cost_usd'])}.")
    add(f"- Wingbox against plain search: tokens {pct(w['tokens'], p['tokens'])}, time {pct(w['seconds'], p['seconds'])}, cost {pct(w['cost_usd'], p['cost_usd'])}.")
    add(f"- Graphify against plain search: tokens {pct(g['tokens'], p['tokens'])}, time {pct(g['seconds'], p['seconds'])}, cost {pct(g['cost_usd'], p['cost_usd'])}.")
    add("")
    add("| Question | Plain: F1 / tokens / s | Wingbox: F1 / tokens / s | Graphify: F1 / tokens / s |")
    add("|---|---|---|---|")
    for row in cc:
        q = row["question"]
        cells = [f"{sm[f'{q}_{s}']['f1']:.3f} / {sm[f'{q}_{s}']['tokens']:,} / {sm[f'{q}_{s}']['seconds']:.1f}" for s in ("plain", "wb", "gf")]
        add(f"| {q} ({row['repo']}, {row['reference_files']} files) | " + " | ".join(cells) + " |")
    add("")
    add(f"Every trial with its answer list is in `results/agent_summary.json`. The {len(trials)} trials cost "
        f"{sum(t['cost_usd'] or 0 for t in trials):.2f} USD in total.")
    add("")

build_path = os.path.join(R, "build_times.json")
if os.path.exists(build_path):
    bt = load("build_times.json")
    info = bt["machine"]
    add("## Test E. Building the graph")
    add("")
    add(f"Seconds from a fresh checkout to a graph: every run starts from a new export of the repository, with no earlier output and no cache, "
        f"and the two tools alternate. Median of {bt['rounds']} runs on a GitHub-hosted runner ({info['system']}, {info['cpu']}, "
        f"{info['logical_cpus']} logical CPUs), workflow `benchmark-build-time`, script `tools/time_builds.py`. {bt['versions']['wingbox']}; "
        f"{bt['versions']['graphify']} with `GRAPHIFY_BACKEND=none`.")
    add("")
    add("`wingbox build .` writes the graph, the report and the map. `graphify extract . --code-only` writes the graph; "
        "`graphify cluster-only . --no-label --no-viz` is the step this benchmark runs before `graphify affected`.")
    add("")
    add("| Corpus | Source files | Wingbox build, s | Graphify extract, s | Graphify cluster, s | Extract / build | Wingbox graph | Graphify graph |")
    add("|---|---|---|---|---|---|---|---|")
    for row in bt["summary"]:
        wg, gg = row["wingbox_graph"], row["graphify_graph"]
        add(f"| {row['corpus']} | {row['source_files']:,} | {row['wingbox_build_wall_s']:.2f} | {row['graphify_extract_wall_s']:.2f} | "
            f"{row['graphify_cluster_wall_s']:.2f} | {row['extract_to_build_wall_ratio']:.1f}x | "
            f"{wg['modules']:,} modules, {wg['import_edges']:,} import edges | {gg['nodes']:,} nodes, {gg['links']:,} links |")
    add("")
    add("The size of the graph is read from the file each build wrote, in every run; the summary stops when two runs of one tool differ. "
        "CPU seconds per step and every single run are in `results/build_times.json`.")
    add("")

add("## Scope")
add("")
add("- Code only. Graphify also reads documents, papers and images with a model; that part is not tested here and Wingbox has no counterpart.")
add("- One kind of question in tests C and D: which files depend on this file. Five questions, three languages (TypeScript, Rust, Python), one agent, one model.")
add("- Tests A, B and C are deterministic and need no model. Test D depends on the model and varies from run to run.")
add("")
add("## Versions and commits")
add("")
add("| Item | Version or commit |")
add("|---|---|")
add(f"| Wingbox | {versions['wingbox']} (PyPI `wingbox`) |")
add(f"| Graphify | {gf} (PyPI `graphifyy`); repository read at commit 18cd78f |")
add(f"| grimp | {versions['grimp']} |")
for repo, sha in commits.items():
    add(f"| {repo} | {sha[:7]} |")
add("")
add("## Reproduce")
add("")
add("```sh")
add("git clone https://github.com/aileron-tech/wingbox && cd wingbox")
add("benchmarks/graphify/run.sh ~/wingbox-bench    # tests A, B, C; needs git, curl and uv; no model, no key")
add("python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench run      # test D: paid Claude Code sessions")
add("python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench report")
add("python3 -I benchmarks/graphify/tools/time_builds.py ~/wingbox-bench             # test E: build time on this machine")
add("```")
add("")
add("`run.sh` installs both tools from PyPI into its own environments, fetches the seven repositories at the commits above, builds both graphs, "
    "computes the reference lists and writes `out/REPORT.md` in the work directory. `WINGBOX_SPEC=<path or requirement>` runs the same tests on another Wingbox.")
add("")
add("| Folder | Content |")
add("|---|---|")
add("| `tools/` | every script of the run; the references use Python's `ast`, grimp and two own resolvers, never Wingbox or Graphify |")
add("| `gold/` | the five reference lists |")
add("| `results/` | the result files of the run this report is written from |")
add("")
open(out_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("written", out_path, len(lines), "lines")
