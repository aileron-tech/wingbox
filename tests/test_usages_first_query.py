"""The first `usages` after a build: answered from the files that hold the name, not from a walk of every file."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts import build_code_graph
from tests.test_cpp_language import USAGE_FILES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

PYTHON = {
    "shop/__init__.py": "",
    "shop/models.py": "class Invoice:\n    def total(self):\n        return 0\n\n\ndef make():\n    return Invoice()\n",
    "shop/billing.py": "from shop.models import Invoice, make\n\n\ndef bill():\n    invoice = Invoice()\n    return invoice.total() + make().total()\n",
    "shop/report.py": "def report(invoice):\n    return invoice.total()\n",
    "shop/unrelated.py": "def other():\n    return 1\n",
    "tests/test_billing.py": "from shop.billing import bill\nfrom shop.models import make\n\n\ndef test_bill():\n    assert bill() == 0 and make()\n",
}
SYMBOLS = (
    "shop/models.py::Invoice", "shop/models.py::Invoice.total", "shop/models.py::make", "shop/billing.py::bill",
    "src/render/painter.h::blend", "src/render/painter.h::Vertex", "src/render/painter.h::Mode",
    "src/render/painter.h::Painter.create", "src/render/painter.h::Painter.paint", "src/render/painter.h::MAX_LAYERS",
)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str, fill: str = "0") -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                       WINGBOX_JOBS="1", WINGBOX_POSITIONS_FILL=fill)
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _usages(root: Path, symbol: str, fill: str = "0") -> dict:
    return json.loads(_run(root, "usages", symbol, "--json", fill=fill).stdout)["usages"]


class FirstUsagesTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _write(self.root, {**PYTHON, **USAGE_FILES})
        _run(self.root, "build", ".")
        self.uses = self.root / ".wingbox" / "index.uses.sexp"

    def test_the_first_answer_is_the_answer_of_the_filled_use_file(self) -> None:
        """/* REQ-CS-159: while the use file of a build has no positions, `usages` of a function, a class, a method (resolved and unresolved calls), a type, an enum member and a macro, in Python and in C++, answers with the rows it answers after the whole use file is filled; the question leaves the use file without positions (`:positions no`), so nothing of the repository was walked for it but the files that hold the name. */"""
        before = {symbol: _usages(self.root, symbol) for symbol in SYMBOLS}
        self.assertIn(":positions no", self.uses.read_text(encoding="utf-8").splitlines()[1])
        self.assertTrue(isinstance(build_code_graph.write_use_positions(str(self.root / ".wingbox" / "index.sexp"), str(self.root)), build_code_graph.Ok))
        self.assertIn(":positions yes", self.uses.read_text(encoding="utf-8").splitlines()[1])
        for symbol in SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertEqual(_usages(self.root, symbol), before[symbol])
        self.assertGreater(before["shop/models.py::Invoice.total"]["uses"], 0)
        self.assertEqual(2, before["shop/models.py::Invoice.total"]["unresolved"])   # report(invoice) and make().total()
        self.assertEqual(1, before["src/render/painter.h::Painter.paint"]["unresolved"])

    def test_only_the_files_that_hold_the_name_are_read(self) -> None:
        """/* REQ-CS-160: the lines read for one name come from the files whose text holds the name and from no other: for `make` of the Python package, the three files that write it; a name no file holds gives no line; a file that changed since the build makes the reader refuse, as the whole fill does. */"""
        graph = str(self.root / ".wingbox" / "index.sexp")
        found = build_code_graph.use_position_lines(graph, str(self.root), "make")
        self.assertIsInstance(found, build_code_graph.Ok)
        sources = {line.split()[1] for line in found.value if line.startswith("(use ")}
        self.assertEqual(3, len(sources), found.value)   # models.py, billing.py and the test; report.py and unrelated.py are not read
        self.assertEqual([], build_code_graph.use_position_lines(graph, str(self.root), "no_such_name_anywhere").value)
        (self.root / "shop" / "billing.py").write_text(PYTHON["shop/billing.py"] + "\n# make\n", encoding="utf-8")
        refused = build_code_graph.use_position_lines(graph, str(self.root), "make")
        self.assertIsInstance(refused, build_code_graph.Err)
        self.assertIn("changed since the build", refused.error)

    @unittest.skipIf(os.name == "nt", "the process is watched on POSIX systems here")
    def test_the_whole_use_file_is_filled_in_a_process_of_its_own(self) -> None:
        """/* REQ-CS-161: the first question that needs positions starts one process that fills the whole use file and does not wait for it: the answer comes first, the use file says `:positions yes` soon after and `.wingbox/positions.lock` is gone; a second start while the lock is fresh starts nothing, and `WINGBOX_POSITIONS_FILL=0` starts nothing at all. */"""
        graph = str(self.root / ".wingbox" / "index.sexp")
        lock = self.root / ".wingbox" / "positions.lock"
        os.environ["WINGBOX_POSITIONS_FILL"] = "0"
        try:
            self.assertFalse(build_code_graph.start_position_fill(graph, str(self.root)))
        finally:
            del os.environ["WINGBOX_POSITIONS_FILL"]
        lock.write_text("", encoding="utf-8")
        self.assertFalse(build_code_graph.start_position_fill(graph, str(self.root)))   # one is running
        lock.unlink()
        answer = _usages(self.root, "shop/models.py::make", fill="1")
        self.assertEqual(2, answer["files"])
        deadline = time.time() + 60
        while time.time() < deadline and (lock.exists() or ":positions yes" not in self.uses.read_text(encoding="utf-8").splitlines()[1]):
            time.sleep(0.2)
        self.assertIn(":positions yes", self.uses.read_text(encoding="utf-8").splitlines()[1])
        self.assertFalse(lock.exists())
        self.assertEqual(answer, _usages(self.root, "shop/models.py::make", fill="1"))


if __name__ == "__main__":
    unittest.main()
