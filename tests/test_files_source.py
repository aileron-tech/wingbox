"""`wingbox files` and `wingbox source`: the files of the graph, and the lines of one of them or of one definition."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

PRICING = "RATE = 2\n\n\ndef total(items):\n    return sum(items) * RATE\n\n\ndef discount(amount):\n    \"\"\"Ten percent off.\"\"\"\n    return amount * 0.9\n"
FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": PRICING,
    "shop/checkout.py": "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n",
    "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total():\n    assert total([1]) == 2\n",
    "notes.txt": "not source\n",
}


def _wingbox(root: Path, *args: str) -> tuple[int, str]:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    done = subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180)
    return done.returncode, done.stdout.decode("utf-8")


class FilesAndSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(os.path.realpath(cls._tmp.name)) / "repo"
        cls.root.mkdir()
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        for name, text in FILES.items():
            (cls.root / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.root / name).write_text(text, encoding="utf-8")
        (cls.root.parent / "secret.py").write_text("TOKEN = 'outside'\n", encoding="utf-8")
        _wingbox(cls.root, "build", ".")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def lines(self, *args: str) -> list[str]:
        return [line for line in _wingbox(self.root, *args)[1].splitlines() if line.strip()]

    def test_files_lists_the_source_files_of_the_graph(self) -> None:
        """/* REQ-CS-124: `wingbox files` prints one `(file <path> :module <id> :lang <l> :layer <layer>)` line per source file of the graph, sorted by path, and a `(files-summary :count N :of N)` line; a file that is not source is not listed. */"""
        lines = self.lines("files")
        files = [line.split()[1] for line in lines if line.startswith("(file ")]
        self.assertEqual(["shop/__init__.py", "shop/checkout.py", "shop/pricing.py", "tests/test_pricing.py"], files)
        self.assertIn("(file shop/pricing.py :module shop/pricing :lang py :layer ", "\n".join(lines))
        self.assertEqual("(files-summary :count 4 :of 4)", lines[-1])

    def test_files_takes_a_pattern(self) -> None:
        """/* REQ-CS-125: `wingbox files <pattern>` keeps the files whose path matches the pattern (literal text with optional `.*`), and the summary says how many of all; a pattern nothing matches prints `(files-empty <pattern>)`. */"""
        lines = self.lines("files", "tests/.*pricing")
        self.assertEqual(["tests/test_pricing.py"], [line.split()[1] for line in lines if line.startswith("(file ")])
        self.assertEqual('(files-summary :count 1 :of 4 :pattern tests/.*pricing)', lines[-1])
        self.assertEqual(["(files-empty nothing_here)"], self.lines("files", "nothing_here"))

    def test_source_prints_the_lines_of_a_file(self) -> None:
        """/* REQ-CS-126: `wingbox source <file>` prints `(source <path> :first 1 :last N :of N)` and one `(line <n> "<text>")` per line of the file, the text quoted; `first` and `last` narrow it to a range. */"""
        lines = self.lines("source", "shop/pricing.py")
        self.assertEqual("(source shop/pricing.py :first 1 :last 10 :of 10)", lines[0])
        self.assertEqual('(line 1 "RATE = 2")', lines[1])
        self.assertEqual('(line 2 "")', lines[2])
        self.assertEqual('(line 9 "    \\"\\"\\"Ten percent off.\\"\\"\\"")', lines[9])
        self.assertEqual(11, len(lines))
        part = self.lines("source", "shop/pricing.py", "4", "5")
        self.assertEqual(["(source shop/pricing.py :first 4 :last 5 :of 10)", '(line 4 "def total(items):")', '(line 5 "    return sum(items) * RATE")'], part)

    def test_source_prints_one_definition(self) -> None:
        """/* REQ-CS-127: `wingbox source <Name>` or `<file>::<Name>` prints the lines of that definition: from its line to the line before the next definition of the file, or to the end of the file, and names it with `:entity`. */"""
        lines = self.lines("source", "total")
        self.assertEqual("(source shop/pricing.py :first 4 :last 7 :of 10 :entity shop/pricing::total)", lines[0])
        self.assertEqual('(line 4 "def total(items):")', lines[1])
        last = self.lines("source", "shop/pricing.py::discount")
        self.assertEqual("(source shop/pricing.py :first 8 :last 10 :of 10 :entity shop/pricing::discount)", last[0])
        self.assertEqual('(line 10 "    return amount * 0.9")', last[-1])

    def test_source_stops_at_the_line_limit_and_says_where_to_go_on(self) -> None:
        """/* REQ-CS-128: one `wingbox source` call prints at most 400 lines; a longer range ends at the limit and the first line says how many lines are left and the line to ask for next (`:more K :next N`). */"""
        long_file = self.root / "shop" / "long.py"
        long_file.write_text("".join(f"V{n} = {n}\n" for n in range(1, 451)), encoding="utf-8")
        _wingbox(self.root, "build", ".")
        try:
            lines = self.lines("source", "shop/long.py")
            self.assertEqual("(source shop/long.py :first 1 :last 400 :of 450 :more 50 :next 401)", lines[0])
            self.assertEqual(401, len(lines))
            rest = self.lines("source", "shop/long.py", "401")
            self.assertEqual("(source shop/long.py :first 401 :last 450 :of 450)", rest[0])
        finally:
            long_file.unlink()
            _wingbox(self.root, "build", ".")

    def test_source_reads_only_files_of_the_graph(self) -> None:
        """/* REQ-CS-129: `wingbox source` answers only for a source file of the graph or a definition in one: a file of the repository that is not source, a path outside the repository (relative with `..` or absolute) and a name nothing defines are answered with `(error no-match ...)`, exit status 0 as for other no-match answers, and no line of any file is printed. A name that several files define is answered with `(error ambiguous ...)` and the candidates. */"""
        for target in ("notes.txt", "../secret.py", str(self.root.parent / "secret.py"), "/etc/hosts", "no_such_name"):
            status, out = _wingbox(self.root, "source", target)
            self.assertTrue(out.startswith("(error no-match "), (target, out))
            self.assertNotIn("(line ", out, target)
            self.assertNotIn("outside", out, target)
        (self.root / "shop" / "other.py").write_text("def total(x):\n    return x\n", encoding="utf-8")
        _wingbox(self.root, "build", ".")
        try:
            status, out = _wingbox(self.root, "source", "total")
            self.assertTrue(out.startswith("(error ambiguous total :candidates "), out)
            self.assertIn("shop/pricing.py::total", out)
            self.assertIn("shop/other.py::total", out)
            self.assertNotIn("(line ", out)
        finally:
            (self.root / "shop" / "other.py").unlink()
            _wingbox(self.root, "build", ".")

    def test_source_checks_its_range(self) -> None:
        """/* REQ-CS-130: `first` and `last` are line numbers from 1; anything else, or a first line past the end of the file, is an `(error invalid-argument ...)` answer. */"""
        for extra in (("0",), ("x",), ("5", "2"), ("99",)):
            status, out = _wingbox(self.root, "source", "shop/pricing.py", *extra)
            self.assertTrue(out.startswith("(error invalid-argument "), (extra, out))


if __name__ == "__main__":
    unittest.main()
