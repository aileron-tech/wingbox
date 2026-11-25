"""Defects of the Python, TypeScript and Rust readers found in the review of 0.19.1: one test per defect."""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Iterator, Union

import scripts.build_code_graph as builder
from scripts.build_code_graph import Ok, build_graph, discover_files, parse_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "code_graph_search.py"
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


def _references(edges: list[dict], source: str) -> set[str]:
    return {edge["target"] for edge in edges if edge["type"] == "REFERENCES" and edge["source"] == source}


def _in_time(script: str, limit: float = 120.0) -> str:
    """Run a script on the code of this repository in a process of its own, stopped at the limit: a reader
    that backtracks or walks without end would otherwise hold the whole suite."""
    done = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), capture_output=True, text=True, timeout=limit)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return done.stdout


LONG = 3000  # terms of one expression, calls of one chain, branches of one statement
PACKAGE: Files = {"pkg/__init__.py": "", "pkg/a.py": "x = 1\ndef start(): ...\n", "pkg/b.py": "class B: ...\n"}


def _uses(answer: str) -> dict[str, dict[str, list[str]]]:
    """The use sites of a `usages` answer: file -> kind of use -> positions (line:column)."""
    found: dict[str, dict[str, list[str]]] = {}
    for line in answer.splitlines()[1:]:
        file, *words = line.strip().strip("()").split()
        kind = ""
        for word in words:
            if word[0].isdigit():
                found.setdefault(file, {}).setdefault(kind, []).append(word)
            else:
                kind = word
    return found


PROJECT = '[project]\nname = "{name}"\nversion = "1"\n'
CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


class PythonSourceRootTests(unittest.TestCase):
    def test_a_package_of_another_part_is_found_through_its_source_root(self) -> None:
        """/* REQ-PY-025: an absolute import that no folder near the importing file holds resolves through the source roots of the repository: a folder that holds a `pyproject.toml`, `setup.py` or `setup.cfg`, its `src/` folder, and the folders those files name (`[tool.setuptools.packages.find] where`, `[tool.setuptools] package-dir`, pytest's `pythonpath`, `[tool.poetry] packages from`, the parent of a `[tool.hatch.build.targets.wheel] packages` entry, `package_dir` and `where` of `setup.cfg` and `setup.py`); `from package import module` reaches the module too. */"""
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
        """/* REQ-PY-026: an absolute import that two source roots hold is linked to neither; the `src/` nearest to the importing file still wins before the source roots are asked; a name of the standard library is never looked for in a source root; a folder a manifest names outside the repository is not read; and a manifest that cannot be read is named in a warning and leaves the build whole. */"""
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
        self.assertEqual(3, errors.count("cannot read the Python source folders of"))
        for manifest in ("pyproject.toml", "setup.cfg", "setup.py"):
            self.assertIn(os.path.join("packages", "c", manifest), errors)


class PythonImportFormTests(unittest.TestCase):
    def test_from_package_import_subpackage_is_an_edge(self) -> None:
        """/* REQ-PY-027: `from pkg import sub` and `from . import sub` depend on `pkg/sub/__init__.py` when `sub` is a subpackage, as they depend on `pkg/sub.py` when it is a module; a folder beside a module of the same name is taken first, as by Python's finder. */"""
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
        """/* REQ-PY-028: inside a package (the folder of the importing file holds `__init__.py`) `import logging` is not the sibling `logging.py` or the sibling folder `queue/`: Python 3 looks an absolute name up on the import path, where the folder of a package is not; in a folder that is not a package (a script folder) the sibling is still what the name means. */"""
        files: Files = {
            "app/__init__.py": "", "app/logging.py": "def getLogger(name): ...\n", "app/queue/__init__.py": "",
            "app/f.py": "import logging\nimport queue\nfrom queue import Queue\n\nlog = logging.getLogger(__name__)\n",
            "scripts/helpers.py": "def run(): ...\n", "scripts/tool.py": "import helpers\n\nhelpers.run()\n",
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual(set(), _imports(edges, "app/f"))
        self.assertEqual({("scripts/tool", "scripts/helpers")}, _imports(edges, "scripts/tool"))


class ScopeImportTests(unittest.TestCase):
    def test_a_name_imported_inside_a_function_or_a_class_is_followed(self) -> None:
        """/* REQ-PY-029: `usages` lists the uses of a name that an import inside a function or a class body binds, as it lists those behind an import at the top of the file: `hook.f()` after `from pkg import hook`, `f()` after `from pkg.hook import f`, `pkg.hook.f()` after `import pkg.hook`, `h.f()` after `import pkg.hook as h`, the constructor call of an imported class and a method called on what it builds; a parameter of the same name still shadows the import of another function, and `entity` names the file for a chain that only such an import reaches. */"""
        files: Files = {
            "pkg/__init__.py": "",
            "pkg/hook.py": "def source(root):\n    return root\n\n\nclass Finder:\n    def find(self): return 1\n",
            "pkg/inner.py": (
                "def a(root):\n"
                "    from pkg import hook\n"
                "    return hook.source(root)\n"
                "def b(root):\n"
                "    from pkg.hook import source\n"
                "    return source(root)\n"
                "def c(root):\n"
                "    import pkg.hook\n"
                "    return pkg.hook.source(root)\n"
                "def d(root):\n"
                "    import pkg.hook as h\n"
                "    return h.source(root)\n"
                "def e():\n"
                "    from pkg.hook import Finder as F\n"
                "    finder = F()\n"
                "    return finder.find()\n"
                "class Holder:\n"
                "    from .hook import source as held\n"
                "    value = held(1)\n"
                "def shadowed(hook, source):\n"
                "    return hook.source(source)\n"
                "def nested():\n"
                "    from . import hook\n"
                "    def inner():\n"
                "        return hook.source(2)\n"
                "    return inner\n"
            ),
            "pkg/chain.py": "def only():\n    import pkg.hook\n    return pkg.hook.Finder\n",
        }
        with _tree(files) as root:
            source = _uses(_query(root, "usages", "pkg/hook.py::source"))
            finder = _uses(_query(root, "usages", "pkg/hook.py::Finder"))
            find = _uses(_query(root, "usages", "pkg/hook.py::Finder.find"))
            entity = _query(root, "entity", "Finder")
        self.assertEqual({"pkg/inner.py": {"import": ["5:26", "18:23"], "call": ["3:17", "6:12", "9:21", "12:14", "19:13", "25:21"]}}, source)
        self.assertEqual({"import": ["14:26"], "call": ["15:14"]}, finder["pkg/inner.py"])
        self.assertEqual({"reference": ["3:21"]}, finder["pkg/chain.py"])
        self.assertEqual({"pkg/inner.py": {"call": ["16:19"]}}, find)
        self.assertIn("pkg/chain", entity)

    @unittest.skipIf(sys.version_info < (3, 12), "type parameters need Python 3.12")
    def test_the_bound_of_a_type_parameter_is_a_use(self) -> None:
        """/* REQ-PY-034: the bound of a type parameter of a class or a function (`class Box[T: Store]`, `def keep[T: Store](x: T)`, Python 3.12) is a use of that name as a type. */"""
        files: Files = {
            "pkg/__init__.py": "", "pkg/store.py": "class Store: ...\n",
            "pkg/box.py": "from pkg.store import Store\n\n\nclass Box[T: Store]:\n    pass\n\n\ndef keep[T: Store](x: T) -> T:\n    return x\n",
        }
        with _tree(files) as root:
            store = _uses(_query(root, "usages", "pkg/store.py::Store"))
        self.assertEqual({"pkg/box.py": {"import": ["1:23"], "type": ["4:14", "8:13"]}}, store)


class MemberUseTests(unittest.TestCase):
    def test_python_class_and_instance_members(self) -> None:
        """/* REQ-PY-030: `usages <file>::Class.method` lists a call through the class name (`Store.create()`, `store.Store.create()`), a method passed as a value (`Store.create`) and a call on a module-level instance (`db = Store()` or `db: Store = make()`, then `db.query()`), in the defining file and in the files that import the names; the class keeps its own reference at each of those places, and a variable bound in two ways has no type. */"""
        files: Files = {
            "pkg/__init__.py": "",
            "pkg/store.py": (
                "class Base:\n"
                "    def query(self):\n"
                "        return 1\n"
                "class Store(Base):\n"
                "    @classmethod\n"
                "    def create(cls):\n"
                "        return cls()\n"
                "def make():\n"
                "    return Store()\n"
                "db = Store()\n"
                "typed: Store = make()\n"
                "mixed = Store()\n"
                "mixed = None\n"
                "def local():\n"
                "    return db.query()\n"
            ),
            "app/c.py": (
                "from pkg.store import Store, db, typed, mixed\n"
                "from pkg import store\n"
                "def run():\n"
                "    s = Store.create()\n"
                "    maker = store.Store.create\n"
                "    return db.query() + typed.query() + mixed.query()\n"
            ),
        }
        with _tree(files) as root:
            create = _uses(_query(root, "usages", "pkg/store.py::Store.create"))
            query = _uses(_query(root, "usages", "pkg/store.py::Base.query"))
            store = _uses(_query(root, "usages", "pkg/store.py::Store"))
        self.assertEqual({"app/c.py": {"call": ["4:15"], "reference": ["5:25"]}}, create)
        self.assertEqual({"app/c.py": {"call": ["6:15", "6:31"]}, "pkg/store.py": {"call": ["15:15"]}}, query)
        self.assertEqual(["4:9", "5:19"], store["app/c.py"]["reference"])

    def test_typescript_class_and_instance_members(self) -> None:
        """/* REQ-TS-SYM-005: in TypeScript `usages <file>::Class.method` lists a call of a static method through the class name (`Database.open()`), a call on a module-level instance (`export const db = new Database()` or `const typed: Database = make()`, then `db.query()`) through a barrel of `export *`, and a member of an enum (`Mode.Fast`). */"""
        files: Files = {
            "src/pkg/leaf.ts": (
                "export class Database { query(): number { return 1; } static open(): Database { return new Database(); } }\n"
                "export const db = new Database();\n"
                "export const typed: Database = Database.open();\n"
                "export enum Mode { Fast, Slow }\n"
            ),
            "src/pkg/index.ts": 'export * from "./leaf";\n',
            "src/app/consumer.ts": (
                'import { db, typed, Database, Mode } from "../pkg";\n'
                "export function run(): number { return db.query() + typed.query() + Database.open().query(); }\n"
                "export const mode = Mode.Fast;\n"
            ),
        }
        with _tree(files) as root:
            query = _uses(_query(root, "usages", "src/pkg/leaf.ts::Database.query"))
            opened = _uses(_query(root, "usages", "src/pkg/leaf.ts::Database.open"))
            fast = _uses(_query(root, "usages", "src/pkg/leaf.ts::Mode.Fast"))
        self.assertEqual(["2:43", "2:59"], query["src/app/consumer.ts"]["call"])
        self.assertEqual({"src/app/consumer.ts": {"call": ["2:78"]}, "src/pkg/leaf.ts": {"call": ["3:41"]}}, opened)
        self.assertEqual({"src/app/consumer.ts": {"reference": ["3:26"]}}, fast)

    def test_rust_type_and_static_members(self) -> None:
        """/* REQ-RUST-042: in Rust `usages <file>::Type.method` lists an associated function called through the type (`Model::create()`, also behind an alias of a `use`), a method called on a static or a constant of that type (`GLOBAL.save()`, `crate::model::GLOBAL.save()`) and a variant of an enum (`Mode::Fast`); a method called on a value whose type is not in sight stays an unresolved call. */"""
        files: Files = {
            "Cargo.toml": CARGO,
            "src/model.rs": (
                "pub struct Model { pub id: u32 }\n"
                "impl Model {\n"
                "    pub fn create() -> Model { Model { id: 1 } }\n"
                "    pub fn save(&self) -> u32 { self.id }\n"
                "}\n"
                "pub static GLOBAL: Model = Model { id: 0 };\n"
                "pub enum Mode { Fast, Slow }\n"
                "pub fn other() -> Model { Model::create() }\n"
            ),
            "src/lib.rs": (
                "pub mod model;\n"
                "use model::{Model as M, Mode, GLOBAL};\n"
                "pub fn run() -> u32 {\n"
                "    let _mode = Mode::Fast;\n"
                "    let made = M::create();\n"
                "    let unknown = model::other();\n"
                "    made.id + GLOBAL.save() + crate::model::GLOBAL.save() + unknown.save()\n"
                "}\n"
            ),
        }
        with _tree(files) as root:
            create = _uses(_query(root, "usages", "src/model.rs::Model.create"))
            save = _uses(_query(root, "usages", "src/model.rs::Model.save"))
            fast = _uses(_query(root, "usages", "src/model.rs::Mode.Fast"))
        self.assertEqual({"src/lib.rs": {"call": ["5:19"]}, "src/model.rs": {"call": ["8:34"]}}, create)
        self.assertEqual({"src/lib.rs": {"call": ["7:22", "7:52"], "unresolved-call": ["7:69"]}}, save)
        self.assertEqual({"src/lib.rs": {"reference": ["4:23"]}}, fast)

    def test_classes_that_name_each_other_as_bases_end_the_search(self) -> None:
        """/* REQ-LANG-018: the search for the class that defines a method visits each class once, also when classes name each other as bases: twelve classes that each name the other eleven answer at once, where the search used to walk every chain of sixteen bases; a method of a base class is still found, the nearest base in the order written first. */"""
        from scripts.lang.symbols import SymbolIndex

        names = [f"K{number}" for number in range(12)]
        clique = "".join(
            f"class {name}({', '.join(other for other in names if other != name)}):\n    def own_{name}(self): ...\n" for name in names
        )
        files: Files = {
            "clique.py": clique,
            "tree.py": "class Root:\n    def top(self): ...\nclass Left(Root):\n    def side(self): ...\nclass Right(Root):\n    def side(self): ...\nclass Leaf(Left, Right): ...\n",
            "caller.py": "from clique import K0\nfrom tree import Leaf\n\n\ndef go(k: K0, leaf: Leaf):\n    return k.missing(), k.own_K7(), leaf.side(), leaf.top()\n",
        }
        walked = 0
        walk = SymbolIndex._walk

        def counted(self, *arguments):  # type: ignore[no-untyped-def]
            nonlocal walked
            walked += 1
            if walked > 20000:
                raise AssertionError("the search for a member does not end")
            return walk(self, *arguments)

        from scripts.lang.python.parser import python_use_positions

        with _tree(files) as root, unittest.mock.patch.object(SymbolIndex, "_walk", counted):
            context = builder.graph_context([parse_file(found) for found in sorted(discover_files(str(root)))], str(root)).value
            index = SymbolIndex(
                context.id_to_result, lambda _language, spec, path: context.bound_languages["python"].resolve_import(spec, path)
            )
            calls = [use for use in python_use_positions(str(root / "caller.py"), files["caller.py"]) if use.get("member")]
            owners = {use["member"]: index.resolve("caller", use) for use in calls}
        self.assertIsNone(owners["missing"])
        self.assertEqual(("clique", "K7.own_K7"), (owners["own_K7"].module, owners["own_K7"].name))
        self.assertEqual(("tree", "Left.side"), (owners["side"].module, owners["side"].name))
        self.assertEqual(("tree", "Root.top"), (owners["top"].module, owners["top"].name))

ABSTRACT_SCRIPT = r'''
import os, tempfile
from scripts.lang.python.parser import parse_python
tail = (
    "import abc\nfrom typing import Protocol\n"
    "class Plain(Base): ...\n"
    "class Port(Protocol): ...\n"
    "class Wide(\n    Base,\n    metaclass=abc.ABCMeta,\n): ...\n"
    "class Late(Base): x = 'ABC()'\n"
)
texts = [
    "# " + "class A(ABC " * 9000 + "\n" + tail,
    'x = "class A(' + "ABC " * 25000 + '"\n' + tail,
    'print "old"\n# ' + "class A(ABC " * 9000 + "\n" + tail,
]
with tempfile.TemporaryDirectory() as folder:
    for number, text in enumerate(texts):
        path = os.path.join(folder, f"m{number}.py")
        with open(path, "w", encoding="utf-8") as source:
            source.write(text)
        print(",".join(parse_python(path, text, "repo", 0.0).exports_abstract))
'''


class LongTreeTests(unittest.TestCase):
    def test_python_reads_a_file_with_long_chains(self) -> None:
        """/* REQ-PY-031: a Python file with an expression of 3,000 terms, a chain of 3,000 calls, 3,000 `elif` branches, a module name joined from 3,000 strings (a name longer than a file name may be) or a decorator called 3,000 times does not stop the build: its imports, the class defined after the long part (with its method and its base) and the use of a name in that class are read as in any file. Where Python's own parser refuses the file (3.11, beyond about 1,500 chained calls), the build warns, goes on and keeps the imports of the file. */"""
        head = "from pkg import a\nfrom pkg.b import B\n"
        tail = "class Last(B):\n    def run(self):\n        return a.x\n"
        shapes: dict[str, str] = {
            "terms.py": head + "total = " + " + ".join(["a.x"] * LONG) + "\n" + tail,
            "calls.py": head + "value = a.start()" + "".join(f".step{number}()" for number in range(LONG)) + "\n" + tail,
            "branches.py": head + "if a.x == 0:\n    pass\n" + "".join(f"elif a.x == {number}:\n    pass\n" for number in range(1, LONG)) + tail,
            "joined.py": head + "import importlib\nimportlib.import_module(" + " + ".join(['"a"'] * LONG) + ")\n" + tail,
            "decorated.py": head + "@a.start" + "()" * LONG + "\n" + tail,
        }
        from scripts.lang.python.parser import python_use_positions

        with _tree({**PACKAGE, **shapes}) as root:
            nodes, edges, errors = _graph(root)
            positions = {name: python_use_positions(str(root / name), text) for name, text in shapes.items()}
        read_by_python = 0
        for name, text in shapes.items():
            module = name[:-3]
            self.assertEqual({(module, "pkg/__init__"), (module, "pkg/a"), (module, "pkg/b")}, _imports(edges, module), name)
            try:
                ast.parse(text)
            except RecursionError:
                self.assertIn("AST parse failed for", errors)
                self.assertIn(name, errors)
                continue
            read_by_python += 1
            last = _entities(nodes, module)["Last"]
            self.assertEqual(("class", ["run"], ["B"]), (last["kind"], last["methods"], last["bases"]), name)
            self.assertIn("pkg/a::x", _references(edges, module), name)
            line = len(text.splitlines())
            self.assertIn({"path": ["a", "x"], "at": [[line, 16], [line, 18]]}, positions[name], name)
        self.assertGreaterEqual(len(positions["terms.py"]), LONG if read_by_python == len(shapes) else 0)
        if sys.version_info >= (3, 12):
            self.assertEqual(len(shapes), read_by_python)


class TextReaderTests(unittest.TestCase):
    def test_a_python_file_the_parser_refuses_keeps_every_import(self) -> None:
        """/* REQ-PY-032: for a Python file that `ast.parse` refuses, every module of `import a.b as x, c.d` and every name of `from pkg import target, other`, also over the lines of a list in parentheses and after a `;`, is read: the file depends on each module and submodule named; a name inside a string of the file is no import. */"""
        files: Files = {
            "pkg/__init__.py": "", "pkg/target.py": "class Target: ...\ndef helper(): ...\n", "pkg/other.py": "OTHER = 1\n",
            "pkg/third.py": "THIRD = 1\n", "pkg/fourth.py": "FOURTH = 1\n", "pkg/fifth.py": "", "pkg/sixth.py": "",
            "pkg/legacy.py": (
                "import os, sys\n"
                "from pkg import target, other  # two submodules\n"
                "from pkg.target import (Target,\n"
                "                        helper)\n"
                "import pkg.third as third, pkg.fourth\n"
                "from . import fifth as five; import pkg.sixth\n"
                "text = 'import pkg.missing; from pkg import nowhere'\n"
                "\n"
                "class Legacy(Target):\n"
                "    def run(self):\n"
                "        print \"hello\"\n"
            ),
        }
        with _tree(files) as root:
            _nodes, edges, errors = _graph(root)
        self.assertIn("AST parse failed", errors)
        self.assertEqual(
            {"pkg/__init__", "pkg/target", "pkg/other", "pkg/third", "pkg/fourth", "pkg/fifth", "pkg/sixth"},
            {target for _source, target in _imports(edges, "pkg/legacy")},
        )

    def test_abstract_classes_are_read_from_the_tree_in_linear_time(self) -> None:
        """/* REQ-PY-033: the classes with an abstract base (`ABC`, `ABCMeta` or `Protocol` among the bases or the keywords of the class statement, also written over several lines) are read from the syntax tree, and from the class lines of a file the parser refuses: a comment or a string that repeats `class A(ABC ` for 100 KB on one line is read in well under two minutes, where the expression used before took hours, and names no class. */"""
        self.assertEqual(["Port,Wide", "Port,Wide", "Port"], _in_time(ABSTRACT_SCRIPT).splitlines())


TS_TAIL = "export class Last extends B { run() { return a.x; } }\n"
TS_HEAD = 'import { a } from "./a";\nimport { B } from "./b";\n'
RUST_HEAD = "use crate::a;\nuse crate::b::B;\n"
RUST_TAIL = "pub struct Last;\nimpl Last { pub fn run(&self, _b: B) -> u32 { a::x() } }\n"
REGEX_SCRIPT = r'''
import os, tempfile
from scripts.lang.rust.imports import _EXTERN_CRATE_ALIAS
from scripts.lang.rust.modules import path_modules
size = 100_000
print(len(path_modules("lib.rs", "// " + "#[" * (size // 2) + "\n")))
print(len(path_modules("lib.rs", "#[" * (size // 2) + ' path = "x.rs"\n')))
print([(m.name, m.paths) for m in path_modules("lib.rs", '#[cfg_attr(unix, path = "unix.rs")]\nmod sys;\nmod inner {\n    #[path = "deep.rs"]\n    mod leaf;\n}\nfn f(path: &[u8]) { let path = "p"; }\n')])
print(_EXTERN_CRATE_ALIAS.findall("extern crate first as one;\n" + "\n" * size + "fn main() {}\n    pub extern crate second as two;\n" + " " * size + "x\n"))
'''


class LongTreeOtherLanguageTests(unittest.TestCase):
    def test_typescript_reads_a_file_with_long_chains(self) -> None:
        """/* REQ-TS-PARSE-001: a TypeScript file with a chain of 3,000 calls, an expression of 3,000 terms, 3,000 `else if` branches or 3,000 nested brackets does not stop the build: its imports, the class defined after the long part (with its method and its base) and the reference to an imported name are read as in any file. */"""
        shapes: dict[str, str] = {
            "src/calls.ts": TS_HEAD + "export const value = a.start()" + "".join(f".step{number}()" for number in range(LONG)) + ";\n" + TS_TAIL,
            "src/terms.ts": TS_HEAD + "export const total = " + " + ".join(["a.x"] * LONG) + ";\n" + TS_TAIL,
            "src/branches.ts": TS_HEAD + "export function pick(v: number) {\n  if (v === 0) { return 0; }\n"
            + "".join(f"  else if (v === {number}) {{ return {number}; }}\n" for number in range(1, LONG)) + "}\n" + TS_TAIL,
            "src/nested.ts": TS_HEAD + "export const deep = " + "[" * LONG + "a.x" + "]" * LONG + ";\n" + TS_TAIL,
        }
        files: Files = {"src/a.ts": "export const a = { x: 1, start() { return a; } };\n", "src/b.ts": "export class B {}\n", **shapes}
        with _tree(files) as root:
            nodes, edges, _errors = _graph(root)
        for name in shapes:
            module = name[:-3]
            self.assertEqual({(module, "src/a"), (module, "src/b")}, _imports(edges, module), name)
            last = _entities(nodes, module)["Last"]
            self.assertEqual(("class", ["run"], ["B"]), (last["kind"], last["methods"], last["bases"]), name)
            self.assertIn("src/a::a", _references(edges, module), name)

    def test_rust_reads_a_file_with_long_chains(self) -> None:
        """/* REQ-RUST-043: a Rust file with a builder chain of 3,000 calls, an expression of 3,000 terms, 3,000 `else if` branches, 3,000 nested groups in the arguments of a macro or a path of 3,000 segments does not stop the build: its `use` declarations, the struct defined after the long part with the method of its `impl`, and every method call of the chain are read as in any file. */"""
        shapes: dict[str, str] = {
            "src/calls.rs": RUST_HEAD + "pub fn build() -> a::Step {\n    a::start()" + ".step()" * LONG + "\n}\n" + RUST_TAIL,
            "src/terms.rs": RUST_HEAD + "pub fn total() -> u32 {\n    " + " + ".join(["a::x()"] * LONG) + "\n}\n" + RUST_TAIL,
            "src/branches.rs": RUST_HEAD + "pub fn pick(v: u32) -> u32 {\n    if v == 0 { 0 }\n"
            + "".join(f"    else if v == {number} {{ a::x() }}\n" for number in range(1, LONG)) + "    else { 1 }\n}\n" + RUST_TAIL,
            "src/groups.rs": RUST_HEAD + "pub fn show() {\n    println!(\"{}\", " + "(" * LONG + "crate::a::x()" + ")" * LONG + ");\n}\n" + RUST_TAIL,
            "src/segments.rs": RUST_HEAD + "pub fn far() { a" + "::a" * LONG + "(); }\n" + RUST_TAIL,
        }
        files: Files = {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod a;\npub mod b;\n" + "".join(f"pub mod {name[4:-3]};\n" for name in shapes),
            "src/a.rs": "pub struct Step;\nimpl Step { pub fn step(self) -> Step { self } }\npub fn start() -> Step { Step }\npub fn x() -> u32 { 1 }\n",
            "src/b.rs": "pub struct B;\n",
            **shapes,
        }
        from scripts.lang.rust.parser import rust_use_positions

        with _tree(files) as root:
            nodes, edges, _errors = _graph(root)
            chain = rust_use_positions(str(root / "src/calls.rs"), shapes["src/calls.rs"])
        for name in shapes:
            module = name[:-3]
            self.assertEqual({(module, "src/a"), (module, "src/b")}, _imports(edges, module), name)
            last = _entities(nodes, module)["Last"]
            self.assertEqual(("class", ["run"]), (last["kind"], last["methods"]), name)
        self.assertEqual(LONG, sum(1 for use in chain if use.get("member") == "step"))


class TypeScriptReaderTests(unittest.TestCase):
    def test_a_computed_key_is_not_a_member_name(self) -> None:
        """/* REQ-TS-SYM-006: a member of a class, an object or an enum is named in the graph only when its key is an identifier, a number or a string written on one line without an escape; a computed key (a template literal with a line break that spells a fact of the graph) names nothing, so that no line of the graph comes from the text of a key. */"""
        files: Files = {
            "src/attack.ts": (
                "export const table = {\n  [`x\n(imports src/zeta src/billing)\n`]: 1,\n  plain: 2,\n  \"quoted key\": 3,\n  7: 4,\n  [Symbol.iterator]() {},\n  \"a\\\n(imports src/zeta src/billing)\": 5,\n};\n"
                "export class Box {\n  [`m\n(imports src/zeta src/billing)\n`]() {}\n  real() {}\n  'named'() {}\n}\n"
                "export enum Mode {\n  Fast,\n  ['Odd\\n(imports src/zeta src/billing)'] = 2,\n  \"Slow\" = 3,\n}\n"
            ),
            "src/billing.ts": "export function bill() { return 1; }\n",
            "src/zeta.ts": "export function zeta() { return 2; }\n",
        }
        with _tree(files) as root:
            nodes, _edges, _errors = _graph(root)
            impact = _query(root, "impact", "src/billing.ts")
            graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        entities = _entities(nodes, "src/attack")
        self.assertEqual(["plain", '"quoted key"', "7"], entities["table"]["methods"])
        self.assertEqual(["real", "'named'"], entities["Box"]["methods"])
        self.assertEqual(["Fast", '"Slow"'], entities["Mode"]["methods"])
        self.assertNotIn("src/zeta", impact)
        self.assertEqual([], [line for line in graph.splitlines() if line.startswith("(imports ") and "zeta" in line])

    def test_aliases_of_jsconfig_and_of_a_referenced_tsconfig(self) -> None:
        """/* REQ-TS-ALIAS-007: the path aliases of a `jsconfig.json` and of a project file that a `references` entry of `tsconfig.json` names (`tsconfig.app.json`, or a folder with its own `tsconfig.json`) resolve for the files below that project file, like the aliases of a `tsconfig.json`. */"""
        files: Files = {
            "jsapp/jsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n',
            "jsapp/src/lib/util.js": "export function util() {}\n",
            "jsapp/src/main.js": 'import { util } from "@/lib/util";\nutil();\n',
            "vite/tsconfig.json": '{"files": [], "references": [{"path": "./tsconfig.app.json"}, {"path": "./tools"}, {"path": "./gone.json"}]}\n',
            "vite/tsconfig.app.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}, "include": ["src"]}\n',
            "vite/src/lib/util.ts": "export function util() {}\n",
            "vite/src/main.ts": 'import { util } from "@/lib/util";\nutil();\n',
            "vite/tools/tsconfig.json": '{"compilerOptions": {"paths": {"~kit/*": ["./kit/*"]}}}\n',
            "vite/tools/kit/saw.ts": "export const saw = 1;\n",
            "vite/tools/run.ts": 'import { saw } from "~kit/saw";\nexport const run = saw;\n',
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual({("jsapp/src/main", "jsapp/src/lib/util")}, _imports(edges, "jsapp/src/main"))
        self.assertEqual({("vite/src/main", "vite/src/lib/util")}, _imports(edges, "vite/src/main"))
        self.assertEqual({("vite/tools/run", "vite/tools/kit/saw")}, _imports(edges, "vite/tools/run"))

    def test_a_project_file_outside_the_repository_is_not_read(self) -> None:
        """/* REQ-TS-ALIAS-008: a tsconfig named by `extends` is read only when the file it leads to lies inside the repository: an absolute path, a relative path that climbs out and a link that leads out are refused with a notice and give no alias, while an `extends` inside the repository is applied as before. */"""
        with tempfile.TemporaryDirectory() as outside_folder:
            outside = Path(os.path.realpath(outside_folder))
            preset = '{"compilerOptions": {"paths": {"@secret/*": ["%s/src/deep/*"]}}}\n'
            files: Files = {
                "src/deep/x.ts": "export const x = 1;\n",
                "absolute/tsconfig.json": json.dumps({"extends": str(outside / "absolute.json")}),
                "absolute/main.ts": 'import { x } from "@secret/x";\nexport const y = x;\n',
                "inside/tsconfig.json": '{"extends": "../presets/base.json"}\n',
                "inside/main.ts": 'import { x } from "@secret/x";\nexport const y = x;\n',
                "presets/base.json": '{"compilerOptions": {"paths": {"@secret/*": ["../src/deep/*"]}}}\n',
                "linked/tsconfig.json": '{"extends": "./link.json"}\n',
                "linked/main.ts": 'import { x } from "@secret/x";\nexport const y = x;\n',
            }
            with _tree(files) as root:
                for name in ("absolute.json", "climbing.json", "linked.json"):
                    (outside / name).write_text(preset % str(root).replace("\\", "/"), encoding="utf-8")
                (root / "climbing").mkdir()
                (root / "climbing" / "tsconfig.json").write_text(
                    json.dumps({"extends": os.path.relpath(outside / "climbing.json", root / "climbing").replace(os.sep, "/")}), encoding="utf-8")
                (root / "climbing" / "main.ts").write_text('import { x } from "@secret/x";\nexport const y = x;\n', encoding="utf-8")
                try:
                    os.symlink(outside / "linked.json", root / "linked" / "link.json")
                    linked = True
                except OSError:  # no right to make links (Windows without developer mode)
                    linked = False
                _nodes, edges, errors = _graph(root)
        self.assertEqual({("inside/main", "src/deep/x")}, _imports(edges, "inside/main"))
        self.assertEqual(set(), _imports(edges, "absolute/main") | _imports(edges, "climbing/main") | _imports(edges, "linked/main"))
        self.assertEqual(3 if linked else 2, errors.count("it lies outside the repository"))

    def test_imports_after_a_syntax_error_are_still_read(self) -> None:
        """/* REQ-TS-PARSE-002: in a TypeScript file with a syntax error the import and re-export statements that stand after the error are read from the text, so that the file still depends on what they name, and the build names the file in a warning; an import written inside a comment or a string of such a file is none. */"""
        files: Files = {
            "a.ts": "export const a = 1;\n", "b.ts": "export const b = 1;\n", "c.ts": "export const c = 1;\n",
            "d.ts": "export const d = 1;\n", "e.ts": "export const e = 1;\n", "f.ts": "export const f = 1;\n",
            "broken.ts": (
                'import { a } from "./a";\n'
                "export function ok() { return a; }\n"
                "export function broken( { return 1; }\n"
                'import { b } from "./b";\n'
                "import {\n  c,\n} from './c';\n"
                'export * from "./d";\n'
                'import "./e";\n'
                "/*\n"
                'import { f } from "./f";\n'
                "*/\n"
                "const text = `\n"
                'import { f } from "./f";\n'
                "`;\n"
            ),
            "conflict.ts": (
                'import { a } from "./a";\n<<<<<<< HEAD\nimport { b } from "./b";\n=======\nimport { c } from "./c";\n>>>>>>> branch\n'
                "export function after() { return a; }\n"
            ),
            "whole.ts": '/*\nimport { f } from "./f";\n*/\nimport { a } from "./a";\nexport const w = a;\n',
        }
        with _tree(files) as root:
            _nodes, edges, errors = _graph(root)
        self.assertEqual({"a", "b", "c", "d", "e"}, {target for _source, target in _imports(edges, "broken")})
        self.assertEqual({"a", "b", "c"}, {target for _source, target in _imports(edges, "conflict")})
        self.assertEqual({"a"}, {target for _source, target in _imports(edges, "whole")})
        self.assertEqual(2, errors.count("WARNING: the TypeScript parser could not read all of"))
        self.assertNotIn("whole.ts", errors)

    def test_the_build_names_the_files_left_out_as_minified(self) -> None:
        """/* REQ-TS-JS-007: a build says how many JavaScript files it left out because of a line over 5,000 characters and names them: a written file with one long data URI is not in the graph, and the notice is where its name appears; a build that leaves no file out prints no such line. */"""
        files: Files = {
            "src/app.js": 'import { icon } from "./icons.js";\nicon();\n',
            "src/icons.js": 'import { helper } from "./helper.js";\nexport const LOGO = "data:image/png;base64,' + "A" * 6000 + '";\nexport function icon() { return helper(LOGO); }\n',
            "src/helper.js": "export function helper(x) { return x; }\n",
        }
        with _tree(files) as root:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, builder.main(["--root", str(root)]))
            (root / "src" / "icons.js").write_text("export function icon() {}\n", encoding="utf-8")
            again = io.StringIO()
            with contextlib.redirect_stdout(again), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, builder.main(["--root", str(root)]))
        notice = [line for line in output.getvalue().splitlines() if "Left out as minified" in line]
        self.assertEqual(1, len(notice))
        self.assertIn("1 JavaScript file with a line over 5,000 characters", notice[0])
        self.assertTrue(notice[0].endswith(": src/icons.js"), notice[0])
        self.assertNotIn("Left out as minified", again.getvalue())


class ManifestReaderTests(unittest.TestCase):
    def test_a_manifest_that_cannot_be_read_does_not_stop_the_build(self) -> None:
        """/* REQ-LANG-019: a `package.json` or a `tsconfig.json` that is not UTF-8 or is nested 200,000 deep, an `exports` of a `package.json` nested 600 deep, a `Cargo.toml` with arrays nested 2,000 deep and a JSON file nested 200,000 deep beside a Python module loader are each named in a warning and left out; the build goes on and the files beside them keep their edges, the 600-deep `exports` included. */"""
        deep = 200_000
        nested_exports: object = "./src/index.ts"
        for _ in range(600):
            nested_exports = {"import": nested_exports}
        files: Files = {
            "latin/package.json": '{"name": "caf\xe9"}'.encode("latin-1"),
            "latin/tsconfig.json": '{"compilerOptions": {"paths": {"@caf\xe9/*": ["./*"]}}}'.encode("latin-1"),
            "latin/a.ts": 'import { b } from "./b";\nexport const a = b;\n', "latin/b.ts": "export const b = 1;\n",
            "deep/package.json": "[" * deep + "]" * deep,
            "deep/tsconfig.json": '{"compilerOptions": ' + "[" * deep + "]" * deep + "}",
            "deep/a.ts": 'import { b } from "./b";\nexport const a = b;\n', "deep/b.ts": "export const b = 1;\n",
            "wide/package.json": json.dumps({"name": "wide", "exports": nested_exports}),
            "wide/src/index.ts": "export const w = 1;\n",
            "user/main.ts": 'import { w } from "wide";\nexport const u = w;\n',
            "crate/Cargo.toml": '[package]\nname = "deep"\nversion = "0.1.0"\nkeywords = ' + "[" * 2000 + "]" * 2000 + "\n",
            "crate/src/lib.rs": "pub mod a;\n", "crate/src/a.rs": "pub fn f() {}\n",
            "loader/registry.py": "import importlib\n\n\ndef load(name):\n    return importlib.import_module(f\"loader.plugins.{name}\")\n",
            "loader/__init__.py": "", "loader/plugins/__init__.py": "", "loader/plugins/alpha.py": "X = 1\n",
            "loader/names.json": '{"plugins": ["alpha"]}', "loader/deep.json": "[" * deep + "]" * deep,
        }
        with _tree(files) as root:
            _nodes, edges, errors = _graph(root)
        self.assertEqual({("latin/a", "latin/b")}, _imports(edges, "latin/a"))
        self.assertEqual({("deep/a", "deep/b")}, _imports(edges, "deep/a"))
        self.assertEqual({("user/main", "wide/src")}, _imports(edges, "user/main"))
        self.assertEqual({("crate/src/lib", "crate/src/a")}, _imports(edges, "crate/src/lib"))
        self.assertIn(("loader/registry", "loader/plugins/alpha"), {(edge["source"], edge["target"]) for edge in edges})
        for name in ("latin/package.json", "latin/tsconfig.json", "deep/package.json", "deep/tsconfig.json", "crate/Cargo.toml", "loader/deep.json"):
            self.assertIn(os.path.join(*name.split("/")), errors, name)


class RustResolutionTests(unittest.TestCase):
    def test_a_crate_name_is_read_from_the_manifest_of_the_importing_package(self) -> None:
        """/* REQ-RUST-044: a crate name in code is the package that the manifest of the importing package takes from a folder of the tree under that name (`utils = { path = "../utils" }`, or `utils.workspace = true` with a path in `[workspace.dependencies]`): with two packages named `utils`, each service reaches its own; a name the manifest takes from a registry (`log = "0.4"`) is no local package, also when the tree holds a package of that name; a name the manifest does not state is the one local package of that name, and none when two share it. */"""
        package = '[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
        files: Files = {
            "Cargo.toml": '[workspace]\nmembers = ["svc_a/*", "svc_b/*", "tool", "log", "shared", "loose"]\n[workspace.dependencies]\nkit = { path = "shared" }\nserde = "1"\n',
            "svc_a/utils/Cargo.toml": package.format(name="utils"), "svc_a/utils/src/lib.rs": "pub fn from_svc_a() {}\n",
            "svc_b/utils/Cargo.toml": package.format(name="utils"), "svc_b/utils/src/lib.rs": "pub fn from_svc_b() {}\n",
            "svc_a/api/Cargo.toml": package.format(name="api-svc_a") + '[dependencies]\nutils = { path = "../utils" }\n',
            "svc_a/api/src/main.rs": "use utils::from_svc_a;\nfn main() { from_svc_a(); }\n",
            "svc_b/api/Cargo.toml": package.format(name="api-svc_b") + '[dependencies]\nutils = { path = "../utils" }\nkit.workspace = true\nserde = { workspace = true }\n',
            "svc_b/api/src/main.rs": "use utils::from_svc_b;\nuse kit::tool;\nuse serde::Serialize;\nfn main() { from_svc_b(); tool(); }\n",
            "log/Cargo.toml": package.format(name="log"), "log/src/lib.rs": "pub fn local_log() {}\n",
            "shared/Cargo.toml": package.format(name="shared-kit"), "shared/src/lib.rs": "pub fn tool() {}\n",
            "serde/Cargo.toml": package.format(name="serde"), "serde/src/lib.rs": "pub trait Serialize {}\n",
            "tool/Cargo.toml": package.format(name="tool") + '[dependencies]\nlog = "0.4"\n',
            "tool/src/main.rs": "use log::info;\nfn main() { info!(\"x\"); tool::helper(); }\n", "tool/src/lib.rs": "pub fn helper() {}\n",
            "loose/Cargo.toml": package.format(name="loose"),
            "loose/src/main.rs": "use utils::from_svc_a;\nuse shared_kit::tool;\nfn main() { from_svc_a(); tool(); }\n",
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
        self.assertEqual({("svc_a/api/src/main", "svc_a/utils/src/lib")}, _imports(edges, "svc_a/api/src/main"))
        self.assertEqual({("svc_b/api/src/main", "svc_b/utils/src/lib"), ("svc_b/api/src/main", "shared/src/lib")}, _imports(edges, "svc_b/api/src/main"))
        self.assertEqual({("tool/src/main", "tool/src/lib")}, _imports(edges, "tool/src/main"))
        self.assertEqual({("loose/src/main", "shared/src/lib")}, _imports(edges, "loose/src/main"))

    def test_an_included_file_is_a_dependency(self) -> None:
        """/* REQ-RUST-045: `include!("file.rs")` with one literal path is a dependency of the including file on that file, relative to its own folder, with the line of the macro; a path built with `concat!` or `env!`, and a file that is not in the graph, give none. */"""
        files: Files = {
            "Cargo.toml": CARGO,
            "src/lib.rs": 'pub mod inner;\ninclude!("included.rs");\ninclude!(concat!(env!("OUT_DIR"), "/generated.rs"));\ninclude!("missing.rs");\npub fn root() -> u32 { included_fn() }\n',
            "src/included.rs": "pub fn included_fn() -> u32 { 3 }\n",
            "src/generated.rs": "pub fn generated() {}\n",
            "src/inner/mod.rs": 'mod wrap {\n    include!("../shared/part.rs");\n}\n',
            "src/shared/part.rs": "pub fn part() {}\n",
        }
        with _tree(files) as root:
            _nodes, edges, _errors = _graph(root)
            impact = _query(root, "impact", "src/included.rs")
            lines = _query(root, "module", "src/lib", "--lines")
        self.assertEqual({("src/lib", "src/inner"), ("src/lib", "src/included")}, _imports(edges, "src/lib"))
        self.assertEqual({("src/inner", "src/shared/part")}, _imports(edges, "src/inner"))
        self.assertIn("src/lib.rs", impact)
        self.assertIn("src/included :line 2", lines)

    def test_the_expressions_of_the_rust_reader_read_a_long_line_once(self) -> None:
        """/* REQ-RUST-046: the search for `#[path = ".."]` attributes and for `extern crate a as b;` reads 100 KB of `#[` without a closing bracket, of empty lines or of blanks in well under half a minute, where the first took over a minute and the second hours; a path attribute, also inside `cfg_attr` and an inline module, and an `extern crate` alias after the long part are still found, and `path = "p"` outside an attribute starts no reading of the file. */"""
        report = _in_time(REGEX_SCRIPT, limit=30.0).splitlines()
        self.assertEqual(["0", "0", "[('sys', ('unix.rs',)), ('leaf', ('deep.rs',))]", "[('first', 'one'), ('second', 'two')]"], report)


if __name__ == "__main__":
    unittest.main()
