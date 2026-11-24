"""`impact` of an implementation file, of tests only, and of the files a diff changes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

PROJECT = {
    "native/CMakeLists.txt": "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src ${CMAKE_CURRENT_SOURCE_DIR}/include)\n",
    # A header beside its source file.
    "native/src/scene/stage.h": "struct Stage { int run(); };\nint build_stage(int size);\n",
    "native/src/scene/stage.cpp": '#include "stage.h"\nint Stage::run() { return 0; }\nint build_stage(int size) { return size; }\n',
    # A header in an include folder, its source file elsewhere.
    "native/include/depot/crate.h": "int crate_size();\n",
    "native/src/crate.cpp": '#include "depot/crate.h"\nint crate_size() { return 4; }\n',
    # Users.
    "native/src/play/round.h": '#include "scene/stage.h"\nint play_round();\n',
    "native/src/play/round.cpp": '#include "play/round.h"\n#include "depot/crate.h"\nint play_round() { return build_stage(crate_size()); }\n',
    "native/tests/test_stage.cpp": '#include "scene/stage.h"\nint main() { return build_stage(1); }\n',
    "native/tests/test_round.cpp": '#include "play/round.h"\nint main() { return play_round(); }\n',
    # A source file with no header of its name, included by nothing.
    "native/src/lone.cpp": "int lone() { return 1; }\n",
    "tools/report.py": "def report():\n    return 0\n",
    "tests/test_report.py": "from tools import report\n\n\ndef test_report():\n    assert report.report() == 0\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    # The include folders as a compiler is given them: every include of the project is then a stated edge.
    units = [name for name in PROJECT if name.endswith(".cpp")]
    (root / "compile_commands.json").write_text(json.dumps([
        {"directory": str(root), "file": name, "arguments": ["c++", "-Inative/src", "-Inative/include", "-c", name]}
        for name in units
    ]), encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _answer(root: Path, *args: str) -> dict:
    return json.loads(_run(root, "impact", *args, "--json").stdout)["impact"][0]


def _rows(answer: dict) -> dict[str, tuple[int, str]]:
    return {row["file"]: (row["depth"], row.get("basis", "source")) for row in answer["rows"]}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], check=True, capture_output=True)


class ImplementationFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        _git(cls.root, "init", "-q")
        _write(cls.root, PROJECT)
        _git(cls.root, "add", ".")
        _git(cls.root, "commit", "-q", "-m", "first")
        _run(cls.root, "build", ".")

    def test_impact_of_a_source_file_lists_the_users_of_its_header(self) -> None:
        """/* REQ-CPP-014: `impact` of a C or C++ source file also lists the files that include its header: the header of the same stem in its folder, or one of the same stem that it includes from an include folder. Those rows are at depth 1 with `basis` `paired-header` (`:basis paired-header` in the compact and the flat form), the rows below them are ordinary ones, the header is named in a `(impact-paired-header <file> <header>)` note and stands in `read_first` after the file; the source file itself is not a row. */"""
        answer = _answer(self.root, "native/src/scene/stage.cpp", "2")
        self.assertEqual(
            {
                "native/src/play/round.h": (1, "paired-header"),
                "native/tests/test_stage.cpp": (1, "paired-header"),
                "native/src/play/round.cpp": (2, "source"),
                "native/tests/test_round.cpp": (2, "source"),
            },
            _rows(answer),
        )
        self.assertEqual(["native/src/scene/stage.cpp", "native/src/scene/stage.h"], answer["read_first"][:2])
        self.assertEqual(("impact-paired-header", ["native/src/scene/stage.cpp", "native/src/scene/stage.h"]), (answer["note"]["fact"], answer["note"]["args"]))
        mirrored = _answer(self.root, "native/src/crate.cpp", "1")
        self.assertEqual({"native/src/play/round.cpp": (1, "paired-header")}, _rows(mirrored))
        compact = _run(self.root, "impact", "native/src/scene/stage.cpp").stdout
        self.assertIn("(impact-paired-header native/src/scene/stage.cpp native/src/scene/stage.h)", compact)
        self.assertIn("native/src/play/round.h :basis paired-header", compact)
        flat = _run(self.root, "impact", "native/src/scene/stage.cpp", "--sexp").stdout
        self.assertIn(":depth 1 :basis paired-header", flat)
        human = _run(self.root, "impact", "native/src/scene/stage.cpp", "--human").stdout
        self.assertIn("its header native/src/scene/stage.h", human)
        # The header itself answers as before: no note, no basis.
        header = _answer(self.root, "native/src/scene/stage.h", "1")
        self.assertNotIn("note", header)
        self.assertEqual({"source"}, {basis for _depth, basis in _rows(header).values()})

    def test_a_row_of_an_include_names_what_the_file_uses(self) -> None:
        """/* REQ-CPP-016: a row of `impact` whose file includes a C or C++ header carries the names of that header the file uses, as rows of other languages carry the names of an import: `(:use <name> ...)` in the compact form (at most three names, `:of N` when there are more), the same in `reasons` of JSON and in the text. A file that includes the header and uses no name the graph resolved has no name list. For a source file answered through its header the names are those of the header. */"""
        def names(answer: dict) -> dict[str, list]:
            return {row["file"]: [(reason["way"], reason["names"], reason["of"]) for reason in row["reasons"]] for row in answer["rows"]}

        header = names(_answer(self.root, "native/src/scene/stage.h", "2"))
        self.assertEqual([("use", ["build_stage"], 1)], header["native/tests/test_stage.cpp"])
        self.assertEqual([], header["native/src/play/round.h"])          # includes it, uses nothing of it
        self.assertEqual([("use", ["play_round"], 1)], header["native/tests/test_round.cpp"])   # depth 2: what it uses of round.h
        compact = _run(self.root, "impact", "native/src/scene/stage.h", "2").stdout
        self.assertIn("native/tests/test_stage.cpp (:use build_stage)", compact)
        human = _run(self.root, "impact", "native/src/scene/stage.h", "--human").stdout
        self.assertIn("build_stage", human)
        source = names(_answer(self.root, "native/src/scene/stage.cpp", "1"))
        self.assertEqual([("use", ["build_stage"], 1)], source["native/tests/test_stage.cpp"])
        crate = names(_answer(self.root, "native/include/depot/crate.h", "1"))
        self.assertEqual([("use", ["crate_size"], 1)], crate["native/src/play/round.cpp"])

    def test_a_source_file_without_a_header_says_what_the_list_cannot_show(self) -> None:
        """/* REQ-CPP-015: `impact` of a C or C++ source file that nothing includes and that has no header of its name has no rows and a `(impact-no-includers <file>)` note; the text for people says that the include graph does not show who calls what the file defines and names `wingbox usages`, where it used to say "No other module depends on it". */"""
        answer = _answer(self.root, "native/src/lone.cpp")
        self.assertEqual((0, 0, []), (answer["direct"], answer["total"], answer["rows"]))
        self.assertEqual("impact-no-includers", answer["note"]["fact"])
        human = _run(self.root, "impact", "native/src/lone.cpp", "--human").stdout
        self.assertIn("does not show who calls what it defines", human)
        self.assertIn("wingbox usages", human)
        self.assertNotIn("No other module depends on it", human)
        self.assertIn("(impact-no-includers native/src/lone.cpp)", _run(self.root, "impact", "native/src/lone.cpp").stdout)

    def test_only_tests_keeps_the_test_files(self) -> None:
        """/* REQ-CS-157: `impact <file> ... --only tests` lists only the dependents that are test files (the layer `test`), at the depths they have in the whole list; `direct` and `total` count them, the form carries `:only tests` (`"only": "tests"` in JSON, "tests only" in the text), and `read_first` holds the file and its direct test files. Any other value of `--only` is an error. */"""
        answer = _answer(self.root, "native/src/scene/stage.h", "2", "--only", "tests")
        self.assertEqual({"native/tests/test_stage.cpp": (1, "source"), "native/tests/test_round.cpp": (2, "source")}, _rows(answer))
        self.assertEqual((1, 2, "tests"), (answer["direct"], answer["total"], answer["only"]))
        self.assertEqual(["native/src/scene/stage.h", "native/tests/test_stage.cpp"], answer["read_first"])
        whole = _answer(self.root, "native/src/scene/stage.h", "2")
        self.assertEqual(5, whole["total"])   # the source file of the header and round.h, round.cpp besides the two tests
        self.assertNotIn("only", whole)
        compact = _run(self.root, "impact", "native/src/scene/stage.cpp", "2", "--only", "tests").stdout
        self.assertIn("(impact native/src/scene/stage.cpp :direct 1 :total 2 :only tests", compact)
        self.assertIn("tests only", _run(self.root, "impact", "tools/report.py", "--only", "tests", "--human").stdout)
        self.assertEqual({"tests/test_report.py": (1, "source")}, _rows(_answer(self.root, "tools/report.py", "--only", "tests")))
        wrong = _run(self.root, "impact", "tools/report.py", "--only", "docs")
        self.assertEqual(2, wrong.returncode)
        self.assertIn("--only takes tests", wrong.stdout)

    def test_diff_answers_for_the_changed_files_and_functions(self) -> None:
        """/* REQ-CS-158: `impact --diff [ref]` (default `HEAD`: the changes of the working tree) answers for every file the diff changes, in path order, as `impact <those files>` does, and adds one `(changed <file>::<name> :line N :used-by (<file> ...))` fact per changed function with the other files that use it, those that use it through the declaration in its header included (`changed` in JSON); with `--only tests` both lists keep the test files. A diff that changes no file answers `(impact-diff-empty <ref>)`. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            _git(root, "init", "-q")
            _write(root, PROJECT)
            _git(root, "add", ".")
            _git(root, "commit", "-q", "-m", "first")
            _run(root, "build", ".")
            self.assertIn("(impact-diff-empty HEAD)", _run(root, "impact", "--diff").stdout)
            _write(root, {
                "native/src/scene/stage.cpp": '#include "stage.h"\nint Stage::run() { return 0; }\nint build_stage(int size) { return size + 1; }\n',
                "tools/report.py": "def report():\n    return 1\n",
            })
            _run(root, "build", ".")
            document = json.loads(_run(root, "impact", "--diff", "--json").stdout)
            self.assertEqual(["native/src/scene/stage.cpp", "tools/report.py"], [item["file"] for item in document["impact"]])
            changed = {item["symbol"]: (item["line"], item["used_by"]) for item in document["changed"]}
            self.assertEqual(
                {
                    "native/src/scene/stage.cpp::build_stage": (3, ["native/src/play/round.cpp", "native/tests/test_stage.cpp"]),
                    "tools/report.py::report": (1, ["tests/test_report.py"]),
                },
                changed,
            )
            compact = _run(root, "impact", "--diff", "HEAD", "--only", "tests").stdout
            self.assertIn("(impact native/src/scene/stage.cpp :direct 1 :total 2 :only tests", compact)
            self.assertIn("(changed tools/report.py::report :line 1 :used-by (tests/test_report.py))", compact)
            self.assertIn("(changed native/src/scene/stage.cpp::build_stage :line 3 :used-by (native/tests/test_stage.cpp))", compact)


if __name__ == "__main__":
    unittest.main()
