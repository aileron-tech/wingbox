"""Compare a rerun of the benchmark with the committed results.

Usage: python3 -I verify.py <committed results dir> <new results dir>
Exit 0 when the questions, the reference lists and every tool's scores are the same; else the differences
are printed and the exit code is 1. The tool versions are compared too; only the date may differ.
"""
from __future__ import annotations

import json
import os
import sys

committed, fresh = sys.argv[1:3]


def load(root: str, name: str):
    return json.load(open(os.path.join(root, name)))


differences = []
for name in ("questions.json", "rows.json"):
    if load(committed, name) != load(fresh, name):
        differences.append(name)
for name in sorted(os.listdir(os.path.join(committed, "gold"))):
    if load(committed, os.path.join("gold", name)) != load(fresh, os.path.join("gold", name)):
        differences.append("gold/" + name)
old_versions, new_versions = load(committed, "versions.json"), load(fresh, "versions.json")
old_versions.pop("date", None)
new_versions.pop("date", None)
if old_versions != new_versions:
    differences.append("versions.json")
if differences:
    rows = {row["id"]: row for row in load(fresh, "rows.json")}
    for row in load(committed, "rows.json"):
        if rows.get(row["id"]) != row:
            print("differs:", row["id"])
    print("different:", ", ".join(differences))
    sys.exit(1)
print("same questions, reference lists and scores as the committed results")
