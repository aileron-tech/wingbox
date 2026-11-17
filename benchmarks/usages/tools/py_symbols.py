"""Exported names of a Python repository, read with Python's `ast` (the language's own parser).

Usage: python3 -I py_symbols.py <repository root> <out.json>

Every top-level `def`, `async def`, `class` and assignment to a plain name whose name does not start with an
underscore, in every .py file outside hidden folders and node_modules. Nothing is imported or executed.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys


def python_files(root: str) -> list[str]:
    found = []
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if not f.startswith(".") and f not in ("node_modules", "__pycache__"))
        found += [os.path.join(directory, f) for f in sorted(files) if f.endswith(".py")]
    return found


def symbols(root: str) -> list[dict]:
    found = []
    for path in python_files(root):
        text = open(path, encoding="utf-8", errors="replace").read()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in tree.body:
            names: list[tuple[str, str, ast.AST]] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append((node.name, "function", node))
            elif isinstance(node, ast.ClassDef):
                names.append((node.name, "class", node))
            elif isinstance(node, ast.Assign):
                names += [(t.id, "variable", t) for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append((node.target.id, "variable", node.target))
            for name, kind, item in names:
                if name.startswith("_"):
                    continue
                line = lines[item.lineno - 1]
                # col_offset counts UTF-8 bytes; the column written here counts characters.
                start = len(line.encode("utf-8")[: item.col_offset].decode("utf-8", errors="replace"))
                match = re.compile(r"\b" + re.escape(name) + r"\b").search(line, start)
                if match:
                    found.append({"file": os.path.relpath(path, root).replace(os.sep, "/"), "name": name, "kind": kind,
                                  "line": item.lineno, "col": match.start()})
    return found


if __name__ == "__main__":
    root = os.path.abspath(sys.argv[1])
    result = symbols(root)
    json.dump({"tool": "python ast", "files": len(python_files(root)), "symbols": result}, open(sys.argv[2], "w"), indent=1)
    print(f"{len(result)} exported names in {len(python_files(root))} files")
