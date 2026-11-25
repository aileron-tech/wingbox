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


if __name__ == "__main__":
    unittest.main()
