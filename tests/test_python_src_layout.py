"""Python projects that keep their package under `src/`: the tests import it by its package name."""
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


def _dependents(root: Path, target: str) -> list[str]:
    lines = _wingbox(root, "rdeps", target, "1").splitlines()
    return sorted(line.split()[2] for line in lines if line.startswith("(rdep "))


PACKAGE = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def total(items):\n    return sum(items)\n",
    "shop/checkout.py": "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n",
}
TESTS = {
    "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    "tests/test_checkout.py": "from shop import checkout\n\n\ndef test_pay():\n    assert checkout.pay([1]) == 1\n",
    "tests/test_both.py": "import shop.pricing\n\n\ndef test_it():\n    assert shop.pricing.total([]) == 0\n",
}


class SourceFolderTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_a_test_reaches_the_package_under_src(self) -> None:
        """/* REQ-PY-012: in a project that keeps its package under `src/`, a file outside it that imports the package by name (`from shop.pricing import total`, `from shop import checkout`, `import shop.pricing`) depends on the module under `src/`: `rdeps` and `impact` of the module name the tests, and `usages` of a function lists its uses in them. */"""
        _write(self.root, {f"src/{name}": text for name, text in PACKAGE.items()})
        _write(self.root, TESTS)
        _wingbox(self.root, "build", ".")
        self.assertEqual(
            ["src/shop/checkout", "tests/test_both", "tests/test_pricing"], _dependents(self.root, "src/shop/pricing")
        )
        self.assertEqual(["tests/test_checkout"], _dependents(self.root, "src/shop/checkout"))
        impact = _wingbox(self.root, "impact", "src/shop/pricing.py")
        self.assertIn("tests/test_pricing.py", impact)
        usages = _wingbox(self.root, "usages", "total", "--all")
        self.assertIn("tests/test_pricing.py", usages)

    def test_each_part_of_a_monorepo_reaches_its_own_src(self) -> None:
        """/* REQ-PY-013: when several parts of a repository each keep a package of the same name under their own `src/`, a test reaches the `src/` nearest to it, never the one of another part. */"""
        for part in ("a", "b"):
            _write(self.root, {f"packages/{part}/src/{name}": text for name, text in PACKAGE.items()})
            _write(self.root, {f"packages/{part}/{name}": text for name, text in TESTS.items()})
        _wingbox(self.root, "build", ".")
        for part in ("a", "b"):
            self.assertEqual(
                [f"packages/{part}/src/shop/checkout", f"packages/{part}/tests/test_both", f"packages/{part}/tests/test_pricing"],
                _dependents(self.root, f"packages/{part}/src/shop/pricing"),
            )

    def test_a_package_beside_the_tests_still_wins(self) -> None:
        """/* REQ-PY-014: a package at the root of the repository is still what `from shop...` means, also when a folder `src/` holds a package of the same name; and a name that no folder of the repository holds is no dependency. */"""
        _write(self.root, PACKAGE)
        _write(self.root, {f"src/{name}": text for name, text in PACKAGE.items() if name != "shop/checkout.py"})
        _write(self.root, TESTS)
        _write(self.root, {"tests/test_other.py": "from requests.models import Response\nimport nothing_here\n"})
        _wingbox(self.root, "build", ".")
        self.assertEqual(["shop/checkout", "tests/test_both", "tests/test_pricing"], _dependents(self.root, "shop/pricing"))
        self.assertEqual([], _dependents(self.root, "src/shop/pricing"))
        self.assertEqual("", "".join(line for line in _wingbox(self.root, "deps", "tests/test_other", "1").splitlines() if line.startswith("(dep ")))


if __name__ == "__main__":
    unittest.main()
