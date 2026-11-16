"""Compare the results of a run with the committed ones; exit 1 when a file differs.

Usage: python3 -I verify.py <committed results dir> <results dir of this run>
Every file of the committed results is compared: scores, maps, versions and the ToCS commit.
"""
from __future__ import annotations

import json
import os
import sys

committed, fresh = sys.argv[1:3]
names = sorted(
    os.path.relpath(os.path.join(folder, name), committed)
    for folder, _dirs, files in os.walk(committed)
    for name in files
)
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
if different:
    print("DIFFERENT from the committed results:")
    for name in different:
        print("  ", name)
    sys.exit(1)
print(f"REPRODUCED: {len(names)} result files are equal to the committed ones")
