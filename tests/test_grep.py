"""`wingbox grep`: the lines of the source files of the graph that hold a text; and what a malformed query answers."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": "RATE = 2\n\n\ndef total(items):\n    # Price of the basket\n    return sum(items) * RATE\n",
    "shop/checkout.py": "from shop.pricing import total\n\nMESSAGE = \"Basket is empty\"\n\n\ndef pay(items):\n    return total(items)\n",
    "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total():\n    assert total([1]) == 2\n",
    "notes.txt": "basket notes, not source\n",
}


def _wingbox(root: Path, *args: str) -> tuple[int, str]:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    done = subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180)
    return done.returncode, done.stdout.decode("utf-8")


def _repository(parent: Path, files: dict[str, str]) -> Path:
    root = parent / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    _wingbox(root, "build", ".")
    return root


def _load_search():
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))
    from wingbox import cli

    return cli.load_script("code_graph_search")


def _captured(action) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        action()
    return out.getvalue()


def _graph(search, root: Path):
    graph = search.CodeGraph()
    graph.load(str(root / ".wingbox" / "index.sexp"), str(root))
    return graph


class GrepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _repository(Path(os.path.realpath(cls._tmp.name)), FILES)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_grep_lists_the_lines_that_hold_the_text(self) -> None:
        """/* REQ-CS-135: `wingbox grep <text>` reads every source file of the graph and prints one
        `(match <file> <line> "<text of the line>")` per line that holds the text, sorted by file and line,
        letter case ignored, then `(matches-summary :matches N :files N :searched N :pattern <text>)`. A
        file that is not a source file of the graph is not read. */"""
        status, out = _wingbox(self.root, "grep", "basket")
        self.assertEqual(0, status, out)
        self.assertEqual(
            [
                '(match shop/checkout.py 3 "MESSAGE = \\"Basket is empty\\"")',
                '(match shop/pricing.py 5 "    # Price of the basket")',
                '(matches-summary :matches 2 :files 2 :searched 4 :pattern basket)',
            ],
            out.splitlines(),
        )

    def test_a_path_pattern_narrows_the_files(self) -> None:
        """/* REQ-CS-136: a second argument is a pattern of file paths, as `wingbox files` takes it: only the
        files whose path matches are read, and the summary names it (`:path`) and counts them (`:searched`). */"""
        status, out = _wingbox(self.root, "grep", "total", "tests/.*")
        self.assertEqual(0, status, out)
        lines = out.splitlines()
        self.assertEqual(['(match tests/test_pricing.py 1 "from shop.pricing import total")'], lines[:1])
        self.assertTrue(all(line.startswith("(match tests/test_pricing.py ") for line in lines[:-1]), out)
        self.assertEqual("(matches-summary :matches 3 :files 1 :searched 1 :pattern total :path tests/.*)", lines[-1])

    def test_a_wildcard_matches_inside_one_line(self) -> None:
        """/* REQ-CS-137: the text may hold `.*` wildcards, matched inside one line and never across lines. */"""
        _status, out = _wingbox(self.root, "grep", "sum.*RATE")
        self.assertEqual(
            ['(match shop/pricing.py 6 "    return sum(items) * RATE")', "(matches-summary :matches 1 :files 1 :searched 4 :pattern sum.*RATE)"],
            out.splitlines(),
        )
        _status, out = _wingbox(self.root, "grep", "RATE.*def")
        self.assertEqual(["(matches-empty RATE.*def :searched 4)"], out.splitlines())

    def test_no_match_is_an_empty_answer_not_an_error(self) -> None:
        """/* REQ-CS-138: a text no line holds answers `(matches-empty <text> :searched N)` with status 0. */"""
        status, out = _wingbox(self.root, "grep", "nowhere_at_all")
        self.assertEqual(0, status, out)
        self.assertEqual(["(matches-empty nowhere_at_all :searched 4)"], out.splitlines())

    def test_a_pattern_without_text_is_refused(self) -> None:
        """/* REQ-CS-139: a pattern that holds no text to search for (empty, or wildcards only), a pattern that
        ends in a lone backslash, an unsupported operator in the pattern of file paths and a missing pattern are
        errors: no line of any file is printed. */"""
        search = _load_search()
        graph = _graph(search, self.root)
        for pattern in ("", ".*", ".*.*", "^$"):
            out = _captured(lambda: search.cmd_grep(graph, pattern))
            self.assertTrue(out.startswith("(error invalid-pattern "), (pattern, out))
            self.assertNotIn("(match ", out)
        for arguments in (("grep",), ("grep", "total\\"), ("grep", ".*"), ("grep", "total", "tests/[a]"), ("grep", "a", "b", "c")):
            _status, out = _wingbox(self.root, *arguments)
            self.assertTrue(out.startswith("(error "), (arguments, out))
            self.assertNotIn("(match ", out)

    def test_every_character_of_the_text_stands_for_itself(self) -> None:
        """/* REQ-CS-144: in the text of `grep` every character stands for itself, brackets, parentheses, `|`,
        `+`, `?`, a lone `*` and a `$` or `^` inside the text included, so that code can be searched as it is
        written. Special are only `.*` (any characters), `^` as the first and `$` as the last character (the
        start and the end of the line), and a backslash, which makes the next character literal. The other
        commands keep refusing these operators. */"""
        for text, expected in (
            ("total(items)", ['(match shop/checkout.py 7 "    return total(items)")', '(match shop/pricing.py 4 "def total(items):")']),
            ("total([1]) ==", ['(match tests/test_pricing.py 5 "    assert total([1]) == 2")']),
            ("(items) * RATE", ['(match shop/pricing.py 6 "    return sum(items) * RATE")']),
            ("^def .*:$", ['(match shop/checkout.py 6 "def pay(items):")', '(match shop/pricing.py 4 "def total(items):")', '(match tests/test_pricing.py 4 "def test_total():")']),
            ("^RATE", ['(match shop/pricing.py 1 "RATE = 2")']),
            ("= 2$", ['(match shop/pricing.py 1 "RATE = 2")', '(match tests/test_pricing.py 5 "    assert total([1]) == 2")']),
            ("\\(items\\)", ['(match shop/checkout.py 6 "def pay(items):")', '(match shop/checkout.py 7 "    return total(items)")',
                              '(match shop/pricing.py 4 "def total(items):")', '(match shop/pricing.py 6 "    return sum(items) * RATE")']),
        ):
            status, out = _wingbox(self.root, "grep", text)
            self.assertEqual(0, status, (text, out))
            self.assertEqual(expected, out.splitlines()[:-1], text)
        for text in ("a|b", "x+?", "{}", "a$b^c"):
            _status, out = _wingbox(self.root, "grep", text)
            self.assertTrue(out.startswith("(matches-empty "), (text, out))
        _status, out = _wingbox(self.root, "files", "shop/(a|b)")
        self.assertTrue(out.startswith("(error "), out)

    def test_a_long_line_is_cut_and_the_matches_stop_at_the_limit(self) -> None:
        """/* REQ-CS-141: a matching line longer than 300 characters is printed cut at that length with
        `:cut <its length>`; one answer lists at most 2,000 matches, and the summary then says how many there
        are and how many are shown (`:matches 2100 :shown 2000`). */"""
        search = _load_search()
        self.assertEqual((300, 2000), (search.GREP_LINE_CHARS, search.GREP_MATCH_LIMIT))
        with tempfile.TemporaryDirectory() as parent:
            root = _repository(Path(os.path.realpath(parent)), {
                "wide.py": "A = 'needle" + "x" * 500 + "'\n",
                "many.py": "".join("V%d = 'straw'\n" % number for number in range(2100)),
            })
            _status, out = _wingbox(root, "grep", "needle")
            self.assertEqual('(match wide.py 1 "' + "A = 'needle" + "x" * 289 + '" :cut 512)', out.splitlines()[0])
            _status, out = _wingbox(root, "grep", "straw")
            lines = out.splitlines()
            self.assertEqual(2001, len(lines))
            self.assertEqual("(match many.py 2000 \"V1999 = 'straw'\")", lines[-2])
            self.assertEqual("(matches-summary :matches 2100 :shown 2000 :files 1 :searched 2 :pattern straw)", lines[-1])

    def test_a_file_replaced_by_a_link_is_not_read(self) -> None:
        """/* REQ-CS-140: a source file of the graph that was replaced by a symbolic link after the build is not
        read, wherever the link points: its lines are never printed, and the answer counts it (`:unread 1`). */"""
        if not hasattr(os, "symlink"):
            self.skipTest("no symbolic links here")
        with tempfile.TemporaryDirectory() as parent:
            root = _repository(Path(os.path.realpath(parent)), FILES)
            (root.parent / "secret.py").write_text("TOKEN = 'outside'\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=inside\n", encoding="utf-8")
            search = _load_search()
            graph = _graph(search, root)
            for target in (root.parent / "secret.py", root / ".env"):
                (root / "shop" / "checkout.py").unlink()
                try:
                    os.symlink(target, root / "shop" / "checkout.py")
                except OSError:
                    self.skipTest("symbolic links are not permitted here")
                out = _captured(lambda: search.cmd_grep(graph, "TOKEN"))
                self.assertEqual(["(matches-empty TOKEN :searched 4 :unread 1)"], out.splitlines(), target)
                out = _captured(lambda: search.cmd_grep(graph, "total"))
                self.assertNotIn("shop/checkout.py", out)
                self.assertIn(":unread 1)", out.splitlines()[-1])


class QueryErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _repository(Path(os.path.realpath(cls._tmp.name)), FILES)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_a_malformed_query_is_one_error_line(self) -> None:
        """/* REQ-CS-142: `wingbox query` answers a malformed expression with one line
        `(error invalid-query "<expression>" "<what is wrong>")` and no result: parentheses that do not match,
        a string that is not closed, an empty expression, and a function the language does not have, which the
        line names. */"""
        for expression, reason in (
            ('(deps "shop/checkout" 1', "a parenthesis is not closed"),
            ('(deps "shop/checkout" 1))', "a closing parenthesis has no opening one"),
            ('(deps "shop/checkout 1)', "a string is not closed"),
            ("   ", "the expression is empty"),
            ('(nosuch "x")', "unknown function: nosuch"),
            ('(files (nosuch "x"))', "unknown function: nosuch"),
        ):
            _status, out = _wingbox(self.root, "query", expression)
            lines = out.splitlines()
            self.assertEqual(1, len(lines), (expression, out))
            self.assertTrue(lines[0].startswith("(error invalid-query "), (expression, out))
            self.assertIn(reason, lines[0], expression)

    def test_a_well_formed_query_is_unchanged(self) -> None:
        """/* REQ-CS-143: a well-formed expression answers as before: a result, an empty result for a
        selection that holds nothing, and a parenthesis inside a string or after a comment sign is text. */"""
        _status, out = _wingbox(self.root, "query", '(rdeps "shop/pricing" 1)')
        self.assertIn("(result shop/checkout)", out.splitlines())
        _status, out = _wingbox(self.root, "query", '(deps "no(such" 1)')
        self.assertFalse(out.startswith("(error invalid-query "), out)
        _status, out = _wingbox(self.root, "query", '(rdeps "shop/pricing" 1) ; who uses it (')
        self.assertIn("(result shop/checkout)", out.splitlines())


if __name__ == "__main__":
    unittest.main()
