"""The build of a Python repository grows with its size and reads each file once: issue #157 (a name
imported through a facade of `from .x import *` made its importer read, keep and store every module behind the
facade; the parser walked the tree of a file again for every function, and compared every class name with every
role suffix letter by letter in every file)."""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_code_graph as builder
from scripts import graph_update
from scripts.lang import purpose
from scripts.lang import symbols as symbol_index
from scripts.lang.python import dynamic, parser as python_parser, symbols as python_symbols
from scripts.lang.python.tree import index_tree

REPOSITORY = Path(__file__).resolve().parents[1]
GROWTH = 4          # the larger project has this many times the models of the smaller one
MARGIN = 1.25       # allowed above GROWTH: headers and paths that do not scale; a square law gives 16


def zoo(models: int) -> dict[str, str]:
    """A package under src/ whose facade re-exports every model through two levels of `import *`, and one
    test file per model that imports its classes from the facade: four files per model, three beside them."""
    files = {
        "src/zoo/__init__.py": "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from .models import *\n    from .utils import helper\n",
        "src/zoo/utils.py": "def helper(value):\n    return value\n",
        "src/zoo/models/__init__.py": "".join(f"from .m{number:03d} import *\n" for number in range(models)),
    }
    for number in range(models):
        name, cls = f"m{number:03d}", f"M{number:03d}"
        files[f"src/zoo/models/{name}/__init__.py"] = f"from .configuration_{name} import *\nfrom .modeling_{name} import *\n"
        files[f"src/zoo/models/{name}/configuration_{name}.py"] = f"class {cls}Config:\n    size = {number}\n"
        files[f"src/zoo/models/{name}/modeling_{name}.py"] = (
            f"from .configuration_{name} import {cls}Config\n\n\n"
            f"class {cls}Model:\n"
            f"    def __init__(self, config: {cls}Config):\n        self.config = config\n\n"
            "    def layers(self):\n"
            "        def scaled(value):\n            return [value * step for step in range(self.config.size)]\n"
            "        total = 0\n        for layer in scaled(2):\n            total += layer\n        return total\n"
        )
        files[f"tests/models/{name}/test_modeling_{name}.py"] = (
            f"from zoo import {cls}Config, {cls}Model, helper\n\n\n"
            f"def test_{name}():\n    model = {cls}Model({cls}Config())\n    assert helper(model.layers()) >= 0\n"
        )
    return files


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build(root: Path, *extra: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return builder.main(["--root", str(root), *extra])


def _records(root: Path) -> dict[str, list]:
    """The fragment store: path of each module -> its record."""
    lines = (root / ".wingbox" / "graph-fragments.json").read_text(encoding="utf-8").splitlines()[1:]
    return {record[1]: record for record in map(json.loads, lines)}


class Measured:
    """One build of zoo(models) in this process, with what it did counted."""

    def __init__(self, models: int) -> None:
        self.files = zoo(models)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(os.path.realpath(folder))
            _write(root, self.files)
            indexes: list[symbol_index.SymbolIndex] = []
            make_index = builder.symbol_index
            export = symbol_index.SymbolIndex.export

            def kept_index(context):
                indexes.append(make_index(context))
                return indexes[-1]

            with mock.patch.dict(os.environ, {"WINGBOX_JOBS": "1"}), \
                    mock.patch.object(builder, "symbol_index", kept_index), \
                    mock.patch.object(symbol_index.SymbolIndex, "export", autospec=True, side_effect=export) as exports, \
                    mock.patch.object(python_parser, "index_tree", wraps=index_tree) as tree_walks, \
                    mock.patch.object(ast, "walk", wraps=ast.walk) as walks, \
                    mock.patch.object(ast, "iter_child_nodes", wraps=ast.iter_child_nodes) as child_walks:
                self.status = _build(root)
            self.exports = exports.call_count
            self.tree_walks = tree_walks.call_count
            self.walks = walks.call_count
            self.child_walks = child_walks.call_count
            # What the memoised answers of the symbol index keep of what they read.
            self.kept = sum(len(read) for index in indexes for read in index._deps.values())
            folder_files = sorted(path for path in (root / ".wingbox").iterdir() if path.is_file())
            self.sizes = {path.name: path.stat().st_size for path in folder_files}
            self.records = _records(root)

    def read_by(self, path: str) -> int:
        """How many items the facts of one module were read from (modules, module names, provider names,
        the modules whose glob imports it searched)."""
        record = self.records[path]
        return sum(len(record[field]) for field in (7, 8, 9, 10))


class GrowthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.small = Measured(40)
        cls.large = Measured(40 * GROWTH)

    def within(self, small: float, large: float, what: str) -> None:
        self.assertLessEqual(large, GROWTH * MARGIN * small, f"{what}: {small} -> {large} for {GROWTH} times the models")

    def test_the_facade_is_followed_and_every_file_of_wingbox_grows_with_the_project(self) -> None:
        """/* REQ-PY-015: in a generated project whose tests import every class through a facade of two levels of
        `from .x import *`, each test resolves its classes to the files that define them, and with four times the
        models no file under .wingbox/ (graph, use file, cache, fragment store, snapshot, ...) is more than about
        four times as large. */"""
        self.assertEqual(0, self.small.status)
        self.assertEqual(0, self.large.status)
        uses = self.large.records["tests/models/m159/test_modeling_m159.py"][4]
        self.assertIn("src/zoo/models/m159/modeling_m159", [use[2] for use in uses if use[3] == "M159Model"])
        self.assertIn("src/zoo/models/m159/configuration_m159", [use[2] for use in uses if use[3] == "M159Config"])
        self.assertGreater(len(self.large.files), GROWTH * len(self.small.files) * 0.95)
        self.assertEqual(sorted(self.small.sizes), sorted(self.large.sizes))
        for name, size in self.small.sizes.items():
            if size:
                self.within(size, self.large.sizes[name], name)

    def test_a_module_reads_what_it_names_not_what_stands_behind_the_facade(self) -> None:
        """/* REQ-PY-016: the facts of a file that imports through the facade are read from the same number of
        items (modules, module names, searched facades) whatever the number of modules behind the facade; the
        symbol index asks what a module exports, and keeps of what its answers read, in proportion to the
        project, not to its square. */"""
        self.assertEqual(
            self.small.read_by("tests/models/m007/test_modeling_m007.py"),
            self.large.read_by("tests/models/m007/test_modeling_m007.py"),
        )
        searched = self.large.records["tests/models/m007/test_modeling_m007.py"][10]
        self.assertEqual(["src/zoo/__init__", "src/zoo/models/__init__", "src/zoo/models/m007/__init__"], searched)
        self.assertEqual(
            [["src/zoo/models/m007/configuration_m007", "src/zoo/models/m007/modeling_m007"],
             [".configuration_m007", ".modeling_m007"]],
            self.large.records["src/zoo/models/m007/__init__.py"][11],
        )
        self.within(self.small.exports, self.large.exports, "questions to the symbol index")
        self.within(self.small.kept, self.large.kept, "items the memoised answers keep")

    def test_the_tree_of_a_file_is_walked_once(self) -> None:
        """/* REQ-PY-017: a build walks the syntax tree of each Python file once (imports, run-time loads,
        registry calls and the names each function binds read that walk): no `ast.walk`, and the children of a
        node are asked for at the top level of the file only, however many functions, classes and loops it has. */"""
        for measured in (self.small, self.large):
            self.assertEqual(len(measured.files), measured.tree_walks)
            self.assertEqual(0, measured.walks)
            self.assertLessEqual(measured.child_walks, len(measured.files))


def _python_sources(tops: tuple[str, ...] = ("scripts", "src", "tests", "tools", "benchmarks")) -> list[Path]:
    """The Python files of this repository: the real code the readers are compared on."""
    found: list[Path] = []
    for top in tops:
        found.extend(sorted((REPOSITORY / top).rglob("*.py")))
    return found


TRICKY = '''
import os, sys as system
from pkg import registry, loader as load_plugin
from pkg.other import *

def outer(items, flag=(marker := 1)):
    global shared, counter
    shared = [item for item in items if (seen := item)]
    handler = registry.get(flag)
    try:
        import json as codec
        from pkg.sub import thing, other as renamed
    except (ValueError, OSError) as problem:
        del problem
    with open(os.devnull) as (stream):
        pass
    for name in items:
        plugin = registry.lookup(name)
        made = plugin.build(name)
        made.run()
        for inner in made.parts:
            part = load_plugin(inner)
            part.start()
            def nested(value, *rest, key=None, **more):
                nonlocal_like = value
                return lambda extra: (extra, nonlocal_like, key)
        class Local(Base):
            attribute: int = 0
            def method(self):
                counter = self.attribute
                return counter
    match items:
        case {"kind": kind, **rest}:
            return rest
        case [first, *others]:
            return others
        case str() as text:
            return text
    return shared

@decorate(option=[entry for entry in range(3)])
class Top(Base, metaclass=Meta):
    field = registry.default
    def loop(self):
        async def worker(queue):
            async for job in queue:
                result = registry.resolve(job)
                await result.execute(job)
        for step in self.steps:
            found = registry.find(step)
            found = found.wrap(found)
            found.apply(step)
        return worker

value = __import__("pkg.literal")
module = system.modules[__name__]
'''


class OneWalkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        texts = [path.read_text(encoding="utf-8-sig") for path in _python_sources()] + [TRICKY]
        cls.trees = [ast.parse(text) for text in texts]

    def test_the_index_gives_the_nodes_in_the_order_of_a_walk(self) -> None:
        """/* REQ-PY-018: for every Python file of this repository and a file with every binding form, the
        index of the one walk gives the nodes of any classes below any function, class or loop (and below the
        module) as the same objects in the same order as `ast.walk` of that node. */"""
        groups = [(ast.Import, ast.ImportFrom), (ast.Call,), (ast.Assign,), (ast.For, ast.AsyncFor), (ast.Name, ast.Attribute)]
        checked = 0
        for tree in self.trees:
            index = index_tree(tree)
            starts = [0] + index.positions((ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.For, ast.AsyncFor))
            for start in starts:
                below = list(ast.walk(index.nodes[start]))
                if start:
                    self.assertEqual(start, index.position_of(index.nodes[start]))
                for classes in groups:
                    expected = [id(node) for node in below if type(node) in classes]
                    self.assertEqual(expected, [id(node) for node in index.walk(classes, start)])
                    checked += len(expected)
        self.assertGreater(checked, 100000)

    def test_the_names_a_scope_binds_are_the_same_from_the_index(self) -> None:
        """/* REQ-PY-019: for every function and class of every Python file of this repository and of a file
        with every binding form (assignments, imports, `as` names, pattern captures, nested definitions, `global`,
        walrus in a comprehension, a decorator or a default), the names its body binds read from the index are
        the names the definition gives (a walk of the body that stops at nested scopes). */"""
        scopes = 0
        for tree in self.trees:
            index = index_tree(tree)
            for position in index.positions((ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope = index.nodes[position]
                self.assertEqual(python_symbols._body_locals(scope.body), set(index.body_locals(scope)), ast.dump(scope)[:200])
                scopes += 1
        self.assertGreater(scopes, 3000)

    def test_the_readers_answer_the_same_with_the_shared_walk(self) -> None:
        """/* REQ-PY-020: the use sites (the list a build keeps and the full list), the run-time module loads
        and the registry calls of every Python file of scripts/, src/ and tools/ of this repository and of a
        file with loops over a registry are the same read from the shared walk as from a walk of their own. */"""
        loads = calls = sites = 0
        texts = [path.read_text(encoding="utf-8-sig") for path in _python_sources(("scripts", "src", "tools"))] + [TRICKY]
        for tree in map(ast.parse, texts):
            index = index_tree(tree)
            bindings = python_symbols.module_bindings(tree)
            names = frozenset(binding["local"] for binding in bindings if binding["local"] != "*")
            targets = {name: [name] for name in names}
            for full in (False, True):
                alone = python_symbols.use_sites(tree, names, True, False, full, names)
                shared = python_symbols.use_sites(tree, names, True, False, full, names, index)
                self.assertEqual(alone, shared)
                sites += len(shared)
            module_of_name = {name: name for name in names}
            module_of_name.update({"import_module": "importlib.import_module", "importlib": "importlib"})
            self.assertEqual(dynamic.find_module_loads(tree, module_of_name), dynamic.find_module_loads(tree, module_of_name, index))
            loads += len(dynamic.find_module_loads(tree, module_of_name, index)[0])
            found = dynamic.find_registry_calls(tree, targets, index)
            self.assertEqual(_registry_calls_by_walking(tree, targets), found)
            calls += len(found)
        self.assertGreater(sites, 10000)
        self.assertGreater(loads, 0)
        self.assertGreater(calls, 3)


def _registry_calls_by_walking(tree: ast.Module, import_targets: dict[str, list[str]]) -> list:
    """find_registry_calls as it was written before the shared walk: two walks of every function and one of
    the body of every loop. The reference the reader of the index is compared with."""
    calls: list = []
    for entity_name, function in dynamic._functions(tree):
        bound = dict(import_targets)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                root = dynamic._root_name(node.value) if isinstance(node.value, ast.Call) else None
                if root in import_targets:
                    bound[node.targets[0].id] = import_targets[root]
        for loop in ast.walk(function):
            if isinstance(loop, (ast.For, ast.AsyncFor)) and isinstance(loop.target, ast.Name):
                calls.extend(_loop_calls_by_walking(entity_name, loop, loop.target.id, bound))
    return calls


def _loop_calls_by_walking(entity_name: str, loop, variable: str, bound: dict[str, list[str]]) -> list:
    looked_up: dict[str, list[str]] = {}

    def source_of(expr):
        if isinstance(expr, ast.Name):
            return looked_up.get(expr.id)
        if isinstance(expr, ast.Call):
            called = source_of(expr.func)
            if called is not None:
                return called
            root = dynamic._root_name(expr.func)
            if root in bound and dynamic._passes(expr, variable):
                return bound[root]
        return None

    body = [node for statement in loop.body for node in ast.walk(statement)]
    for _ in range(3):
        for node in body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                found = source_of(node.value)
                if found is not None:
                    looked_up[node.targets[0].id] = found
    folds = {
        id(node.value)
        for node in body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call) and dynamic._passes(node.value, node.targets[0].id)
    }
    calls = []
    for node in body:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            modules = source_of(node.func.value)
            if modules is not None:
                calls.append({"modules": list(modules), "method": node.func.attr, "source_entity": entity_name,
                              "line": node.lineno, "fold": id(node) in folds})
    return calls


class PurposeTest(unittest.TestCase):
    # Names one edit away from a role suffix (a plural, a doubled or a missing letter), and names that are not.
    NEAR = {"UserControllers": "API controller", "OrderServices": "service", "EventHandlers": "handler",
            "PaymentGateways": "gateway", "TaskSchedulers": "scheduler", "Observers": "Observer pattern",
            "ItemRepositoryy": "repository", "Controler": "API controller", "Servic": "service"}
    NAMES = (*NEAR, "OrderServce", "UserControler", "EventHandlre", "ItemRepositry", "Storey", "PaymentGatewy",
             "TaskSchedulr", "XProcessor", "Processors", "MyFactory1", "Buildr", "abcde", "Observr", "ChainHandlr",
             "SpecificationX", "ValueObjec", "Polic", "PolicyY", "sdk", "Client", "Clientt", "Cliemt", "lient")

    @staticmethod
    def by_letters(function):
        """function with every comparison made by the full edit distance and nothing kept between calls."""
        def plain(name: str, suffix: str, max_dist: int = 1) -> bool:
            if len(suffix) < 6:
                return False
            return purpose._levenshtein(name[-len(suffix) - max_dist:].lower(), suffix.lower()) <= max_dist

        def reference(*arguments):
            with mock.patch.object(purpose, "_fuzzy_suffix_match", plain), \
                    mock.patch.object(purpose, "_entity_role", purpose._entity_role.__wrapped__), \
                    mock.patch.object(purpose, "_stem_role", purpose._stem_role.__wrapped__):
                return function(*arguments)
        return reference

    def test_a_class_name_is_compared_with_the_role_suffixes_once(self) -> None:
        """/* REQ-LANG-014: the role a class name gives a file (its suffix, or a suffix one edit away) is worked
        out once per name: parsing the same sources again compares no name with a suffix. */"""
        sources = list(zoo(6).items())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(os.path.realpath(folder))
            _write(root, dict(sources))
            paths = [str(root / name) for name, _text in sources]
            first = [builder.parse_file((path, "python", "zoo", str(root))).purpose for path in paths]
            with mock.patch.object(purpose, "_fuzzy_suffix_match", wraps=purpose._fuzzy_suffix_match) as compared:
                again = [builder.parse_file((path, "python", "zoo", str(root))).purpose for path in paths]
        self.assertEqual(first, again)
        self.assertEqual(0, compared.call_count)

    def test_one_edit_is_the_edit_distance_of_one(self) -> None:
        """/* REQ-LANG-015: two words are within one edit exactly when their edit distance is at most 1: for
        40,000 generated pairs (equal, one or two edits apart, of different lengths, empty) and for every role
        suffix against every tail of every class name of this repository. */"""
        source = random.Random(157)
        letters = "abcx"
        pairs = [("", ""), ("a", ""), ("", "a"), ("ab", ""), ("abc", "abd"), ("abc", "ab"), ("abc", "bc"), ("abc", "ac")]
        for _ in range(40000):
            word = "".join(source.choice(letters) for _ in range(source.randint(0, 9)))
            other = list(word)
            for _edit in range(source.randint(0, 2)):
                at = source.randint(0, len(other))
                kind = source.choice("ids")
                if kind == "i":
                    other.insert(at, source.choice(letters))
                elif other and at < len(other):
                    if kind == "d":
                        del other[at]
                    else:
                        other[at] = source.choice(letters)
            pairs.append((word, "".join(other)))
        names = sorted({node.name for path in _python_sources() for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig")))
                        if isinstance(node, ast.ClassDef)})
        for name in names + list(self.NAMES):
            for suffix, _label, _keyword in purpose._ENTITY_SUFFIX_MAP:
                pairs.append((name[-len(suffix) - 1:].lower(), suffix.lower()))
        wrong = [(a, b) for a, b in pairs if purpose._within_one_edit(a, b) != (purpose._levenshtein(a, b) <= 1)]
        self.assertEqual([], wrong[:5], f"{len(wrong)} of {len(pairs)} pairs")
        self.assertGreater(sum(1 for a, b in pairs if a != b and purpose._within_one_edit(a, b)), 5000)

    def test_every_purpose_is_the_one_the_full_comparison_gives(self) -> None:
        """/* REQ-LANG-016: the purpose of every Python file of this repository, of the same files under the
        other languages' rules, and of files that hold class names one edit away from a role suffix, is the one
        worked out with every name compared with every suffix by the full edit distance and nothing kept. */"""
        reference = self.by_letters(purpose.infer_purpose)
        cases = []
        for path in _python_sources():
            text = path.read_text(encoding="utf-8-sig")
            relative = path.relative_to(REPOSITORY).as_posix()
            entities = [{"name": node.name, "kind": "class", "line": node.lineno} for node in ast.parse(text).body
                        if isinstance(node, ast.ClassDef)]
            cases.append((relative, text, "python", entities))
            cases.append((relative, "", "typescript", [dict(entity, kind="interface") for entity in entities]))
        for number, name in enumerate(self.NAMES):
            cases.append((f"lib/part{number}.py", "", "python", [{"name": name, "kind": "class", "line": 1}]))
            cases.append((f"lib/{name.lower()}.py", "", "python", []))
            cases.append((f"lib/pair{number}.py", "", "python", [{"name": other, "kind": "class", "line": 1}
                                                             for other in (name, self.NAMES[number - 1])]))
        different = [case[:3] for case in cases if reference(*case) != purpose.infer_purpose(*case)]
        self.assertEqual([], different[:5], f"{len(different)} of {len(cases)} purposes")
        self.assertGreater(len({purpose.infer_purpose(*case) for case in cases}), 40)
        for name, label in self.NEAR.items():
            self.assertEqual(label, purpose.infer_purpose("lib/part.py", "", "python", [{"name": name, "kind": "class", "line": 1}]))
        for name in ("EventHandlre", "Storey", "UserControler"):
            self.assertEqual("general module", purpose.infer_purpose("lib/part.py", "", "python", [{"name": name, "kind": "class", "line": 1}]))


class ExhaustiveIndex(symbol_index.SymbolIndex):
    """The glob search as it was written before the shortcut: every glob import of a module is followed for
    every name, and what an answer read is every module on the way. The reference of GlobSearchTest."""

    def _globs_of(self, module: str) -> list[tuple[str, bool]]:
        key = ("g", module)
        if module in self._glob_targets:
            self._hit(key)
            return self._glob_targets[module]
        self._open()
        result = self._results[module]
        found: list[tuple[str, bool]] = []
        for binding in self._bindings_named(module, "*"):
            target = self.module_id(symbol_index.ModuleRef(binding["module"], result.path, result.language))
            if target is not None:
                found.append((target, binding["scope"]))
        self._glob_targets[module] = found
        self._close(key, True)
        return found

    def _from_globs(self, module, name, depth, seen, in_scope=False):
        self._touch(("m", module))
        result = self._results[module]
        if result.language == "python" and name.startswith("_"):
            return None
        found = set()
        for target, scope in self._globs_of(module):
            if in_scope and not scope:
                continue
            resolved = self.export(target, name, depth + 1, seen)
            if resolved is not None:
                found.add(resolved)
        return next(iter(found)) if len(found) == 1 else None


def _random_project(source: random.Random, language: str) -> dict[str, str]:
    """Modules that define, import and glob-import names of each other at random: facades of several levels,
    names defined twice behind one facade, cycles, private names, chains longer than the depth limit."""
    count = source.randint(4, 14)
    names = [f"N{number}" for number in range(6)] + ["_hidden"]
    files: dict[str, str] = {}
    for number in range(count):
        others = [other for other in range(count) if other != number]
        lines: list[str] = []
        for _ in range(source.randint(0, 3)):
            target = source.choice(others)
            if language == "python":
                lines.append(f"from .m{target} import *")
            else:
                lines.append(f"export * from './m{target}';")
        for _ in range(source.randint(0, 2)):
            target, name = source.choice(others), source.choice(names)
            alias = source.choice(names)
            if language == "python":
                lines.append(f"from .m{target} import {name} as {alias}" if source.random() < 0.5 else f"from .m{target} import {name}")
            else:
                lines.append(f"export {{ {name} as {alias} }} from './m{target}';" if source.random() < 0.5
                             else f"import {{ {name} }} from './m{target}';")
        for name in source.sample(names, source.randint(0, 2)):
            lines.append(f"class {name}:\n    pass" if language == "python" else f"export class {name} {{}}")
        used = source.sample(names, 3)
        if language == "python":
            lines.append("def use():\n    return [" + ", ".join(used) + "]")
        else:
            lines.append("export const used = [" + ", ".join(used) + "];")
        files[f"pkg/m{number}.py" if language == "python" else f"pkg/m{number}.ts"] = "\n".join(lines) + "\n"
    if language == "python":
        files["pkg/__init__.py"] = "".join(f"from .m{number} import *\n" for number in source.sample(range(count), min(3, count)))
        chain = source.randint(15, 20)  # longer than the depth limit of the index
        for link in range(chain):
            files[f"pkg/c{link}.py"] = f"from .c{link + 1} import *\n" if link + 1 < chain else "class Deep:\n    pass\n"
        files["pkg/user.py"] = "from pkg import *\nfrom .c0 import Deep\nfrom .c0 import *\n\n\ndef use():\n    return [N0, N1, N2, N3, N4, N5, Deep]\n"
    return files


class GlobSearchTest(unittest.TestCase):
    def contexts(self):
        source = random.Random(157)
        for round_number in range(60):
            language = "python" if round_number % 3 else "typescript"
            with tempfile.TemporaryDirectory() as folder:
                root = Path(os.path.realpath(folder))
                files = _random_project(source, language)
                _write(root, files)
                results = [builder.parse_file((str(root / name), language, "pkg", str(root))) for name in sorted(files)]
                built = builder.graph_context(results, str(root))
                assert isinstance(built, builder.Ok)
                yield source, built.value

    @staticmethod
    def index(cls, context, trace: bool):
        bound = context.bound_languages
        return cls(
            context.id_to_result,
            lambda language, spec, path: bound[builder.language_for_result(language).name].resolve_import(spec, path),
            trace=trace,
        )

    def test_the_search_behind_glob_imports_answers_as_the_exhaustive_one(self) -> None:
        """/* REQ-LANG-017: for 60 generated Python and TypeScript projects (facades of several levels, a name
        defined twice behind a facade, re-export cycles, private names, chains longer than the depth limit),
        what every name means to an importer of every module, the definition every use site resolves to, and
        the resolved uses of every module are the same from the search that follows only the glob imports
        reaching a holder of the name as from the search that follows every glob import; in any order of
        questions. */"""
        asked = answered = 0
        for source, context in self.contexts():
            modules = list(context.id_to_result)
            names = [f"N{number}" for number in range(6)] + ["_hidden", "Deep", "use", "nothing"]
            questions = [(module, name) for module in modules for name in names]
            source.shuffle(questions)
            # Both are asked the same questions in the same order, and must keep the same answers.
            expected_index = self.index(ExhaustiveIndex, context, False)
            index = self.index(symbol_index.SymbolIndex, context, False)
            for question in questions:
                expected = expected_index.export(*question)
                self.assertEqual(expected, index.export(*question), question)
                asked += 1
                answered += expected is not None
            self.assertEqual(
                {key: value for key, value in expected_index._exports.items() if value is not None},
                {key: value for key, value in index._exports.items() if value is not None},
            )
            for cls in (ExhaustiveIndex, symbol_index.SymbolIndex):
                fresh = self.index(cls, context, False)
                resolved = [
                    (module, builder._module_uses(fresh, context.methods, module, result))
                    for module, result in context.id_to_result.items()
                ]
                if cls is ExhaustiveIndex:
                    expected_uses = resolved
                else:
                    self.assertEqual(expected_uses, resolved)
        self.assertGreater(asked, 5000)
        self.assertGreater(answered, 500)

    def test_what_an_answer_read_covers_what_the_exhaustive_search_read(self) -> None:
        """/* REQ-FRESH-054: for the same generated projects, what the facts of a module are recorded to have
        read (modules, module names, and for each module whose glob imports were searched every module they
        reach and the names of their glob imports) holds everything the exhaustive search read: a change that
        could alter an answer of the exhaustive search always reaches the module. */"""
        covered = searched = 0
        for _source, context in self.contexts():
            index = builder.symbol_index(context)
            reference = self.index(ExhaustiveIndex, context, True)
            records = {module: index.glob_record(module) for module in context.id_to_result}
            for module, result in context.id_to_result.items():
                index.begin()
                builder._module_uses(index, context.methods, module, result)
                read = index.end()
                reference.begin()
                builder._module_uses(reference, context.methods, module, result)
                expected = reference.end()
                modules = {value for kind, value in read if kind == "m"}
                specs = {value for kind, value in read if kind == "q"}
                todo = [value for kind, value in read if kind == "c"]
                searched += len(todo)
                seen: set[str] = set()
                while todo:
                    current = todo.pop()
                    if current in seen:
                        continue
                    seen.add(current)
                    modules.add(current)
                    targets, names = records[current]
                    specs.update(names)
                    todo.extend(targets)
                missing = {item for item in expected if item[1] not in (modules if item[0] == "m" else specs)}
                self.assertEqual(set(), missing, module)
                covered += len(expected)
        self.assertGreater(covered, 5000)
        self.assertGreater(searched, 300)


FACADE = {
    "src/zoo/__init__.py": "from .models import *\n",
    "src/zoo/models/__init__.py": "from .cat import *\nfrom .dog import *\nfrom .owl import *\n",
    "src/zoo/models/cat.py": "class Cat:\n    def speak(self):\n        return 'meow'\n",
    "src/zoo/models/dog.py": "class Dog:\n    def speak(self):\n        return 'woof'\n",
    "tests/test_cat.py": "from zoo import Cat\n\n\ndef test_cat():\n    assert Cat().speak()\n",
    "tests/test_dog.py": "from zoo.models.dog import Dog\n\n\ndef test_dog():\n    assert Dog().speak()\n",
}


class FacadeUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _write(self.root, FACADE)
        self.assertEqual(0, _build(self.root))

    def published(self) -> dict[str, bytes]:
        names = ("index.sexp", "index.uses.sexp", "cache.json", "graph-fragments.json")
        return {name: (self.root / ".wingbox" / name).read_bytes() for name in names}

    def assert_same_as_full_build(self, changed: list[str]) -> "graph_update.Updated":
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = graph_update.update_graph(str(self.root), changed, str(self.root / ".wingbox" / "graph-fragments.json"))
        self.assertIsInstance(result, graph_update.Updated, result)
        patched = self.published()
        self.assertEqual(0, _build(self.root, "--full"))
        rebuilt = self.published()
        for name in patched:
            self.assertEqual(patched[name], rebuilt[name], f"{name} differs after {changed}")
        return result

    def test_a_change_behind_the_facade_reaches_the_file_that_imports_through_it(self) -> None:
        """/* REQ-FRESH-055: a file that imports a class through a facade of `from .x import *` is stored with
        the facades it searched, not with every module behind them; when a module behind the facade starts to
        define the same class (the name becomes ambiguous), when the module a glob import was waiting for is
        created, and when the defining module is deleted, a partial update redoes the importer and publishes
        the graph, use file, cache and fragment store of a full build; a change of a module that is not behind
        the facade's glob imports leaves it alone. */"""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            (self.root / "tests/test_dog.py").write_text(FACADE["tests/test_dog.py"] + "\n\ndef test_more():\n    assert Dog\n", encoding="utf-8")
            alone = graph_update.update_graph(str(self.root), ["tests/test_dog.py"], str(self.root / ".wingbox" / "graph-fragments.json"))
        self.assertEqual(1, alone.redone)
        record = _records(self.root)["tests/test_cat.py"]
        self.assertEqual(["src/zoo/__init__", "src/zoo/models/__init__"], record[10])
        self.assertEqual(["src/zoo/__init__", "src/zoo/models/__init__", "src/zoo/models/cat"], record[7])
        self.assertEqual(["src/zoo/models/cat"], [use[2] for use in record[4] if use[3] == "Cat"])

        (self.root / "src/zoo/models/dog.py").write_text(FACADE["src/zoo/models/dog.py"] + "\n\nclass Cat:\n    pass\n", encoding="utf-8")
        result = self.assert_same_as_full_build(["src/zoo/models/dog.py"])
        self.assertLess(result.redone, result.modules)
        self.assertEqual([], [use for use in _records(self.root)["tests/test_cat.py"][4] or [] if use[3] == "Cat"])

        (self.root / "src/zoo/models/dog.py").write_text(FACADE["src/zoo/models/dog.py"], encoding="utf-8")
        self.assert_same_as_full_build(["src/zoo/models/dog.py"])
        self.assertEqual(["src/zoo/models/cat"], [use[2] for use in _records(self.root)["tests/test_cat.py"][4] if use[3] == "Cat"])

        _write(self.root, {"src/zoo/models/owl.py": "class Cat:\n    pass\n\n\nclass Owl:\n    pass\n"})
        self.assert_same_as_full_build(["src/zoo/models/owl.py"])
        self.assertEqual([], [use for use in _records(self.root)["tests/test_cat.py"][4] or [] if use[3] == "Cat"])

        (self.root / "src/zoo/models/owl.py").unlink()
        self.assert_same_as_full_build(["src/zoo/models/owl.py"])
        (self.root / "src/zoo/models/cat.py").unlink()
        self.assert_same_as_full_build(["src/zoo/models/cat.py"])
        self.assertEqual([], [use for use in _records(self.root)["tests/test_cat.py"][4] or [] if use[3] == "Cat"])

    def test_readers_of_a_facade_are_found_through_its_glob_imports(self) -> None:
        """/* REQ-FRESH-056: the modules to redo after a change are those that searched the glob imports of a
        module from which the changed module is reached through any number of glob imports, or whose glob
        imports carry a module name a created path could answer; a module that read neither is kept. */"""
        key, exclusions, stored = builder.read_fragments(str(self.root / ".wingbox" / "graph-fragments.json"), str(self.root)).value
        self.assertEqual({"src/zoo/__init__", "src/zoo/models/__init__", "src/zoo/models/dog"},
                         graph_update.glob_readers(stored, {"src/zoo/models/dog"}, frozenset()))
        self.assertEqual({"src/zoo/__init__", "src/zoo/models/__init__"},
                         graph_update.glob_readers(stored, set(), graph_update.path_tokens("src/zoo/models/owl.py")))
        self.assertEqual({"tests/test_dog"}, graph_update.glob_readers(stored, {"tests/test_dog"}, frozenset()))
        self.assertTrue(stored["tests/test_cat"].closures & graph_update.glob_readers(stored, {"src/zoo/models/dog"}, frozenset()))
        self.assertFalse(stored["tests/test_dog"].closures)


if __name__ == "__main__":
    unittest.main()
