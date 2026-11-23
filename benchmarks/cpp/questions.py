"""Impact questions on a C or C++ tree: the answer of `wingbox impact <file> 2 --json` against the reference.

Usage: python3 -I questions.py <wingbox command> <tree built by wingbox> <reference.json> <graph edges.json> <out.json>

Edge test: every include edge of the graph against the reference (wrong: in the graph, not in the
reference; missing: the reverse). Questions: 20 files drawn without replacement with a fixed seed from
the files that at least one other file includes; for each, the files that include it directly or
through one other file by the reference, against the rows of the answer.
"""
from __future__ import annotations

import collections
import json
import random
import subprocess
import sys

SEED = 20261123
PER_TREE = 20
EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp")


def two_steps(edges: set[tuple[str, str]], target: str) -> set[str]:
    dependents: dict[str, set[str]] = collections.defaultdict(set)
    for source, destination in edges:
        dependents[destination].add(source)
    first = set(dependents[target])
    second = set().union(*(dependents[item] for item in first)) if first else set()
    return (first | second) - {target}


def main() -> None:
    wingbox, tree, reference_path, graph_path, out_path = sys.argv[1:6]
    reference = json.load(open(reference_path, encoding="utf-8"))
    required = {tuple(edge) for edge in reference["edges"]}
    graph = {tuple(edge) for edge in json.load(open(graph_path, encoding="utf-8"))["edges"].get("imports", [])
             if all(path.lower().endswith(EXTENSIONS) for path in edge)}
    included = sorted({target for _source, target in required})
    chosen = sorted(random.Random(SEED).sample(included, min(PER_TREE, len(included))))
    rows = []
    for file in chosen:
        gold = two_steps(required, file)
        done = subprocess.run([*wingbox.split(), "impact", file, "2", "--json"], capture_output=True, text=True, cwd=tree)
        answer = json.loads(done.stdout)["impact"][0]
        named = {row["file"] for row in answer.get("rows", [])}
        rows.append({"file": file, "gold": len(gold), "named": len(named), "missing": sorted(gold - named),
                     "extra": sorted(named - gold), "inferred_rows": sum(1 for row in answer.get("rows", []) if row.get("basis") == "inferred"),
                     "tokens": len(done.stdout) // 4})
    result = {
        "tree": reference["root"], "include_roots": reference["include_roots"], "files": len(reference["files"]),
        "edges": {"reference": len(required), "graph": len(graph), "wrong": sorted(graph - required)[:20],
                  "wrong_count": len(graph - required), "missing": sorted(required - graph)[:20], "missing_count": len(required - graph)},
        "questions": rows,
        "complete_and_nothing_extra": sum(1 for row in rows if not row["missing"] and not row["extra"]),
        "with_a_missing_file": sum(1 for row in rows if row["missing"]),
    }
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1)
    sizes = sorted(row["gold"] for row in rows)
    print(f"{result['tree']}: edges {len(required)} wrong {len(graph - required)} missing {len(required - graph)}; "
          f"questions {len(rows)} complete {result['complete_and_nothing_extra']} with a missing file {result['with_a_missing_file']}; "
          f"gold lists {sizes[0]} to {sizes[-1]}, median {sizes[len(sizes) // 2]}")


if __name__ == "__main__":
    main()
