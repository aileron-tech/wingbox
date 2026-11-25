"""docs/assurance: the requirements index generated from the test docstrings, and what the other pages point at."""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "requirements_index.py"
FOLDER = REPO / "docs" / "assurance"
INDEX = FOLDER / "requirements.md"

STAND_IN_A = '''\
import unittest


class ATest(unittest.TestCase):
    def test_two(self):
        """/* REQ-CS-10: the second one | with a pipe, `a|b` in code, a <tag>, a *star* and __init__.py,
        written over two lines. */"""

    def test_one(self):
        """/* REQ-CS-2: the first one. */"""

    def test_none(self):
        """No requirement here."""

    def helper(self):
        """Not a test."""


class WorkspaceTest(unittest.TestCase):
    def test_workspace(self):
        """/* REQ-TS-WS-001: a prefix of two words is an area of its own. */"""
'''
STAND_IN_B = '''\
def test_again():
    """/* REQ-CS-3: a third one, in a folder below. */"""
'''
# What the index accepted until an id had to belong to one test: the id of ATest.test_one a second time.
STAND_IN_DOUBLED = '''\
def test_again():
    """/* REQ-CS-2: the first one, checked a second time. */"""


def test_other():
    """/* REQ-TS-WS-001: another behaviour under an id that is taken. */"""
'''
EXPECTED_ROWS = [
    "| REQ-CS-2 | the first one. | `tests/test_a.py::ATest::test_one` |",
    "| REQ-CS-3 | a third one, in a folder below. | `tests/sub/test_b.py::test_again` |",
    "| REQ-CS-10 | the second one \\| with a pipe, `a\\|b` in code, a &lt;tag>, a \\*star\\* and \\_\\_init\\_\\_.py, "
    "written over two lines. | `tests/test_a.py::ATest::test_two` |",
    "| REQ-TS-WS-001 | a prefix of two words is an area of its own. | `tests/test_a.py::WorkspaceTest::test_workspace` |",
]


def load_tool():
    spec = importlib.util.spec_from_file_location("requirements_index", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["requirements_index"] = module
    spec.loader.exec_module(module)
    return module


def stand_in(folder: Path) -> Path:
    """A repository with the tool, two test files and no index yet; the path of the tool's copy."""
    (folder / "tools").mkdir()
    shutil.copy(TOOL, folder / "tools" / TOOL.name)
    (folder / "tests" / "sub").mkdir(parents=True)
    (folder / "tests" / "test_a.py").write_text(STAND_IN_A, encoding="utf-8")
    (folder / "tests" / "sub" / "test_b.py").write_text(STAND_IN_B, encoding="utf-8")
    return folder / "tools" / TOOL.name


def run(script: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *arguments], capture_output=True, text=True)


class RequirementsIndexTest(unittest.TestCase):
    def test_committed_index_is_what_the_generator_writes(self) -> None:
        """/* REQ-DOC-004: docs/assurance/requirements.md is, byte for byte, what `tools/requirements_index.py` writes from the test docstrings of this checkout, so the list of requirements cannot fall behind the tests; `tools/requirements_index.py --check` exits 0 on the repository. */"""
        tool = load_tool()
        self.assertTrue(tool.render(REPO).encode("utf-8") == INDEX.read_bytes(),
                        "docs/assurance/requirements.md is stale: run python3 tools/requirements_index.py")
        done = run(TOOL, "--check")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_one_table_per_area_and_one_row_per_test(self) -> None:
        """/* REQ-DOC-005: the index has one table per area (the id without its number: `CS`, `TS-WS`), areas in name order, and one row per test whose docstring states a requirement (`REQ-<AREA>-<n>: text` between the comment marks): the id, the text on one line with what Markdown would read as markup escaped outside code, and the test as `file::Class::method`; rows follow the number of the id; the page states how many tests and areas it lists and names every test that carries no id. */"""
        with tempfile.TemporaryDirectory() as tmp:
            script = stand_in(Path(tmp))
            done = run(script)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            text = (Path(tmp) / "docs" / "assurance" / "requirements.md").read_text(encoding="utf-8")
        self.assertEqual(EXPECTED_ROWS, [line for line in text.splitlines() if line.startswith("| REQ-")])
        self.assertEqual(["## CS", "## TS-WS"], [line for line in text.splitlines() if line.startswith("## ")])
        self.assertIn("4 tests carry a requirement id, each its own, in 2 areas.", text)
        self.assertIn("1 test carries no requirement id:", text)
        self.assertIn("- `tests/test_a.py::ATest::test_none`", text)
        self.assertNotIn("helper", text)
        self.assertTrue(text.isascii())
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_check_names_a_stale_index_and_a_run_repairs_it(self) -> None:
        """/* REQ-DOC-006: after a test docstring changes, `tools/requirements_index.py --check` exits 1 and names docs/assurance/requirements.md without writing it; a run without the option rewrites the file, and `--check` then exits 0. */"""
        with tempfile.TemporaryDirectory() as tmp:
            script = stand_in(Path(tmp))
            index = Path(tmp) / "docs" / "assurance" / "requirements.md"
            missing = run(script, "--check")
            self.assertEqual(1, missing.returncode, missing.stdout + missing.stderr)
            self.assertFalse(index.exists())
            self.assertEqual(0, run(script).returncode)
            self.assertEqual(0, run(script, "--check").returncode)
            before = index.read_bytes()
            changed = Path(tmp) / "tests" / "test_a.py"
            changed.write_text(STAND_IN_A.replace("the first one.", "the first one, reworded."), encoding="utf-8")
            stale = run(script, "--check")
            self.assertEqual(1, stale.returncode, stale.stdout + stale.stderr)
            self.assertIn("docs/assurance/requirements.md", stale.stdout)
            self.assertEqual(before, index.read_bytes())
            self.assertEqual(0, run(script).returncode)
            self.assertIn("the first one, reworded.", index.read_text(encoding="utf-8"))
            self.assertEqual(0, run(script, "--check").returncode)


    def test_an_id_that_two_tests_carry_is_refused(self) -> None:
        """/* REQ-DOC-008: an id belongs to one test: when two tests carry the same id (one behaviour checked twice, or two behaviours under one number), `tools/requirements_index.py` and `--check` exit 1, name on standard error each such id with the tests that carry it, and write nothing, so the index never lists one id for two behaviours; once every test has its own id the index is written; no id of this repository is carried by two tests. */"""
        with tempfile.TemporaryDirectory() as tmp:
            script = stand_in(Path(tmp))
            index = Path(tmp) / "docs" / "assurance" / "requirements.md"
            doubled = Path(tmp) / "tests" / "sub" / "test_b.py"
            doubled.write_text(STAND_IN_DOUBLED, encoding="utf-8")
            for arguments in ((), ("--check",)):
                done = run(script, *arguments)
                self.assertEqual(1, done.returncode, done.stdout + done.stderr)
                self.assertIn("REQ-CS-2 is carried by 2 tests: tests/sub/test_b.py::test_again, tests/test_a.py::ATest::test_one", done.stderr)
                self.assertIn("REQ-TS-WS-001 is carried by 2 tests: tests/sub/test_b.py::test_other, tests/test_a.py::WorkspaceTest::test_workspace", done.stderr)
                self.assertNotIn("REQ-CS-10", done.stderr)
                self.assertFalse(index.exists())
            doubled.write_text(STAND_IN_B, encoding="utf-8")
            self.assertEqual(0, run(script).returncode)
            self.assertTrue(index.is_file())
            before = index.read_bytes()
            doubled.write_text(STAND_IN_DOUBLED, encoding="utf-8")
            self.assertEqual(1, run(script).returncode)
            self.assertEqual(before, index.read_bytes())
        tool = load_tool()
        self.assertEqual({}, tool.doubled(tool.read_tests(REPO)))


class AssurancePagesTest(unittest.TestCase):
    def test_pages_point_at_files_and_tests_that_exist(self) -> None:
        """/* REQ-DOC-007: every page of docs/assurance cites its sources, and a citation that leads nowhere is a wrong citation: each relative Markdown link of the folder leads to a file or folder of the repository, each test named as `tests/<file>.py::<Class>` exists with that class, and the folder holds README.md, requirements.md, verification-plan.md, known-limits.md and tool-errors.md. */"""
        pages = sorted(FOLDER.glob("*.md"))
        names = {page.name for page in pages}
        self.assertLessEqual({"README.md", "requirements.md", "verification-plan.md", "known-limits.md", "tool-errors.md"}, names)
        offenders = []
        for page in pages:
            text = page.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                if not (page.parent / target.split("#", 1)[0]).exists():
                    offenders.append(f"{page.name}: link {target}")
            if page.name == "requirements.md":
                continue   # generated from the tests themselves (REQ-DOC-004)
            for file, name in re.findall(r"`(tests/[\w/]+\.py)::(\w+)", text):
                path = REPO / file
                if not path.is_file() or not re.search(rf"^class {name}\b", path.read_text(encoding="utf-8"), re.M):
                    offenders.append(f"{page.name}: test {file}::{name}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
