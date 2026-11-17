from __future__ import annotations

import contextlib
import importlib.metadata
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_code_graph


class DartOptionalDependencyTest(unittest.TestCase):
    def test_a_missing_dart_grammar_skips_dart_files_and_the_build_goes_on(self) -> None:
        """/* REQ-DART-015: tree-sitter-dart is an opt-in extra; without it the build skips .dart files with a warning naming wingbox[dart] and still writes the graph of the other languages; a Dart-only project keeps the install error (REQ-DART-012). */"""
        real_version = importlib.metadata.version

        def version(distribution: str) -> str:
            if distribution == "tree-sitter-dart":
                raise importlib.metadata.PackageNotFoundError(distribution)
            return real_version(distribution)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "lib").mkdir()
            (root / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
            (root / "tool.py").write_text("import helper\n", encoding="utf-8")
            (root / "helper.py").write_text("X = 1\n", encoding="utf-8")
            errors = io.StringIO()
            with mock.patch.object(build_code_graph.importlib.metadata, "version", version), contextlib.redirect_stderr(
                errors
            ), contextlib.redirect_stdout(io.StringIO()):
                status = build_code_graph.main(["--root", str(root)])
            self.assertEqual(0, status, errors.getvalue())
            self.assertIn("Dart files are skipped", errors.getvalue())
            self.assertIn('wingbox[dart]', errors.getvalue())
            graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
            self.assertIn("helper", graph)
            self.assertNotIn("main.dart", graph)
            self.assertNotIn("lib/main", graph)


if __name__ == "__main__":
    unittest.main()
