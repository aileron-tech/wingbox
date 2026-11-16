"""Rewrite the paths of a ts_resolve.py result relative to the folder it was scoped to.

Usage: python3 -I strip_scope.py <in.json> <scope folder> <out.json>
"""
from __future__ import annotations

import json
import sys

source, scope, out_path = sys.argv[1:4]
cut = len(scope.rstrip("/")) + 1
files = json.load(open(source))["files"]
stripped = {path[cut:]: [dict(item, target=item["target"][cut:] if item.get("target") else None) for item in items]
            for path, items in files.items()}
json.dump({"files": stripped}, open(out_path, "w"))
print("files", len(stripped))
