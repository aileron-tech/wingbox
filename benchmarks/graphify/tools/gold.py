"""Reference lists for the five "what depends on this file" questions.

Usage: python3 -I gold.py <work dir> [<committed gold dir>]

For every question: the files that depend on the target directly, and the files that depend
on one of those (two hops). The edges never come from Wingbox or Graphify:
  TypeScript  tools/ts_resolve.py    (<work>/out/<repo>.ts_refs.json)
  Rust        tools/rust_resolve.py  (<work>/out/<repo>.rust_refs.json)
  Python      tools/py_edges.py      (<work>/out/fastapi.ref.json), equal to grimp on FastAPI
Writes <work>/bench/gold/<id>.json. With a second argument, exits 1 when a computed list
differs from the committed one.
"""
from __future__ import annotations

import collections
import json
import os
import sys

work = sys.argv[1]
committed = sys.argv[2] if len(sys.argv) > 2 else ""
here = os.path.dirname(os.path.abspath(__file__))
questions = json.load(open(os.path.join(here, "..", "questions.json")))
SOURCES = {
    "typescript": "tools/ts_resolve.py (own TypeScript resolver)",
    "rust": "tools/rust_resolve.py (own Rust resolver)",
    "python": "tools/py_edges.py (Python ast), equal to grimp 3.20 on FastAPI",
}


def reference_edges(question: dict) -> set[tuple[str, str]]:
    out = os.path.join(work, "out")
    if question["language"] == "typescript":
        files = json.load(open(os.path.join(out, question["repo"] + ".ts_refs.json")))["files"]
        return {(path, item["target"]) for path, items in files.items() for item in items if item["target"]}
    if question["language"] == "rust":
        refs = json.load(open(os.path.join(out, question["repo"] + ".rust_refs.json")))["refs"]
        return {(ref["frm"], ref["to"]) for ref in refs}
    return {tuple(pair) for pair in json.load(open(os.path.join(out, question["repo"] + ".ref.json")))["pairs"]}


def two_hops(edges: set[tuple[str, str]], target: str) -> tuple[set[str], set[str]]:
    dependents: dict[str, set[str]] = collections.defaultdict(set)
    for source, destination in edges:
        if source != destination:
            dependents[destination].add(source)
    first = set(dependents.get(target, ()))
    second: set[str] = set()
    for dependent in first:
        second |= dependents.get(dependent, set())
    second -= first
    second.discard(target)
    return first, second


os.makedirs(os.path.join(work, "bench", "gold"), exist_ok=True)
different = []
for question in questions:
    first, second = two_hops(reference_edges(question), question["file"])
    gold = sorted(first | second)
    record = {"question": {key: question[key] for key in ("id", "repo", "file")}, "source": SOURCES[question["language"]],
              "direct": sorted(first), "through_one": sorted(second), "gold": gold}
    json.dump(record, open(os.path.join(work, "bench", "gold", question["id"] + ".json"), "w"), indent=1)
    note = ""
    if committed:
        same = json.load(open(os.path.join(committed, question["id"] + ".json")))["gold"] == gold
        note = "equal to the committed list" if same else "DIFFERENT from the committed list"
        if not same:
            different.append(question["id"])
    print(question["id"], question["repo"], question["file"], "reference list:", len(gold), "files,", len(first), "direct;", note)
if different:
    raise SystemExit("reference lists differ from the committed ones: " + ", ".join(different))
