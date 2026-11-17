"""CodeGraph on the five impact questions, without an agent: files it names against the reference lists.

Usage: python3 -I cg_command_check.py <work dir> <out.json>
The same questions, reference lists and scoring as command_check.py; written to its own file so the
Wingbox and Graphify results stay as they are.

codegraph:                  `codegraph impact <file name> --file <path> --depth 2`, the impact command of
                            CodeGraph; a file is a node of its graph, and --file picks the one at <path>.
                            Files named: the file of every affected node (--json); tokens: the text output.
codegraph_file_dependents:  CodeGraph's file-level graph, two steps back from the file (tools/cg_graph.js):
                            the walk `codegraph affected <file> --depth 2` runs before it keeps test files.
                            Checked against the count that command prints (totalDependentsTraversed).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

P, out_path = sys.argv[1:3]
BENCH = os.path.join(P, "bench")
HERE = os.path.dirname(os.path.abspath(__file__))
CODEGRAPH = os.path.join(P, "cg", "node_modules", ".bin", "codegraph")
BUNDLE = sorted(glob.glob(os.path.join(P, "cg", "node_modules", "@colbymchenry", "codegraph-*-*")))[0]
NO_TEST_FILE = "no-file-matches-this-filter"
# Every CodeGraph run: a private HOME, no daemon, no file watcher, no background network calls.
ENV = dict(os.environ, HOME=os.path.join(P, "cg_home"), DO_NOT_TRACK="1", CODEGRAPH_NO_DAEMON="1",
           CODEGRAPH_NO_WATCH="1", CODEGRAPH_NO_DOWNLOAD="1", NO_COLOR="1")


def run(command: list[str], cwd: str) -> str:
    done = subprocess.run(command, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=600)
    if done.returncode:
        raise SystemExit(f"{' '.join(command)} failed in {cwd}: {done.stderr[-800:]}")
    return done.stdout


def score(files: set[str], gold: set[str], target: str) -> dict:
    """The scoring of command_check.py."""
    files = files - {target}
    hit = len(files & gold)
    precision = hit / len(files) if files else 0.0
    recall = hit / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"files_named": len(files), "right": hit, "wrong": len(files - gold), "missing": len(gold - files),
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "missing_files": sorted(gold - files)[:40], "wrong_files": sorted(files - gold)[:40]}


rows = []
for name in sorted(os.listdir(os.path.join(BENCH, "gold"))):
    gold_data = json.load(open(os.path.join(BENCH, "gold", name)))
    question, gold = gold_data["question"], set(gold_data["gold"])
    repo, target = question["repo"], question["file"]
    cwd = os.path.join(BENCH, "cg", repo)
    impact = [CODEGRAPH, "impact", os.path.basename(target), "--file", target, "--depth", "2"]
    text = run(impact, cwd)
    data = json.loads(run([*impact, "--json"], cwd))
    if [t["filePath"] for t in data["targets"]] != [target] or data["filteredOut"]:
        raise SystemExit(f"codegraph impact did not resolve {target}: {data['targets']}")
    files = {node["filePath"] for node in data["affected"]}
    row = {"question": question["id"], "repo": repo, "file": target, "reference_files": len(gold),
           "codegraph": {"lines": text.count("\n"), "chars": len(text), "tokens_chars_div_4": len(text) // 4,
                         "affected_nodes": data["nodeCount"], **score(files, gold, target)}}
    walk = json.loads(run([os.path.join(BUNDLE, "node"), "--disable-warning=ExperimentalWarning", os.path.join(HERE, "cg_graph.js"),
                           os.path.join(BUNDLE, "lib", "dist", "index.js"), "dependents", cwd, target, "2"], cwd))
    traversed = json.loads(run([CODEGRAPH, "affected", target, "--depth", "2", "--filter", NO_TEST_FILE, "--json"], cwd))
    if traversed["totalDependentsTraversed"] != len(walk):
        raise SystemExit(f"{target}: the walk names {len(walk)} files, codegraph affected traversed {traversed['totalDependentsTraversed']}")
    row["codegraph_file_dependents"] = {"affected_traversed": traversed["totalDependentsTraversed"], **score(set(walk), gold, target)}
    rows.append(row)
    print(row["question"], repo, target, "reference", len(gold))
    for label in ("codegraph", "codegraph_file_dependents"):
        r = row[label]
        tokens = f"tokens {r['tokens_chars_div_4']:6d}" if "tokens_chars_div_4" in r else " " * 13
        print(f"   {label:26s} {tokens} | files {r['files_named']:4d} right {r['right']:3d} wrong {r['wrong']:4d} missing {r['missing']:3d}"
              f" | P {r['precision']} R {r['recall']} F1 {r['f1']}")
json.dump(rows, open(out_path, "w"), indent=1)
