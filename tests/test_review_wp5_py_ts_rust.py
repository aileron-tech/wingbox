"""Defects of the Python, TypeScript and Rust readers found in the review of 0.19.1: one test per defect."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator, Union

import scripts.build_code_graph as builder
from scripts.build_code_graph import Ok, build_graph, discover_files, parse_file

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "code_graph_search.py"
_SPEC = importlib.util.spec_from_file_location("code_graph_search_for_review_wp5", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
search = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = search
_SPEC.loader.exec_module(search)

Files = dict[str, Union[str, bytes]]


@contextlib.contextmanager
def _tree(files: Files) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        yield root


def _graph(root: Path) -> tuple[list[dict], list[dict], str]:
    """(nodes, edges, what the readers printed to standard error) of the files discovery finds under root."""
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        results = [parse_file(found) for found in sorted(discover_files(str(root)))]
        graph = build_graph(results, str(root))
    if not isinstance(graph, Ok):
        raise AssertionError(f"graph build failed: {graph.error}")
    return graph.value.nodes, graph.value.edges, errors.getvalue()


def _imports(edges: list[dict], source: str = "") -> set[tuple[str, str]]:
    return {
        (edge["source"], edge["target"])
        for edge in edges
        if edge["type"] == "IMPORTS" and (not source or edge["source"] == source)
    }


def _entities(nodes: list[dict], module: str) -> dict[str, dict]:
    node = next(node for node in nodes if node["id"] == module)
    return {entity["name"]: entity for entity in node.get("entities", [])}


def _query(root: Path, *argv: str) -> str:
    """Build the graph of root as `wingbox build` does, then answer one query command."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert builder.main(["--root", str(root)]) == 0
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        search.main([*argv, "--map", str(root / ".wingbox/index.sexp"), "--root", str(root)])
    return output.getvalue()


PROJECT = '[project]\nname = "{name}"\nversion = "1"\n'


class PythonSourceRootTests(unittest.TestCase):
    def test_a_package_of_another_part_is_found_through_its_source_root(self) -> None:
        """/* REQ-PY-9500: an absolute import that no folder near the importing file holds resolves through the source roots of the repository: a folder that holds a `pyproject.toml`, `setup.py` or `setup.cfg`, its `src/` folder, and the folders those files name (`[tool.setuptools.packages.find] where`, `[tool.setuptools] package-dir`, pytest's `pythonpath`, `[tool.poetry] packages from`, the parent of a `[tool.hatch.build.targets.wheel] packages` entry, `package_dir` and `where` of `setup.cfg` and `setup.py`); `from package import module` reaches the module too. */"""
        files: Files = {
            "pyproject.toml": PROJECT.format(name="root") + (
                '[tool.setuptools.packages.find]\nwhere = ["lib"]\n'
                '[tool.pytest.ini_options]\npythonpath = ["libs/shared"]\n'
            ),
            "lib/findpkg/__init__.py": "", "lib/findpkg/session.py": "class Session: ...\n",
            "libs/shared/sharedpkg/__init__.py": "", "libs/shared/sharedpkg/thing.py": "S = 1\n",
            "packages/sub/pyproject.toml": PROJECT.format(name="sub-dist"),
            "packages/sub/src/subpkg/__init__.py": "", "packages/sub/src/subpkg/transport.py": "T = 1\n",
            "packages/flat/pyproject.toml": PROJECT.format(name="flat-dist"),
            "packages/flat/flatpkg/__init__.py": "", "packages/flat/flatpkg/mod.py": "M = 1\n",
            "packages/dirs/pyproject.toml": PROJECT.format(name="dirs") + '[tool.setuptools.package-dir]\n"" = "python"\n',
            "packages/dirs/python/dirpkg/__init__.py": "", "packages/dirs/python/dirpkg/core.py": "D = 1\n",
            "packages/poet/pyproject.toml": '[tool.poetry]\nname = "poet"\npackages = [{include = "poetpkg", from = "code"}]\n',
            "packages/poet/code/poetpkg/__init__.py": "", "packages/poet/code/poetpkg/verse.py": "P = 1\n",
            "packages/hatch/pyproject.toml": '[tool.hatch.build.targets.wheel]\npackages = ["py/hatchpkg"]\n',
            "packages/hatch/py/hatchpkg/__init__.py": "", "packages/hatch/py/hatchpkg/egg.py": "H = 1\n",
            "packages/cfg/setup.cfg": "[options]\npackage_dir =\n    = source\n[options.packages.find]\nwhere = found\n[tool:pytest]\npythonpath = extra more\n",
            "packages/cfg/source/cfgpkg/__init__.py": "", "packages/cfg/source/cfgpkg/a.py": "A = 1\n",
            "packages/cfg/found/cfgfound/__init__.py": "", "packages/cfg/found/cfgfound/b.py": "B = 1\n",
            "packages/cfg/more/cfgmore/__init__.py": "", "packages/cfg/more/cfgmore/c.py": "C = 1\n",
            "packages/old/setup.py": (
                "from setuptools import find_packages, setup\n"
                'setup(name="old", package_dir={"": "py2"}, packages=find_packages(where="py3") + find_packages("py4"))\n'
            ),
            "packages/old/py2/oldpkg/__init__.py": "", "packages/old/py2/oldpkg/a.py": "A = 1\n",
            "packages/old/py3/newpkg/__init__.py": "", "packages/old/py3/newpkg/b.py": "B = 1\n",
            "packages/old/py4/lastpkg/__init__.py": "", "packages/old/py4/lastpkg/c.py": "C = 1\n",
            "tests/test_root.py": (
                "from findpkg.session import Session\n"
                "from sharedpkg import thing\n"
                "from subpkg import transport\n"
                "import flatpkg.mod\n"
                "from dirpkg.core import D\n"
                "from poetpkg import verse\n"
                "import hatchpkg.egg as egg\n"
                "from cfgpkg import a\n"
                "from cfgfound.b import B\n"
                "from cfgmore.c import C\n"
                "def late():\n"
                "    from oldpkg import a as old_a\n"
                "    import newpkg.b, lastpkg.c\n"
            ),
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual(
            {
                "lib/findpkg/session", "libs/shared/sharedpkg/__init__", "libs/shared/sharedpkg/thing",
                "packages/sub/src/subpkg/__init__", "packages/sub/src/subpkg/transport",
                "packages/flat/flatpkg/mod", "packages/dirs/python/dirpkg/core",
                "packages/poet/code/poetpkg/__init__", "packages/poet/code/poetpkg/verse",
                "packages/hatch/py/hatchpkg/egg", "packages/cfg/source/cfgpkg/__init__", "packages/cfg/source/cfgpkg/a",
                "packages/cfg/found/cfgfound/b", "packages/cfg/more/cfgmore/c",
                "packages/old/py2/oldpkg/__init__", "packages/old/py2/oldpkg/a",
                "packages/old/py3/newpkg/b", "packages/old/py4/lastpkg/c",
            },
            {target for _source, target in _imports(edges, "tests/test_root")},
        )

    def test_a_name_that_several_source_roots_hold_stays_unlinked(self) -> None:
        """/* REQ-PY-9501: an absolute import that two source roots hold is linked to neither; the `src/` nearest to the importing file still wins before the source roots are asked; a name of the standard library is never looked for in a source root; a folder a manifest names outside the repository is not read; and a manifest that cannot be read is named in a warning and leaves the build whole. */"""
        files: Files = {
            "packages/a/pyproject.toml": PROJECT.format(name="a") + '[tool.setuptools.packages.find]\nwhere = ["../../..", "/"]\n',
            "packages/a/src/dup/__init__.py": "", "packages/a/src/dup/x.py": "X = 1\n",
            "packages/a/src/onlya/__init__.py": "",
            "packages/a/tests/test_a.py": "from dup import x\nimport onlya\n",
            "packages/b/pyproject.toml": PROJECT.format(name="b"),
            "packages/b/src/dup/__init__.py": "", "packages/b/src/dup/x.py": "X = 2\n",
            "packages/b/logging.py": "def getLogger(name): ...\n",
            "packages/c/pyproject.toml": b'[project]\nname = "\xff\xfe"\n',
            "packages/c/setup.cfg": "no section header\n",
            "packages/c/setup.py": "setup(\n",
            "tools/check.py": "from dup import x\nimport dup.x\nimport logging\nimport onlya\n",
        }
        with _tree(files) as root:
            _nodes, edges, errors = _graph(root)
        self.assertEqual({("tools/check", "packages/a/src/onlya/__init__")}, _imports(edges, "tools/check"))
        self.assertEqual(
            {"packages/a/src/dup/__init__", "packages/a/src/dup/x", "packages/a/src/onlya/__init__"},
            {target for _source, target in _imports(edges, "packages/a/tests/test_a")},
        )
        for manifest in ("pyproject.toml", "setup.cfg", "setup.py"):
            self.assertIn(f"cannot read the Python source folders of {root / 'packages' / 'c' / manifest}", errors)


class PythonImportFormTests(unittest.TestCase):
    def test_from_package_import_subpackage_is_an_edge(self) -> None:
        """/* REQ-PY-9502: `from pkg import sub` and `from . import sub` depend on `pkg/sub/__init__.py` when `sub` is a subpackage, as they depend on `pkg/sub.py` when it is a module; a folder beside a module of the same name is taken first, as by Python's finder. */"""
        files: Files = {
            "pkg/__init__.py": "", "pkg/sub/__init__.py": "VALUE = 1\n", "pkg/both/__init__.py": "", "pkg/both.py": "",
            "pkg/inner.py": "from . import sub\nfrom . import both\n",
            "app/b.py": "from pkg import sub\n\nprint(sub.VALUE)\n",
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual({("app/b", "pkg/__init__"), ("app/b", "pkg/sub/__init__")}, _imports(edges, "app/b"))
        self.assertEqual(
            {("pkg/inner", "pkg/__init__"), ("pkg/inner", "pkg/sub/__init__"), ("pkg/inner", "pkg/both/__init__")},
            _imports(edges, "pkg/inner"),
        )

    def test_an_absolute_import_inside_a_package_is_not_the_sibling_module(self) -> None:
        """/* REQ-PY-9503: inside a package (the folder of the importing file holds `__init__.py`) `import logging` is not the sibling `logging.py` or the sibling folder `queue/`: Python 3 looks an absolute name up on the import path, where the folder of a package is not; in a folder that is not a package (a script folder) the sibling is still what the name means. */"""
        files: Files = {
            "app/__init__.py": "", "app/logging.py": "def getLogger(name): ...\n", "app/queue/__init__.py": "",
            "app/f.py": "import logging\nimport queue\nfrom queue import Queue\n\nlog = logging.getLogger(__name__)\n",
            "scripts/helpers.py": "def run(): ...\n", "scripts/tool.py": "import helpers\n\nhelpers.run()\n",
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual(set(), _imports(edges, "app/f"))
        self.assertEqual({("scripts/tool", "scripts/helpers")}, _imports(edges, "scripts/tool"))


if __name__ == "__main__":
    unittest.main()
