"""Compare the results of a run with the committed ones; exit 1 on the first difference.

Usage: python3 -I verify.py <committed results dir> <results dir of this run>
Compared: the questions, every reference list, every row of the command test, the edge test and the
check of the Python reference against grimp. Not compared: versions.json (it holds the date of the run)
and the files of the tests this script does not run (agent.json, build_times.json).
"""
from __future__ import annotations

import json
import os
import sys

committed, fresh = sys.argv[1:3]
names = ["questions.json", "command.json", "edges.json", "fastapi.grimp_check.json", "commits.txt"]
names += sorted(os.path.join("gold", name) for name in os.listdir(os.path.join(committed, "gold")))
different = []
for name in names:
    a, b = os.path.join(committed, name), os.path.join(fresh, name)
    if not os.path.exists(b):
        different.append(name + " (not produced)")
    elif name.endswith(".json"):
        if json.load(open(a)) != json.load(open(b)):
            different.append(name)
    elif open(a).read() != open(b).read():
        different.append(name)
extra = sorted(set(os.listdir(os.path.join(fresh, "gold"))) - set(os.listdir(os.path.join(committed, "gold"))))
different += [os.path.join("gold", name) + " (not committed)" for name in extra]
if different:
    print("DIFFERENT from the committed results:")
    for name in different:
        print("  ", name)
    sys.exit(1)
print(f"REPRODUCED: {len(names)} result files are equal to the committed ones "
      f"(questions, {len(names) - 5} reference lists, command test, edge test, grimp check, commits)")
