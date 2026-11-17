"""The Python reference of the benchmarks (tools/py_edges.py) counts modules loaded by a literal name."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "app/__init__.py": "",
    "app/plugins/__init__.py": "",
    "app/plugins/alpha.py": "X = 1\n",
    "app/plugins/beta.py": "Y = 2\n",
    "app/plugins/gamma.py": "Z = 3\n",
    "app/loader.py": (
        "import importlib\nfrom importlib import import_module\n\n\n"
        "def load(name):\n"
        "    importlib.import_module(\"app.plugins.alpha\")\n"
        "    import_module(\"app.plugins.beta\")\n"
        "    __import__(\"app.plugins.gamma\")\n"
        "    importlib.import_module(\"json\")\n"
        "    importlib.import_module(name)\n"
        "    importlib.import_module(\"app.plugins.\" + name)\n"
        "    importlib.import_module(\".alpha\", __package__)\n"
    ),
    "app/main.py": "from app import loader\n",
}


class PyEdgesTest(unittest.TestCase):
    def test_a_module_loaded_by_a_literal_name_is_an_edge(self) -> None:
        """/* REQ-BENCH-013: both copies of tools/py_edges.py count `importlib.import_module("a.b")`, `import_module("a.b")` and `__import__("a.b")` with a literal absolute name as an edge to that module (kind import-call), with the package __init__.py files on the way as implied pairs; a name built at run time, a relative name and a module outside the tree give no edge. */"""
        for copy in ("impact", "graphify"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "tree"
                for name, text in FILES.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                out = Path(tmp) / "ref.json"
                subprocess.run([sys.executable, "-I", str(ROOT / "benchmarks" / copy / "tools" / "py_edges.py"), str(root), str(out)],
                               check=True, capture_output=True)
                ref = json.loads(out.read_text(encoding="utf-8"))
                posix = lambda path: path.replace(os.sep, "/")   # the references write the system's separator
                calls = sorted((posix(r["to"]), r["stmt"]) for r in ref["refs"] if r["kind"] == "import-call")
                self.assertEqual([
                    ("app/plugins/alpha.py", "importlib.import_module('app.plugins.alpha')"),
                    ("app/plugins/beta.py", "import_module('app.plugins.beta')"),
                    ("app/plugins/gamma.py", "__import__('app.plugins.gamma')"),
                ], calls, copy)
                self.assertIn(["app/loader.py", "app/plugins/alpha.py"], [[posix(a), posix(b)] for a, b in ref["pairs"]])
                self.assertIn(["app/loader.py", "app/plugins/__init__.py"], [[posix(a), posix(b)] for a, b in ref["implied_pairs"]])

    def test_questions_are_drawn_from_import_statements_only(self) -> None:
        """/* REQ-BENCH-014: the impact benchmark draws its questions from the files that an import statement reaches, so that counting modules loaded by name changes no question; the edge of a module loaded by name is still in the reference list of every question. */"""
        sys.path.insert(0, str(ROOT / "benchmarks" / "impact"))
        import impact100  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            refs = [{"frm": "a.py", "to": "b.py", "kind": "import"}, {"frm": "t.py", "to": "c.py", "kind": "import-call"}]
            Path(tmp, "out", "repo.ref.json").write_text(json.dumps({"refs": refs, "pairs": [["a.py", "b.py"], ["t.py", "c.py"]],
                                                                      "implied_pairs": []}), encoding="utf-8")
            required, _accepted = impact100.reference(tmp, "repo", "python")
            self.assertEqual({("a.py", "b.py"), ("t.py", "c.py")}, required)
            self.assertEqual({("a.py", "b.py")}, impact100.drawn_from(tmp, "repo", "python", required))
            self.assertEqual(required, impact100.drawn_from(tmp, "repo", "typescript", required))   # Rust: REQ-BENCH-015


if __name__ == "__main__":
    unittest.main()
