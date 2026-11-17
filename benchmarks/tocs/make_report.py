"""Write benchmarks/tocs/README.md from the result files; no number is typed by hand except the paper's.

Usage: python3 -I make_report.py <results dir> <out README.md>
"""
from __future__ import annotations

import json
import os
import sys

R, out_path = sys.argv[1:3]
load = lambda name: json.load(open(os.path.join(R, name)))
text = lambda name: open(os.path.join(R, name)).read().strip()
before, after = load("scores.before.json"), load("scores.after.json")
version_before, version_after = text("version.before.txt").split()[-1], text("version.after.txt").split()[-1]
tocs_commit = text("commits.txt").split()[-1]
if version_after == version_before:
    version_before, version_after = f"{version_before} (release)", f"{version_after} (unreleased changes)"
TYPES = ("IMPORTS", "CALLS_API", "REGISTRY_WIRES", "DATA_FLOWS_TO")
SHIPPED_MEDIUM = ("codebase_42", "codebase_123", "codebase_999")
SHIPPED_LARGE = ("large_42", "large_123", "large_999")

# Medium codebases, budget 20 (paper, Tables 2 and 3: Dep F1, precision, recall; recall per edge type, aggregated).
PAPER_ACTIVE = [
    ("GPT-5.3-Codex", 0.676, 0.782, 0.597, (0.69, 0.15, 1.00, 0.31)),
    ("Claude Sonnet 4.6", 0.664, 0.983, 0.502, (0.56, 0.15, 1.00, 0.00)),
    ("Config-Aware (rule baseline)", 0.577, 0.736, 0.475, (0.58, 0.00, 1.00, 0.00)),
]
# Paper, Table 4 (medium) and Table 6 (large, one model): Dep F1 when the model is given every file at once.
PAPER_PASSIVE_FULL = [("GPT-5.3-Codex", 0.457, None), ("Gemini 2.5 Flash", 0.696, 0.616)]

by_name = {label: {row["codebase"]: row for row in rows} for label, rows in (("before", before), ("after", after))}
lines: list[str] = []
add = lines.append


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def summary(label: str, names: tuple[str, ...] | list[str], key: str) -> str:
    values = [by_name[label][name][key] for name in names]
    return f"{mean(values):.3f} (+/-{(max(values) - min(values)) / 2:.3f})"


def pooled(label: str, names, edge_type: str) -> tuple[int, int, int]:
    rows = [by_name[label][name]["per_type"][edge_type] for name in names]
    return sum(r["true"] for r in rows), sum(r["predicted"] for r in rows), sum(r["gold"] for r in rows)


generated = sorted(name for name in by_name["after"] if name.startswith("gen_"))
groups = [("Shipped medium (the paper's 3)", list(SHIPPED_MEDIUM)), ("Shipped large", list(SHIPPED_LARGE)),
          (f"Generated ({len(generated)})", generated)]

add("# Wingbox on Theory of Code Space (ToCS)")
add("")
add(f"[Theory of Code Space](https://arxiv.org/abs/2603.00601) (ToCS) generates Python codebases with a known "
    f"architecture and scores a map of their module dependencies against the ground truth, by edge type. This "
    f"file compares Wingbox {version_before} with Wingbox {version_after} on ToCS commit `{tocs_commit[:7]}`. "
    f"It is written by `make_report.py` from the files in `results/`; `run.sh` reproduces everything from nothing.")
add("")
add("## What is compared")
add("")
add("ToCS agents explore a codebase with a budget of 20 actions (open a file, list a folder, search, inspect a "
    "symbol) and write down what they believe; Wingbox reads every file, with no model and no budget. Its "
    "condition is closest to the paper's passive-full condition, where a model is given every file at once, "
    "not to the active condition of the headline table. The numbers below place Wingbox next to the paper's "
    "numbers; they do not rank it against the models.")
add("")
add("Scoring is ToCS's own: `edge_prf` of `metrics/map_accuracy.py`, precision, recall and F1 over "
    "(source, target, type) triples of the final map. Wingbox facts become ToCS types by meaning:")
add("")
add("| Wingbox fact | ToCS type | Meaning |")
add("|---|---|---|")
add("| `imports` | IMPORTS | an import statement, or a literal module name given to `importlib` |")
add("| `registry-wires` | REGISTRY_WIRES | a module loaded by a name read from a JSON, YAML or TOML file beside the loader |")
add("| `calls` | CALLS_API | a function or method of the target called by the source |")
add("| `data-flow :pipeline` | DATA_FLOWS_TO | the target step of a configured pipeline consumes the output of the source step |")
add("| `calls-api`, `data-flow :validated`, `refs` | none | implements a base class; passes data to a validation module; uses a name |")
add("")

add("## Summary")
add("")
add("Dep F1, mean over the codebases of a group (+/- half the range), and the edges of each type found (true of gold, pooled):")
add("")
add(f"| Codebases | Wingbox {version_before} | Wingbox {version_after} | P | R | IMPORTS | CALLS_API | REGISTRY_WIRES | DATA_FLOWS_TO |")
add("|---|---|---|---|---|---|---|---|---|")
for title, names in groups:
    cells = []
    for edge_type in TYPES:
        true, _predicted, gold = pooled("after", names, edge_type)
        cells.append(f"{true} of {gold}")
    add(f"| {title} | {summary('before', names, 'f1')} | {summary('after', names, 'f1')} | "
        f"{summary('after', names, 'precision')} | {summary('after', names, 'recall')} | " + " | ".join(cells) + " |")
add("")
add("The same three medium codebases in the paper (budget 20, active exploration), with Wingbox's row for comparison:")
add("")
add("| Method | Condition | Dep F1 | P | R | Recall IMPORTS | CALLS_API | REGISTRY_WIRES | DATA_FLOWS_TO |")
add("|---|---|---|---|---|---|---|---|---|")
recalls = []
for edge_type in TYPES:
    true, _predicted, gold = pooled("after", SHIPPED_MEDIUM, edge_type)
    recalls.append(f"{true / gold:.2f}")
add(f"| Wingbox {version_after} | reads every file, no model | {summary('after', SHIPPED_MEDIUM, 'f1').split()[0]} | "
    f"{summary('after', SHIPPED_MEDIUM, 'precision').split()[0]} | {summary('after', SHIPPED_MEDIUM, 'recall').split()[0]} | "
    + " | ".join(recalls) + " |")
for name, f1, precision, recall, per_type in PAPER_ACTIVE:
    add(f"| {name} | 20 actions | {f1:.3f} | {precision:.3f} | {recall:.3f} | " + " | ".join(f"{v:.2f}" for v in per_type) + " |")
add("")
add("Paper, every file given at once (passive-full): " + "; ".join(
    f"{name} {medium:.3f} on medium" + (f", {large:.3f} on large" if large is not None else "")
    for name, medium, large in PAPER_PASSIVE_FULL) + ".")
add("")

add("## Every codebase")
add("")
add(f"| Codebase | Tier | Modules | Dep F1 {version_before} | Dep F1 {version_after} | P | R |")
add("|---|---|---|---|---|---|---|")
for title, names in groups:
    for name in names:
        b, a = by_name["before"][name], by_name["after"][name]
        add(f"| {name} | {a['complexity']} | {a['modules']} | {b['f1']:.3f} | {a['f1']:.3f} | {a['precision']:.3f} | {a['recall']:.3f} |")
add("")
add("Per edge type, pooled over all codebases (true / predicted / gold):")
add("")
add(f"| Type | {version_before} | {version_after} |")
add("|---|---|---|")
everything = [name for _title, names in groups for name in names]
for edge_type in TYPES:
    cells = [pooled(label, everything, edge_type) for label in ("before", "after")]
    add(f"| {edge_type} | " + " | ".join(f"{t} / {p} / {g}" for t, p, g in cells) + " |")
add("")

add("## What Wingbox still gets wrong")
add("")
for edge_type in TYPES:
    missed = [(name, pair) for name in everything for pair in by_name["after"][name]["per_type"][edge_type]["missed"]]
    extra = [(name, pair) for name in everything for pair in by_name["after"][name]["per_type"][edge_type]["extra"]]
    if not missed and not extra:
        continue
    add(f"**{edge_type}**: {len(missed)} missed, {len(extra)} not in the ground truth. Examples from `large_42`:")
    add("")
    for word, items in (("missed", missed), ("not in the ground truth", extra)):
        sample = [pair for name, pair in items if name == "large_42"][:4]
        for source, target in sample:
            add(f"- {word}: `{source}` -> `{target}`")
    add("")
diag = {key: sum(row["diagnostics"][key] for row in after) for key in after[0]["diagnostics"]}
missed_calls = sum(row["per_type"]["CALLS_API"]["gold"] - row["per_type"]["CALLS_API"]["true"] for row in after)
extra_imports = sum(len(row["per_type"]["IMPORTS"]["extra"]) for row in after)
add("Checked against the code with Python's `ast`, independently of Wingbox (`code_facts` in `tocs_score.py`), over all codebases:")
add("")
add(f"- The {extra_imports} IMPORTS outside the ground truth are import statements of the code: the code holds "
    f"{diag['imports_in_code_not_in_gold']} import pairs that the ground truth does not list, all in large codebases, where a "
    "stage imports the validator it calls and the ground truth records only CALLS_API for the pair.")
add(f"- The CALLS_API that Wingbox finds are the calls of the runner into the stages, made through the registry, "
    f"which no import shows. Of the {missed_calls} it misses, {diag['calls_api_gold_with_import_in_code']} join files where "
    f"the source imports the target; {diag['calls_api_gold_with_direct_call_in_code']} of them call an imported name inside "
    "a function, the others import a hook or a middleware that they never call or only place in a list. The "
    f"ground truth does not count every call as CALLS_API: of the {diag['direct_calls_in_code']} pairs "
    f"where a function calls an imported name, {diag['direct_calls_in_code_not_calls_api_gold']} are not CALLS_API "
    "(calls to helpers, model constructors, exceptions, configuration). Mapping every direct call to CALLS_API would "
    "lower precision more than it raises recall, so Wingbox does not guess this type; telling an API call from a "
    "helper call is left to a model.")
add("")
add("Each case is listed in `results/scores.after.json` (`missed`, `extra`); the maps sent to the metric are in `results/maps/`.")
add("")
add("## Reproduce")
add("")
add("```sh")
add("benchmarks/tocs/run.sh /tmp/wingbox-tocs")
add("```")
add("")
add("Needs git and uv; no model and no key. It installs both Wingbox versions, fetches ToCS at the pinned commit, "
    "copies the six shipped codebases, generates the others with ToCS's generator (`python -m generator --pattern "
    "pipeline --complexity <tier> --seed <n>`), moves every `ground_truth.json` out of the tree Wingbox reads, builds "
    "the graphs, scores the maps and compares the result files with the committed ones.")
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
print("wrote", out_path)
