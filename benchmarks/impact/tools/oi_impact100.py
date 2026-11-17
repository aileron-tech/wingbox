"""OntoIndex on the 100 impact questions of this benchmark, and the size of its answers.

Usage: python3 -I oi_impact100.py <graphs dir> <out.json> [ontoindex executable]
<graphs dir>/<repo> holds an OntoIndex graph of each repository at the pinned commit
(`ontoindex analyze . --skip-agents-md`; immich-server is the server/ folder, as for Wingbox).
Questions and reference lists: results/questions.json and results/gold/, as committed.

Command: `ontoindex impact File:<file> --depth 2 --include-tests --repo <repo> --read-mode committed` (the
question asks for test files too; `--read-mode committed` reads the index of the pinned commit directly, as the
CLI advises when its workspace check fails, and skips that check, which takes OntoIndex tens of seconds per call). Files named: the filePath of every
impacted node. Scoring: score() of impact100.py (accepted files are neither required nor wrong).
Tokens: characters of the printed answer divided by four, as for Wingbox. The context ratio divides
Graphify's corpus estimate (results/graphify_benchmark.json) by the mean answer, as context_ratio.py does.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
graphs, out_path = sys.argv[1:3]
ONTOINDEX = sys.argv[3] if len(sys.argv) > 3 else "ontoindex"


def score(named: set[str], gold: dict, target: str) -> dict:
    """score() of impact100.py."""
    required, accepted = set(gold["gold"]), set(gold["gold"]) | set(gold["also_accepted"])
    named = named - {target}
    right = len(named & required)
    off = len(named - accepted)
    counted = len(named & required) + off
    precision = right / counted if counted else (1.0 if not required else 0.0)
    recall = right / len(required) if required else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"named": len(named), "right": right, "not_on_list": off, "missing": len(required - named),
            "accepted_extra": len((named & accepted) - required),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "exact": off == 0 and right == len(required),
            "exact_strict": named == required}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(100 * (centre - half)), round(100 * (centre + half))


def capture(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a command with its standard output going to a file: on a pipe, `ontoindex` 3.1.9 stops its
    output at 65,536 bytes (Node exits before the pipe is drained), so a pipe would cut long answers."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        done = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.PIPE, text=True, timeout=900)
        out.seek(0)
        return subprocess.CompletedProcess(command, done.returncode, out.read(), done.stderr)


def answer(repo: str, target: str, extra: list[str]) -> tuple[str, set[str], dict]:
    done = capture([ONTOINDEX, "impact", f"File:{target}", "--depth", "2", *extra, "--repo", repo, "--read-mode", "committed"],
                   os.path.join(graphs, repo))
    text = done.stdout
    data = json.loads(text[text.index("{"):]) if "{" in text else {}
    if str(data.get("target", {}).get("filePath", "")) != target:
        return text, set(), {"resolved": False, "error": (data.get("error") or done.stderr[-300:] or text[:300])}
    nodes = [node for group in data.get("byDepth", {}).values() for node in group]
    return text, {str(node["filePath"]) for node in nodes if node.get("filePath")}, {"resolved": True, "partial": bool(data.get("partial"))}


def main() -> None:
    questions = json.load(open(os.path.join(RESULTS, "questions.json")))["questions"]
    rows = []
    for question in questions:
        gold = json.load(open(os.path.join(RESULTS, "gold", question["id"] + ".json")))
        repo, target = question["repo"], question["file"]
        row = {"id": question["id"], "repo": repo, "language": question["language"], "file": target, "reference_files": len(gold["gold"])}
        for label, extra in (("ontoindex", ["--include-tests"]),):
            text, files, status = answer(repo, target, extra)
            row[label] = {"tokens": len(text) // 4, "longer_than_a_pipe_carries": len(text.encode()) > 65536, **status, **score(files, gold, target)}
        rows.append(row)
        o = row["ontoindex"]
        print(row["id"], target, "reference", row["reference_files"], f"| ontoindex {o['right']}/{o['not_on_list']}/{o['missing']} tokens {o['tokens']}", flush=True)
    corpus = json.load(open(os.path.join(RESULTS, "graphify_benchmark.json")))
    wingbox = {row["id"]: row["wingbox"] for row in json.load(open(os.path.join(RESULTS, "command.json")))}
    summary = {}
    for label in ("ontoindex",):
        per_repo = {}
        for repo in dict.fromkeys(row["repo"] for row in rows):
            mine = [row[label] for row in rows if row["repo"] == repo]
            mean = statistics.mean(r["tokens"] for r in mine)
            per_repo[repo] = {"questions": len(mine), "exact": sum(r["exact"] for r in mine), "with_missing": sum(r["missing"] > 0 for r in mine),
                              "median_tokens": statistics.median(r["tokens"] for r in mine), "mean_tokens": round(mean),
                              "context_ratio": round(corpus[repo]["corpus_tokens"] / mean, 1)}
        exact = sum(row[label]["exact"] for row in rows)
        summary[label] = {"questions": len(rows), "exact": exact, "exact_interval_pct": wilson(exact, len(rows)),
                          "with_missing": sum(row[label]["missing"] > 0 for row in rows),
                          "unresolved": sum(not row[label]["resolved"] for row in rows),
                          "mean_recall": round(statistics.mean(row[label]["recall"] for row in rows), 3),
                          "mean_precision": round(statistics.mean(row[label]["precision"] for row in rows), 3),
                          "median_tokens": statistics.median(row[label]["tokens"] for row in rows),
                          "max_tokens": max(row[label]["tokens"] for row in rows), "per_repo": per_repo}
    summary["wingbox_committed"] = {"exact": sum(w["exact"] for w in wingbox.values()),
                                    "median_tokens": statistics.median(w["tokens"] for w in wingbox.values())}
    json.dump({"method": __doc__.strip().split("\n\n")[1], "summary": summary, "rows": rows}, open(out_path, "w"), indent=1)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_repo"} for k, v in summary.items()}, indent=1))


if __name__ == "__main__":
    main()
