"""Edges name the line they are read from; the graph names what it was built from.

An `imports` or `refs` fact read from import statements carries `:line N` (or
`:lines N M` for several statements); a fact that no single statement states
carries none. The header is followed by `(built-from :tree <digest> [:commit
<sha>] [:wingbox <version>])`. Every output that existed before prints the same
bytes; `module --lines` and `search --lines` show the lines. A graph built by
an earlier version is read as before. The commands run as processes.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import graph_snapshot  # noqa: E402
from scripts.lang import language_for_name  # noqa: E402

PYTHON = {
    "pkg/__init__.py": "",
    "pkg/b.py": "VALUE = 1\n\n\ndef helper():\n    return VALUE\n\n\nclass Base:\n    pass\n",
    "pkg/c.py": "def other():\n    return 2\n",
    # line 2: import os; 3: helper from pkg.b; 7: the module pkg.c; 9: Base from pkg.b
    "pkg/a.py": (
        '"""Module a."""\nimport os\nfrom pkg.b import helper\n\n\n\nfrom pkg import c\n\nfrom pkg.b import Base\n'
        "\n\nclass Child(Base):\n    pass\n\n\ndef use():\n    return helper() + c.other() + len(os.sep)\n"
    ),
}
TYPESCRIPT = {
    "package.json": '{"name": "shop"}\n',
    "src/b.ts": "export const b = 1;\n",
    "src/t.ts": "export interface T { id: number }\n",
    "src/c.ts": "export const c = 3;\n",
    # line 1: b; 2: the type T; 5: b again, loaded at run time; 6: the re-export of c
    "src/a.ts": (
        "import { b } from './b';\nimport type { T } from './t';\n\nexport const x: T = { id: b };\n"
        "export const lazy = () => import('./b');\nexport { c } from './c';\n"
    ),
}
_MARK = re.compile(r" :lines?(?: \d+)+")


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_AUTO_REFRESH"] = "0"
    environment.pop("CLAUDECODE", None)
    return environment


class _Project(unittest.TestCase):
    def folder(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8", newline="\n")
        return root

    def wingbox(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=root, env=_environment(), timeout=600)

    def build(self, root: Path, *args: str) -> str:
        built = self.wingbox(root, "build", str(root), *args)
        self.assertEqual(0, built.returncode, built.stderr)
        return (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")

    def lines(self, root: Path, *args: str) -> list[str]:
        done = self.wingbox(root, *args)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return done.stdout.splitlines()


class EdgeLinesTest(_Project):
    def test_python_imports_name_the_lines_of_their_statements(self) -> None:
        """/* REQ-CS-097: in Python, an import on line 7 gives an imports fact with :line 7, two import statements of the same module give :lines with both, and the refs fact of an imported name carries the line of its import statement; a refs fact that comes from a class naming its base, and a data-flow fact, carry no line. */"""
        root = self.folder(PYTHON)
        graph = self.build(root)
        shown = self.lines(root, "module", "pkg/a", "--lines")
        self.assertIn("(imports pkg/a pkg/c :line 7)", shown)
        self.assertIn("(imports pkg/a pkg/b :lines 3 9)", shown)
        self.assertIn("(refs pkg/a pkg/b::helper :line 3)", shown)
        self.assertIn("(refs pkg/a::Child pkg/b::Base)", shown)
        stored = [line for line in graph.splitlines() if _MARK.search(line) and line.startswith("(")]
        self.assertTrue(stored)
        for line in stored:
            self.assertTrue(line.startswith(("(imports ", "(refs ")), line)

    def test_typescript_imports_name_the_lines_of_their_statements(self) -> None:
        """/* REQ-CS-098: in TypeScript, a static import, a type import, an import() call and a re-export each give the line where the statement starts; a module named by two of them carries both lines, and the lines are the last mark of the fact, after :via-base and :type-only. */"""
        root = self.folder(TYPESCRIPT)
        self.build(root)
        shown = self.lines(root, "module", "src/a", "--lines")
        self.assertIn("(imports src/a src/b :lines 1 5)", shown)
        self.assertIn("(imports src/a src/t :via-base :type-only :line 2)", shown)
        self.assertIn("(imports src/a src/c :line 6)", shown)

    def test_no_output_changes_unless_lines_are_asked_for(self) -> None:
        """/* REQ-CS-099: module, deps, rdeps, impact (text and --sexp), analyze, search, quick, layers and boundaries print the same bytes from a graph with lines and a built-from fact as from the same graph without them (what an earlier version wrote, which is read without an error); only module --lines and search --lines differ. */"""
        root = self.folder(PYTHON)
        graph = self.build(root)
        self.assertIn("\n(built-from :tree ", graph)
        commands = [
            ("module", "pkg/a"), ("deps", "pkg/a"), ("rdeps", "pkg/b"), ("impact", "pkg/b.py"),
            ("impact", "pkg/b.py", "--sexp"), ("analyze", "pkg/a"), ("search", "imports"), ("search", "helper"),
            ("search", "built"), ("quick", "line"), ("layers",), ("boundaries", "all"),
        ]
        with_lines = {command: self.wingbox(root, *command) for command in commands}
        asked = self.lines(root, "module", "pkg/a", "--lines")
        raw = self.lines(root, "search", "imports", "--lines")
        earlier = "".join(
            _MARK.sub("", line, count=1) if line.startswith(("(imports ", "(refs ")) else line
            for line in graph.splitlines(keepends=True) if not line.startswith("(built-from ")
        )
        self.assertNotEqual(graph, earlier)
        (root / ".wingbox" / "index.sexp").write_text(earlier, encoding="utf-8", newline="\n")
        for command, done in with_lines.items():
            again = self.wingbox(root, *command)
            self.assertEqual((done.returncode, done.stdout), (again.returncode, again.stdout), command)
            self.assertNotIn(":line 7", done.stdout, command)
            self.assertNotIn("built-from", done.stdout, command)
        self.assertEqual([_MARK.sub("", line) for line in asked], self.lines(root, "module", "pkg/a", "--lines"))
        self.assertTrue(any(" :line 7" in line for line in asked), asked)
        self.assertTrue(any(" :line 7" in line for line in raw), raw)

    def test_other_languages_give_the_line_of_each_import_statement(self) -> None:
        """/* REQ-CS-100: the parsers of Go, Java, C#, Ruby, PHP, Dart, Rust and Pascal record the line of each import statement (import spec, import declaration, using directive, require, use declaration, import directive, use and mod declaration, uses clause) in import_lines, every key being an entry of imports, and none for an import no single statement states (a type of the file's own package or namespace, a Ruby constant). */"""
        # language: (file, source, the lines expected for these imports, imports that must carry no line)
        cases = {
            "go": ("main.go", 'package main\n\nimport (\n\t"fmt"\n\tm "example.com/app/model"\n)\n\nfunc main() { fmt.Println(m.X) }\n',
                   {"fmt": [4], "example.com/app/model": [5]}, ()),
            "java": ("src/app/Main.java",
                     "package app;\n\nimport app.model.Order;\nimport app.util.*;\n\npublic class Main {\n    Order order;\n    Helper helper;\n}\n",
                     {"app.model.Order": [3], "app.util.*": [4], "app.util.Helper": [4]}, ("app.Helper", "app.Order")),
            "csharp": ("App/Main.cs",
                       "using System;\nusing Model = App.Models.Order;\n\nnamespace App\n{\n    public class Main { Helper helper; }\n}\n",
                       {"System": [1], "App.Models.Order": [2], "System.Helper": [1]}, ("App.Helper",)),
            "ruby": ("lib/main.rb", "# frozen_string_literal: true\nrequire 'json'\nrequire_relative 'helper'\n\nclass Main\n  def run\n    Helper.new\n  end\nend\n",
                     {"ruby-require:json": [2], "ruby-require-relative:helper": [3]}, ("ruby-constant:Helper",)),
            "php": ("src/Main.php", "<?php\n\nnamespace App;\n\nuse App\\Models\\Order;\nuse App\\Support\\Money as Cash;\n\nclass Main\n{\n}\n",
                    {"php-class:App\\Models\\Order": [5], "php-class:App\\Support\\Money": [6]}, ()),
            "dart": ("lib/main.dart", "import 'dart:async';\nimport 'src/helper.dart';\nexport 'src/model.dart';\n\nvoid main() {}\n",
                     {"dart:async": [1], "src/helper.dart": [2], "src/model.dart": [3]}, ()),
            "rust": ("src/main.rs", "mod helper;\n\nuse crate::helper::run;\nuse std::fmt;\n\nfn main() { run(); }\n",
                     {"self::helper": [1], "crate::helper::run": [3], "std::fmt": [4]}, ()),
            "pascal": ("Main.pas", "unit Main;\n\ninterface\n\nuses\n  SysUtils,\n  Helper;\n\nimplementation\n\nend.\n",
                       {"SysUtils": [6], "Helper": [7]}, ()),
        }
        for name, (relative, content, expected, unstated) in cases.items():
            with self.subTest(language=name):
                root = self.folder({relative: content})
                path = root / relative
                result = language_for_name(name).parse(str(path), content, root.name, path.stat().st_mtime)
                self.assertEqual(expected, {module: result.import_lines.get(module) for module in expected})
                for module in unstated:
                    self.assertIn(module, result.imports)
                    self.assertNotIn(module, result.import_lines)
                self.assertLessEqual(set(result.import_lines), set(result.imports))


class BuiltFromTest(_Project):
    def git(self, root: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def built_from(self, graph: str) -> graph_snapshot.Snapshot:
        lines = graph.splitlines()
        header = next(index for index, line in enumerate(lines) if line.startswith("(code-graph "))
        self.assertTrue(lines[header + 1].startswith("(built-from :tree "), lines[header + 1])
        return graph_snapshot.read_snapshot(lines[header + 1].strip("()").split())

    def test_the_same_files_give_the_same_graph_and_the_same_tree_digest(self) -> None:
        """/* REQ-CS-101: the fact after the header names a tree digest of 32 hex digits that depends only on the paths and the text of the source files: two builds of the same files, incremental or --full, give the same bytes; a changed character gives another digest; the same files in another folder, or saved with CRLF line endings and a byte order mark, give the same digest; outside a Git repository no commit is named. */"""
        root = self.folder(PYTHON)
        first = self.build(root)
        snapshot = self.built_from(first)
        self.assertRegex(snapshot.tree, r"^[0-9a-f]{32}$")
        self.assertEqual("", snapshot.commit)
        self.assertEqual(first, self.build(root))
        self.assertEqual(first, self.build(root, "--full"))
        elsewhere = self.folder(PYTHON)
        self.assertEqual(snapshot.tree, self.built_from(self.build(elsewhere)).tree)
        windows = self.folder({})
        for name, text in PYTHON.items():
            (windows / name).parent.mkdir(parents=True, exist_ok=True)
            (windows / name).write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(snapshot.tree, self.built_from(self.build(windows)).tree)
        (root / "pkg" / "c.py").write_text("def other():\n    return 3\n", encoding="utf-8", newline="\n")
        self.assertNotEqual(snapshot.tree, self.built_from(self.build(root)).tree)

    def test_the_commit_is_named_only_for_a_clean_tree(self) -> None:
        """/* REQ-CS-102: in a Git repository whose working tree has no change and no untracked file, the fact names the commit checked out; with a modified file, or an untracked one, it names no commit; once the change is committed it names the new commit. */"""
        root = self.folder(PYTHON)
        self.git(root, "init", "-q")
        self.git(root, "config", "core.autocrlf", "false")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "base")
        head = self.git(root, "rev-parse", "HEAD")
        clean = self.built_from(self.build(root))
        self.assertEqual(head, clean.commit)
        (root / "pkg" / "c.py").write_text("def other():\n    return 3\n", encoding="utf-8", newline="\n")
        changed = self.built_from(self.build(root))
        self.assertEqual("", changed.commit)
        self.assertNotEqual(clean.tree, changed.tree)
        self.git(root, "commit", "-qam", "change")
        committed = self.built_from(self.build(root))
        self.assertEqual(self.git(root, "rev-parse", "HEAD"), committed.commit)
        self.assertEqual(changed.tree, committed.tree)
        (root / "pkg" / "d.py").write_text("D = 4\n", encoding="utf-8", newline="\n")
        self.assertEqual("", self.built_from(self.build(root)).commit)

    def test_the_fact_is_a_pure_rendering_of_the_snapshot(self) -> None:
        """/* REQ-CS-103: tree_digest does not depend on the order of the files; snapshot_fact writes the commit and the version only when they are known, and read_snapshot reads the fact back. */"""
        files = [("pkg/a.py", "1" * 64), ("pkg/b.py", "2" * 64)]
        digest = graph_snapshot.tree_digest(files)
        self.assertEqual(digest, graph_snapshot.tree_digest(reversed(files)))
        self.assertNotEqual(digest, graph_snapshot.tree_digest([("pkg/a.py", "1" * 64), ("pkg/b.py", "3" * 64)]))
        self.assertEqual(f"(built-from :tree {digest})", graph_snapshot.snapshot_fact(graph_snapshot.Snapshot(digest)))
        full = graph_snapshot.Snapshot(digest, "c" * 40, "1.2.3")
        fact = graph_snapshot.snapshot_fact(full)
        self.assertEqual(f"(built-from :tree {digest} :commit {'c' * 40} :wingbox 1.2.3)", fact)
        self.assertEqual(full, graph_snapshot.read_snapshot(fact.strip("()").split()))


if __name__ == "__main__":
    unittest.main()
