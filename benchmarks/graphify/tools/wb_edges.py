"""File-to-file edges of a Wingbox graph (.wingbox/index.sexp), by edge kind.

Usage: python3 -I wb_edges.py <dir with .wingbox/index.sexp> <out.json> [--ext .py]
A module id of the graph is a path without extension; the file is found on disk.
"""
from __future__ import annotations

import json
import os
import re
import sys

root = os.path.abspath(sys.argv[1])
out_path = sys.argv[2]
exts = (sys.argv[sys.argv.index("--ext") + 1].split(",") if "--ext" in sys.argv else [".py"])

id_to_path: dict[str, str] = {}
edges: dict[str, set[tuple[str, str]]] = {}
section = ""
stack: list[str] = []
in_tree = False
LEAF = re.compile(r'^\((\S+) (.+)\)$')
with open(os.path.join(root, ".wingbox", "index.sexp"), encoding="utf-8") as handle:
    for line in handle:
        s = line.strip()
        if s.startswith("; ---"):
            section = s.strip("; -").strip()
            continue
        if section == "paths":
            if s == "(paths":
                in_tree = True
                continue
            if not in_tree or not s:
                continue
            if s == ")":
                if stack:
                    stack.pop()
                else:
                    in_tree = False
                continue
            match = LEAF.match(s)
            if match and not s.startswith("(def "):
                name = match.group(2).strip('"')
                id_to_path[match.group(1)] = "/".join(stack) if name == ":self" else "/".join(stack + [name])
                continue
            match = re.match(r'^\(def (\S+) "(.*)"\)$', s)
            if match:
                id_to_path[match.group(1)] = match.group(2)
                continue
            if s.startswith("(") and not s.endswith(")"):
                stack.append(s[1:].strip('"'))
                continue
            raise SystemExit(f"unparsed paths line: {s!r}")
        elif section == "edges":
            if not s or s.startswith(";"):
                continue
            parts = s.strip("()").split()
            edges.setdefault(parts[0], set()).add((parts[1].split("::")[0], parts[2].split("::")[0]))


def file_of(module_id: str) -> str:
    path = id_to_path.get(module_id, module_id)
    for ext in exts:
        if os.path.isfile(os.path.join(root, path + ext)):
            return path + ext
    if os.path.isfile(os.path.join(root, path)):
        return path
    raise SystemExit(f"no file for module {module_id!r} -> {path!r}")


result = {"modules": sorted(file_of(i) for i in id_to_path),
          "edges": {kind: sorted({(file_of(a), file_of(b)) for a, b in pairs if file_of(a) != file_of(b)})
                    for kind, pairs in sorted(edges.items())}}
json.dump(result, open(out_path, "w"), indent=1)
print("modules", len(result["modules"]), {k: len(v) for k, v in result["edges"].items()})
