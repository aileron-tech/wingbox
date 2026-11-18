"""Report sections for tools measured after the first run, one section per tool, from its own result files.

make_report.py adds, for every tool listed here whose results are committed: one line to the title (names()),
one bullet to "Which tool for which job" (which_job()), one bullet to "Summary" (summary()), one paragraph to
"Why the numbers differ" (why()), and its own section before "Scope" (sections()). A tool is listed in
TOOLS with the names of its result files; a tool whose command-check file is missing is skipped, so the
section appears only once its results are committed. Nothing here changes the Wingbox or Graphify numbers.

Result files of a tool <name> with prefix <p> (see TOOLS):
  command_check.<name>.json                   test C rows (oi_command_check.py)
  <corpus>.<p>.<kind>.score.<scope>.json      test B scores (score_edges.py), kind in KINDS
  build_times.<name>.json                     test E on one machine, the tools interleaved (time_builds_tools.py)
  ../../impact/results/command.<name>.json    the 100 questions of benchmarks/impact (its tools/oi_impact100.py)
  versions.<name>.txt                         "<name> <version>"
"""
from __future__ import annotations

import json
import os

TOOLS = {
    "ontoindex": {
        "label": "OntoIndex",
        "prefix": "oi",
        "source": "GitHub release asset `ontoindex-{version}.tgz` (github.com/ontograph/ontoindex, AGPL-3.0), checked against its sha256 and installed "
                  "without lifecycle scripts in a Linux container (`tools/oi.Dockerfile`, `tools/oi_run.sh`); every OntoIndex step runs with `--network none`. "
                  "Its `analyze` takes a native file lock that exists only on Linux, so it does not index on macOS. Not yet measured: build time (`tools/time_builds_tools.py`, Wingbox, Graphify and OntoIndex interleaved, cold and after a one-line edit); the script is in place.",
        "kinds": {"imports": "IMPORTS edges between files", "all": "every relation its impact follows, lifted to files"},
        "command": "`ontoindex impact File:<file> --depth 2`",
        "variants": {"ontoindex": "default (test files left out)", "ontoindex_include_tests": "with `--include-tests`, as in its README",
                     "ontoindex_file_imports": "its file-level IMPORTS edges, two steps back (`ontoindex cypher`)"},
        "agent_date": "2026-11-18",
        "cold": [("analyze", "`analyze`")],
        "edit": [("edit", "`analyze` again"), ("edit_overlay", "`analyze --dirty-overlay`")],
    },
}
OTHERS = {"wingbox": ("Wingbox", [("build", "`build`")], [("edit", "`build`")]),
          "graphify": ("Graphify", [("extract", "`extract`"), ("cluster", "`cluster-only`")], [("edit", "`update`")])}


def _load(results: str, name: str):
    path = os.path.join(results, name)
    return json.load(open(path)) if os.path.exists(path) else None


def _section(results: str, name: str, spec: dict) -> list[str]:
    lines: list[str] = []
    add = lines.append
    cc = _load(results, f"command_check.{name}.json")
    if cc is None:
        return lines
    version_file = os.path.join(results, f"versions.{name}.txt")
    version = open(version_file).read().split()[1] if os.path.exists(version_file) else "?"
    label, prefix = spec["label"], spec["prefix"]
    add(f"## {label}")
    add("")
    add(f"{label} {version}: " + spec["source"].format(version=version))
    add("")
    add(f"Test B, edges against the import statements (same reference and scoring as above):")
    add("")
    add("| Corpus | Pairs in the code | Edges | Wrong | Missing | Precision | Recall |")
    add("|---|---|---|---|---|---|---|")
    for corpus, title, scope in (("karpathy", "Karpathy repositories", "all"), ("fastapi", "FastAPI, whole tree", "all"), ("fastapi", "FastAPI, `fastapi/` package", "fastapi")):
        for kind, kind_label in spec["kinds"].items():
            s = _load(results, f"{corpus}.{prefix}.{kind}.score.{scope}.json")
            if s is None:
                add(f"| {title} | | {label}, {kind_label}: not measured | | | | |")
                continue
            add(f"| {title} | {s['reference_pairs']:,} | {label}, {kind_label}: {s['tool_edges']:,} | {len(s['tool_edges_wrong_strict'])} | "
                f"{len(s['code_pairs_missing_strict'])} | {s['strict']['precision']:.3f} | {s['strict']['recall']:.3f} |")
    add("")
    add(f"Test C, the five questions: {spec['command']}, the files of the nodes it names against the reference list.")
    add("")
    variants = [v for v in spec["variants"] if v in cc[0]]
    sized = [v for v in variants if "tokens_chars_div_4" in cc[0][v]]
    add("| Question | Reference files | " + " | ".join(f"{spec['variants'][v]}: right / wrong / missing" for v in variants)
        + " | " + " | ".join(f"output, tokens ({spec['variants'][v]})" for v in sized) + " |")
    add("|---|---|" + "---|" * (len(variants) + len(sized)))
    for row in cc:
        cells = [f"{row[v]['right']} / {row[v]['wrong']} / {row[v]['missing']}" for v in variants]
        cells += [f"{row[v]['tokens_chars_div_4']:,}" for v in sized]
        add(f"| {row['question']} ({row['repo']}) | {row['reference_files']} | " + " | ".join(cells) + " |")
    add("")
    exact = {v: sum(r[v]["wrong"] == 0 and r[v]["missing"] == 0 for r in cc) for v in variants}
    add("Exact list: " + "; ".join(f"{spec['variants'][v]} {exact[v]} of {len(cc)}" for v in variants) + ".")
    add("")
    hundred = _load(os.path.join(results, "..", "..", "impact", "results"), f"command.{name}.json")
    if hundred:
        s = hundred["summary"][name]
        low, high = s["exact_interval_pct"]
        add(f"The 100 questions of `benchmarks/impact` ({spec['command']} with `--include-tests --repo <repo> --read-mode committed`, scored as there): complete and nothing extra in "
            f"{s['exact']} of {s['questions']} (95 % interval {low} to {high} %), a required file missing in {s['with_missing']}, "
            f"median answer {s['median_tokens']:,.0f} tokens; Wingbox {hundred['summary']['wingbox_committed']['exact']} of 100, median "
            f"{hundred['summary']['wingbox_committed']['median_tokens']:,.0f} tokens. Every row is in `benchmarks/impact/results/command.{name}.json`.")
        add("")
        wb_rows = json.load(open(os.path.join(results, "..", "..", "impact", "results", "command.json")))
        ratio = _load(os.path.join(results, "..", "..", "impact", "results"), "context_ratio.json")
        wb_ratio = {r["repo"]: r.get("ratio") for r in (ratio or {}).get("repos", [])}
        add(f"| Repository | Questions | {label}: complete | {label}: a required file missing | {label}: median answer, tokens | {label}: context ratio | Wingbox: complete | Wingbox: median answer, tokens | Wingbox: context ratio |")
        add("|---|---|---|---|---|---|---|---|---|")
        for repo, r in s["per_repo"].items():
            mine = sorted(x["wingbox"]["tokens"] for x in wb_rows if x["repo"] == repo)
            median = (mine[len(mine) // 2] + mine[(len(mine) - 1) // 2]) / 2
            add(f"| {repo} | {r['questions']} | {r['exact']} | {r['with_missing']} | {r['median_tokens']:,.0f} | {r['context_ratio']:,.0f}x | "
                f"{sum(x['wingbox']['exact'] for x in wb_rows if x['repo'] == repo)} | {median:,.0f} | {wb_ratio.get(repo) or 0:,}x |")
        add("")
        add("Context ratio: Graphify's corpus estimate of the repository divided by the mean answer, as in `benchmarks/impact` (`tools/context_ratio.py`); "
            "a short answer that misses files gives a high ratio, so read it next to the complete answers.")
        add("")
    agent = _load(results, f"agent_summary.{name}.json")
    base = _load(results, "agent_summary.json")
    if agent and base:
        a, b = agent["summary"], base["summary"]
        key = f"all_{spec['prefix']}"
        add(f"Test D, the agent of test D with the `{name}` command (`tools/{spec['prefix']}_agent_test.py`: same prompt, model alias, flags, budget, "
            f"{a[key]['trials']} trials, run of {spec['agent_date']}; the other rows are the committed test D):")
        add("")
        add("| Setup | Trials | F1 mean | F1 lowest | Tokens | Turns | USD per trial | Refused shell commands |")
        add("|---|---|---|---|---|---|---|---|")
        for setup, title in (("plain", "Plain search"), ("wb", "Wingbox"), ("gf", "Graphify"), ("cg", "CodeGraph"), (spec["prefix"], label)):
            src = a if setup == spec["prefix"] else b
            r = src.get(f"all_{setup}")
            if r:
                add(f"| {title} | {r['trials']} | {r['f1']:.3f} | {r['f1_min']:.3f} | {r['tokens']:,} | {r['turns']:.1f} | {r['cost_usd']:.3f} | {r['denials']} |")
        add("")
        add(f"Every trial is in `results/agent_summary.{name}.json`.")
        add("")
    bt = _load(results, f"build_times.{name}.json")
    if bt:
        m = bt["machine"]
        tools = [t for t in OTHERS if any(k.startswith(t + "_") for k in bt["summary"][0])] + [name]
        steps = []
        for t in tools:
            title, cold, edit = (OTHERS[t] if t in OTHERS else (label, spec["cold"], spec["edit"]))
            steps += [(t, s, f"{title} {d}, s") for s, d in cold + edit]
        add(f"Test E, build time on one machine: median of {bt['rounds']} rounds, the tools interleaved in one container "
            f"({m['system']} {m['machine']}, {m['cpu']}, {m['logical_cpus']} logical CPUs, {m['memory_gb']} GB), a fresh export of the corpus for every cold build, "
            "then one appended line in the file of the question and each tool's command for a changed project (`tools/time_builds_tools.py`). "
            "Load: the median 1-minute load average before the steps.")
        add("")
        add("| Corpus | Source files | Load | " + " | ".join(h for _, _, h in steps) + " |")
        add("|---|---|---|" + "---|" * len(steps))
        for row in bt["summary"]:
            cells = [f"{row[f'{t}_{s}_wall_s']:.2f}" if f"{t}_{s}_wall_s" in row else "" for t, s, _ in steps]
            add(f"| {row['corpus']} | {row['source_files']:,} | {row['load_1min_median']:.1f} | " + " | ".join(cells) + " |")
        add("")
    return lines


def sections(results: str) -> list[str]:
    lines: list[str] = []
    for name, spec in TOOLS.items():
        lines += _section(results, name, spec)
    return lines


def _present(results: str) -> list[tuple[str, dict, list]]:
    out = []
    for name, spec in TOOLS.items():
        cc = _load(results, f"command_check.{name}.json")
        if cc is not None:
            out.append((name, spec, cc))
    return out


def _version(results: str, name: str) -> str:
    path = os.path.join(results, f"versions.{name}.txt")
    return open(path).read().split()[1] if os.path.exists(path) else "?"


def _exact(cc: list, variant: str) -> int:
    return sum(row[variant]["wrong"] == 0 and row[variant]["missing"] == 0 for row in cc)


def names(results: str) -> list[str]:
    """Labels of the extra tools whose results are committed, for the title."""
    return [spec["label"] for _, spec, _ in _present(results)]


def _ontoindex_facts(results: str, cc: list) -> dict:
    rows = {row["question"]: row for row in cc}
    edges = _load(results, "fastapi.oi.imports.score.all.json")
    wb = {row["question"]: row["wingbox"] for row in _load(results, "command_check.json")}
    gf = {row["question"]: row["graphify"] for row in _load(results, "command_check.json")}
    main = "ontoindex_include_tests"
    answered = [q for q, row in rows.items() if row[main]["files_named"]]
    return {
        "rows": rows, "edges": edges, "main": main, "answered": answered,
        "empty": [q for q in rows if q not in answered],
        "exact": _exact(cc, main), "exact_default": _exact(cc, "ontoindex"), "n": len(cc),
        "mean_answered": sum(rows[q][main]["tokens_chars_div_4"] for q in answered) / len(answered),
        "wb_mean_answered": sum(wb[q]["tokens_chars_div_4"] for q in answered) / len(answered),
        "gf_mean_answered": sum(gf[q]["tokens_chars_div_4"] for q in answered) / len(answered),
        "q4_gf_wrong": gf["Q4"]["wrong"],
    }


def _hundred(results: str, name: str):
    data = _load(os.path.join(results, "..", "..", "impact", "results"), f"command.{name}.json")
    return data["summary"][name] if data else None


def which_job(results: str) -> list[str]:
    lines = []
    for name, spec, cc in _present(results):
        if name != "ontoindex":
            continue
        f = _ontoindex_facts(results, cc)
        e, q4 = f["edges"], f["rows"]["Q4"][f["main"]]
        lines.append(
            f"- OntoIndex {_version(results, name)}: exact lists {f['exact']} of {f['n']} with `--include-tests` ({f['exact_default']} of {f['n']} with its default, which leaves "
            f"test files out); FastAPI file edges {len(e['tool_edges_not_in_code'])} wrong / {len(e['code_pairs_missing_either'])} missing; no file on "
            f"{' and '.join(f['empty'])} (Rust)"
            + (lambda h: f"; complete lists in {h['exact']} of the {h['questions']} questions of `benchmarks/impact` (Wingbox {h.get('wb_exact', 100)})" if h else "")(_hundred(results, name))
            + f". Where it does better: on Q4 it names {q4['wrong']} files beyond the reference list against Graphify's {f['q4_gf_wrong']}, "
            f"and on {f['rows']['Q1']['repo']} (TypeScript) its list is exact. Per its documentation it records calls, inheritance, routes, execution flows and "
            "clusters, maps a git diff to the symbols it touches and serves a web UI; its indexing runs on Linux only."
            + (lambda a, b: f" In the agent test it costs {a['cost_usd']:.3f} USD per answer at F1 {a['f1']:.3f} and {a['tokens']:,} tokens, fewer tokens than "
               f"Graphify ({b['all_gf']['tokens']:,}) and CodeGraph ({b['all_cg']['tokens']:,}), more than Wingbox ({b['all_wb']['tokens']:,})." if a and b else "")(
                (_load(results, f"agent_summary.{name}.json") or {}).get("summary", {}).get("all_oi"), (_load(results, "agent_summary.json") or {}).get("summary")))
    return lines


def summary(results: str) -> list[str]:
    lines = []
    for name, spec, cc in _present(results):
        if name != "ontoindex":
            continue
        f = _ontoindex_facts(results, cc)
        e = f["edges"]
        lines.append(
            f"- OntoIndex {_version(results, name)}, `ontoindex impact File:<file> --depth 2 --include-tests` names the exact list in "
            f"{f['exact']} of {f['n']} questions"
            + (lambda h: f" and the complete list in {h['exact']} of {h['questions']} questions of `benchmarks/impact`" if h else "")(_hundred(results, name))
            + f"; FastAPI file edges {len(e['tool_edges_not_in_code'])} wrong and {len(e['code_pairs_missing_either'])} missing "
            f"(precision {e['strict']['precision']:.3f}, recall {e['strict']['recall']:.3f})"
            + (lambda a: f"; with OntoIndex the agent spends {a['tokens']:,} tokens at F1 {a['f1']:.3f} (test D)." if a else ".")(
                (_load(results, f"agent_summary.{name}.json") or {}).get("summary", {}).get("all_oi")))
    return lines


def why(results: str) -> list[str]:
    lines = []
    for name, spec, cc in _present(results):
        if name != "ontoindex":
            continue
        f = _ontoindex_facts(results, cc)
        e, rows, main = f["edges"], f["rows"], f["main"]
        lines.append(
            "**OntoIndex** is a fork of GitNexus (its NOTICE: \"This product includes code originally developed as GitNexus\"). It builds a symbol-level "
            "graph stored in LadybugDB, a fork of Kuzu: files, folders, functions, classes, methods, routes, plus execution-flow and cluster nodes, linked "
            "through one relation table whose type is IMPORTS, CALLS, EXTENDS, IMPLEMENTS and others, each edge with a confidence. `ontoindex impact` "
            "(\"Blast radius analysis: what breaks if you change a symbol\") walks these relations from a symbol; a file is the node `File:<path>`, and "
            "the walk follows the edges into that node, which for imports are file-to-file IMPORTS edges. In TypeScript and Python its answers are "
            f"close to the import lists ({rows['Q1']['question']} exact, {rows['Q5']['question']} {rows['Q5'][main]['missing']} missing). In the two Rust "
            f"repositories no edge from another file reaches the target file or its symbols, so {' and '.join(f['empty'])} returned no file; in immich-server, whose imports go through the tsconfig alias `src/*` "
            "and name `.js` files, none of the 20 questions got a complete list. On FastAPI it "
            "links some standard-library imports to repository files of the same name (`import gzip` to `fastapi/middleware/gzip.py`) and leaves out "
            f"imports of a submodule through its package (`from fastapi import routing`): {len(e['tool_edges_not_in_code'])} and "
            f"{len(e['code_pairs_missing_either'])} pairs (test B). By default `impact` leaves test files out; its README example adds `--include-tests`, "
            "which the main rows use. Its answers are JSON with the depth, relation, confidence and provenance of every node, "
            f"{f['mean_answered'] / f['wb_mean_answered']:.1f} times the tokens of Wingbox's on the questions it answers. Its `analyze` takes a native "
            "file lock built only for Linux, so it ran in a Linux container.")
        lines.append("")
    return lines


def intro(results: str) -> list[str]:
    lines = []
    for name, spec, cc in _present(results):
        lines += [f"{spec['label']} {_version(results, name)}, run of 2026-11-17 in a Linux container (its indexing runs only on Linux), on the same commits, "
                  "questions, reference lists and scoring; build time not yet measured; its own section is at the end.", ""]
    return lines
