"""Verify Wingbox against the reference and the committed results.

Usage: python3 -I verify.py <work dir>                    run and score the benchmark
       python3 -I verify.py --compare <committed> <fresh>  compare two result sets

`<work dir>` is prepared by `run.sh <work dir>`: Wingbox in `venv_wb`, the
checkouts at pinned commits, a graph per corpus, the PHP file lists, the
reference files of `php_reference.php` and the graph edges of `graph_edges.py`.
The first form runs `wingbox impact <file> 2 --all` on every question of
`out/questions.json`, scores the answer against the reference list, audits every
IMPORTS edge of every graph against the reference, and writes
`out/command.json` and `out/edges.json`. It also prints the Wilson 95 % interval
of the complete-answer rate.

The comparison form is what the container and CI run: it exits 1 and names the
first files that differ from the committed results, or prints REPRODUCED.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "impact"))
from questions import reference_edges  # noqa: E402
from stats import wilson  # noqa: E402

RESULT_FILES = ("questions.json", "command.json", "edges.json", "commits.txt")
MAX_REPORTED_EDGES = 50


def module_id(path: str) -> str:
    """The module ID of a source file: strip `.blade.php` before `.php`, never both."""
    if path.endswith(".blade.php"):
        return path[: -len(".blade.php")]
    if path.endswith(".php"):
        return path[: -len(".php")]
    return path


def wingbox_files(text: str) -> set[str]:
    """The files named under the Depth sections of `wingbox impact` output."""
    files: set[str] = set()
    section = ""
    for line in text.split("\n"):
        if line.startswith("Depth "):
            section = "depth"
        elif line and not line.startswith(" "):
            section = ""
        elif section and line.strip() and not line.strip().startswith("..."):
            files.add(line.split()[0])
    return files


def score(named: set[str], gold: list[str], target: str) -> dict:
    """Named files against the reference: required, outside it, missing.

    The comparison is by module ID. Two files can share one (`x.php` and
    `x.blade.php`, a single-file Livewire component) and the product answers with
    a module; naming either file satisfies the question for both.
    """
    required = set(gold)
    required_modules = {module_id(path) for path in required}
    named_files = {path for path in named if module_id(path) != module_id(target)}
    named_modules = {module_id(path) for path in named_files}
    outside = sorted(path for path in named_files if module_id(path) not in required_modules)
    missing = sorted(path for path in required if module_id(path) not in named_modules)
    right_modules = len(required_modules & named_modules)
    off_modules = len(named_modules - required_modules)
    counted = right_modules + off_modules
    precision = right_modules / counted if counted else (1.0 if not required else 0.0)
    recall = right_modules / len(required_modules) if required_modules else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    right = sum(1 for path in required if module_id(path) in named_modules)
    return {
        "named": len(named_files),
        "right": right,
        "not_on_list": len(outside),
        "missing": len(missing),
        "outside_files": outside[:20],
        "missing_files": missing[:20],
        "accepted_extra": 0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "exact": not outside and not missing,
        "exact_strict": named_modules == required_modules,
    }


def run_command(work: str, question: dict) -> dict:
    binary = os.environ.get("WINGBOX_BIN") or os.path.join(work, "venv_wb", "bin", "wingbox")
    if not os.path.exists(binary):
        raise SystemExit(f"no wingbox at {binary}; set WINGBOX_BIN or run run.sh first")
    directory = os.path.join(work, "bench", "wb", question["corpus"])
    try:
        done = subprocess.run(
            [binary, "impact", question["file"], "2", "--all"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=600,
        )
        text, returncode = done.stdout, done.returncode
    except subprocess.TimeoutExpired as error:
        text, returncode = error.stdout or "", -1
    named = wingbox_files(text)
    row = {
        "id": question["id"],
        "corpus": question["corpus"],
        "language": question["language"],
        "file": question["file"],
        "reference_files": question["reference_files"],
        "returncode": returncode,
        "wingbox": {"tokens": len(text) // 4, **score(named, question["gold"], question["file"])},
    }
    state = row["wingbox"]
    print(question["id"], question["file"], "reference", row["reference_files"],
          f"| wingbox {state['right']}/{state['not_on_list']}/{state['missing']}", flush=True)
    return row


def audit_edges(work: str, corpus: str) -> dict:
    """Every IMPORTS edge of the graph against the reference of one corpus.

    Edges are compared by module ID: `app/Models/User.php` is module
    `app/Models/User`, and a `.php` / `.blade.php` pair of one stem is one module,
    as in the graph.
    """
    with open(os.path.join(work, "out", corpus + ".graph.json"), encoding="utf-8") as handle:
        graph = json.load(handle)
    required, accepted = reference_edges(work, corpus)
    required_modules = {
        (module_id(source), module_id(target)) for source, target in required
        if module_id(source) != module_id(target)
    }
    accepted_modules = {
        (module_id(source), module_id(target)) for source, target in accepted
        if module_id(source) != module_id(target)
    }
    edges = {tuple(pair) for pair in graph.get("edges", [])}
    wrong = sorted(edges - accepted_modules)
    missing = sorted(required_modules - edges)
    return {
        "corpus": corpus,
        "language": "php",
        "pairs": len(required_modules),
        "edges": len(edges),
        "wrong": len(wrong),
        "missing": len(missing),
        "accepted": len(accepted_modules - required_modules),
        "wrong_edges": [list(pair) for pair in wrong[:MAX_REPORTED_EDGES]],
        "missing_edges": [list(pair) for pair in missing[:MAX_REPORTED_EDGES]],
        "unmapped_edges": graph.get("unmapped_edges", 0),
        "unmapped_by_cause": graph.get("unmapped_by_cause", {}),
        "unmapped_modules": graph.get("unmapped_modules", []),
        "shared_modules": graph.get("shared_modules", {}),
    }


def run_benchmark(work: str) -> None:
    with open(os.path.join(work, "out", "questions.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    rows = [run_command(work, question) for question in meta["questions"]]
    with open(os.path.join(work, "out", "command.json"), "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)
        handle.write("\n")
    corpora = list(dict.fromkeys(row["corpus"] for row in rows))
    audits = [audit_edges(work, corpus) for corpus in corpora]
    with open(os.path.join(work, "out", "edges.json"), "w", encoding="utf-8") as handle:
        json.dump(audits, handle, indent=1)
        handle.write("\n")
    exact = sum(row["wingbox"]["exact"] for row in rows)
    low, high = wilson(exact, len(rows))
    wrong = sum(row["wrong"] for row in audits)
    missing = sum(row["missing"] for row in audits)
    print(f"questions {len(rows)}: complete and nothing extra {exact} "
          f"(95 % interval {low * 100:.0f} to {high * 100:.0f} %)")
    print(f"edges: {wrong} wrong, {missing} missing in {len(audits)} corpora")


def compare(committed: str, fresh: str) -> None:
    names: list[str] = []
    for name in RESULT_FILES:
        if os.path.exists(os.path.join(committed, name)) or os.path.exists(os.path.join(fresh, name)):
            names.append(name)
    different: list[str] = []
    for name in names:
        first, second = os.path.join(committed, name), os.path.join(fresh, name)
        if not os.path.exists(second):
            different.append(name + " (not produced)")
        elif not os.path.exists(first):
            different.append(name + " (not committed)")
        elif name.endswith(".json"):
            if json.load(open(first)) != json.load(open(second)):
                different.append(name)
        elif open(first).read() != open(second).read():
            different.append(name)
    if different:
        print("DIFFERENT from the committed results:")
        for name in different:
            print("  ", name)
        raise SystemExit(1)
    print(f"REPRODUCED: {len(names)} result files are equal to the committed ones "
          "(questions, command test, edge audit, commits)")


def main(arguments: list[str]) -> int:
    if arguments[:1] == ["--compare"]:
        if len(arguments) < 3:
            print("usage: verify.py --compare <committed results dir> <results dir of this run>", file=sys.stderr)
            return 2
        compare(arguments[1], arguments[2])
        return 0
    if len(arguments) < 1:
        print("usage: verify.py <work dir> | verify.py --compare <committed> <fresh>", file=sys.stderr)
        return 2
    run_benchmark(os.path.abspath(arguments[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))