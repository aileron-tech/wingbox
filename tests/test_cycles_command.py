"""`wingbox cycles`: what the dependency graph says about a repository that has no layer style."""
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


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _module(imports: list[str]) -> str:
    return "".join(f"import {name}\n" for name in imports) + "\nVALUE = 1\n"


# core <- store <- shop; shop.cart and shop.order import each other, and so do store.catalog and shop.cart
# (a cycle of three files over two folders). hub.py is imported by twelve files and imports twelve.
FILES = {
    "core/__init__.py": "", "store/__init__.py": "", "shop/__init__.py": "", "leaf/__init__.py": "", "user/__init__.py": "",
    "core/base.py": "VALUE = 1\n",
    "store/catalog.py": "import core.base\nimport shop.cart\n",
    "shop/cart.py": "import store.catalog\nimport shop.order\n",
    "shop/order.py": "import shop.cart\nimport core.base\n",
    "hub.py": _module([f"leaf.part{number}" for number in range(12)]),
    **{f"leaf/part{number}.py": "VALUE = 1\n" for number in range(12)},
    **{f"user/client{number}.py": "import hub\n" for number in range(12)},
}


class CyclesCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        _write(cls.root, FILES)
        _run(cls.root, "build", ".")

    def test_cycles_names_the_files_that_depend_on_each_other(self) -> None:
        """/* REQ-CS-162: `wingbox cycles` needs no layer style: it prints one `(cycle N :files K <file> ...)` fact per group of files that depend on each other through imports (two files that import each other, or a longer ring), the largest group first, its files in path order; a file outside every group is in none; the last fact `(cycles-summary :cycles N :files-in-cycles K ...)` counts them, and a repository without such a group prints `:cycles 0`. */"""
        answer = _run(self.root, "cycles")
        self.assertEqual(0, answer.returncode, answer.stderr)
        self.assertIn("(cycle 1 :files 3 shop/cart.py shop/order.py store/catalog.py)", answer.stdout)
        self.assertEqual(1, answer.stdout.count("(cycle "))
        self.assertRegex(answer.stdout, r"\(cycles-summary :cycles 1 :files-in-cycles 3 ")
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(os.path.realpath(directory))
            _write(plain, {"a.py": "import b\n", "b.py": "VALUE = 1\n"})
            _run(plain, "build", ".")
            self.assertRegex(_run(plain, "cycles").stdout, r"\(cycles-summary :cycles 0 :files-in-cycles 0 ")

    def test_cycles_names_hubs_and_folders(self) -> None:
        """/* REQ-CS-163: `wingbox cycles` also prints `(hub <file> :dependents N :dependencies M)` for a file that at least ten files depend on and that depends on at least ten; `(folder-cycle <a> <b> :forward N :back M)` for two top-level folders with dependencies in both directions, with the count of file pairs each way; and `(folder-order (<folder> ...) ...)`, the top-level folders from those that depend on others to those the others depend on, folders that depend on each other in one group. `wingbox cycles <folder>` answers for the files below that folder, with its subfolders as the folders. `--json` gives the same facts. */"""
        answer = _run(self.root, "cycles").stdout
        self.assertIn("(hub hub.py :dependents 12 :dependencies 12)", answer)
        self.assertEqual(1, answer.count("(hub "))
        self.assertIn("(folder-cycle shop store :forward 1 :back 1)", answer)
        self.assertIn("(folder-order (shop store) (core) (user) (.) (leaf))", answer)
        self.assertRegex(answer, r"\(cycles-summary :cycles 1 :files-in-cycles 3 :hubs 1 :folder-cycles 1 :files \d+\)")
        scoped = _run(self.root, "cycles", "shop").stdout
        self.assertIn("(cycle 1 :files 2 shop/cart.py shop/order.py)", scoped)
        self.assertNotIn("store/catalog.py", scoped)
        facts = json.loads(_run(self.root, "cycles", "--json").stdout)["facts"]
        self.assertEqual(["cycle", "hub", "folder-cycle", "folder-order", "cycles-summary"], [fact["fact"] for fact in facts])


if __name__ == "__main__":
    unittest.main()
