"""The tools of benchmarks/cpp and benchmarks/collisions: the references they build, on small trees."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
CPP = ROOT / "benchmarks" / "cpp"
COLLISIONS = ROOT / "benchmarks" / "collisions" / "run.py"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _tree(root: Path, files: dict[str, str]) -> None:
    _write(root, files)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)


TREE = {
    "include/depot/crate.h": "struct Crate {};\n",
    "include/depot/pallet.h": '#include "crate.h"\n',
    "src/crate.cpp": '#include "depot/crate.h"\n#include <depot/pallet.h>\n#include <vector>\n// #include "depot/gone.h"\n/* #include "depot/pallet.h" */\n',
    "src/local.h": "struct Local {};\n",
    "src/shelf.cpp": '#include "local.h"\n#include <local.h>\n#if defined(DEPOT_NEVER_DEFINED)\n#  include "depot/pallet.h"\n#endif\n',
}


class CppReferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))

    def _reference(self) -> dict:
        tree = self.root / "tree"
        _tree(tree, TREE)
        out = self.root / "ref.json"
        subprocess.run([sys.executable, "-I", str(CPP / "tools" / "cpp_reference.py"), str(tree), str(out), "include"],
                       check=True, capture_output=True)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_the_reference_reads_the_include_lines_as_a_compiler_looks_for_them(self) -> None:
        """/* REQ-BENCH-021: the reference of the C and C++ benchmark takes every `#include` line outside a comment, inside an `#if` block too; a quoted name is the file beside the including file, then the file in an include root; a name in angle brackets is looked for in the include roots only; a name found nowhere is counted as external and gives no edge. */"""
        reference = self._reference()
        self.assertEqual(
            [
                ["include/depot/pallet.h", "include/depot/crate.h"],
                ["src/crate.cpp", "include/depot/crate.h"],
                ["src/crate.cpp", "include/depot/pallet.h"],
                ["src/shelf.cpp", "include/depot/pallet.h"],
                ["src/shelf.cpp", "src/local.h"],
            ],
            reference["edges"],
        )
        self.assertEqual(2, reference["external_includes"])   # <vector> and <local.h>

    @unittest.skipUnless(shutil.which("clang"), "clang is not installed")
    def test_the_compiler_confirms_the_reference_and_names_what_it_does_not_reach(self) -> None:
        """/* REQ-BENCH-022: the compiler check counts, per translation unit, the files of the tree `clang -MM` reads and how many of them the reference reaches; with one edge taken out of the reference the file it led to is reported as not reached. */"""
        reference = self._reference()
        tree = self.root / "tree"

        def check(edges: list) -> dict:
            path = self.root / "edited.json"
            path.write_text(json.dumps({**reference, "edges": edges}), encoding="utf-8")
            out = self.root / "clang.json"
            subprocess.run([sys.executable, "-I", str(CPP / "tools" / "clang_check.py"), str(tree), str(path), str(out), "include"],
                           check=True, capture_output=True)
            return json.loads(out.read_text(encoding="utf-8"))

        whole = check(reference["edges"])
        self.assertEqual((2, 0, 0), (whole["translation_units"], whole["stopped"], whole["not_reached"]))
        self.assertEqual(whole["files_read_by_the_compiler"], whole["reached_by_the_reference"])
        cut = check([edge for edge in reference["edges"] if edge != ["include/depot/pallet.h", "include/depot/crate.h"]])
        self.assertEqual(0, cut["not_reached"])   # crate.cpp still reaches crate.h by its own include
        cut = check([edge for edge in reference["edges"] if edge[0] != "src/crate.cpp" or edge[1] != "include/depot/pallet.h"])
        self.assertEqual([["src/crate.cpp", "include/depot/pallet.h"]], cut["examples"])


class BenchmarkPagesTest(unittest.TestCase):
    def test_the_pages_are_what_the_result_files_say(self) -> None:
        """/* REQ-BENCH-024: benchmarks/cpp/README.md is what `make_report.py` writes from `benchmarks/cpp/results`, and benchmarks/collisions/README.md what `run.py --report` writes from its results: no number of either page is typed by hand. */"""
        with tempfile.TemporaryDirectory() as directory:
            for script, arguments, page in (
                (CPP / "make_report.py", [str(CPP / "results")], CPP / "README.md"),
                (COLLISIONS, ["--report", str(COLLISIONS.parent / "results")], COLLISIONS.parent / "README.md"),
            ):
                out = os.path.join(directory, "page.md")
                subprocess.run([sys.executable, "-I", str(script), *arguments, out], check=True, capture_output=True)
                with open(out, encoding="utf-8") as written:
                    self.assertEqual(page.read_text(encoding="utf-8"), written.read(), str(page))


@unittest.skipIf(os.name == "nt", "the benchmark is run on POSIX systems")
class CollisionBenchmarkTest(unittest.TestCase):
    def test_the_graph_of_this_checkout_equals_what_the_interpreter_loads(self) -> None:
        """/* REQ-BENCH-023: on the corpus of benchmarks/collisions (a package beside a module file of its name, a module named like its package, a folder without `__init__.py` beside a module file, a package under `src/`, a script beside a script, packages with the same last name), the import edges of the graph of this checkout are those the Python interpreter loads, statement by statement: none wrong, none missing; the reference holds at least 50 edges read from at least 60 loads. */"""
        spec = importlib.util.spec_from_file_location("collisions_run", COLLISIONS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = f"PYTHONPATH={SOURCE}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"
        with tempfile.TemporaryDirectory() as directory:
            previous = dict(os.environ)
            os.environ.update(PYTHONPATH=environment.split("=", 1)[1], WINGBOX_JOBS="1")
            try:
                result = module.run(os.path.join(directory, "corpus"), [sys.executable, "-m", "wingbox"])
            finally:
                os.environ.clear()
                os.environ.update(previous)
        self.assertEqual(([], []), (result["wrong"], result["missing"]))
        self.assertGreaterEqual(result["required_edges"], 50)
        self.assertGreaterEqual(result["imports_loaded_by_the_interpreter"], 60)


if __name__ == "__main__":
    unittest.main()
