"""Source trees for the tests of the output forms, and a way to run a command on a built graph.

`tests/data/outputs_0_13_0.json` holds what version 0.13.0 printed for the calls listed here. It was
written by this file, copied into a checkout of the tag v0.13.0 and run there:

    python -m tests.output_trees tests/data/outputs_0_13_0.json

A long output is kept as its length and SHA-256.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts import build_code_graph, code_graph_search
from tests.test_impact_evidence import RUST_FILES, TS_FILES

REPO = Path(__file__).resolve().parents[1]
KEPT_FILE = Path(__file__).parent / "data" / "outputs_0_13_0.json"

PYTHON_FILES = {
    "app/__init__.py": "from .models import Invoice\n",
    "app/models.py": "class Invoice:\n    def total(self): ...\n",
    "app/billing.py": (
        "from app import Invoice\n"
        "from app.models import Invoice as Inv\n"
        "\n"
        "class Draft(Invoice):\n"
        "    pass\n"
        "\n"
        "def make(x: Invoice) -> Inv:\n"
        "    inv = Invoice()\n"
        "    return inv.total()\n"
    ),
    "app/other.py": "def f(x):\n    return x.total()\n",
    "legacy/models.py": "class Invoice:\n    pass\n",
}

WIDE_SERVICES = 205


def wide_files() -> dict[str, str]:
    """A hub that 205 services import, each used by one handler, and a facade over ten of the services:
    more dependents than the text form lists, at two depths."""
    files = {"src/core/hub.ts": "export const limit = 3;\nexport function clamp(value: number): number { return value; }\n"}
    for index in range(WIDE_SERVICES):
        name = f"service{index:03d}"
        files[f"src/services/{name}.ts"] = (
            'import { clamp, limit } from "../core/hub";\n'
            f"export const {name} = clamp(limit) + clamp({index});\n"
        )
        files[f"src/handlers/handle{index:03d}.ts"] = (
            f'import {{ {name} }} from "../services/{name}";\nexport const handle{index:03d} = {name};\n'
        )
    files["src/facade.ts"] = "".join(
        f'import {{ service{index:03d} }} from "./services/service{index:03d}";\n' for index in range(10)
    ) + "export const all = [" + ", ".join(f"service{index:03d}" for index in range(10)) + "];\n"
    return files


TREES: dict[str, dict[str, str]] = {"rust": RUST_FILES, "ts": TS_FILES, "python": PYTHON_FILES, "wide": wide_files()}
ROOT_MARK = "<ROOT>"

# Calls of the search script whose output without a form option is kept as version 0.13.0 printed it:
# (tree, arguments as 0.13.0 took them). The same call with --human must print the same bytes.
HUMAN_CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rust", ("impact", "src/native/scripts.rs")),
    ("rust", ("impact", "src/native/scripts.rs", "3", "--all")),
    ("rust", ("impact", "src/settings/running.rs")),
    ("rust", ("impact", "src/settings/seed.rs", "1")),
    ("rust", ("impact", "src/many.rs", "1")),
    ("rust", ("impact", "src/main.rs")),
    ("rust", ("impact", "src/nowhere.rs")),
    ("rust", ("usages", "src/native/actions.rs::HarEntry")),
    ("rust", ("usages", "script")),
    ("rust", ("usages", "Nowhere")),
    ("rust", ("deps", "src/native/actions", "2")),
    ("rust", ("rdeps", "src/native/scripts", "2")),
    ("rust", ("analyze", "src/native/actions")),
    ("rust", ("quick", "running")),
    ("rust", ("module", "src/many")),
    ("rust", ("module", "src/native/actions", "--lines")),
    ("rust", ("boundary", "src/store")),
    ("rust", ("boundary", "src/store", "--files-only")),
    ("rust", ("entity", "HarEntry")),
    ("rust", ("entity", "HarEntry", "--positions")),
    ("rust", ("flow", "src/native/actions", "act")),
    ("rust", ("search", "HarEntry")),
    ("rust", ("search", "HarEntry", "--lines")),
    ("rust", ("layers",)),
    ("rust", ("boundaries",)),
    ("rust", ("boundaries", "all", "--strict")),
    ("rust", ("invariants",)),
    ("rust", ("find_function", "act")),
    ("rust", ("find_type", "HarEntry")),
    ("rust", ("find_callers", "script", "1")),
    ("rust", ("find_calls", "act", "1")),
    ("rust", ("find_callchain", "main", "script")),
    ("rust", ("grep_functions", "script")),
    ("rust", ("query", '(count (rdeps "src/native/scripts" 2))')),
    ("ts", ("impact", "src/core/util.ts")),
    ("ts", ("impact", "src/api/handler.ts")),
    ("ts", ("usages", "src/core/util.ts::limit")),
    ("ts", ("analyze", "src/core/util")),
    ("ts", ("rdeps", "src/core/util", "2")),
    ("python", ("impact", "app/models.py")),
    ("python", ("usages", "app/models.py::Invoice")),
    ("python", ("usages", "Invoice")),
    ("python", ("usages", "app/models.py::Invoice.total")),
    ("wide", ("impact", "src/core/hub.ts")),
    ("wide", ("impact", "src/core/hub.ts", "2", "--all")),
    ("wide", ("usages", "src/core/hub.ts::clamp")),
    ("wide", ("usages", "src/core/hub.ts::clamp", "--all")),
    ("wide", ("analyze", "src/core/hub")),
    ("wide", ("analyze", "src/core/hub", "--all")),
)

# Calls with --sexp, kept as version 0.13.0 printed them: the flat facts that scripts written for
# 0.13 and earlier read (the pull request check calls `impact <file> <hops> --sexp --all` and
# `usages <target> --sexp --all`). The same call must print the same bytes.
SEXP_CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rust", ("impact", "src/native/scripts.rs", "2", "--sexp", "--all")),
    ("rust", ("impact", "src/native/scripts.rs", "3", "--sexp")),
    ("rust", ("impact", "src/settings/running.rs", "2", "--sexp", "--all")),
    ("rust", ("impact", "src/settings/seed.rs", "1", "--sexp", "--all")),
    ("rust", ("impact", "src/many.rs", "--sexp")),
    ("rust", ("impact", "--sexp", "src/main.rs", "2", "--all")),
    ("rust", ("impact", "src/nowhere.rs", "2", "--sexp", "--all")),
    ("rust", ("impact", "README.md", "2", "--sexp", "--all")),
    ("rust", ("usages", "src/native/actions.rs::HarEntry", "--sexp", "--all")),
    ("rust", ("usages", "src/native/actions::HarEntry", "--sexp", "--all")),
    ("rust", ("usages", "script", "--sexp")),
    ("rust", ("usages", "Nowhere", "--sexp", "--all")),
    ("ts", ("impact", "src/core/util.ts", "2", "--sexp", "--all")),
    ("ts", ("impact", "src/api/handler.ts", "2", "--sexp", "--all")),
    ("ts", ("usages", "src/core/util::limit", "--sexp", "--all")),
    ("python", ("impact", "app/models.py", "2", "--sexp", "--all")),
    ("python", ("usages", "app/models::Invoice", "--sexp", "--all")),
    ("python", ("usages", "Invoice", "--sexp", "--all")),
    ("python", ("usages", "app/models::Invoice.total", "--sexp", "--all")),
    ("wide", ("impact", "src/core/hub.ts", "2", "--sexp")),
    ("wide", ("impact", "src/core/hub.ts", "2", "--sexp", "--all")),
    ("wide", ("usages", "src/core/hub::clamp", "--sexp")),
    ("wide", ("usages", "src/core/hub::clamp", "--sexp", "--all")),
)

# A project with layers, one import that breaks a layer rule and one that follows them.
LAYERED = {
    "package.json": '{"name": "shop"}\n',
    **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
       for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)},
    "src/models/models0.ts": "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n",
    "src/services/services0.ts": "import { models0 } from '../models/models0';\nexport const services0 = models0;\n",
}

# Calls of the `wingbox` command in the LAYERED project, kept as version 0.13.0 printed them (standard
# output and exit status). The commands the pull request check runs are among them.
CLI_CASES: tuple[tuple[str, ...], ...] = (
    ("impact", "src/routes/routes0.ts"),
    ("impact", "src/routes/routes0.ts", "2", "--sexp", "--all"),
    ("impact", "package.json", "2", "--sexp", "--all"),
    ("usages", "src/routes/routes0::routes0"),
    ("usages", "src/routes/routes0::routes0", "--sexp", "--all"),
    ("module", "src/models/models0"),
    ("boundaries",),
    ("boundaries", "--strict"),
    ("advice",),
    ("advice", "--sexp"),
    ("advice", "--learned"),
    ("advice", "--learned", "--sexp"),
    ("align",),
    ("align", "--sexp"),
    ("compare",),
    ("compare", "--sexp"),
    ("place", "a new route for users"),
    ("place", "a new route for users", "--sexp"),
)
# Of those, the calls without a form option whose output is now what --human prints.
CLI_TEXT_COMMANDS = ("impact", "usages", "advice", "align", "compare", "place")


def case_key(tree: str, argv: tuple[str, ...]) -> str:
    return tree + " | " + " ".join(argv)


def names_the_folder(argv: tuple[str, ...]) -> bool:
    """Whether the output of a call holds absolute paths: they are written in the form of the system, so such an
    output is compared only where paths are written with /."""
    return argv[0] in ("module", "boundary", "entity", "find_function", "find_type", "find_callers", "find_calls",
                       "find_callchain", "grep_functions")


def kept(status: int, output: str) -> dict[str, object]:
    """What is kept of one output: the text itself, or for a long one its length and SHA-256."""
    if len(output) <= 4000:
        return {"status": status, "output": output}
    return {"status": status, "length": len(output), "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest()}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class BuiltTrees:
    """Each tree written to a folder and built once, as `wingbox build` does."""

    def __init__(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.roots: dict[str, Path] = {}
        base = Path(self._folder.name).resolve()
        for name, files in TREES.items():
            root = base / name
            write_tree(root, files)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if build_code_graph.main(["--root", str(root)]) != 0:
                    raise AssertionError(f"the tree {name} does not build")
            self.roots[name] = root

    def close(self) -> None:
        self._folder.cleanup()

    def run_raw(self, tree: str, argv: list[str]) -> tuple[int, str]:
        """(exit status, standard output) of `code_graph_search.py <argv>` on the graph of a tree."""
        root = self.roots[tree]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            status = code_graph_search.main([*argv, "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])
        return int(status), output.getvalue()

    def run(self, tree: str, argv: list[str]) -> tuple[int, str]:
        """The same with the folder of the tree written <ROOT> in the output."""
        status, output = self.run_raw(tree, argv)
        return status, output.replace(str(self.roots[tree]), ROOT_MARK)


def command_environment(**extra: str) -> dict[str, str]:
    """The environment of a `wingbox` process of a test: this checkout on the module path."""
    environment = dict(os.environ, **extra)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment.pop("CLAUDECODE", None)
    return environment


class BuiltProject:
    """The LAYERED project in a git repository, built with `wingbox build`; commands run in it as processes."""

    def __init__(self, files: dict[str, str] = LAYERED) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._folder.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        write_tree(self.root, files)
        built = self.wingbox("build", str(self.root))
        if built.returncode != 0:
            raise AssertionError(built.stderr)

    def close(self) -> None:
        self._folder.cleanup()

    def wingbox(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=self.root, env=command_environment(**env), timeout=600)

    def run(self, *args: str, **env: str) -> tuple[int, str]:
        """(exit status, standard output with the folder of the project written <ROOT>)."""
        done = self.wingbox(*args, **env)
        return done.returncode, done.stdout.replace(str(self.root), ROOT_MARK)


def read_forms(text: str) -> list:
    """The forms of an answer: a form is a list of atoms (str) and forms; quotes are removed."""
    forms: list = []
    stack: list[list] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "(":
            stack.append([])
            i += 1
        elif ch == ")":
            done = stack.pop()
            (stack[-1] if stack else forms).append(done)
            i += 1
        elif ch == '"':
            j = i + 1
            chars: list[str] = []
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                chars.append(text[j])
                j += 1
            stack[-1].append("".join(chars))
            i = j + 1
        else:
            j = i
            while j < len(text) and not text[j].isspace() and text[j] not in "()":
                j += 1
            stack[-1].append(text[i:j])
            i = j
    if stack:
        raise AssertionError("an answer with an open bracket")
    return forms


def record() -> dict[str, dict[str, object]]:
    """What this checkout prints for every kept call."""
    found: dict[str, dict[str, object]] = {}
    trees = BuiltTrees()
    try:
        for tree, argv in HUMAN_CASES + SEXP_CASES:
            found[case_key(tree, argv)] = kept(*trees.run(tree, list(argv)))
    finally:
        trees.close()
    project = BuiltProject()
    try:
        for argv in CLI_CASES:
            found[case_key("cli", argv)] = kept(*project.run(*argv))
    finally:
        project.close()
    return found


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
