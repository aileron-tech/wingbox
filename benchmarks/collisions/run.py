"""Python import edges of a generated corpus against what the interpreter itself loads.

Usage: python3 -I benchmarks/collisions/run.py <work dir> "<wingbox command>" [<out.json>]
       e.g. python3 -I benchmarks/collisions/run.py /tmp/collisions "uvx --from wingbox==0.17.0 wingbox"

Exit status 1 when an edge is wrong or missing.

The corpus is a set of small projects whose names collide on purpose: a package folder beside a module
file of the same name (a command line tool and the package it drives), a module named like its
package, a folder without `__init__.py` beside a module file, a package under `src/`, a script that
imports the file beside it, relative imports at the top of a file, in a function and in a method.

The reference is Python: every file is loaded by the interpreter (a file of a package by its dotted
name with the project root on the path, any other file as a script with its own folder first, as
`python file.py` does), and a hook on `__import__` records which file each import statement of that
file loads. No resolver is written here: what `import depot` means is what CPython says.

Required: the file of the module a statement names, and of a submodule named after `from`. Accepted
and not required: a package `__init__.py` that Python runs on the way. Wrong: an edge of the graph
that is neither. Missing: a required edge the graph lacks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

TOOL = "from depot import ledger\nfrom depot import crate\nimport depot\n\n\ndef main():\n    return ledger.load(), crate.load(), depot.__name__\n"
MODULES = {
    "crate.py": "def load():\n    return 'crate'\n",
    "pallet.py": "def load():\n    return 'pallet'\n",
    "ledger.py": "from .crate import load as load_crate\n\n\ndef load():\n    return load_crate()\n",
    "shelf.py": "from . import crate\n\n\ndef build():\n    from . import pallet\n    return crate.load(), pallet.load()\n",
    "label.py": "from .crate import load\n\n\nclass Label:\n    def text(self):\n        from . import pallet\n        from .ledger import load as ledger\n        return load(), pallet.load(), ledger()\n",
}
INIT = "__all__ = ['crate', 'ledger']\n"

# project -> {file: text}. Every function with an import in its body is called by the reference.
PROJECTS: dict[str, dict[str, str]] = {
    # A command line tool beside the package it drives, both named depot.
    "tool_beside_package": {"depot.py": TOOL, "depot/__init__.py": INIT, **{f"depot/{name}": text for name, text in MODULES.items()},
                            "tests/test_depot.py": "import depot\nfrom depot.shelf import build\n\n\ndef test():\n    return depot, build\n"},
    # The same, two folders down, with a second tool that imports the first by its file name.
    "nested_tool": {"apps/stock/depot.py": TOOL, "apps/stock/depot/__init__.py": INIT,
                    **{f"apps/stock/depot/{name}": text for name, text in MODULES.items()},
                    "apps/stock/audit.py": "import depot\nfrom depot import shelf\n\n\ndef run():\n    return depot, shelf\n"},
    # A module named like its package: depot/depot.py.
    "module_named_like_package": {"depot/__init__.py": "", "depot/depot.py": "from . import crate\n\n\ndef open():\n    return crate.load()\n",
                                  "depot/crate.py": MODULES["crate.py"],
                                  "run.py": "from depot import depot\nfrom depot.depot import open\nimport depot.crate\n\n\ndef main():\n    return depot, open\n"},
    # A folder without __init__.py beside a module file of its name: `import depot` is the file.
    "folder_without_init": {"depot.py": "def main():\n    return 0\n", "depot/crate.py": MODULES["crate.py"],
                            "use.py": "import depot\n\n\ndef main():\n    return depot.main()\n"},
    # The package under src/, a tool of its name at the root, tests that add src/ to the path.
    "package_under_src": {"src/depot/__init__.py": INIT, **{f"src/depot/{name}": text for name, text in MODULES.items()},
                          "src/depot.py": TOOL},
    # A script imports the file beside it by its bare name; a file of that name lies at the root too.
    "script_beside_script": {"helper.py": "ROOT = 1\n", "tools/helper.py": "LOCAL = 1\n",
                             "tools/sweep.py": "import helper\n\n\ndef main():\n    return helper.LOCAL\n",
                             "main.py": "import helper\n\n\ndef main():\n    return helper.ROOT\n"},
    # Packages two deep with the same last name, and an import of a dotted chain.
    "same_last_name": {"store/__init__.py": "", "store/depot/__init__.py": "", "store/depot/crate.py": MODULES["crate.py"],
                       "ship/__init__.py": "", "ship/depot/__init__.py": "", "ship/depot/crate.py": MODULES["crate.py"],
                       "ship/depot/route.py": "from . import crate\nfrom ..depot import crate as again\nimport store.depot.crate\n\n\ndef plan():\n    return crate, again\n",
                       "plan.py": "from ship.depot import route\nfrom store.depot.crate import load\n\n\ndef main():\n    return route, load\n"},
}

# Loaded by the interpreter for one file: prints the files its own import statements load.
DRIVER = r'''
import builtins, importlib, importlib.util, json, os, runpy, sys, types
root, target = sys.argv[1], os.path.realpath(sys.argv[2])
found = []
real_import = builtins.__import__

def spy(name, globals=None, locals=None, fromlist=(), level=0):
    module = real_import(name, globals, locals, fromlist, level)
    if globals and os.path.realpath(str(globals.get("__file__", ""))) == target:
        full = importlib.util.resolve_name("." * level + name, globals.get("__package__") or "") if level else name
        named = sys.modules.get(full)
        file = getattr(named, "__file__", None)
        if file:
            found.append(["module", os.path.realpath(file)])
        for item in fromlist or ():
            sub = sys.modules.get(full + "." + item)
            if getattr(sub, "__file__", None):
                found.append(["submodule", os.path.realpath(sub.__file__)])
    return module

builtins.__import__ = spy
folder = os.path.dirname(target)
relative = os.path.relpath(target, root)
if os.path.isfile(os.path.join(folder, "__init__.py")):
    # A file of a package: the top package folder's parent is on the path, as for `python -m` and for tests.
    top = folder
    parts = [os.path.splitext(os.path.basename(target))[0]]
    while os.path.isfile(os.path.join(top, "__init__.py")):
        parts.insert(0, os.path.basename(top))
        top = os.path.dirname(top)
    if parts[-1] == "__init__":
        parts.pop()
    sys.path[:0] = [top, root]
    namespace = vars(importlib.import_module(".".join(parts)))
else:
    sys.path[:0] = [folder, root]
    namespace = runpy.run_path(target, run_name="__reference__")
# The imports inside functions and methods run when they are called.
for value in list(namespace.values()):
    callables = [value] if isinstance(value, types.FunctionType) else \
        [member for member in vars(value).values() if isinstance(member, types.FunctionType)] if isinstance(value, type) else []
    for function in callables:
        if os.path.realpath(function.__code__.co_filename) != target:
            continue
        try:
            function(*([object()] * function.__code__.co_argcount))
        except Exception:
            pass
print(json.dumps(found))
'''


def write_corpus(work: str) -> list[str]:
    files = []
    for project, contents in sorted(PROJECTS.items()):
        for name, text in sorted(contents.items()):
            path = os.path.join(work, project, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            files.append(f"{project}/{name}")
    return files


def reference(work: str, files: list[str]) -> tuple[set[tuple[str, str]], set[tuple[str, str]], int]:
    """(required edges, accepted edges, import statements seen) from the interpreter, file by file."""
    work = os.path.realpath(work)
    required: set[tuple[str, str]] = set()
    accepted: set[tuple[str, str]] = set()
    seen = 0
    for file in files:
        project = file.split("/", 1)[0]
        done = subprocess.run([sys.executable, "-I", "-c", DRIVER, os.path.join(work, project), os.path.join(work, file)],
                              capture_output=True, text=True, cwd=os.path.join(work, project), timeout=60)
        if done.returncode != 0:
            raise SystemExit(f"the interpreter could not load {file}:\n{done.stderr}")
        for kind, loaded in json.loads(done.stdout.strip().splitlines()[-1]):
            if not loaded.startswith(work + os.sep):
                continue
            seen += 1
            target = os.path.relpath(loaded, work).replace(os.sep, "/")
            if target != file:
                required.add((file, target))
                # The packages Python runs on the way to the module: accepted when the graph has them.
                folder = os.path.dirname(target)
                while folder and folder != project:
                    init = folder + "/__init__.py"
                    if os.path.isfile(os.path.join(work, init)) and init not in (file, target):
                        accepted.add((file, init))
                    folder = os.path.dirname(folder)
    return required, accepted | required, seen


def graph_edges(work: str, wingbox: list[str]) -> set[tuple[str, str]]:
    """The import edges of the graph Wingbox builds of the corpus, as file pairs, read from the graph file
    by benchmarks/impact/tools/wb_edges.py."""
    subprocess.run([*wingbox, "build", "."], capture_output=True, text=True, cwd=work, check=True)
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "impact", "tools", "wb_edges.py")
    out = os.path.join(work, ".wingbox", "edges.json")
    subprocess.run([sys.executable, "-I", tool, work, out, "--ext", ".py"], capture_output=True, text=True, check=True)
    with open(out, encoding="utf-8") as handle:
        return {tuple(pair) for pair in json.load(handle)["edges"].get("imports", [])}


def run(work: str, wingbox: list[str]) -> dict:
    work = os.path.realpath(work)
    os.makedirs(work, exist_ok=True)
    files = write_corpus(work)
    required, accepted, seen = reference(work, files)
    graph = graph_edges(work, wingbox)
    version = subprocess.run([*wingbox, "--version"], capture_output=True, text=True).stdout.strip()
    return {
        "wingbox": version, "python": sys.version.split()[0], "projects": len(PROJECTS), "files": len(files),
        "imports_loaded_by_the_interpreter": seen, "required_edges": len(required), "graph_edges": len(graph),
        "wrong": sorted(graph - accepted), "missing": sorted(required - graph),
        "accepted_and_not_required": len((graph & accepted) - required),
    }


def report(results: str, out_path: str) -> None:
    """Write README.md from the result files of the versions that were run (results/<version>.json)."""
    rows = []
    for name in sorted(os.listdir(results)):
        if name.endswith(".json"):
            with open(os.path.join(results, name), encoding="utf-8") as handle:
                rows.append(json.load(handle))
    rows.sort(key=lambda row: [int(part) if part.isdigit() else 0 for part in row["wingbox"].replace("wingbox ", "").split(".")])
    first = rows[-1]
    lines = [
        "# Wingbox benchmark: Python names that collide, against the interpreter",
        "",
        "This file is written by `run.py --report` from the files in `results/`; no number is typed by hand.",
        "",
        "## The question",
        "",
        "Which file does an import statement load, when two files could answer to the name? The corpus is "
        f"{first['projects']} small projects ({first['files']} files) written by `run.py`, each with a collision on purpose:",
        "",
        "- a command line tool `depot.py` beside the package `depot/` it drives, at the root and two folders down;",
        "- a module named like its package (`depot/depot.py`);",
        "- a folder without `__init__.py` beside a module file of its name;",
        "- a package under `src/` with a tool of its name beside it;",
        "- a script that imports the file beside it by its bare name, while a file of that name lies at the root;",
        "- two packages with the same last name, and an import of a dotted chain;",
        "- relative imports at the top of a file, in a function and in a method.",
        "",
        "## The reference",
        "",
        "Python itself. Every file of the corpus is loaded by the interpreter: a file of a package by its dotted name with the "
        "folder above its top package on the path, any other file as a script with its own folder first, as `python file.py` "
        "does; the functions and methods of the file are called, so that the imports inside them run. A hook on `__import__` "
        "records the file each import statement of that file loads. No resolver is written for the reference.",
        "",
        "Required: the file of the module a statement names, and of a submodule named after `from`. Accepted and not required: "
        "a package `__init__.py` Python runs on the way. Wrong: an edge of the graph that is neither. Missing: a required edge "
        "the graph lacks.",
        "",
        "## Results",
        "",
        "| Wingbox | Loads seen by the interpreter | Required edges | Edges in the graph | Wrong | Missing |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(f"| {row['wingbox'].replace('wingbox ', '')} | {row['imports_loaded_by_the_interpreter']} | {row['required_edges']} | "
                     f"{row['graph_edges']} | {len(row['wrong'])} | {len(row['missing'])} |")
    lines += [
        "",
        f"Python {first['python']}. A version with a wrong or a missing edge takes a module file for the package of its name "
        "(issue #177, fixed in 0.16.1): the files of the package become dependents of the tool beside it.",
        "",
        "## Reproduce",
        "",
        "```sh",
        'python3 -I benchmarks/collisions/run.py /tmp/collisions "uvx --from wingbox==0.17.0 wingbox" result.json',
        "```",
        "",
        "The exit status is 1 when an edge is wrong or missing. `tests/test_benchmark_cpp_collisions.py` runs the same corpus "
        "against the checkout.",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        report(sys.argv[2], sys.argv[3])
        sys.exit(0)
    result = run(sys.argv[1], sys.argv[2].split())
    if len(sys.argv) > 3:
        with open(sys.argv[3], "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=1)
            handle.write("\n")
    print(json.dumps(result, indent=1))
    sys.exit(1 if result["wrong"] or result["missing"] else 0)
