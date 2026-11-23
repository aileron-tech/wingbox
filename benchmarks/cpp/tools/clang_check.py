"""The reference include edges against the dependency output of a compiler.

Usage: python3 -I clang_check.py <root> <reference.json> <out.json> <include root> [...]

For every translation unit of the tree (`.c`, `.cc`, `.cpp`, `.cxx`) `clang -MM -MG` prints the files
the preprocessor reads, with the include roots of the reference. Those of them that are files of the
tree must all be reached from the translation unit over the reference edges: a file the compiler
reads and the reference does not reach is a missing edge of the reference. The reference may reach
more: it reads every branch of an `#if`, the compiler one.
A translation unit the compiler stops on (an `#error` for another platform) is counted and left out.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> None:
    root, reference_path, out_path, roots = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4:]
    reference = json.load(open(reference_path, encoding="utf-8"))
    known = set(reference["files"])
    reached_from: dict[str, set[str]] = {}
    for source, target in reference["edges"]:
        reached_from.setdefault(source, set()).add(target)
    units = [f for f in reference["files"] if f.lower().endswith((".c", ".cc", ".cpp", ".cxx"))]
    confirmed = unreached = stopped = 0
    examples: list[list[str]] = []
    version = subprocess.run(["clang", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    for unit in units:
        language = ["-x", "c"] if unit.lower().endswith(".c") else ["-x", "c++", "-std=c++20"]
        command = ["clang", "-MM", "-MG", "-w", *language, *[f"-I{os.path.join(root, r)}" for r in roots], os.path.join(root, unit)]
        done = subprocess.run(command, capture_output=True, text=True, cwd=root)
        if done.returncode != 0:
            stopped += 1
            continue
        words = done.stdout.replace("\\\n", " ").split()[2:]
        read = set()
        for word in words:
            path = os.path.normpath(word if os.path.isabs(word) else os.path.join(root, word))
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if relative in known and relative != unit:
                read.add(relative)
        closure: set[str] = set()
        todo = [unit]
        while todo:
            for target in reached_from.get(todo.pop(), ()):
                if target not in closure:
                    closure.add(target)
                    todo.append(target)
        confirmed += len(read & closure)
        missing = sorted(read - closure)
        unreached += len(missing)
        examples.extend([unit, header] for header in missing[:3])
    result = {"compiler": version, "translation_units": len(units), "stopped": stopped,
              "files_read_by_the_compiler": confirmed + unreached, "reached_by_the_reference": confirmed,
              "not_reached": unreached, "examples": examples[:20]}
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1)
    print(result)


if __name__ == "__main__":
    main()
