"""Issue #105: a source file that starts with a UTF-8 byte order mark is read like the same file without it."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_code_graph.py"
BOM = b"\xef\xbb\xbf"

# One small project with a file or two per language whose parser the test environment has.
SOURCES = {
    "app/alpha.py": "def bravo_func():\n    return 1\n\n\nclass Charlie:\n    def run(self):\n        return bravo_func()\n",
    "app/uses_alpha.py": "from app.alpha import bravo_func\n\n\ndef call():\n    return bravo_func()\n",
    "web/delta.ts": "export function deltaTs(): number {\n  return 1;\n}\n",
    "web/uses_delta.ts": 'import { deltaTs } from "./delta";\n\nexport const value = deltaTs();\n',
    "core/src/lib.rs": "pub fn echo_rs() -> i32 {\n    1\n}\n",
    "svc/Foxtrot.java": "public class Foxtrot {\n    int run() {\n        return 1;\n    }\n}\n",
    "svc/Golf.cs": "namespace Svc\n{\n    public class Golf\n    {\n        public int Run() { return 1; }\n    }\n}\n",
    "cmd/hotel.go": "package main\n\nfunc Hotel() int {\n\treturn 1\n}\n",
    "lib/india.rb": "class India\n  def run\n    1\n  end\nend\n",
    "site/Juliet.php": "<?php\n\nclass Juliet\n{\n    public function run()\n    {\n        return 1;\n    }\n}\n",
    # A configuration file with a BOM: the path alias it declares must still resolve.
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["lib/*"]}}}\n',
    "web/lib/kilo.ts": "export function kiloTs(): number {\n  return 2;\n}\n",
    "web/uses_kilo.ts": 'import { kiloTs } from "@lib/kilo";\n\nexport const other = kiloTs();\n',
}
NAMES = ("bravo_func", "Charlie", "deltaTs", "echo_rs", "Foxtrot", "Golf", "Hotel", "India", "Juliet", "kiloTs")


def build(folder: Path, bom: bool) -> tuple[str, str]:
    """Write the project into folder (every source with or without a BOM), build it, return (index, build output)."""
    for name, text in SOURCES.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    done = subprocess.run([sys.executable, str(BUILD_SCRIPT), "--root", str(folder)], capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    return (folder / ".wingbox" / "index.sexp").read_text(encoding="utf-8"), done.stdout + done.stderr


class ByteOrderMarkTest(unittest.TestCase):
    def test_a_python_file_with_a_bom_keeps_its_functions_and_classes(self) -> None:
        """/* REQ-PY-006: a Python file saved with a UTF-8 byte order mark (Visual Studio and some Windows editors write one) must be parsed like any other: its functions and classes are in the graph, a file that imports it gets its reference edges, and the build prints no "AST parse failed" warning for it. */"""
        with tempfile.TemporaryDirectory() as tmp:
            index, output = build(Path(tmp) / "project", bom=True)
        self.assertNotIn("AST parse failed", output)
        self.assertIn(" bravo_func ", index)
        self.assertIn(" Charlie ", index)
        self.assertIn("::bravo_func)", index)

    def test_a_bom_changes_nothing_in_the_graph_of_any_language(self) -> None:
        """/* REQ-LANG-013: for every language (Python, TypeScript, Rust, Java, C#, Go, Ruby, PHP) the graph of a project whose source files and tsconfig.json start with a UTF-8 byte order mark must be byte for byte the graph of the same project without it: same nodes, entities with the same line numbers, edges and layers. */"""
        with tempfile.TemporaryDirectory() as tmp:
            plain, _ = build(Path(tmp) / "plain" / "project", bom=False)
            marked, output = build(Path(tmp) / "marked" / "project", bom=True)
        for name in NAMES:
            self.assertIn(f" {name} ", plain, name)
        self.assertIn("::kiloTs)", plain)          # the alias of tsconfig.json resolves
        self.assertEqual(plain, marked, output)


if __name__ == "__main__":
    unittest.main()
