"""Defects of the query commands found in a review of version 0.19.1, one test class per defect.

The graphs are written by hand where a few facts show the defect, and built from a small tree where the
answer depends on what the builder writes."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import code_graph_search  # noqa: E402

_EDGE_HEADS = tuple(f"({kind} " for kind in code_graph_search._EDGE_KINDS)


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def write_graph(root: Path, nodes: dict[str, str], facts: Sequence[str] = (), uses: Sequence[str] = ()) -> Path:
    """A graph file written by hand at root/.wingbox/index.sexp: nodes maps a module ID to its language,
    facts are the entity and edge lines with module IDs. The header counts the nodes and the edges. uses are
    the lines of the use file beside it, written with positions and with the digest of this graph."""
    edges = sum(1 for fact in facts if fact.startswith(_EDGE_HEADS))
    lines = [f"(code-graph :schema 1 :files {len(nodes)} :nodes {len(nodes)} :edges {edges} :violations 0)", "(paths", ")"]
    lines += [f'(node {module} {language} "general module")' for module, language in nodes.items()]
    path = root / ".wingbox" / "index.sexp"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([*lines, *facts]) + "\n"
    path.write_text(text, encoding="utf-8")
    if uses:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        header = f"(code-graph-uses :schema 1 :key test :positions yes :graph {digest})"
        (path.parent / "index.uses.sexp").write_text("\n".join([header, *uses]) + "\n", encoding="utf-8")
    return path


def run(root: Path, *argv: str) -> tuple[int, str]:
    """(exit status, standard output) of the query script for the graph of root."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        status = code_graph_search.main([*argv, "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])
    return int(status), output.getvalue()


class TemporaryProject(unittest.TestCase):
    root: Path

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))

    def lines(self, *argv: str) -> list[str]:
        return run(self.root, *argv)[1].splitlines()


# core <- a_first; a_first <- b_second; core <- b_second; b_second <- c_third: b_second is reached at depth 2
# (through a_first) before it is reached at depth 1.
LATE_SHORT_PATH = {"app/core": "py", "app/a_first": "py", "app/b_second": "py", "app/c_third": "py"}
LATE_SHORT_PATH_EDGES = (
    "(imports app/a_first app/core)",
    "(imports app/b_second app/a_first)",
    "(imports app/b_second app/core)",
    "(imports app/c_third app/b_second)",
)
# The same shape for outgoing dependencies: top -> a_first -> b_second and top -> b_second -> leaf.
LATE_SHORT_PATH_OUT = {"app/top": "py", "app/a_first": "py", "app/b_second": "py", "app/leaf": "py"}
LATE_SHORT_PATH_OUT_EDGES = (
    "(imports app/top app/a_first)",
    "(imports app/top app/b_second)",
    "(imports app/a_first app/b_second)",
    "(imports app/b_second app/leaf)",
    "(fn app/leaf settle 1)",
)


class DependencyTreeTest(TemporaryProject):
    def test_rdeps_lists_every_dependent_within_the_depth(self) -> None:
        """/* REQ-CS-9200: `rdeps <id> <depth>` lists every module that depends on the module within the depth,
        each on one line without `:cycle` at its shortest depth: a module that is reached through a longer path
        first (at the last depth, where it is not followed further) is listed and followed at its shorter depth,
        so the files are those of `impact` for the same depth. */"""
        write_graph(self.root, LATE_SHORT_PATH, LATE_SHORT_PATH_EDGES)
        self.assertEqual(
            ["(rdeps-root app/core :lang py)",
             "(rdep app/core app/a_first :lang py :via imports :depth 1)",
             "(rdep app/core app/b_second :lang py :via imports :depth 1)",
             "(rdep app/core app/c_third :lang py :via imports :depth 2)"],
            self.lines("rdeps", "app/core", "2"),
        )
        _status, impact = run(self.root, "impact", "app/core", "2", "--json")
        listed = {row["file"] for row in json.loads(impact)["impact"][0]["rows"]}
        self.assertEqual({"app/a_first", "app/b_second", "app/c_third"}, listed)

    def test_deps_lists_every_dependency_within_the_depth(self) -> None:
        """/* REQ-CS-9201: `deps <id> <depth>` lists every module the module depends on within the depth, each
        at its shortest depth, also one that a longer path reaches first; the `dep` lines of `analyze` (depth 2)
        and the `calls-module` lines of `find_calls` are the same walk. */"""
        write_graph(self.root, LATE_SHORT_PATH_OUT, LATE_SHORT_PATH_OUT_EDGES)
        expected = [
            "(dep app/top app/a_first :lang py :via imports :depth 1)",
            "(dep app/top app/b_second :lang py :via imports :depth 1)",
            "(dep app/top app/leaf :lang py :via imports :depth 2)",
        ]
        self.assertEqual(["(deps-root app/top :lang py)", *expected], self.lines("deps", "app/top", "2"))
        self.assertEqual(expected, [line for line in self.lines("analyze", "app/top") if line.startswith("(dep ")])

    def test_the_callers_of_a_function_are_walked_the_same_way(self) -> None:
        """/* REQ-CS-9202: the `caller-module` lines of `find_callers <fn> <depth>` name every module that
        depends on the module of the function within the depth, each at its shortest depth. */"""
        write_graph(self.root, LATE_SHORT_PATH, (*LATE_SHORT_PATH_EDGES, "(fn app/core settle 1)"))
        modules = [line.split()[1] + line.split(":depth")[1][:2] for line in self.lines("find_callers", "settle", "2")
                   if line.startswith("(caller-module ")]
        self.assertEqual(["app/a_first 1", "app/b_second 1", "app/c_third 2"], modules)

    def test_a_module_that_comes_up_again_is_marked_as_before(self) -> None:
        """/* REQ-CS-9203: a module that is already listed and comes up again keeps its `:cycle` line, as version
        0.13.0 printed it: the second path of a diamond, and the way back to the module asked about. */"""
        write_graph(self.root, {"a": "py", "b": "py", "c": "py", "d": "py"},
                    ("(imports b a)", "(imports c a)", "(imports d b)", "(imports d c)", "(imports a d)"))
        self.assertEqual(
            ["(rdeps-root a :lang py)",
             "(rdep a b :lang py :via imports :depth 1)",
             "(rdep a d :lang py :via imports :depth 2)",
             "(rdep a a :lang py :via imports :depth 3 :cycle)",
             "(rdep a c :lang py :via imports :depth 1)",
             "(rdep a d :lang py :via imports :depth 2 :cycle)"],
            self.lines("rdeps", "a", "3"),
        )

    def test_the_query_forms_hold_every_module_within_the_depth(self) -> None:
        """/* REQ-CS-9204: the query forms `(rdeps "id" depth)`, `(deps "id" depth)`, `(find-callers "fn" depth)`
        and `(find-calls "fn" depth)` hold every module within the depth, also one a longer path reaches first. */"""
        write_graph(self.root, LATE_SHORT_PATH, (*LATE_SHORT_PATH_EDGES, "(fn app/core settle 1)"))
        self.assertEqual(["(result-count 3)"], self.lines("query", '(count (rdeps "app/core" 2))'))
        self.assertEqual(["(result app/a_first)", "(result app/b_second)"], self.lines("query", '(rdeps "app/core" 1)'))
        self.assertIn("(result app/c_third)", self.lines("query", '(find-callers "settle" 2)'))
        write_graph(self.root, LATE_SHORT_PATH_OUT, LATE_SHORT_PATH_OUT_EDGES)
        self.assertEqual(["(result app/a_first)", "(result app/b_second)", "(result app/leaf)"],
                         self.lines("query", '(deps "app/top" 2)'))
        write_graph(self.root, LATE_SHORT_PATH_OUT, (*LATE_SHORT_PATH_OUT_EDGES, "(fn app/top start 1)"))
        self.assertIn("(result app/leaf)", self.lines("query", '(find-calls "start" 2)'))


# Files on disk and the graph of them. src/icons.js is on disk and not in the graph (the build left it out);
# app/user_service.py, app/core.py and app/tool.rs have names that a wrong path is like.
NAMED_FILES = {
    "app/core.py": "def helper():\n    return 1\n",
    "app/hardcore.py": "LEVEL = 9\n",
    "app/user_service.py": "from app.core import helper\n\n\ndef serve():\n    return helper()\n",
    "app/tool.rs": "pub fn tool() -> u8 { 1 }\n",
    "src/icons_ts.ts": "export const icon = 1;\n",
    "src/icons.js": "var a=1;\n",
    "a/util.py": "def helper():\n    return 2\n",
    "a/user.py": "from a.util import helper\n\n\ndef use():\n    return helper()\n",
    "c/util.py": "def helper():\n    return 3\n",
    "b/other.py": "x = 1\n",
    "docs/guide.md": "# Guide\n",
}
NAMED_NODES = {"app/core": "py", "app/hardcore": "py", "app/user_service": "py", "app/tool": "rs", "src/icons_ts": "ts",
               "a/util": "py", "a/user": "py", "c/util": "py", "b/other": "py"}
NAMED_FACTS = (
    "(fn app/core helper 1)",
    "(fn app/user_service serve 4)",
    "(fn a/util helper 1)",
    "(fn a/user use 4)",
    "(fn c/util helper 1)",
    "(imports app/user_service app/core)",
    "(imports a/user a/util)",
)
NAMED_USES = (
    "(use app/user_service app/core::helper 1:22i 5:12c)",
    "(use a/user a/util::helper 1:20i 5:12c)",
)


class NamedFilesProject(TemporaryProject):
    def setUp(self) -> None:
        super().setUp()
        write_tree(self.root, NAMED_FILES)
        write_graph(self.root, NAMED_NODES, NAMED_FACTS, NAMED_USES)

    def run_in(self, folder: str, *argv: str) -> tuple[int, str]:
        with contextlib.chdir(self.root / folder):
            return run(self.root, *argv)


class ExactFileArgumentTest(NamedFilesProject):
    def test_impact_of_a_path_that_names_no_file_of_the_graph_is_an_error(self) -> None:
        """/* REQ-CS-9210: `impact <path>` of a path with a source extension that is the file of no module of
        the graph is never answered for another file whose name is like it (a longer name, a name that holds
        it, the same stem in another language or letter case): the answer is `(error no-match <path>
        :suggestions ...)`, or `(error not-in-graph <path> :reason ...)` for a file that exists, with exit
        status 1; `--json` carries the error for that file; `--sexp` keeps the no-match line of 0.13. */"""
        for asked in ("app/user.py", "app/cor.py", "app/Tool.cs", "app/tool.cs", "core.py", "icons.js"):
            with self.subTest(asked=asked):
                status, output = run(self.root, "impact", asked)
                self.assertEqual(1, status, output)
                self.assertTrue(output.startswith(f"(error no-match {asked}"), output)
                self.assertNotIn("(impact ", output)
        status, output = run(self.root, "impact", "src/icons.js")
        self.assertEqual((1, '(error not-in-graph src/icons.js :reason excluded :language "TypeScript")\n'), (status, output))
        status, output = run(self.root, "impact", "app/user.py", "--json")
        self.assertEqual((1, "no-match"), (status, json.loads(output)["impact"][0]["error"]))
        status, output = run(self.root, "impact", "app/user.py", "--sexp")
        self.assertEqual(0, status)
        self.assertTrue(output.startswith("(error no-match app/user"), output)
        self.assertNotIn("impact-root", output)

    def test_impact_takes_the_path_of_a_file_from_the_root_or_from_the_current_folder(self) -> None:
        """/* REQ-CS-9211: `impact` answers for the module whose file is exactly the path given: relative to
        the graph root, absolute, or relative to the current folder when no file of the graph has that path
        from the root; a module ID is taken as before. */"""
        for asked in ("app/core.py", "./app/core.py", "app/core", str(self.root / "app" / "core.py")):
            with self.subTest(asked=asked):
                status, output = run(self.root, "impact", asked)
                self.assertEqual(0, status, output)
                self.assertTrue(output.startswith("(impact app/core.py :direct 1 :total 1\n"), output)
        status, output = self.run_in("app", "impact", "core.py")
        self.assertEqual(0, status, output)
        self.assertTrue(output.startswith("(impact app/core.py :direct 1 "), output)
        status, output = self.run_in("b", "impact", "../a/util.py")
        self.assertTrue(output.startswith("(impact a/util.py :direct 1 "), output)

    def test_a_python_index_file_is_the_module_of_its_folder(self) -> None:
        """/* REQ-CS-9218: a Python file named index.py is the module of its folder, beside the __init__.py of
        the package: `impact pkg/index.py` answers for it and names that file, where it named
        pkg/__init__.py before; `impact pkg/__init__.py` answers for the package file. */"""
        write_tree(self.root, {"pkg/__init__.py": "", "pkg/index.py": "def start():\n    return 1\n",
                               "use.py": "from pkg.index import start\n\n\ndef go():\n    return start()\n"})
        build(self.root)
        self.assertEqual("(impact pkg/index.py :direct 1 :total 1\n (1 use.py)\n (read-first pkg/index.py use.py))\n",
                         run(self.root, "impact", "pkg/index.py")[1])
        self.assertEqual("(impact pkg/__init__.py :direct 0 :total 0\n (read-first pkg/__init__.py))\n",
                         run(self.root, "impact", "pkg/__init__.py")[1])
        self.assertTrue(run(self.root, "analyze", "pkg/index.py")[1].startswith('(analyze pkg :lang py "module entry point" :file pkg/index.py)'))

    def test_usages_of_a_file_that_is_not_in_the_graph_is_an_error(self) -> None:
        """/* REQ-CS-9212: `usages <file>::Name` answers for the file named exactly, from the root or from the
        current folder: asked in another folder, the name of a file that is elsewhere answers
        `(error no-match ...)` with exit status 1, in the three forms, and never the uses of that other file;
        `--sexp` keeps exit status 0. */"""
        status, output = self.run_in("b", "usages", "util.py::helper")
        self.assertEqual(1, status, output)
        self.assertTrue(output.startswith("(error no-match util.py::helper"), output)
        self.assertNotIn("(usages ", output)
        status, output = self.run_in("b", "usages", "util.py::helper", "--json")
        self.assertEqual((1, "no-match"), (status, json.loads(output)["error"]["code"]))
        self.assertEqual(1, self.run_in("b", "usages", "util.py::helper", "--human")[0])
        status, output = self.run_in("b", "usages", "util.py::helper", "--sexp")
        self.assertEqual((0, True), (status, output.startswith("(error no-match")))
        for folder, asked in (("a", "util.py::helper"), ("b", "a/util.py::helper"), ("b", "a/util::helper")):
            with self.subTest(folder=folder, asked=asked):
                status, output = self.run_in(folder, "usages", asked)
                self.assertEqual(0, status, output)
                self.assertEqual("(usages helper fn :at a/util.py:1 :uses 2 :files 1\n (a/user.py import 1:20 call 5:12))\n", output)

    def test_a_module_pattern_with_several_definitions_lists_the_candidates(self) -> None:
        """/* REQ-CS-9213: `usages <pattern>::Name` with a module pattern (no file extension) that matches
        several modules which define the name lists them as candidates, each as it can be asked, instead of
        answering for the first. */"""
        status, output = run(self.root, "usages", "util::helper")
        self.assertEqual(0, status)
        self.assertEqual(
            "(usages-ambiguous helper :definitions 2\n (a/util.py::helper fn 1)\n (c/util.py::helper fn 1))\n", output)

    def test_a_module_pattern_matches_a_suffix_at_a_boundary_first(self) -> None:
        """/* REQ-CS-9214: an argument without a file extension stays a module pattern; its suffix match starts
        at a path or name boundary, so `core` names app/core and not app/hardcore as well, and
        `service` names app/user_service. */"""
        self.assertEqual(["(rdeps-root app/core :lang py)"],
                         [line for line in self.lines("rdeps", "core", "1") if line.startswith("(rdeps-root")])
        self.assertEqual(["(result app/core)"], self.lines("query", '(resolve "core")'))
        self.assertEqual(["(result app/user_service)"], self.lines("query", '(resolve "service")'))
        self.assertEqual(["(result app/hardcore)"], self.lines("query", '(resolve "hardc")'))

    def test_an_empty_argument_is_an_error_for_every_command(self) -> None:
        """/* REQ-CS-9215: an argument that is empty or holds only spaces is an invalid argument (exit status
        2) for every command that takes a name, a path or a pattern; none answers for an arbitrary module or
        prints the whole graph. */"""
        for argv in (("impact", ""), ("impact", "", "2"), ("impact", " "), ("deps", "", "50"), ("rdeps", ""), ("analyze", ""),
                     ("module", ""), ("boundary", ""), ("quick", ""), ("flow", "", "helper"), ("usages", ""), ("entity", ""),
                     ("search", ""), ("find_function", ""), ("find_type", ""), ("find_callers", ""), ("files", ""),
                     ("source", ""), ("grep_functions", "")):
            with self.subTest(argv=argv):
                status, output = run(self.root, *argv)
                self.assertEqual(2, status, output)
                self.assertTrue(output.startswith("(error invalid-argument "), output)
                self.assertEqual(1, len(output.splitlines()), output)
        self.assertEqual(["(result-empty)"], self.lines("query", '(deps "" 5)'))


class FilePathForModuleCommandsTest(NamedFilesProject):
    def test_the_commands_that_take_a_module_take_its_file_path(self) -> None:
        """/* REQ-CS-9216: `analyze`, `deps`, `rdeps`, `module` and `boundary` take the path of a file of the
        graph (relative to the root, absolute, or relative to the current folder) where they take its module
        ID, and answer for that module; so do the query forms. */"""
        heads = {"analyze": "(analyze app/core :lang py", "deps": "(deps-root app/core :lang py)",
                 "rdeps": "(rdeps-root app/core :lang py)", "module": "(node app/core :lang py",
                 "boundary": "(boundary app/core :lang py"}
        for command, head in heads.items():
            for asked in ("app/core.py", str(self.root / "app" / "core.py"), "app/core"):
                with self.subTest(command=command, asked=asked):
                    status, output = run(self.root, command, asked)
                    self.assertEqual(0, status, output)
                    self.assertTrue(output.startswith(head), output)
            status, output = self.run_in("app", command, "core.py")
            self.assertTrue(output.startswith(head), output)
        self.assertEqual(["(result app/user_service)"], self.lines("query", '(rdeps "app/core.py" 1)'))

    def test_a_file_that_is_not_in_the_graph_is_an_error_with_exit_status_1(self) -> None:
        """/* REQ-CS-9217: `analyze`, `deps`, `rdeps`, `module` and `boundary` exit with status 1 when the file
        or module asked about is not in the graph: `(error not-in-graph <file> :reason ...)` for a file of the
        repository, `(error no-match ...)` for any other name, never the answer of a file whose name is like
        it; with `--json` the document carries the error and the status is the same. */"""
        for command in ("analyze", "deps", "rdeps", "module", "boundary"):
            with self.subTest(command=command):
                self.assertEqual((1, '(error not-in-graph src/icons.js :reason excluded :language "TypeScript")\n'),
                                 run(self.root, command, "src/icons.js"))
                self.assertEqual((1, "(error not-in-graph docs/guide.md :reason not-source)\n"),
                                 run(self.root, command, "docs/guide.md"))
                for asked in ("app/user.py", "app/tool.cs", "nosuchmodule"):
                    status, output = run(self.root, command, asked)
                    self.assertEqual(1, status, output)
                    self.assertTrue(output.startswith(f"(error no-match {asked}"), output)
                    self.assertEqual(1, len(output.splitlines()), output)
                status, output = run(self.root, command, "src/icons.js", "--json")
                self.assertEqual((1, "not-in-graph"), (status, json.loads(output)["error"]["code"]))
                self.assertEqual(0, run(self.root, command, "app/core")[0])


def build(root: Path) -> None:
    """Build the graph of a tree as `wingbox build` does."""
    from scripts import build_code_graph

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if build_code_graph.main(["--root", str(root)]) != 0:
            raise AssertionError(f"{root} does not build")


# A method whose signature takes three lines, a function longer than 100 lines, and a class of another language.
BODIES = {
    "shop/cart.py": (
        "LIMIT = 3\n\n\ndef first():\n    return LIMIT\n\n\n"
        "class Cart:\n    rate = 2\n\n    def __init__(self):\n        self.items = []\n\n"
        "    def total(\n        self,\n    ):\n        marker_in_method = sum(self.items)\n        return marker_in_method\n\n\n"
        "def long():\n" + "".join(f"    step_{n} = {n}\n" for n in range(1, 131)) + "    marker_far_down = 1\n    return marker_far_down\n"
    ),
    "web/cart.ts": (
        "export class Basket {\n  items: number[] = [];\n\n  add(item: number): number {\n    const markerInTs = item;\n"
        "    return markerInTs;\n  }\n}\n\nexport function plain(): number {\n  return 1;\n}\n"
    ),
}


class GrepFunctionsTest(TemporaryProject):
    def test_a_match_is_named_by_the_function_or_method_that_holds_it(self) -> None:
        """/* REQ-CS-9220: `grep_functions <pattern>` searches the whole body of every function and of every
        method of a class: a line inside a method is named `<module>::<Class>.<method>` (never by the function
        defined before the class), a line more than 100 lines below the start of a function is found, and a
        class of a file without top-level functions is searched. */"""
        write_tree(self.root, BODIES)
        build(self.root)
        file = f"{self.root}/shop/cart.py"
        self.assertEqual(
            [f'(grep-fn shop/cart::Cart.total :line 17 :lang py :file {file} :match "        marker_in_method = sum(self.items)")',
             f'(grep-fn shop/cart::Cart.total :line 18 :lang py :file {file} :match "        return marker_in_method")',
             "(grep-functions-summary :matches 2 :pattern marker_in_method)"],
            self.lines("grep_functions", "marker_in_method"),
        )
        self.assertEqual(
            [f'(grep-fn shop/cart::long :line 152 :lang py :file {file} :match "    marker_far_down = 1")',
             f'(grep-fn shop/cart::long :line 153 :lang py :file {file} :match "    return marker_far_down")',
             "(grep-functions-summary :matches 2 :pattern marker_far_down)"],
            self.lines("grep_functions", "marker_far_down"),
        )
        self.assertEqual(["web/cart::Basket.add", "web/cart::Basket.add"],
                         [line.split()[1] for line in self.lines("grep_functions", "markerInTs") if line.startswith("(grep-fn ")])
        named = [line.split()[1] for line in self.lines("grep_functions", "self.items") if line.startswith("(grep-fn ")]
        self.assertEqual(["shop/cart::Cart.__init__", "shop/cart::Cart.total"], named)
        self.assertEqual(["shop/cart::Cart"],
                         [line.split()[1] for line in self.lines("grep_functions", "rate = 2") if line.startswith("(grep-fn ")])

    def test_only_files_of_the_graph_under_the_root_are_read(self) -> None:
        """/* REQ-CS-9221: `grep_functions` reads a file only as `source` and `grep` do: a module of the graph
        file whose path leaves the root, or whose file is a link, is not read, and none of its lines is
        printed. */"""
        project = self.root / "project"
        write_tree(self.root, {"outside/secret.py": "def leak():\n    token = 'marker_outside_the_root'\n",
                               "project/app/inside.py": "def kept():\n    return 'marker_inside_the_root'\n"})
        (project / "app" / "linked.py").symlink_to(self.root / "outside" / "secret.py")
        write_graph(project, {"../outside/secret": "py", "app/inside": "py", "app/linked": "py"},
                    ("(fn ../outside/secret leak 1)", "(fn app/inside kept 1)", "(fn app/linked leak 1)"))
        self.assertEqual(["(grep-functions-empty marker_outside_the_root)"],
                         run(project, "grep_functions", "marker_outside_the_root")[1].splitlines())
        inside = run(project, "grep_functions", "marker_inside_the_root")[1]
        self.assertIn("(grep-fn app/inside::kept :line 2 ", inside)


# Characters at which str.splitlines ends a line and the parsers do not.
_LINE_LIKE = ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


class SourceLineNumbersTest(TemporaryProject):
    def test_source_and_grep_number_the_lines_as_the_graph_does(self) -> None:
        """/* REQ-CS-9230: `source` and `grep` number the lines of a file as the graph numbers them: a line
        ends at a line feed (with the carriage return before it) and at nothing else, so a form feed, a
        vertical tab, a file, group or record separator, NEL, U+2028 or U+2029 inside a line does not shift
        the numbers of the lines below it; such a character is printed as a space, so an answer keeps one
        fact a line. */"""
        for character in _LINE_LIKE:
            with self.subTest(character=repr(character)):
                write_tree(self.root, {"app/paged.py": f"FIRST = 1{character}\r\ndef second():\n    return 'needle'\n"})
                write_graph(self.root, {"app/paged": "py"}, ("(fn app/paged second 2)",))
                status, output = run(self.root, "source", "app/paged.py")
                self.assertEqual(
                    (0, ["(source app/paged.py :first 1 :last 3 :of 3)", '(line 1 "FIRST = 1 ")', '(line 2 "def second():")',
                         "(line 3 \"    return 'needle'\")"]),
                    (status, output.split("\n")[:-1]),
                )
                self.assertEqual("(source app/paged.py :first 2 :last 3 :of 3 :entity app/paged::second)",
                                 self.lines("source", "second")[0])
                self.assertEqual("(match app/paged.py 3 \"    return 'needle'\")", self.lines("grep", "needle")[0])
                self.assertIn("(grep-fn app/paged::second :line 3 ", run(self.root, "grep_functions", "needle")[1])

    def test_the_numbers_are_those_of_a_built_graph(self) -> None:
        """/* REQ-CS-9231: in a graph built from a file that holds a form feed, the line the graph gives a
        definition is the line `source` prints for it. */"""
        write_tree(self.root, {"app/paged.py": "FIRST = 1\n\x0c\n\ndef second():\n    return 2\n"})
        build(self.root)
        self.assertEqual(
            ["(source app/paged.py :first 4 :last 5 :of 5 :entity app/paged::second)", '(line 4 "def second():")', '(line 5 "    return 2")'],
            run(self.root, "source", "second")[1].split("\n")[:-1],
        )


NOT_UTF8 = b"; \xff\xfe\n"
SUPERSCRIPT_TWO = chr(0xB2)   # a character str.isdigit accepts and int() refuses


def run_with_errors(root: Path, *argv: str) -> tuple[int, str, str]:
    """(exit status, standard output, standard error) of the query script for the graph of root."""
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        status = code_graph_search.main([*argv, "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])
    return int(status), output.getvalue(), errors.getvalue()


class ErrorsInPlaceOfTracebacksTest(NamedFilesProject):
    def test_a_graph_file_that_is_not_utf8_is_an_invalid_graph(self) -> None:
        """/* REQ-CS-9240: a graph file that holds bytes that are not UTF-8 answers `(error invalid-map ...)`
        with exit status 1, as any graph that cannot be read; `boundaries --strict` ends with its
        `boundaries-result` line and exit status 3 (no readable graph), never 1; with `--json` the answer is
        one JSON object whose first key is `error`. */"""
        path = self.root / ".wingbox" / "index.sexp"
        path.write_bytes(path.read_bytes() + NOT_UTF8)
        status, output = run(self.root, "impact", "app/core.py")
        self.assertEqual(1, status)
        self.assertTrue(output.startswith("(error invalid-map "), output)
        status, output = run(self.root, "boundaries", "--strict")
        self.assertEqual(3, status, output)
        self.assertTrue(output.splitlines()[-1].startswith("(boundaries-result :status error "), output)
        status, output = run(self.root, "analyze", "app/core", "--json")
        self.assertEqual((1, "invalid-map"), (status, json.loads(output)["error"]["code"]))

    def test_a_number_that_is_no_decimal_number_is_refused(self) -> None:
        """/* REQ-CS-9241: a count or a line number written with a character that is a digit and no decimal
        number (a superscript two) is refused where a number is read: the node count of the graph header is
        an invalid graph, the line of an entity is 0, and a line number of `source` or the `--first` of `grep`
        is an invalid argument (exit status 2). */"""
        self.assertEqual((2, '(error invalid-argument "source: first and last are line numbers from 1")\n'),
                         run(self.root, "source", "app/core.py", SUPERSCRIPT_TWO))
        self.assertEqual((2, '(error invalid-argument "grep: --first takes the number of a match, from 1")\n'),
                         run(self.root, "grep", "helper", "--first", SUPERSCRIPT_TWO))
        write_graph(self.root, NAMED_NODES, tuple(fact.replace("helper 1)", f"helper {SUPERSCRIPT_TWO})") for fact in NAMED_FACTS))
        self.assertIn("(fn-def app/core::helper :line 0 ", run(self.root, "find_function", "helper")[1])
        path = self.root / ".wingbox" / "index.sexp"
        path.write_text(path.read_text(encoding="utf-8").replace(f":nodes {len(NAMED_NODES)} ", f":nodes {SUPERSCRIPT_TWO} "), encoding="utf-8")
        self.assertEqual((1, '(error invalid-map "code-graph header has an invalid node count")\n'), run(self.root, "impact", "app/core.py"))

    def test_a_query_function_without_its_argument_is_an_invalid_query(self) -> None:
        """/* REQ-CS-9242: a function of the query language called without the set it works on (`(count)`,
        `(intersect)`, `(diff)`, `(filter)`, `(files)`, `(layer-of)`) answers one `(error invalid-query ...)`
        line that names the function; with `--json` one JSON object whose first key is `error`. */"""
        for name in ("count", "intersect", "diff", "filter", "files", "layer-of"):
            with self.subTest(name=name):
                _status, output = run(self.root, "query", f"({name})")
                self.assertEqual(1, len(output.splitlines()), output)
                self.assertTrue(output.startswith(f'(error invalid-query "({name})" "{name} takes a set as its first argument'), output)
                document = json.loads(run(self.root, "query", f"({name})", "--json")[1])
                self.assertEqual(("error", "invalid-query"), (next(iter(document)), document["error"]["code"]))
        self.assertEqual("(result-count 1)\n", run(self.root, "query", '(count (rdeps "app/core" 1))')[1])

    def test_a_query_nested_too_deep_is_an_invalid_query(self) -> None:
        """/* REQ-CS-9243: a query nested deeper than the language allows (100 levels) answers one
        `(error invalid-query ...)` line that says so, whatever its depth; a query of a few levels is
        evaluated. */"""
        deep = "(count " * 3000 + '(deps "app/user_service" 1)' + ")" * 3000
        _status, output = run(self.root, "query", deep)
        self.assertEqual(1, len(output.splitlines()))
        self.assertTrue(output.startswith("(error invalid-query "), output[:80])
        self.assertTrue(output.endswith('"the expression is nested deeper than 100 levels")\n'), output[-120:])
        self.assertEqual("(result-count 1)\n", run(self.root, "query", '(count (union (deps "app/user_service" 1)))')[1])

    def test_a_use_file_that_is_not_utf8_is_said_and_not_a_traceback(self) -> None:
        """/* REQ-CS-9244: a use file that holds bytes that are not UTF-8 is not read: `usages` answers from
        the graph, marks the answer `:incomplete unreadable-use-file` and standard error carries the
        `(warning uses-unreadable ...)` line. */"""
        path = self.root / ".wingbox" / "index.uses.sexp"
        path.write_bytes(path.read_bytes() + NOT_UTF8)
        status, output, errors = run_with_errors(self.root, "usages", "a/util.py::helper")
        self.assertEqual(0, status)
        self.assertEqual("(usages helper fn :at a/util.py:1 :uses 0 :files 0 :incomplete unreadable-use-file)\n", output)
        self.assertIn("(warning uses-unreadable ", errors)


USED_THROUGH_A_BARREL = {
    "src/button.ts": 'export default class Button {\n  render(): string { return "b"; }\n}\n',
    "src/barrel.ts": 'export { default as Knob } from "./button";\n',
    "src/uses_named.ts": 'import Button from "./button";\nexport function make(): string { return new Button().render(); }\n',
    "src/uses_barrel.ts": 'import { Knob } from "./barrel";\nexport const k = new Knob();\n',
}


class IncompleteUsagesTest(TemporaryProject):
    def tearDown(self) -> None:
        os.environ.pop("WINGBOX_POSITIONS_FILL", None)

    def test_an_answer_made_before_the_positions_are_read_says_so_and_names_every_file(self) -> None:
        """/* REQ-CS-9250: while the use file of a graph has no positions and `usages` reads only the files
        that hold the name (a repository of over 300 files; WINGBOX_POSITIONS_FILL=0 here), the answer says
        `:incomplete positions-pending` and lists every file the use lines name: a file that uses the class
        under another name through a barrel is a row without positions, not left out. `--json` carries
        "incomplete" and the text for people says it; once the positions are read the answer is complete and
        carries no mark. */"""
        write_tree(self.root, USED_THROUGH_A_BARREL)
        build(self.root)
        os.environ["WINGBOX_POSITIONS_FILL"] = "0"
        status, output = run(self.root, "usages", "src/button.ts::Button")
        self.assertEqual(0, status)
        self.assertEqual(
            "(usages Button cls :at src/button.ts:1 :uses 4 :files 3 :incomplete positions-pending\n"
            " (src/barrel.ts reference)\n"
            " (src/uses_barrel.ts reference)\n"
            " (src/uses_named.ts import 1:8 call 2:45))\n",
            output,
        )
        document = json.loads(run(self.root, "usages", "src/button.ts::Button", "--json")[1])["usages"]
        self.assertEqual("positions-pending", document["incomplete"])
        self.assertIn({"file": "src/uses_barrel.ts", "line": None, "column": None, "kind": "reference"}, document["rows"])
        text = run(self.root, "usages", "src/button.ts::Button", "--human")[1]
        self.assertIn("  Not complete yet: ", text)
        self.assertIn("src/uses_barrel.ts\n  -   use (position not read yet)\n", text)
        os.environ.pop("WINGBOX_POSITIONS_FILL")
        complete = run(self.root, "usages", "src/button.ts::Button")[1]
        self.assertNotIn(":incomplete", complete)
        self.assertIn(" (src/uses_barrel.ts import 1:10 call 2:22)\n", complete)
        self.assertNotIn("incomplete", json.loads(run(self.root, "usages", "src/button.ts::Button", "--json")[1])["usages"])

    def test_a_missing_or_stale_use_file_is_said_in_the_answer(self) -> None:
        """/* REQ-CS-9251: when the use file of the graph is missing, or belongs to another build of the graph,
        `usages` says so in the answer (`:incomplete no-use-file`, `:incomplete stale-use-file`; "incomplete"
        with --json; a line of the text for people) and on standard error, instead of answering 0 uses as if
        nothing used the symbol; `--sexp` keeps its lines. */"""
        write_tree(self.root, NAMED_FILES)
        write_graph(self.root, NAMED_NODES, NAMED_FACTS, NAMED_USES)
        uses = self.root / ".wingbox" / "index.uses.sexp"
        kept = uses.read_text(encoding="utf-8")
        uses.unlink()
        status, output, errors = run_with_errors(self.root, "usages", "a/util.py::helper")
        self.assertEqual((0, "(usages helper fn :at a/util.py:1 :uses 0 :files 0 :incomplete no-use-file)\n"), (status, output))
        self.assertIn("(warning uses-missing ", errors)
        self.assertEqual("no-use-file", json.loads(run(self.root, "usages", "a/util.py::helper", "--json")[1])["usages"]["incomplete"])
        self.assertIn("  Not complete: the use file of the graph is missing", run(self.root, "usages", "a/util.py::helper", "--human")[1])
        self.assertEqual("(usages-target a/util::helper :kind fn :file a/util.py :line 1)\n(usages-summary a/util::helper :uses 0 :files 0 :unresolved 0)\n",
                         run(self.root, "usages", "a/util::helper", "--sexp")[1])
        uses.write_text(kept.replace(":graph ", ":graph 0"), encoding="utf-8")
        self.assertEqual("(usages helper fn :at a/util.py:1 :uses 0 :files 0 :incomplete stale-use-file)\n",
                         run(self.root, "usages", "a/util.py::helper")[1])
        uses.write_text(kept, encoding="utf-8")
        self.assertEqual("(usages helper fn :at a/util.py:1 :uses 2 :files 1\n (a/user.py import 1:20 call 5:12))\n",
                         run(self.root, "usages", "a/util.py::helper")[1])


class GraphCutShortTest(TemporaryProject):
    def test_a_graph_with_fewer_edges_than_its_header_declares_is_refused(self) -> None:
        """/* REQ-CS-9260: the header of a graph file declares its edges (`:edges N`, one per edge fact the
        builder writes), and a graph that holds fewer edge facts is an invalid graph: a file cut short
        before or among its edges answers `(error invalid-map "code-graph edge count mismatch: ...")` with
        exit status 1 for every command, `usages` (which reads the graph for one name) included, and
        `boundaries --strict` exits 3, where it answered from fewer dependencies before. */"""
        write_tree(self.root, {"app/core.py": "def helper():\n    return 1\n",
                               "app/a_first.py": "from app.core import helper\n\n\ndef a():\n    return helper()\n",
                               "app/b_second.py": "from app.core import helper\nfrom app.a_first import a\n\n\ndef b():\n    return helper() + a()\n"})
        build(self.root)
        path = self.root / ".wingbox" / "index.sexp"
        whole = path.read_text(encoding="utf-8")
        self.assertIn(":edges 6 ", whole)
        self.assertTrue(run(self.root, "impact", "app/core.py")[1].startswith("(impact app/core.py :direct 2 :total 2\n"))
        self.assertEqual(0, run(self.root, "usages", "app/core.py::helper")[0])
        for cut_before in ("(imports ", "(refs "):
            with self.subTest(cut_before=cut_before):
                path.write_text(whole[: whole.index(cut_before)], encoding="utf-8")
                for argv in (("impact", "app/core.py"), ("rdeps", "app/core"), ("usages", "app/core.py::helper"), ("layers",)):
                    status, output = run(self.root, *argv)
                    self.assertEqual(1, status, output)
                    self.assertTrue(output.startswith('(error invalid-map "code-graph edge count mismatch: expected 6, loaded '), output)
                status, output = run(self.root, "boundaries", "--strict")
                self.assertEqual(3, status, output)
        path.write_text(whole, encoding="utf-8")
        self.assertEqual(0, run(self.root, "boundaries")[0] % 2)

    def test_a_graph_that_declares_no_edge_count_or_a_smaller_one_is_read(self) -> None:
        """/* REQ-CS-9261: a graph whose header states no edge count, or a smaller one than the edge facts it
        holds (a graph written by hand), is read as before: no edge is missing from it. */"""
        path = write_graph(self.root, {"a": "py", "b": "py"}, ("(imports b a)",))
        expected = ["(rdeps-root a :lang py)", "(rdep a b :lang py :via imports :depth 1)"]
        for header in ("", " :edges 0"):
            with self.subTest(header=header):
                path.write_text(path.read_text(encoding="utf-8").replace(" :edges 1", header), encoding="utf-8")
                self.assertEqual(expected, self.lines("rdeps", "a", "1"))
                path = write_graph(self.root, {"a": "py", "b": "py"}, ("(imports b a)",))


# Paths as projects have them: a route group of Next.js, a folder with a space, a semicolon.
ODD_NAMES = {
    "app/shared.ts": "export const shared = 1;\n",
    "app/(group)/page.tsx": 'import { shared } from "../shared";\nexport function Page() { return shared; }\n',
    "my dir/a.py": "def spaced():\n    return 1\n",
    "my dir/b.py": "from a import spaced\n\n\ndef user():\n    return spaced()\n",
    "semi;colon/c.py": "def semi():\n    return 1\n",
}


class QuotedAtomsTest(TemporaryProject):
    def setUp(self) -> None:
        super().setUp()
        write_tree(self.root, ODD_NAMES)
        build(self.root)

    def test_a_name_that_is_no_plain_symbol_is_one_quoted_atom(self) -> None:
        """/* REQ-CS-9270: in the fact lines of the query commands a module or a file whose name holds a
        parenthesis, a semicolon or a space is one quoted atom, wherever a module is named (`file`, `dep`,
        `rdep`, `node`, `analyze`, `fn-def`, an edge), so the line reads as one form and `--json` gives the
        name as one argument; a plain name is written bare, as before. */"""
        self.assertEqual(
            ['(file "app/(group)/page.tsx" :module "app/(group)/page" :lang tsx :layer other)',
             "(file app/shared.ts :module app/shared :lang ts :layer other)",
             '(file "my dir/a.py" :module "my dir/a" :lang py :layer other)',
             '(file "my dir/b.py" :module "my dir/b" :lang py :layer other)',
             '(file "semi;colon/c.py" :module "semi;colon/c" :lang py :layer other)',
             "(files-summary :count 5 :of 5)"],
            self.lines("files"),
        )
        self.assertEqual(["(rdeps-root app/shared :lang ts)", '(rdep app/shared "app/(group)/page" :lang tsx :via imports :depth 1)'],
                         self.lines("rdeps", "app/shared", "1"))
        self.assertEqual(['(deps-root "app/(group)/page" :lang tsx)', '(dep "app/(group)/page" app/shared :lang ts :via imports :depth 1)'],
                         self.lines("deps", "app/(group)/page", "1"))
        self.assertEqual('(analyze "semi;colon/c" :lang py "general module" :file "semi;colon/c.py")', self.lines("analyze", "semi;colon/c")[0])
        self.assertTrue(self.lines("find_function", "Page")[0].startswith('(fn-def "app/(group)/page::Page" :line 2 :lang tsx :file "'))
        module = self.lines("module", "my dir/b")
        self.assertTrue(module[0].startswith('(node "my dir/b" :lang py "general module" :file "'), module[0])
        self.assertIn('(imports "my dir/b" "my dir/a")', module)
        self.assertIn('(refs "my dir/b" "my dir/a::spaced")', module)
        facts = json.loads(run(self.root, "module", "my dir/b", "--json")[1])["facts"]
        self.assertEqual(["my dir/b", "general module"], facts[0]["args"])
        self.assertIn({"fact": "imports", "args": ["my dir/b", "my dir/a"]}, facts)
        self.assertEqual({"fact": "rdep", "args": ["app/shared", "app/(group)/page"], "lang": "tsx", "via": "imports", "depth": 1},
                         json.loads(run(self.root, "rdeps", "app/shared", "1", "--json")[1])["facts"][1])

    def test_the_flat_facts_keep_the_atoms_of_version_0_13(self) -> None:
        """/* REQ-CS-9271: the flat facts of `impact --sexp` and `usages --sexp` write such a name as versions
        up to 0.13 wrote it (quoted only for a space or a quote), so their lines stay byte for byte what
        scripts written for those versions read. */"""
        self.assertEqual(
            ["(impact-root app/(group)/page :lang tsx :file app/(group)/page.tsx)",
             "(impact-summary app/(group)/page :direct 0 :total 0 :depth 2)",
             "(read-first app/(group)/page.tsx)"],
            self.lines("impact", "app/(group)/page.tsx", "--sexp"),
        )
        self.assertEqual("(usages-target app/(group)/page::Page :kind fn :file app/(group)/page.tsx :line 2)",
                         self.lines("usages", "app/(group)/page.tsx::Page", "--sexp")[0])

    def test_the_command_of_a_summary_quotes_the_file_for_a_shell(self) -> None:
        """/* REQ-CS-9282: the command `impact --summary` names for the complete list (`full`) writes a file
        whose name holds a character a shell reads (a parenthesis, a space) quoted for a shell, in the three
        forms; a plain file name is written as before. */"""
        self.assertEqual(""" (full "wingbox impact 'app/(group)/page.tsx' 2"))""", self.lines("impact", "app/(group)/page.tsx", "--summary")[-1])
        self.assertEqual(""" (full "wingbox impact 'my dir/a.py' 2"))""", self.lines("impact", "my dir/a.py", "--summary")[-1])
        self.assertEqual(' (full "wingbox impact app/shared.ts 2"))', self.lines("impact", "app/shared.ts", "--summary")[-1])
        document = json.loads(run(self.root, "impact", "my dir/a.py", "--summary", "--json")[1])
        self.assertEqual("wingbox impact 'my dir/a.py' 2", document["impact"][0]["full"])
        self.assertIn("  Complete list: wingbox impact 'my dir/a.py' 2\n", run(self.root, "impact", "my dir/a.py", "--summary", "--human")[1])


class SmallFixesTest(TemporaryProject):
    def test_an_escaped_dollar_at_the_end_of_a_text_is_text(self) -> None:
        """/* REQ-CS-9280: in a pattern a last `$` after a backslash is the character itself, not the end of
        the line: `grep 'price\\$'` finds the lines that hold `price$`; an unescaped last `$` is still the end
        of the line, also after an escaped backslash. */"""
        write_tree(self.root, {"app/money.py": 'LABEL = "price$ (net)"\nprice = 1\nx = price\nPATH = "dir\\\\"\n'})
        write_graph(self.root, {"app/money": "py"})
        self.assertEqual(['(match app/money.py 1 "LABEL = \\"price$ (net)\\"")', "(matches-summary :matches 1 :files 1 :searched 1 :pattern price\\$)"],
                         self.lines("grep", "price\\$"))
        self.assertEqual(['(match app/money.py 3 "x = price")'], self.lines("grep", "price$")[:1])
        self.assertEqual(1, len([line for line in self.lines("grep", "price$") if line.startswith("(match ")]))
        self.assertTrue(self.lines("grep", "dir.*\\\\\"$")[0].startswith("(match app/money.py 4 "))
        self.assertTrue(run(self.root, "grep", "price\\")[1].startswith("(error invalid-argument "))

    def test_entity_matches_a_name_whatever_its_letter_case(self) -> None:
        """/* REQ-CS-9281: `entity <name>` with a name no definition is spelled as finds the definitions its
        pattern matches, the letter case ignored, as `entity '<name>.*'` does: `entity engine` finds the
        class Engine. */"""
        write_graph(self.root, {"app/motor": "py", "app/car": "py"},
                    ("(cls app/motor Engine 3)", "(imports app/car app/motor)", "(refs app/car app/motor::Engine)"))
        self.assertEqual(self.lines("entity", "engine.*"), self.lines("entity", "engine"))
        self.assertTrue(self.lines("entity", "engine")[0].startswith("(entity-def cls app/motor Engine 3 :lang py "))
        self.assertIn("(refs app/car app/motor::Engine)", self.lines("entity", "engine"))
        self.assertEqual(["(error no-match nothing_named_so)"], self.lines("entity", "nothing_named_so"))


class CallsHintTest(TemporaryProject):
    def test_without_calls_edges_the_answer_says_where_calls_are_listed(self) -> None:
        """/* REQ-CS-9290: when the graph holds no calls edge for the function (a build without --lsp writes
        none), `find_callers` and `find_calls` print the lines they printed before on standard output and say
        on standard error that calls edges need a build with --lsp and that `wingbox usages <fn>` lists the
        call sites the build resolved; with a calls edge nothing is added. */"""
        facts = ("(fn app/core helper 1)", "(fn app/user serve 4)", "(imports app/user app/core)")
        write_graph(self.root, {"app/core": "py", "app/user": "py"}, facts)
        status, output, errors = run_with_errors(self.root, "find_callers", "helper", "1")
        self.assertEqual(0, status)
        self.assertEqual("; no calls edges found, showing module-level callers via imports/refs", output.splitlines()[1])
        self.assertTrue(output.splitlines()[2].startswith("(caller-module app/user -> app/core :via imports :depth 1 "), output)
        self.assertIn("build with --lsp", errors)
        self.assertIn("wingbox usages helper", errors)
        status, output, errors = run_with_errors(self.root, "find_calls", "serve", "1")
        self.assertEqual("; no calls edges found, showing module-level deps via imports", output.splitlines()[1])
        self.assertIn("build with --lsp", errors)
        write_graph(self.root, {"app/core": "py", "app/user": "py"}, (*facts, "(calls app/user::serve app/core::helper :lines 5 :lsp)"))
        status, output, errors = run_with_errors(self.root, "find_callers", "helper", "1")
        self.assertIn("(caller app/user::serve -> app/core::helper :lang py ", output)
        self.assertEqual("", errors)


class McpFilePathTest(unittest.TestCase):
    def test_the_tools_that_take_a_module_take_its_file_path(self) -> None:
        """/* REQ-MCP-9200: the tools `describe_module`, `list_dependencies` and `check_boundaries` take the
        path of a file of the graph where they take a module ID, as their descriptions say, and answer for
        that module; the path of a file that is not in the graph is a tool error, never the answer of
        another file. */"""
        from tests.test_mcp_server import McpSession, _call, _initialize, _make_shop, _texts

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        _make_shop(root)
        session = McpSession(root, [
            _initialize(),
            _call(2, "build_graph", {}),
            _call(3, "describe_module", {"module": "shop/pricing.py"}),
            _call(4, "list_dependencies", {"module": "shop/checkout.py", "depth": 1}),
            _call(5, "list_dependencies", {"module": "shop/pricing.py", "direction": "in", "depth": 1}),
            _call(6, "check_boundaries", {"scope": "shop/pricing.py"}),
            _call(7, "list_dependencies", {"module": "shop/pric.py", "depth": 1}),
            _call(8, "describe_module", {"module": "shop/check.py"}),
        ])
        self.assertFalse(session.reply(2)["result"]["isError"], session.reply(2))
        for identifier, head in ((3, "(analyze shop/pricing :lang py "), (4, "(deps-root shop/checkout :lang py)"),
                                 (5, "(rdeps-root shop/pricing :lang py)")):
            reply = session.reply(identifier)["result"]
            self.assertFalse(reply["isError"], reply)
            self.assertTrue(_texts(session.reply(identifier))[0].startswith(head), reply)
        self.assertIn("(dep shop/checkout shop/pricing ", _texts(session.reply(4))[0])
        self.assertIn("(rdep shop/pricing shop/checkout ", _texts(session.reply(5))[0])
        boundaries = session.reply(6)["result"]
        self.assertFalse(boundaries["isError"], boundaries)
        self.assertNotIn("no-match", _texts(session.reply(6))[0])
        for identifier, asked in ((7, "shop/pric.py"), (8, "shop/check.py")):
            reply = session.reply(identifier)["result"]
            self.assertTrue(reply["isError"], reply)
            self.assertIn(f"(error no-match {asked}", _texts(session.reply(identifier))[0])


if __name__ == "__main__":
    unittest.main()
