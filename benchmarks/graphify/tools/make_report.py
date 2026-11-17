"""Assemble the benchmark report from the result files; no number is typed by hand.

Usage: python3 -I make_report.py <results dir> <out README.md>
<results dir> is <work dir>/out after run.sh, or the committed benchmarks/graphify/results.
Test D is included when <results dir>/agent_summary.json exists (tools/agent_test.py report).
Test F is included when <results dir>/build_times.same_machine.json exists (tools/time_builds_same_machine.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

R, out_path = sys.argv[1:3]
# Tools measured after the first three: their lines come from their own result files (tools/other_tools.py).
_spec = importlib.util.spec_from_file_location("other_tools", os.path.join(os.path.dirname(os.path.abspath(__file__)), "other_tools.py"))
other_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(other_tools)
load = lambda name: json.load(open(os.path.join(R, name)))
text = lambda name: open(os.path.join(R, name), encoding="utf-8").read()
num = lambda value: f"{value:,}" if isinstance(value, int) else (f"{value:,.3f}" if value < 10 else f"{value:,.1f}")
pct = lambda new, old: f"{(new - old) / old * 100:+.0f} %"

versions = dict(line.split(None, 1) for line in text("versions.txt").splitlines() if line.strip())
commits = dict(line.split()[:2] for line in text("commits.txt").splitlines() if line.strip())
kf, ff = load("karpathy.formula.json"), load("fastapi.formula.json")
published = load("karpathy.gf_published.json")
cc = load("command_check.json")
cg_cc = {row["question"]: row for row in load("command_check.codegraph.json")}
agent = load("agent_summary.json") if os.path.exists(os.path.join(R, "agent_summary.json")) else None
gf = versions["graphify"]
cgv = versions["codegraph"]
lines: list[str] = []
add = lines.append

exact_w = sum(row["wingbox"]["wrong"] == 0 and row["wingbox"]["missing"] == 0 for row in cc)
exact_g = sum(row["graphify"]["wrong"] == 0 and row["graphify"]["missing"] == 0 for row in cc)
exact_c = sum(row["codegraph"]["wrong"] == 0 and row["codegraph"]["missing"] == 0 for row in cg_cc.values())
kw, kg = kf["wingbox"], kf["graphify"]

_extra = other_tools.names(R)
add("# Wingbox, Graphify, CodeGraph" + "".join(f", {label}" for label in _extra[:-1]) + (f" and {_extra[-1]}" if _extra else "") + " on the same public tests"
    if _extra else "# Wingbox, Graphify and CodeGraph on the same public tests")
add("")
add(f"Wingbox {versions['wingbox']} from PyPI, run of {versions.get('wingbox_date', versions['date'])}. Graphify {gf} from PyPI (`graphifyy`), "
    f"`GRAPHIFY_BACKEND=none` (no model, no key), run of {versions['date']}. Every number below is produced by the scripts in `tools/` "
    "from the files in `results/`; this file is written by `tools/make_report.py`, and `run.sh` reproduces tests A, B and C from nothing.")
add("")
add(f"CodeGraph {cgv} from npm (`@colbymchenry/codegraph`), run of {versions['codegraph_date']}. The three tools ran on the same commits, questions, "
    "reference lists and scoring.")
add("")
add(f"Versions: Wingbox {versions['wingbox']}, Graphify {gf}, CodeGraph {cgv}. Test E (build time on a hosted runner) is the only part still measured with "
    "an earlier Wingbox; its version is named there.")
add("")
for _line in other_tools.intro(R):
    add(_line)
add("## Which tool for which job")
add("")
cg_rows = list(cg_cc.values())
mean_of = lambda tool, rows: sum(row[tool]["tokens_chars_div_4"] for row in rows) / len(rows)
edge = {name: load(f"fastapi.{key}.score.all.json") for name, key in (("w", "wb.dependency"), ("g", "gf.imports"), ("c", "cg.all"))}
wrong_missing = lambda name: f"{len(edge[name]['tool_edges_not_in_code'])} wrong / {len(edge[name]['code_pairs_missing_either'])} missing"
empty_q = [row["question"] for row in cg_rows if not row["codegraph"]["files_named"]]
answered_q = {row["question"] for row in cg_rows if row["codegraph"]["files_named"]}
cg_mean_answered = mean_of("codegraph", [row for row in cg_rows if row["question"] in answered_q])
wb_mean_answered = mean_of("wingbox", [row for row in cc if row["question"] in answered_q])
if agent:
    asm = agent["summary"]
    usd = {setup: asm[f"all_{setup}"]["cost_usd"] for setup in ("plain", "wb", "gf", "cg") if f"all_{setup}" in asm}
    f1 = {setup: asm[f"all_{setup}"]["f1"] for setup in usd}
    label = {"plain": "built-in search", "wb": "Wingbox", "gf": "Graphify", "cg": "CodeGraph"}
    costliest = max(usd, key=usd.get)
add(f"- Choose Wingbox for the exact list of files a change reaches: exact lists {exact_w} of {len(cc)} (Graphify {exact_g}, CodeGraph {exact_c}); "
    f"FastAPI file edges {wrong_missing('w')} (Graphify {wrong_missing('g')}, CodeGraph {wrong_missing('c')})"
    + (f"; agent cost {usd['wb']:.3f} USD per answer at F1 {f1['wb']:.3f} (Graphify {usd['gf']:.3f} at {f1['gf']:.3f}, CodeGraph {usd['cg']:.3f} at {f1['cg']:.3f}, "
       f"built-in search {usd['plain']:.3f} at {f1['plain']:.3f})" if agent else "")
    + ". Per its documentation it also draws an architecture map with layers and boundaries, and runs fully local.")
add(f"- Where the others fall short for this job: `codegraph impact` named no file on {' and '.join(empty_q)} (Rust), and its file graph links imports to the "
    f"files that define the names; `graphify affected` answers average {mean_of('graphify', cc) / mean_of('wingbox', cc):.1f} times the tokens of Wingbox's "
    f"({num(round(mean_of('graphify', cc)))} against {num(round(mean_of('wingbox', cc)))})"
    + (f", and {label[costliest]} costs the most per answer in the agent test ({usd[costliest]:.3f} USD)." if agent else "."))
gf_mean_answered = mean_of("graphify", [row for row in cc if row["question"] in answered_q])
shortest = cg_mean_answered < min(wb_mean_answered, gf_mean_answered)
add("- What the others do better: " + (
    f"CodeGraph's answers are the shortest where it answers ({num(round(cg_mean_answered))} tokens against {num(round(wb_mean_answered))} for Wingbox "
    f"and {num(round(gf_mean_answered))} for Graphify on the same {len(answered_q)} questions). " if shortest else
    f"where it answers, CodeGraph's answers average {num(round(cg_mean_answered))} tokens ({num(round(wb_mean_answered))} for Wingbox, "
    f"{num(round(gf_mean_answered))} for Graphify, same {len(answered_q)} questions). ")
    + "Per its documentation CodeGraph covers more languages "
    "(Swift, Kotlin, C, C++, Dart, Lua and others), keeps its index in sync with file changes automatically and answers symbol and call-flow "
    "questions; per its documentation Graphify also maps documents, PDFs, images and videos.")
for _line in other_tools.which_job(R):
    add(_line)
add("")
add("## Summary")
add("")
add(f"- Graphify's \"71.5x fewer tokens per query\" is the size of the whole corpus divided by the size of one graph answer. "
    f"No answer is checked. On the code of its own example corpus Graphify publishes 8.8x; Graphify {gf} gives "
    f"{kg['default_questions']['reduction_ratio']}x and {kg['published_questions']['reduction_ratio']}x today, and the same formula gives Wingbox "
    f"{kw['analyze_every_module']['reduction_by_mean']}x (test A).")
add(f"- Asked which files depend on a file, `wingbox impact` names the exact list in {exact_w} of {len(cc)} questions, "
    f"`graphify affected` in {exact_g} of {len(cc)}, `codegraph impact` in {exact_c} of {len(cc)} (test C).")
cg_fa, gf_fa, wb_fa = (load(f"fastapi.{name}.score.all.json") for name in ("cg.all", "gf.imports", "wb.dependency"))
add(f"- File-to-file edges of FastAPI against its {cg_fa['reference_pairs']:,} import pairs, a link to the file that defines an imported name counted as right: "
    f"Wingbox {len(wb_fa['tool_edges_not_in_code'])} wrong and {len(wb_fa['code_pairs_missing_either'])} missing, Graphify {len(gf_fa['tool_edges_not_in_code'])} and "
    f"{len(gf_fa['code_pairs_missing_either'])}, CodeGraph {len(cg_fa['tool_edges_not_in_code'])} and {len(cg_fa['code_pairs_missing_either'])} (test B).")
if agent:
    sm = agent["summary"]
    w, g, p = sm["all_wb"], sm["all_gf"], sm["all_plain"]
    add(f"- A coding agent answers the same questions correctly in all three setups. It spends {w['tokens']:,} tokens with Wingbox, "
        f"{p['tokens']:,} with its built-in search alone and {g['tokens']:,} with Graphify: Wingbox {pct(w['tokens'], p['tokens'])} "
        f"against plain search, Graphify {pct(g['tokens'], p['tokens'])} (test D).")
    if "all_cg" in sm:
        c = sm["all_cg"]
        add(f"- With CodeGraph the agent spends {c['tokens']:,} tokens (F1 {c['f1']:.3f}): {pct(c['tokens'], g['tokens'])} against Graphify, "
            f"{pct(c['tokens'], p['tokens'])} against plain search (test D).")
if os.path.exists(os.path.join(R, "build_times.json")):
    rows = load("build_times.json")["summary"]
    big = max(rows, key=lambda row: row["source_files"])
    ratios = [row["extract_to_build_wall_ratio"] for row in rows]
    add(f"- Building the graph of {big['corpus']} ({big['source_files']:,} source files) takes Wingbox {big['wingbox_build_wall_s']:.1f} s "
        f"({load('build_times.json')['versions']['wingbox']}) and Graphify "
        f"{big['graphify_extract_wall_s']:.1f} s on a hosted runner; over the five corpora Graphify's extract takes {min(ratios):.1f} to {max(ratios):.1f} times "
        f"as long as the whole Wingbox build (test E).")
if os.path.exists(os.path.join(R, "build_times.same_machine.json")):
    rows = load("build_times.same_machine.json")["summary"]
    big = max(rows, key=lambda row: row["source_files"])
    add(f"- On one machine, a fresh graph of {big['corpus']} takes Wingbox {big['wingbox_build_wall_s']:.1f} s, Graphify "
        f"{big['graphify_extract_wall_s'] + big['graphify_cluster_wall_s']:.1f} s and CodeGraph {big['codegraph_init_wall_s']:.1f} s; after a one-line edit "
        f"Wingbox {big['wingbox_edit_wall_s']:.1f} s, Graphify {big['graphify_edit_wall_s']:.1f} s and CodeGraph {big['codegraph_edit_wall_s']:.1f} s (test F).")
for _line in other_tools.summary(R):
    add(_line)
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
add("Test A checks a number Graphify publishes; it has no CodeGraph row. The size of CodeGraph's impact answers is in test C.")
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
loaded = gr.get("reference_only_loaded_by_name", [])
add(f"Reference: `tools/py_edges.py` reads every `import` with Python's own parser (`ast`) and resolves it to a file"
    + (", and counts a module loaded by a literal name (`importlib.import_module(\"a.b\")`, `__import__(\"a.b\")`) like an import" if loaded else "")
    + f". Check of the reference itself: the import graph built by grimp {versions['grimp']} (the engine of import-linter) on FastAPI gives {gr['grimp_pairs']:,} file pairs; "
    f"{gr['in_both']:,} of them are the reference's {gr['reference_pairs_between_covered_files']:,} pairs of import statements; "
    f"{len(gr['grimp_only_unexplained'])} only in grimp, {len(gr['reference_only'])} only in the reference"
    + (f"; grimp reads import statements only, so the {len(loaded)} pair of a module loaded by name is not in its graph" if loaded else "") + "."
    + (f" The Wingbox rows of this test were run again with Wingbox {versions['wingbox_test_b']} on {versions['wingbox_test_b_date']}, when the "
       "reference started to count modules loaded by a literal name; the other tools' saved edges were scored again against the same reference."
       if "wingbox_test_b" in versions else ""))
add("")
add("| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |")
add("|---|---|---|---|---|---|---|---|")


def edge_row(corpus: str, label: str, name: str, suffix: str = "all") -> None:
    s = load(f"{name}.score.{suffix}.json")
    add(f"| {corpus} | {s['reference_pairs']:,} | {label} | {s['tool_edges']:,} | {len(s['tool_edges_wrong_strict']):,} | "
        f"{len(s['code_pairs_missing_strict']):,} | {s['strict']['precision']:.3f} | {s['strict']['recall']:.3f} |")


edge_row("Karpathy repositories", "Wingbox", "karpathy.wb.dependency")
edge_row("Karpathy repositories", "Graphify, module-level imports", "karpathy.gf.modules")
edge_row("FastAPI, whole tree", "Wingbox", "fastapi.wb.dependency")
edge_row("FastAPI, whole tree", "Graphify, module-level imports", "fastapi.gf.modules")
edge_row("FastAPI, `fastapi/` package", "Wingbox", "fastapi.wb.dependency", "fastapi")
edge_row("FastAPI, `fastapi/` package", "Graphify, module-level imports", "fastapi.gf.modules", "fastapi")
edge_row("Karpathy repositories", "CodeGraph, file graph", "karpathy.cg.all")
edge_row("FastAPI, whole tree", "CodeGraph, file graph", "fastapi.cg.all")
edge_row("FastAPI, `fastapi/` package", "CodeGraph, file graph", "fastapi.cg.all", "fastapi")
add("")
s = load("fastapi.gf.imports.score.all.json")
add(f"An edge to a package `__init__.py` that Python executes on the way to the named module counts as right and is not required. "
    f"Graphify also links an import to the file that defines the imported name; with those links accepted its FastAPI figures are "
    f"precision {s['either']['precision']:.3f} and recall {s['either']['recall']:.3f} "
    f"({len(s['tool_edges_not_in_code'])} wrong, {len(s['code_pairs_missing_either'])} missing).")
add("")
add("CodeGraph's file graph is the one its file-level queries read (`getFileDependencyPairs`, exported by `tools/cg_graph.js`): two files are linked "
    "when a symbol of one has a resolved edge (import, call, instantiation, reference, inheritance, decorator) to a symbol of the other. An import is "
    "linked to the file that defines the imported name, so the strict rule above counts most re-exported imports as wrong. The same rule "
    "with those links accepted, for the three tools:")
add("")
add("| Corpus | Pairs in the code | Tool | Edges | Wrong | Missing | Precision | Recall |")
add("|---|---|---|---|---|---|---|---|")


def followed_row(corpus: str, label: str, name: str, suffix: str = "all") -> None:
    s = load(f"{name}.score.{suffix}.json")
    add(f"| {corpus} | {s['reference_pairs']:,} | {label} | {s['tool_edges']:,} | {len(s['tool_edges_not_in_code'])} | "
        f"{len(s['code_pairs_missing_either'])} | {s['either']['precision']:.3f} | {s['either']['recall']:.3f} |")


followed_row("Karpathy repositories", "Wingbox", "karpathy.wb.dependency")
followed_row("Karpathy repositories", "CodeGraph, file graph", "karpathy.cg.all")
followed_row("FastAPI, whole tree", "Wingbox", "fastapi.wb.dependency")
followed_row("FastAPI, whole tree", "Graphify, all import links", "fastapi.gf.imports")
followed_row("FastAPI, whole tree", "CodeGraph, file graph", "fastapi.cg.all")
followed_row("FastAPI, `fastapi/` package", "Wingbox", "fastapi.wb.dependency", "fastapi")
followed_row("FastAPI, `fastapi/` package", "CodeGraph, file graph", "fastapi.cg.all", "fastapi")
add("")
ki = load("karpathy.cg.all.score.all.json")
cross_repo = [pair for pair in ki["tool_edges_not_in_code"] if pair[0].split("/")[0] != pair[1].split("/")[0]]
fi = load("fastapi.cg.imports.score.all.json")
add(f"In the Karpathy corpus {len(cross_repo)} of CodeGraph's {len(ki['tool_edges_not_in_code'])} wrong edges join two different repositories "
    "(for example a call in nanoGPT resolved to a class of the same name in minGPT); nothing in one repository imports the other. "
    f"CodeGraph's import edges alone give {fi['tool_edges']:,} edges on FastAPI, {len(fi['tool_edges_not_in_code'])} wrong and "
    f"{len(fi['code_pairs_missing_either'])} missing under the same rule.")
add("")
add("CodeGraph's README publishes a \"measured cross-file coverage\" per language (Python on psf/requests: 100 %): the share of source files "
    "that have at least one resolved dependent. It counts files, not edges, and asks only whether something links to a file: a file with ten real "
    "dependents of which one is found counts as covered, and so does a file whose only dependent is wrong. It cannot see a missing edge into a "
    "file that has another, or an edge that should not exist. The test above checks every edge against the import statements, so the two "
    "numbers measure different things and a 100 % coverage is compatible with wrong and missing edges.")
add("")

add("## Test C. The impact command without an agent")
add("")
add("Question: every file that depends on file X directly or through one other file. Five files in four public repositories. "
    "`wingbox impact <file> 2`, `graphify affected \"<file>\" --depth 2` and `codegraph impact <file name> --file <file> --depth 2` are run as they are "
    "and the files they name are compared with the reference list.")
add("")
add("Reference lists (`gold/`), none taken from either tool: Q1 from an own TypeScript resolver (`tools/ts_resolve.py`); Q2 and Q3 from an own Rust resolver "
    "(`tools/rust_resolve.py`), every file of the two lists read against the source; Q4 and Q5 from `tools/py_edges.py`, whose import statements equal grimp. "
    "`run.sh` computes the five lists again and stops when one differs from the committed list.")
add("")
add("| Question | Reference files | Wingbox: right / wrong / missing | Wingbox output, tokens | Graphify: right / wrong / missing | Graphify output, tokens "
    "| CodeGraph: right / wrong / missing | CodeGraph output, tokens |")
add("|---|---|---|---|---|---|---|---|")
for row in cc:
    w, g, c = row["wingbox"], row["graphify"], cg_cc[row["question"]]["codegraph"]
    add(f"| {row['question']} `{row['file']}` ({row['repo']}) | {row['reference_files']} | {w['right']} / {w['wrong']} / {w['missing']} | {num(w['tokens_chars_div_4'])} | "
        f"{g['right']} / {g['wrong']} / {g['missing']} | {num(g['tokens_chars_div_4'])} | {c['right']} / {c['wrong']} / {c['missing']} | {num(c['tokens_chars_div_4'])} |")
mean_tokens = lambda tool, rows: round(sum(row[tool]["tokens_chars_div_4"] for row in rows) / len(rows))
add(f"| Mean output, tokens | | | {num(mean_tokens('wingbox', cc))} | | {num(mean_tokens('graphify', cc))} | | {num(mean_tokens('codegraph', list(cg_cc.values())))} |")
add("")
add(f"Exact list: Wingbox {exact_w} of {len(cc)}, Graphify {exact_g} of {len(cc)}, CodeGraph {exact_c} of {len(cc)}. `graphify affected` works on functions and methods and follows calls and references as well as imports; "
    "restricted to its import relations (`--relation imports_from --relation imports --relation re_exports --relation dynamic_import`) it gives: "
    + "; ".join(f"{r['question']} {r['graphify_import_relations']['right']} / {r['graphify_import_relations']['wrong']} / {r['graphify_import_relations']['missing']}" for r in cc) + ".")
add("")
answered = [row for row in cg_cc.values() if row["codegraph"]["files_named"]]
empty = [row for row in cg_cc.values() if not row["codegraph"]["files_named"]]
same = [row for row in cc if row["question"] in {r["question"] for r in answered}]
add("`codegraph impact` takes a symbol; a file is a node of CodeGraph's graph, and `--file` picks the node of that path. From a file node it follows "
    "the edges that point at the file itself. " + (
        f"In {' and '.join(row['question'] for row in empty)} (Rust) no edge points at the file node, so the answer names no file. " if empty else "")
    + f"On the {len(answered)} questions it answers, its output averages {num(mean_tokens('codegraph', answered))} tokens against "
    f"{num(mean_tokens('wingbox', same))} for Wingbox. "
    "CodeGraph's file-level graph, walked two steps back from the file as `codegraph affected` walks it before it keeps only test files "
    "(`tools/cg_graph.js`; the file count equals what that command reports as traversed), gives: "
    + "; ".join(f"{q} {r['codegraph_file_dependents']['right']} / {r['codegraph_file_dependents']['wrong']} / {r['codegraph_file_dependents']['missing']}"
                for q, r in sorted(cg_cc.items())) + ".")
add("")

if agent:
    sm, trials = agent["summary"], agent["trials"]
    names = {"plain": "Plain search (Read, Grep, Glob)", "wb": "Wingbox", "gf": "Graphify", "cg": "CodeGraph"}
    setups = [setup for setup in ("plain", "wb", "gf", "cg") if f"all_{setup}" in sm]
    models = ", ".join(sorted({m for t in trials for m in t["models"]}))
    repeats = max(t["repeat"] for t in trials)
    add("## Test D. A coding agent answers the same five questions")
    add("")
    add(f"Claude Code, headless, model alias `sonnet` ({models}), no user settings, skills or connectors. Four setups: built-in search only; search plus the `wingbox` command; "
        "search plus the `graphify` command; search plus the `codegraph` command. Graphs are built beforehand. The prompt names the tool's impact command in one sentence of the same shape for every tool "
        f"(`tools/agent_test.py`). {repeats} repeats per question and setup, four trials at a time. Score: F1 of the answer list against the reference list. "
        "Tokens: everything the model read and wrote, cache included. Seconds: wall clock of one trial while four run.")
    if "all_cg" in sm:
        add("")
        add(f"Plain search and Graphify ran interleaved on {versions['date']}. The Wingbox {versions['wingbox']} trials ran on {versions.get('wingbox_date', versions['date'])} "
            f"and the CodeGraph trials on {versions['codegraph_date']}, each setup on its own, four trials at a time, with the same prompt, model and limits. "
            "Tokens, cost and F1 compare directly; seconds less so, since the other trials running at the same time differ.")
    add("")
    add("| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | Seconds | USD per trial | Refused shell commands |")
    add("|---|---|---|---|---|---|---|---|---|")
    for setup in setups:
        s = sm[f"all_{setup}"]
        add(f"| {names[setup]} | {s['trials']} | {s['f1']:.3f} | {s['f1_min']:.3f} | {s['tokens']:,} | {s['turns']:.1f} | {s['seconds']:.1f} | {s['cost_usd']:.3f} | {s['denials']} |")
    add("")
    w, g, p = sm["all_wb"], sm["all_gf"], sm["all_plain"]
    add(f"- Wingbox against Graphify: tokens {pct(w['tokens'], g['tokens'])}, time {pct(w['seconds'], g['seconds'])}, cost {pct(w['cost_usd'], g['cost_usd'])}.")
    add(f"- Wingbox against plain search: tokens {pct(w['tokens'], p['tokens'])}, time {pct(w['seconds'], p['seconds'])}, cost {pct(w['cost_usd'], p['cost_usd'])}.")
    add(f"- Graphify against plain search: tokens {pct(g['tokens'], p['tokens'])}, time {pct(g['seconds'], p['seconds'])}, cost {pct(g['cost_usd'], p['cost_usd'])}.")
    if "all_cg" in sm:
        c = sm["all_cg"]
        add(f"- CodeGraph against plain search: tokens {pct(c['tokens'], p['tokens'])}, cost {pct(c['cost_usd'], p['cost_usd'])}; against Graphify: tokens "
            f"{pct(c['tokens'], g['tokens'])}, cost {pct(c['cost_usd'], g['cost_usd'])}; Wingbox against CodeGraph: tokens {pct(w['tokens'], c['tokens'])}, "
            f"cost {pct(w['cost_usd'], c['cost_usd'])}.")
    add("")
    short = {"plain": "Plain", "wb": "Wingbox", "gf": "Graphify", "cg": "CodeGraph"}
    add("| Question | " + " | ".join(f"{short[s]}: F1 / tokens / s" for s in setups) + " |")
    add("|---" * (len(setups) + 1) + "|")
    for row in cc:
        q = row["question"]
        cells = [f"{sm[f'{q}_{s}']['f1']:.3f} / {sm[f'{q}_{s}']['tokens']:,} / {sm[f'{q}_{s}']['seconds']:.1f}" for s in setups]
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
    measured = os.path.exists(os.path.join(R, "build_times.same_machine.json"))
    pending = "see test F" if measured else "not measured yet (machine under load)"
    add("| Corpus | Source files | Wingbox build, s | Graphify extract, s | Graphify cluster, s | Extract / build | Wingbox graph | Graphify graph | CodeGraph build, s |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in bt["summary"]:
        wg, gg = row["wingbox_graph"], row["graphify_graph"]
        add(f"| {row['corpus']} | {row['source_files']:,} | {row['wingbox_build_wall_s']:.2f} | {row['graphify_extract_wall_s']:.2f} | "
            f"{row['graphify_cluster_wall_s']:.2f} | {row['extract_to_build_wall_ratio']:.1f}x | "
            f"{wg['modules']:,} modules, {wg['import_edges']:,} import edges | {gg['nodes']:,} nodes, {gg['links']:,} links | {pending} |")
    add("")
    add("The size of the graph is read from the file each build wrote, in every run; the summary stops when two runs of one tool differ. "
        "CPU seconds per step and every single run are in `results/build_times.json`.")
    add("")
    if not measured:
        add("CodeGraph was not part of the hosted-runner run. `tools/time_builds_same_machine.py` times the three tools on one machine, the tools taking turns "
            "and their order rotating from round to round, cold and after a one-line edit; it has not been run on a quiet machine yet.")
        add("")

same_path = os.path.join(R, "build_times.same_machine.json")
if os.path.exists(same_path):
    bt = load("build_times.same_machine.json")
    info, load_avg = bt["machine"], bt["load_1min"]
    add("## Test F. Building the graph, three tools on one machine")
    add("")
    add(f"CodeGraph is not part of the hosted-runner run of test E, so the three tools were timed again on one machine: {info['system']} {info['machine']}, "
        f"{info['cpu']}, {info['logical_cpus']} logical CPUs, {info['memory_gb']:g} GB (`tools/time_builds_same_machine.py`). Median of {bt['rounds']} rounds; "
        "in each round the tools take turns on every corpus and the order of the tools rotates. "
        f"Load average over one minute, read before every step: median {load_avg['median']:.2f}, lowest {load_avg['min']:.2f}, highest {load_avg['max']:.2f}. "
        f"{bt['versions']['wingbox']}; {bt['versions']['graphify']} with `GRAPHIFY_BACKEND=none`; codegraph {bt['versions']['codegraph']}.")
    add("")
    add("Cold: a fresh export of the repository, no earlier output and no cache; `wingbox build .`, `graphify extract . --code-only` followed by "
        "`graphify cluster-only . --no-label --no-viz`, `codegraph init --yes`. After an edit: one line appended to one source file "
        f"(the file of the impact question, `{bt['edited_files']['karpathy']}` for karpathy), then each tool's own command for a changed project on the graph it has just "
        "built: `wingbox build .`, `graphify update .`, `codegraph sync`.")
    add("")
    add("| Corpus | Source files | Wingbox cold, s | Graphify cold, s | CodeGraph cold, s | Wingbox after edit, s | Graphify after edit, s | CodeGraph after edit, s | CodeGraph graph |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in bt["summary"]:
        cg_graph = row["codegraph_graph"]
        add(f"| {row['corpus']} | {row['source_files']:,} | {row['wingbox_build_wall_s']:.2f} | "
            f"{row['graphify_extract_wall_s'] + row['graphify_cluster_wall_s']:.2f} | {row['codegraph_init_wall_s']:.2f} | "
            f"{row['wingbox_edit_wall_s']:.2f} | {row['graphify_edit_wall_s']:.2f} | {row['codegraph_edit_wall_s']:.2f} | "
            f"{cg_graph['files']:,} files, {cg_graph['nodes']:,} nodes, {cg_graph['edges']:,} edges |")
    add("")
    sizes = lambda graph: ", ".join(f"{value:,} {key.replace('_', ' ')}" for key, value in graph.items())
    cpu = lambda tool, step: sum(row[f"{tool}_{step}_cpu_s"] for row in bt["summary"])
    wall = lambda tool, step: sum(row[f"{tool}_{step}_wall_s"] for row in bt["summary"])
    add(f"Summed over the five corpora, cold: Wingbox {wall('wingbox', 'build'):.1f} s wall and {cpu('wingbox', 'build'):.1f} s CPU; Graphify "
        f"{wall('graphify', 'extract') + wall('graphify', 'cluster'):.1f} s wall and {cpu('graphify', 'extract') + cpu('graphify', 'cluster'):.1f} s CPU; CodeGraph "
        f"{wall('codegraph', 'init'):.1f} s wall and {cpu('codegraph', 'init'):.1f} s CPU. CodeGraph and Graphify spread the work over several cores, "
        "Wingbox runs on one. The size of every graph is read after every step; the summary stops when two cold builds of one tool, or two of its builds "
        "after the edit, differ. "
        + " ".join(f"After the edit {'CodeGraph' if tool == 'codegraph' else tool.capitalize()} wrote a graph of {corpus} different from its cold build "
                   f"({sizes(before)} against {sizes(after)})."
                   for row in bt["summary"] for tool, corpus, before, after in
                   [(t, row["corpus"], row[f"{t}_edit_graph"], row[f"{t}_graph"]) for t in ("wingbox", "graphify", "codegraph")]
                   if before != after)
        + " Every single run is in `results/build_times.same_machine.json`.")
    add("")

add("## Why the numbers differ")
add("")
add("The three tools are built for different questions. Tests B and C ask one question, which files depend on a file through its import "
    "statements, and the reference lists are built from import statements. Each tool's design explains its numbers on that question; "
    "the design points below are taken from each tool's own documentation.")
add("")
add("**Wingbox** is built for this question. Its README describes the blast radius of a change as \"everything that depends, directly or "
    "transitively, on the code you are about to edit\", computed from the reverse dependencies of a file-level code graph, and `wingbox impact "
    "<file>` prints the dependents of a file by depth. Wingbox resolves each import statement to a file, which is also how the reference lists "
    "are made, so its answers match them. The benchmark measures the question Wingbox is built for; it does not measure symbol-level or "
    "flow questions.")
add("")
gi = load("fastapi.gf.imports.score.all.json")
q4 = next(row for row in cc if row["question"] == "Q4")
add("**Graphify** builds a knowledge graph of a whole project: \"code, docs, PDFs, images, videos\" (Graphify README). In code it records "
    "functions, methods and classes and links them through `calls`, `imports` and `inherits`, and tags every edge `EXTRACTED` (read from "
    "the source) or `INFERRED` (resolved by Graphify). `graphify affected` is a \"reverse traversal to find nodes impacted by X\" over all "
    "these relations, a wider question than which files import X: it names files that call or reference something in X, which is why Q4 "
    f"lists {q4['graphify']['wrong']} files beyond the import statements, and {q4['graphify_import_relations']['wrong']} when the same command follows only "
    "its import relations (test C). Graphify also links an import to the file that defines the imported name and resolves some names across files; that adds and "
    f"drops some file pairs ({len(gi['tool_edges_not_in_code'])} and {len(gi['code_pairs_missing_either'])} on FastAPI, test B). "
    "Its documents, papers and images are outside these tests.")
add("")
add("**CodeGraph** builds a symbol-level graph: \"nodes (functions, classes, methods) and edges (calls, imports, extends, implements)\" stored "
    "in SQLite, with references resolved after extraction (\"function calls -> definitions, imports -> source files\", CodeGraph README, "
    "How It Works). Two files are linked when a symbol of one reaches a symbol of the other, and an import is linked to the file that "
    "defines the imported name. A file-to-file check against import statements therefore counts many of its links as extra although they "
    "are correct in its model; the second table of test B accepts those links. Its `impact` command takes a symbol (\"Analyze what code is "
    "affected by changing a symbol\"), so a file question needs `codegraph impact <file name> --file <path>`; from a file node it follows the "
    "edges into that node, and in the two Rust repositories no edge points at the file node, so Q2 and Q3 returned no file. Its file-level "
    "command `codegraph affected` is documented for test files (\"Find test files affected by changed source files\"). When it answers, its "
    "answers are the shortest of the three (test C). Its main agent tool, `codegraph_explore`, returns source code and call paths for "
    "symbols, which these tests do not measure.")
add("")

for _line in other_tools.why(R) + other_tools.sections(R):
    add(_line)

add("## Scope")
add("")
add("- Code only. Graphify also reads documents, papers and images with a model; that part is not tested here and Wingbox has no counterpart.")
add("- CodeGraph also answers questions about symbols and call flows (`codegraph explore`, `callers`, `impact <symbol>`); these tests ask about files only.")
add("- One kind of question in tests C and D: which files depend on this file. Five questions, three languages (TypeScript, Rust, Python), one agent, one model.")
add("- Tests A, B and C are deterministic and need no model. Test D depends on the model and varies from run to run.")
add("")
add("## Versions and commits")
add("")
add("| Item | Version or commit |")
add("|---|---|")
add(f"| Wingbox | {versions['wingbox']} (PyPI `wingbox`) |")
add(f"| Graphify | {gf} (PyPI `graphifyy`); repository read at commit 18cd78f |")
add(f"| CodeGraph | {cgv} (npm `@colbymchenry/codegraph`) |")
add(f"| grimp | {versions['grimp']} |")
for repo, sha in commits.items():
    add(f"| {repo} | {sha[:7]} |")
add("")
add("## Reproduce")
add("")
add("```sh")
add("git clone https://github.com/aileron-tech/wingbox && cd wingbox")
add("benchmarks/graphify/run.sh ~/wingbox-bench    # tests A, B, C; needs git, curl, uv and npm; no model, no key")
add("python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench run      # test D: paid Claude Code sessions")
add("python3 -I benchmarks/graphify/tools/agent_test.py ~/wingbox-bench report")
add("python3 -I benchmarks/graphify/tools/time_builds.py ~/wingbox-bench             # test E: build time on this machine")
add("python3 -I benchmarks/graphify/tools/time_builds_same_machine.py ~/wingbox-bench # test F: the three tools, cold and after an edit")
add("```")
add("")
add("`run.sh` installs Wingbox and Graphify from PyPI into their own environments and CodeGraph from npm into its own folder, fetches the seven repositories "
    "at the commits above, builds the three graphs, computes the reference lists and writes `out/REPORT.md` in the work directory. CodeGraph runs with a "
    "private HOME, no daemon and no file watcher. `WINGBOX_SPEC=<path or requirement>` runs the same tests on another Wingbox.")
add("")
add("| Folder | Content |")
add("|---|---|")
add("| `tools/` | every script of the run; the references use Python's `ast`, grimp and two own resolvers, never Wingbox, Graphify or CodeGraph |")
add("| `gold/` | the five reference lists |")
add("| `results/` | the result files of the run this report is written from |")
add("")
open(out_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("written", out_path, len(lines), "lines")
