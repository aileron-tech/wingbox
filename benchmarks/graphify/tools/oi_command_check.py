"""OntoIndex on the five impact questions, without an agent: files it names against the reference lists.

Usage: python3 -I oi_command_check.py <work dir> <out.json> [ontoindex executable]
The same questions, reference lists and scoring as command_check.py; written to its own file so the
Wingbox and Graphify results stay as they are. The graphs are built beforehand in <work dir>/bench/oi/<repo>
(`ontoindex analyze . --skip-agents-md` on a git checkout of the pinned commit, see oi_run.sh).

ontoindex:                 `ontoindex impact File:<path> --depth 2`, its impact command as documented
                           (a file is the graph node `File:<path>`; test files are left out by default).
                           Files named: the filePath of every impacted node (the JSON it prints).
ontoindex_include_tests:   the same with --include-tests; the reference lists hold test files.
ontoindex_file_imports:    its file-level IMPORTS edges (File -> File), two steps back from the file,
                           read with `ontoindex cypher`; the walk the reference lists are made of.
Tokens: characters of the printed answer divided by four, as command_check.py counts them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

P, out_path = sys.argv[1:3]
ONTOINDEX = sys.argv[3] if len(sys.argv) > 3 else "ontoindex"
BENCH = os.path.join(P, "bench")
PAGE = 5000  # `ontoindex cypher` refuses a query without LIMIT <= 5000


def capture(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a command with its standard output going to a file: on a pipe, `ontoindex` 3.1.9 stops its
    output at 65,536 bytes (Node exits before the pipe is drained), so a pipe would cut long answers."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        done = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.PIPE, text=True, timeout=900)
        out.seek(0)
        return subprocess.CompletedProcess(command, done.returncode, out.read(), done.stderr)


def run(command: list[str], cwd: str) -> str:
    done = capture(command, cwd)
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


def json_tail(text: str) -> dict:
    """The JSON object the command prints (it may print notices before it)."""
    return json.loads(text[text.index("{"):])


def impacted_files(answer: dict) -> set[str]:
    nodes = answer.get("impacted") or [node for group in answer.get("byDepth", {}).values() for node in group]
    return {str(node["filePath"]).replace("\\", "/") for node in nodes if node.get("filePath")}


def import_pairs(cwd: str) -> set[tuple[str, str]]:
    """Every File -> File IMPORTS edge of the graph, paged under the LIMIT the CLI requires."""
    pairs: set[tuple[str, str]] = set()
    skip = 0
    while True:
        query = ("MATCH (a:File)-[r:CodeRelation]->(b:File) WHERE r.type = 'IMPORTS' "
                 f"RETURN a.filePath AS source, b.filePath AS target ORDER BY source, target SKIP {skip} LIMIT {PAGE}")
        rows = cypher_rows(run([ONTOINDEX, "cypher", query], cwd))
        pairs |= {(row["source"], row["target"]) for row in rows}
        if len(rows) < PAGE:
            return pairs
        skip += PAGE


def cypher_rows(text: str) -> list[dict]:
    """Rows of `ontoindex cypher`: it prints JSON whose "markdown" field is a Markdown table."""
    data = json.loads(text[text.index("{"):])
    lines = [line for line in data["markdown"].split("\n") if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    return [dict(zip(header, (cell.strip() for cell in line.strip("|").split("|")))) for line in lines[2:]]


def two_hops(pairs: set[tuple[str, str]], target: str) -> set[str]:
    dependents: dict[str, set[str]] = {}
    for source, destination in pairs:
        if source != destination:
            dependents.setdefault(destination, set()).add(source)
    first = dependents.get(target, set())
    second = set().union(*(dependents.get(f, set()) for f in first)) if first else set()
    return (first | second) - {target}


def main() -> None:
    rows = []
    graphs: dict[str, set[tuple[str, str]]] = {}
    for name in sorted(os.listdir(os.path.join(BENCH, "gold"))):
        gold_data = json.load(open(os.path.join(BENCH, "gold", name)))
        question, gold = gold_data["question"], set(gold_data["gold"])
        repo, target = question["repo"], question["file"]
        cwd = os.path.join(BENCH, "oi", repo)
        row = {"question": question["id"], "repo": repo, "file": target, "reference_files": len(gold)}
        for label, extra in (("ontoindex", []), ("ontoindex_include_tests", ["--include-tests"])):
            text = run([ONTOINDEX, "impact", f"File:{target}", "--depth", "2", *extra], cwd)
            answer = json_tail(text)
            if str(answer.get("target", {}).get("filePath", "")).replace("\\", "/") != target:
                raise SystemExit(f"ontoindex impact did not resolve {target}: {text[:600]}")
            row[label] = {"lines": text.count("\n"), "chars": len(text), "tokens_chars_div_4": len(text) // 4,
                          "impacted_nodes": answer.get("impactedCount"), "partial": bool(answer.get("partial")),
                          "longer_than_a_pipe_carries": len(text.encode()) > 65536,
                          **score(impacted_files(answer), gold, target)}
        if repo not in graphs:
            graphs[repo] = import_pairs(cwd)
        row["ontoindex_file_imports"] = {"import_pairs_in_graph": len(graphs[repo]),
                                         **score(two_hops(graphs[repo], target), gold, target)}
        rows.append(row)
        print(row["question"], repo, target, "reference", len(gold))
        for label in ("ontoindex", "ontoindex_include_tests", "ontoindex_file_imports"):
            r = row[label]
            tokens = f"tokens {r['tokens_chars_div_4']:6d}" if "tokens_chars_div_4" in r else " " * 13
            print(f"   {label:26s} {tokens} | files {r['files_named']:4d} right {r['right']:3d} wrong {r['wrong']:4d} missing {r['missing']:3d}"
                  f" | P {r['precision']} R {r['recall']} F1 {r['f1']}")
    json.dump(rows, open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main()
