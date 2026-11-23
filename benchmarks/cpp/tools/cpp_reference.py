"""Reference include edges of a C or C++ tree, from the text of its files.

Usage: python3 -I cpp_reference.py <root> <out.json> <include root> [<include root> ...]

Rules, those of a compiler's include search applied to the text:
  - every `#include` line outside a comment counts, wherever it stands (inside `#if` blocks too:
    the preprocessor is not run);
  - a quoted name is looked for beside the including file, then in the include roots in the order
    given; a name in angle brackets in the include roots only;
  - a name found nowhere is external (a system header, a library outside the tree) and gives no edge;
  - an include whose name is a macro is not read.
The include roots are those the build of the project passes to the compiler (its CMake files), given
by hand on the command line. No code of Wingbox is used and no code of the tree is run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp")
TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*(<[^>\n]+>|"[^"\n]+")', re.M)


def without_comments(text: str) -> str:
    """The text with every comment blanked; string and character literals stay."""
    def replace(match: re.Match) -> str:
        found = match.group(0)
        return found if found[0] in "\"'" else re.sub(r"[^\n]", " ", found)
    return TOKEN.sub(replace, text)


def main() -> None:
    root, out_path, roots = os.path.abspath(sys.argv[1]), sys.argv[2], [r.strip("/") for r in sys.argv[3:]]
    roots = ["" if r == "." else r for r in roots]
    listed = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
    files = sorted(f for f in listed if f.lower().endswith(EXTENSIONS))
    known = set(files)
    edges: set[tuple[str, str]] = set()
    external = 0
    for path in files:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            text = without_comments(handle.read())
        for match in INCLUDE.finditer(text):
            written = match.group(1)
            name = written[1:-1].strip().replace("\\", "/")
            bases = ([os.path.dirname(path)] if written[0] == '"' else []) + roots
            for base in bases:
                candidate = os.path.normpath(os.path.join(base, name)).replace(os.sep, "/")
                if candidate in known:
                    if candidate != path:
                        edges.add((path, candidate))
                    break
            else:
                external += 1
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"root": os.path.basename(root), "include_roots": roots, "files": files,
                   "edges": sorted(edges), "external_includes": external}, handle, indent=1)
    print(f"files {len(files)} edges {len(edges)} external includes {external}")


if __name__ == "__main__":
    main()
