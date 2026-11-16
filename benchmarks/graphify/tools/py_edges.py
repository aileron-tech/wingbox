"""Reference file-to-file import edges of a Python tree, from Python's own parser (ast).

Usage: python3 -I py_edges.py <root> <out.json> [--repo-roots a,b,c]

Rules (those of the Python import system, applied statically):
  - every Import / ImportFrom node of every .py file counts, wherever it stands
    (module level, function body, try/except, `if TYPE_CHECKING:`);
  - a relative import resolves from the package of the importing file;
  - an absolute import resolves against, in this order: the directory of the
    importing file when that directory is not a package (a script: sys.path[0]),
    then the repository root that contains the file;
  - `import a.b.c` -> the file of module a.b.c;
  - `from a.b import x` -> the file of submodule a.b.x when it exists, otherwise
    the file of a.b (a module file or a package __init__.py);
  - `to_followed`: the file that defines an imported name, following re-exports
    (`from .m import x` inside the module the name was imported from), at most
    five steps; equal to `to` when nothing is re-exported;
  - `implied`: package __init__.py files that Python executes on the way to the named
    module (`import a.b.c` runs a/__init__.py and a/b/__init__.py; `from a.b import x`
    with x a submodule runs a/b/__init__.py). An edge to such a file is a true
    dependency; it is accepted when a tool has it and not required when it has not;
  - a name that resolves to nothing in the tree is external (stdlib, third party).
No code of the tree is executed.
"""
from __future__ import annotations

import ast
import json
import os
import sys

root = os.path.abspath(sys.argv[1])
out_path = sys.argv[2]
repo_roots = [""]
if "--repo-roots" in sys.argv:
    repo_roots = [r.strip("/") for r in sys.argv[sys.argv.index("--repo-roots") + 1].split(",")]

files: list[str] = []
for base, dirs, names in os.walk(root):
    dirs[:] = sorted(d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"})
    for name in sorted(names):
        if name.endswith(".py"):
            files.append(os.path.relpath(os.path.join(base, name), root))
file_set = set(files)


def module_file(directory: str, dotted: list[str]) -> str | None:
    """File of module `dotted` looked up from `directory` (both relative to root)."""
    path = os.path.normpath(os.path.join(directory, *dotted)) if dotted else os.path.normpath(directory)
    path = "" if path == "." else path
    if path.startswith(".."):
        return None
    if dotted and path + ".py" in file_set:
        return path + ".py"
    init = os.path.join(path, "__init__.py") if path else "__init__.py"
    if init in file_set:
        return init
    return None


def is_dir(directory: str, dotted: list[str]) -> bool:
    return os.path.isdir(os.path.join(root, directory, *dotted))


def repo_root_of(path: str) -> str:
    best = ""
    for candidate in repo_roots:
        if candidate and (path == candidate or path.startswith(candidate + "/")) and len(candidate) > len(best):
            best = candidate
    return best


def search_dirs(path: str) -> list[str]:
    directory = os.path.dirname(path)
    result = []
    init = os.path.join(directory, "__init__.py") if directory else "__init__.py"
    if init not in file_set:
        result.append(directory)
    repo = repo_root_of(path)
    if repo not in result:
        result.append(repo)
    return result


def resolve_absolute(path: str, dotted: list[str]) -> tuple[str | None, str | None]:
    """(file of the module, directory it was found from) for an absolute name."""
    for directory in search_dirs(path):
        first = module_file(directory, dotted[:1])
        if first is None and not is_dir(directory, dotted[:1]):
            continue
        found = module_file(directory, dotted)
        if found:
            return found, directory
        if is_dir(directory, dotted):
            return None, directory  # namespace package: no file of its own
    return None, None


trees: dict[str, ast.AST] = {}
parse_errors: list[str] = []
for path in files:
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            trees[path] = ast.parse(handle.read(), filename=path)
    except SyntaxError as error:
        parse_errors.append(f"{path}: {error}")


def imported_names(path: str) -> dict[str, tuple[str, list[str], int, str]]:
    """Names a module binds by `from X import name` at module level: name -> (kind, dotted, level, original)."""
    table = {}
    tree = trees.get(path)
    if tree is None:
        return table
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    table[alias.asname or alias.name] = ("from", (node.module or "").split(".") if node.module else [], node.level, alias.name)
    return table


def defines(path: str, name: str) -> bool:
    tree = trees.get(path)
    if tree is None:
        return False
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
    return False


def from_target(path: str, dotted: list[str], level: int, name: str) -> tuple[str | None, str | None]:
    """(module file the statement names, submodule file when `name` is a submodule)."""
    if level:
        directory = os.path.dirname(path)
        for _ in range(level - 1):
            directory = os.path.dirname(directory)
        base = module_file(directory, dotted)
        sub = module_file(directory, dotted + [name]) if name != "*" and is_dir(directory, dotted) else None
        if sub and sub.endswith("__init__.py") and not is_dir(directory, dotted + [name]):
            sub = None
        return base, sub
    base, directory = resolve_absolute(path, dotted)
    if directory is None:
        return None, None
    sub = None
    if name != "*" and is_dir(directory, dotted):
        candidate = module_file(directory, dotted + [name])
        if candidate and (candidate.endswith("/" + name + ".py") or is_dir(directory, dotted + [name])):
            sub = candidate
    return base, sub


def follow(path: str, name: str, steps: int = 5) -> str:
    """File that defines `name`, starting from module file `path`, following re-exports."""
    current = path
    for _ in range(steps):
        if defines(current, name):
            return current
        entry = imported_names(current).get(name)
        if entry is None:
            return current
        _, dotted, level, original = entry
        base, sub = from_target(current, dotted, level, original)
        target = sub or base
        if target is None or target == current:
            return current
        if sub:
            return sub
        current, name = target, original
    return current


def ancestors(target: str) -> list[str]:
    """__init__.py of every package above the module file `target`."""
    found = []
    directory = os.path.dirname(target)
    if target.endswith("__init__.py"):
        directory = os.path.dirname(directory)
    while directory:
        init = os.path.join(directory, "__init__.py")
        if init in file_set:
            found.append(init)
        directory = os.path.dirname(directory)
    return found


refs = []
implied: set[tuple[str, str]] = set()
external = 0
for path in files:
    tree = trees.get(path)
    if tree is None:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted = alias.name.split(".")
                target, _ = resolve_absolute(path, dotted)
                if target is None:
                    external += 1
                    continue
                refs.append({"frm": path, "to": target, "to_followed": target, "line": node.lineno,
                             "stmt": "import " + alias.name, "kind": "import"})
                implied.update((path, init) for init in ancestors(target))
        elif isinstance(node, ast.ImportFrom):
            dotted = node.module.split(".") if node.module else []
            for alias in node.names:
                base, sub = from_target(path, dotted, node.level, alias.name)
                target = sub or base
                if target is None:
                    external += 1
                    continue
                followed = target if sub or alias.name == "*" else follow(target, alias.name)
                implied.update((path, init) for init in ancestors(target))
                if sub and base:
                    implied.add((path, base))
                refs.append({"frm": path, "to": target, "to_followed": followed, "line": node.lineno,
                             "stmt": "from " + "." * node.level + (node.module or "") + " import " + alias.name,
                             "kind": "from-submodule" if sub else "from"})

pairs = sorted({(r["frm"], r["to"]) for r in refs if r["frm"] != r["to"]})
followed_pairs = sorted({(r["frm"], r["to_followed"]) for r in refs if r["frm"] != r["to_followed"]})
implied_pairs = sorted(p for p in implied if p[0] != p[1] and p not in set(pairs))
json.dump({"root": os.path.basename(root), "py_files": files, "parse_errors": parse_errors, "import_names_external": external,
           "refs": refs, "pairs": pairs, "followed_pairs": followed_pairs, "implied_pairs": implied_pairs},
          open(out_path, "w"), indent=1)
print("files", len(files), "parse errors", len(parse_errors), "first-party import names", len(refs),
      "external", external, "file pairs", len(pairs), "followed pairs", len(followed_pairs), "implied", len(implied_pairs))
