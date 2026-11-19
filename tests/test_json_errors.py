"""The JSON form for code: a call that fails as a whole says so in one place, on standard output."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from scripts import fact_json  # noqa: E402

FILES = {
    "app/__init__.py": "",
    "app/pricing.py": "VALUE = 1\n\n\ndef price(x):\n    return x * VALUE\n",
    "app/cart.py": "from app.pricing import price\n\n\ndef total(items):\n    return sum(price(i) for i in items)\n",
}


def _environment() -> dict[str, str]:
    environment = dict(os.environ, WINGBOX_JOBS="1")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment.pop("CLAUDECODE", None)
    return environment


def wingbox(folder: Path, *args: str) -> tuple[int, str, str]:
    done = subprocess.run([sys.executable, "-P", "-m", "wingbox.cli", *args], cwd=folder, capture_output=True, text=True,
                          encoding="utf-8", env=_environment(), timeout=600)
    return done.returncode, done.stdout, done.stderr


class JsonErrorsTest(unittest.TestCase):
    root: Path
    empty: Path
    _folder: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._folder = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(cls._folder.name))
        cls.root, cls.empty = base / "project", base / "empty"
        cls.empty.mkdir()
        for name, text in FILES.items():
            (cls.root / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.root / name).write_text(text, encoding="utf-8")
        status, _out, err = wingbox(cls.root, "build", str(cls.root))
        if status != 0:
            raise AssertionError(err)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._folder.cleanup()

    def test_a_call_that_fails_as_a_whole_has_one_error_object_on_standard_output(self) -> None:
        """/* REQ-CLI-044: with --json a call that fails as a whole prints one JSON object on standard output whose first key is `error`, an object with `code` and `message`: no graph in the folder (`no-graph`, exit 1), a depth out of range (`invalid-argument`, exit 2), a name nothing matches for `usages` and `quick` (`no-match`), a git reference that does not exist for `diff_functions` (`git-diff-failed`, with the whole message of git in `message`), and an architecture command without a graph. Code reads `answer.error` and needs no other check. */"""
        cases = [
            (self.empty, ("impact", "a.py", "2", "--json"), "no-graph", 1),
            (self.empty, ("usages", "price", "--json"), "no-graph", 1),
            (self.root, ("impact", "app/cart.py", "99", "--json"), "invalid-argument", 2),
            (self.root, ("usages", "nosuchname", "--json"), "no-match", None),
            (self.root, ("quick", "nosuchthing", "--json"), "no-match", None),
            (self.root, ("diff_functions", "no-such-ref", "--json"), "git-diff-failed", None),
        ]
        for folder, args, code, status in cases:
            with self.subTest(args=args):
                got, out, _err = wingbox(folder, *args)
                document = json.loads(out)
                self.assertEqual("error", next(iter(document)), out)
                self.assertEqual(code, document["error"]["code"], out)
                self.assertIsInstance(document["error"]["message"], str)
                self.assertEqual(1, len(out.strip().splitlines()), out)
                if status is not None:
                    self.assertEqual(status, got)
        _status, out, _err = wingbox(self.root, "diff_functions", "no-such-ref", "--json")
        self.assertIn("no-such-ref", json.loads(out)["error"]["message"])

    def test_an_answer_has_no_error_key_and_one_file_of_several_fails_alone(self) -> None:
        """/* REQ-CLI-045: an answer has no top-level `error`: `impact`, `usages`, `quick`, `module`, `boundaries` with --json on a built project; and when one file of an `impact` call is not in the graph, that file alone carries `error` in place of `rows`, the other files are answered and there is no top-level `error`. */"""
        for args in (("impact", "app/pricing.py", "2", "--json"), ("usages", "price", "--json"), ("quick", "pricing", "--json"),
                     ("module", "app/pricing", "--json"), ("boundaries", "--json")):
            with self.subTest(args=args):
                _status, out, err = wingbox(self.root, *args)
                self.assertNotIn("error", json.loads(out), out + err)
        status, out, _err = wingbox(self.root, "impact", "nofile.py", "app/pricing.py", "2", "--json")
        document = json.loads(out)
        self.assertNotIn("error", document)
        self.assertEqual(("no-match", None), (document["impact"][0]["error"], document["impact"][0].get("rows")))
        self.assertEqual(["app/cart.py"], [row["file"] for row in document["impact"][1]["rows"]])
        self.assertEqual(1, status)

    def test_a_fact_that_runs_over_several_lines_is_one_object(self) -> None:
        """/* REQ-CLI-046: a fact whose quoted text holds line breaks (the message of a failed git command) becomes one JSON object, not one object per line, and `with_error` takes the first error fact of an answer as its error, or the fallback when the answer is empty. */"""
        text = '(error git-diff-failed "fatal: bad revision\nUse -- to separate paths")\n(diff-functions-summary :changed 0)\n'
        facts = fact_json.facts_json(text)
        self.assertEqual(2, len(facts), facts)
        self.assertEqual(["git-diff-failed", "fatal: bad revision\nUse -- to separate paths"], facts[0]["args"])
        document = fact_json.with_error({"facts": facts}, None)
        self.assertEqual(["error", "facts"], list(document))
        self.assertEqual({"code": "git-diff-failed", "message": "fatal: bad revision\nUse -- to separate paths"}, document["error"])
        self.assertEqual({"error": {"code": "no-graph", "message": "run wingbox build"}, "facts": []},
                         fact_json.with_error({"facts": []}, ("no-graph", "run wingbox build")))
        self.assertEqual({"facts": [{"fact": "fn", "args": ["a", "b", 4]}]}, fact_json.with_error({"facts": [{"fact": "fn", "args": ["a", "b", 4]}]}, None))
        self.assertEqual(["error", "usages"], list(fact_json.with_error({"usages": {"symbol": "x", "error": "no-match", "suggestions": []}}, None)))


RECIPES = REPO / "references" / "code-mode.md"


class CodeModeRecipesTest(unittest.TestCase):
    root: Path
    _folder: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._folder = tempfile.TemporaryDirectory()
        cls.root = Path(os.path.realpath(cls._folder.name)) / "project"
        files = {**FILES, "app/checkout.py": "from app.cart import total\n\n\ndef checkout(items):\n    return total(items)\n",
                 "tests/__init__.py": "", "tests/test_pricing.py": "from app.pricing import price\n\n\ndef test_price():\n    assert price(2) == 2\n"}
        for name, text in files.items():
            (cls.root / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.root / name).write_text(text, encoding="utf-8")
        git = ["git", "-C", str(cls.root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
        (cls.root / "app" / "pricing.py").write_text("VALUE = 2\n\n\ndef price(x):\n    return x * VALUE + 0\n", encoding="utf-8")
        status, _out, err = wingbox(cls.root, "build", str(cls.root))
        if status != 0:
            raise AssertionError(err)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._folder.cleanup()

    def answer(self, *args: str) -> dict:
        _status, out, err = wingbox(self.root, *args, "--json")
        document = json.loads(out)
        self.assertNotIn("error", document, out + err)
        return document

    def test_every_call_of_the_recipes_answers_with_the_keys_the_recipes_read(self) -> None:
        """/* REQ-INSTALL-046: references/code-mode.md, the text for Claude Code's Code Mode, gives for each question an instruction first (what one call returns and that the value of the block is the answer) and a short example whose last expression is that value (no example ends with an assignment); for `impact` it states the three facts of the answer: each dependent once and never the file itself, the list not cut, read from every source file of the graph with test files included. Every command the text calls (`impact` for several files, `usages`, `quick`, `diff_functions`, `boundaries`) answers on a real project with the keys the text reads, the item a `diff-fn` fact names is accepted by `usages`, and the test files of the change come out of the text's own pattern. The skill and the always-on block point to the file and say the same in one line, and the plugin copies are the same text. */"""
        text = RECIPES.read_text(encoding="utf-8")
        import re
        self.assertIn("Make the call in one block of code and let the value of that block be the answer", text)
        for fact in ("Each dependent is listed once, never the file itself, and the list\nis not cut.",
                     "read from the import statements and uses of every source file of the graph, test files\nincluded",
                     "`graph.stale` is 0 when none changed since the\ngraph was built"):
            self.assertIn(fact, text)
        self.assertIn("const answer = (await $`wingbox impact ${files} 2 --json`.nothrow()).json();", text)
        blocks = re.findall(r"```javascript\n(.*?)```", text, re.S)
        self.assertEqual(5, len(blocks))
        for block in blocks:
            last = block.strip().splitlines()[-1]
            self.assertNotRegex(last, r"^\s*(const|let|var)\s", block)       # the block's value is its last expression
        self.assertEqual({"impact", "usages", "quick", "diff_functions", "boundaries"},
                         set(re.findall(r"`wingbox ([a-z_]+)[ `]", text)) & {"impact", "usages", "quick", "diff_functions", "boundaries"})
        impact = self.answer("impact", "app/pricing.py", "app/cart.py", "2")
        self.assertEqual({"built", "files", "stale"}, set(impact["graph"]))
        self.assertEqual(0, impact["graph"]["stale"])
        for entry in impact["impact"]:
            for row in entry["rows"]:
                self.assertLessEqual({"file", "depth", "via", "through", "reasons"}, set(row))
        self.assertEqual({"app/pricing.py": ["app/cart.py", "tests/test_pricing.py", "app/checkout.py"], "app/cart.py": ["app/checkout.py"]},
                         {entry["file"]: [row["file"] for row in entry["rows"]] for entry in impact["impact"]})
        usages = self.answer("usages", "price")["usages"]
        self.assertLessEqual({"file", "line", "kind"}, set(usages["rows"][0]))
        found = self.answer("quick", "pricing")["facts"]
        self.assertIn("app/pricing.py", {fact.get("file") for fact in found})
        changed = [fact["args"][0] for fact in self.answer("diff_functions", "HEAD")["facts"] if fact["fact"] == "diff-fn"]
        self.assertEqual(["app/pricing::price"], changed)
        pattern = re.search(r"if \(/(.+)/\.test\(row\.file\)\)", text).group(1)
        tests = {row["file"] for item in changed for row in self.answer("usages", item)["usages"]["rows"] if re.search(pattern, row["file"])}
        self.assertEqual({"tests/test_pricing.py"}, tests)
        rules = self.answer("boundaries")["facts"]
        self.assertTrue(all("fact" in fact for fact in rules))
        self.assertIn("boundaries-not-checked", {fact["fact"] for fact in rules})
        self.assertIn("references/code-mode.md", (REPO / "SKILL.md").read_text(encoding="utf-8"))
        from wingbox import install
        block = install.ALWAYS_ON_BLOCK
        self.assertIn("references/code-mode.md", block)
        self.assertIn("Make the call in one block and let its value be the map from each file to its rows' files.", block)
        self.assertIn("listed once, the list not cut, test files included", block)
        for copy in ("plugins/wingbox/skills/wingbox/references/code-mode.md", "plugins/wingbox-skill/skills/wingbox/references/code-mode.md"):
            self.assertEqual(text, (REPO / copy).read_text(encoding="utf-8"), copy)

    def test_the_rows_of_an_impact_answer_hold_each_dependent_once_and_never_the_file(self) -> None:
        """/* REQ-CLI-047: in the JSON answer of `impact`, for every source file of a project, `rows` names each dependent file once, never the file asked about, and `total` is the number of rows: what the code for Code Mode says of the answer. */"""
        files = ["app/pricing.py", "app/cart.py", "app/checkout.py", "tests/test_pricing.py", "app/__init__.py"]
        for entry in self.answer("impact", *files, "2")["impact"]:
            with self.subTest(file=entry["file"]):
                named = [row["file"] for row in entry["rows"]]
                self.assertEqual(len(named), len(set(named)))
                self.assertNotIn(entry["file"], named)
                self.assertEqual(entry["total"], len(named))


if __name__ == "__main__":
    unittest.main()
