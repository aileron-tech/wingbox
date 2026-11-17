"""Exported items of a Rust repository: top-level `pub` items, read line by line.

Usage: python3 -I rs_symbols.py <repository root> <out.json>

An item counts when its line starts in column 0 (rustfmt layout: a top-level item of the file) with a `pub`
visibility (`pub`, `pub(crate)`, `pub(super)`, `pub(in ...)`) and is a fn, struct, enum, union, trait,
type, const or static. Items inside inline `mod { }` blocks and `impl` blocks are indented and not taken.
Files under target/ and hidden folders are skipped. The references themselves come from rust-analyzer.
"""
from __future__ import annotations

import json
import os
import re
import sys

ITEM = re.compile(r"^pub(?:\([^)]*\))?\s+(?:(?:const|async|unsafe|extern\s+\"[^\"]*\")\s+)*"
                  r"(fn|struct|enum|union|trait|type|const|static)\s+(?:mut\s+)?([A-Za-z_][A-Za-z0-9_]*)")
KINDS = {"fn": "function", "struct": "struct", "enum": "enum", "union": "struct", "trait": "trait", "type": "type",
         "const": "constant", "static": "constant"}


def rust_files(root: str) -> list[str]:
    found = []
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if not f.startswith(".") and f not in ("target", "node_modules"))
        found += [os.path.join(directory, f) for f in sorted(files) if f.endswith(".rs")]
    return found


def symbols(root: str) -> list[dict]:
    found = []
    for path in rust_files(root):
        for number, line in enumerate(open(path, encoding="utf-8", errors="replace").read().split("\n"), start=1):
            match = ITEM.match(line)
            if match:
                found.append({"file": os.path.relpath(path, root).replace(os.sep, "/"), "name": match.group(2),
                              "kind": KINDS[match.group(1)], "line": number, "col": match.start(2)})
    return found


if __name__ == "__main__":
    root = os.path.abspath(sys.argv[1])
    result = symbols(root)
    json.dump({"tool": "line pattern", "files": len(rust_files(root)), "symbols": result}, open(sys.argv[2], "w"), indent=1)
    print(f"{len(result)} exported items in {len(rust_files(root))} files")
