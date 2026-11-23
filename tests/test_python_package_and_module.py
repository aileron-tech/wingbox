"""A Python package folder beside a module file of the same name (`depot/` and `depot.py`): the name means the package."""
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


def _wingbox(root: Path, *args: str) -> str:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    done = subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180)
    return done.stdout.decode("utf-8").replace("\\", "/")


def _neighbours(root: Path, command: str, fact: str, target: str) -> list[str]:
    lines = _wingbox(root, command, target, "1").splitlines()
    return sorted(line.split()[2] for line in lines if line.startswith(f"({fact} "))


def _dependencies(root: Path, target: str) -> list[str]:
    return _neighbours(root, "deps", "dep", target)


def _dependents(root: Path, target: str) -> list[str]:
    return _neighbours(root, "rdeps", "rdep", target)


def _impact_rows(root: Path, target: str) -> dict[str, int]:
    answer = json.loads(_wingbox(root, "impact", target, "2", "--json"))
    return {row["file"]: row["depth"] for row in answer["impact"][0]["rows"]}


# The command line tool `depot.py` beside the package `depot/` it drives.
TOOL = "from depot import ledger\nfrom depot import crate\n\n\ndef main():\n    return ledger.load(), crate.load()\n"
PACKAGE = {
    "depot/crate.py": "def load():\n    return 'crate'\n",
    "depot/pallet.py": "def load():\n    return 'pallet'\n",
    "depot/ledger.py": "def load():\n    return 'ledger'\n",
    "depot/shelf.py": "from . import crate\n\n\ndef build():\n    from . import pallet\n    return crate.load(), pallet.load()\n",
    "depot/label.py": "from .crate import load\n\n\nclass Label:\n    def text(self):\n        from . import pallet\n        return load(), pallet.load()\n",
}


class PackageBesideModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_a_relative_import_stays_inside_the_package(self) -> None:
        """/* REQ-PY-021: in a package folder `depot/` beside a module file `depot.py`, `from . import crate` at the top of a file, `from . import pallet` inside a function or a method and `from .crate import load` depend on the modules of the package and on its `__init__.py`, never on `depot.py`; the impact within two steps of a module that `depot.py` imports names `depot.py` and no file of the package that does not import that module. */"""
        _write(self.root, {"depot.py": TOOL, "depot/__init__.py": "__all__ = ['crate', 'ledger']\n", **PACKAGE})
        _wingbox(self.root, "build", ".")
        self.assertEqual(["depot/__init__", "depot/crate", "depot/pallet"], _dependencies(self.root, "depot/shelf"))
        self.assertEqual(["depot/__init__", "depot/crate", "depot/pallet"], _dependencies(self.root, "depot/label"))
        self.assertEqual({"depot.py": 1}, _impact_rows(self.root, "depot/ledger.py"))

    def test_the_name_means_the_package_folder(self) -> None:
        """/* REQ-PY-022: `import depot` and `from depot import ledger` mean the package folder `depot/` when a module file `depot.py` lies beside it, as for Python's own finder: the importing file, `depot.py` included, depends on `depot/__init__.py` and nothing depends on `depot.py`. */"""
        _write(self.root, {"depot.py": TOOL, "depot/__init__.py": "__all__ = ['crate', 'ledger']\n", **PACKAGE})
        _write(self.root, {"tests/test_depot.py": "import depot\n\n\ndef test_it():\n    assert depot.__all__\n"})
        _wingbox(self.root, "build", ".")
        self.assertEqual(["depot/__init__"], _dependencies(self.root, "tests/test_depot"))
        self.assertEqual(["depot/__init__", "depot/crate", "depot/ledger"], _dependencies(self.root, "depot"))
        self.assertEqual([], _dependents(self.root, "depot"))

    def test_a_folder_without_init_is_not_the_module_beside_it(self) -> None:
        """/* REQ-PY-023: `from . import crate` in a folder `depot/` that has no `__init__.py` depends on `depot/crate` only, also when a module file `depot.py` lies beside the folder; `import depot` then still means `depot.py`. */"""
        _write(self.root, {"depot.py": "def main():\n    return 0\n", **PACKAGE})
        _write(self.root, {"tests/test_depot.py": "import depot\n\n\ndef test_it():\n    assert depot.main() == 0\n"})
        _wingbox(self.root, "build", ".")
        self.assertEqual(["depot/crate", "depot/pallet"], _dependencies(self.root, "depot/shelf"))
        self.assertEqual(["tests/test_depot"], _dependents(self.root, "depot"))


if __name__ == "__main__":
    unittest.main()
