"""Third-party check of the reference: the import graph of the packages of a tree built by grimp
(the engine of import-linter), mapped to files and compared with the pairs of py_edges.py.

Usage: <venv with grimp>/bin/python grimp_check.py <repo root> <reference.json> <out.json> <package> [<package> ...]
grimp reads the source statically; the packages are located through sys.path and are not executed.
"""
import json
import os
import sys

root, ref_path, out_path = sys.argv[1:4]
packages = sys.argv[4:]
sys.path.insert(0, os.path.abspath(root))
import grimp  # noqa: E402

graph = grimp.build_graph(*packages)


def file_of(module: str):
    path = module.replace(".", "/")
    if os.path.isfile(os.path.join(root, path + ".py")):
        return path + ".py"
    if os.path.isfile(os.path.join(root, path, "__init__.py")):
        return path + "/__init__.py"
    return None


pairs = set()
no_file = set()
for module in graph.modules:
    a = file_of(module)
    if a is None:
        no_file.add(module)
        continue
    for imported in graph.find_modules_directly_imported_by(module):
        b = file_of(imported)
        if b is None:
            no_file.add(imported)
        elif a != b:
            pairs.add((a, b))
ref = json.load(open(ref_path))
covered_files = {file_of(m) for m in graph.modules} - {None}
inside = lambda p: p[0] in covered_files and p[1] in covered_files
strict = {tuple(p) for p in ref["pairs"] if inside(p)}
implied = {tuple(p) for p in ref["implied_pairs"]}
result = {"packages": packages, "grimp_modules_with_file": len(covered_files), "reference_py_files": len(ref["py_files"]),
          "grimp_pairs": len(pairs), "reference_pairs_between_covered_files": len(strict), "in_both": len(pairs & strict),
          "grimp_only_explained_as_implied_package_init": sorted((pairs - strict) & implied),
          "grimp_only_unexplained": sorted(pairs - strict - implied), "reference_only": sorted(strict - pairs),
          "modules_without_file": sorted(no_file)[:50]}
json.dump(result, open(out_path, "w"), indent=1)
print({k: (v if isinstance(v, (int, list)) and not isinstance(v, list) else len(v)) for k, v in result.items()})
for p in result["grimp_only_unexplained"][:15]: print("grimp only:", p)
for p in result["reference_only"][:15]: print("reference only:", p)
