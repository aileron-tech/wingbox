"""What the graph does not cover is said, not left out: a file of a language Wingbox has no parser for,
the count of such files, and boundaries that no rule checked."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import source_kinds
from scripts.lang import LANGUAGES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

FILES = {
    "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(depot)\ninclude(cmake/flags.cmake)\n",
    "cmake/flags.cmake": "set(CMAKE_CXX_STANDARD 20)\n",
    "scripts/release.sh": "#!/bin/sh\necho release\n",
    "docs/guide.md": "# Guide\n",
    "config/formats.yaml": "formats: []\n",
    "depot/__init__.py": "",
    "depot/crate.py": "def load():\n    return 'crate'\n",
    "depot/shelf.py": "from depot import crate\n\n\ndef build():\n    return crate.load()\n",
    "build/made.py": "VALUE = 1\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


class OutsideTheGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        _write(cls.root, FILES)
        cls.build = _run(cls.root, "build", ".")

    def test_impact_of_a_file_without_a_parser_says_why_it_is_not_in_the_graph(self) -> None:
        """/* REQ-CS-146: `wingbox impact` of a file of the repository whose language Wingbox has no parser for answers `(error not-in-graph <file> :reason not-parsed-language :language "CMake")`, with no suggestion of other files; `--json` gives `error` `not-in-graph`, `reason` and `language`; `--human` says it in a sentence; the exit status is 1. A file that does not exist is still `no-match`, and `--sexp` prints the `no-match` line of 0.13. */"""
        answer = _run(self.root, "impact", "cmake/flags.cmake")
        self.assertEqual(1, answer.returncode)
        self.assertIn('(error not-in-graph cmake/flags.cmake :reason not-parsed-language :language "CMake")', answer.stdout)
        self.assertNotIn("suggestions", answer.stdout)
        document = json.loads(_run(self.root, "impact", "CMakeLists.txt", "--json").stdout)
        self.assertEqual(
            {"file": "CMakeLists.txt", "error": "not-in-graph", "reason": "not-parsed-language", "language": "CMake"},
            document["impact"][0],
        )
        human = _run(self.root, "impact", "scripts/release.sh", "--human").stdout
        self.assertIn("scripts/release.sh is not in the graph: Wingbox has no parser for Shell", human)
        self.assertIn("(error no-match depot/missing", _run(self.root, "impact", "depot/missing.py").stdout)
        self.assertIn("(error no-match cmake/flags.cmake", _run(self.root, "impact", "cmake/flags.cmake", "--sexp").stdout)

    def test_the_other_reasons_a_file_is_not_in_the_graph(self) -> None:
        """/* REQ-CS-147: a file that is no source file (a document, a data file) answers `:reason not-source`; a source file of a parsed language that the build left out (a skipped folder such as `build/`, a path Git ignores) answers `:reason excluded :language "Python"`. */"""
        self.assertIn("(error not-in-graph docs/guide.md :reason not-source)", _run(self.root, "impact", "docs/guide.md").stdout)
        self.assertIn("(error not-in-graph config/formats.yaml :reason not-source)", _run(self.root, "impact", "config/formats.yaml").stdout)
        self.assertIn('(error not-in-graph build/made.py :reason excluded :language "Python")', _run(self.root, "impact", "build/made.py").stdout)

    def test_the_other_file_commands_give_the_same_answer(self) -> None:
        """/* REQ-CS-148: `analyze`, `deps`, `rdeps` and `module` of a file of the repository that is not in the graph print the same `not-in-graph` fact instead of `no-match` with files of similar names. */"""
        for command in ("analyze", "deps", "rdeps", "module"):
            with self.subTest(command=command):
                answer = _run(self.root, command, "cmake/flags.cmake").stdout
                self.assertIn('(error not-in-graph cmake/flags.cmake :reason not-parsed-language :language "CMake")', answer)

    def test_the_build_counts_the_files_it_has_no_parser_for(self) -> None:
        """/* REQ-CS-149: a build counts the files of languages it has no parser for, per language, most files first: it prints the count, writes `(not-parsed-language "CMake" :files 2)` lines to `.wingbox/graph-unparsed.sexp`, and the graph fact of an `impact` answer carries `:not-parsed ("CMake" 2 "Shell" 1)`, `graph.not_parsed` in JSON. Files in skipped folders are not counted, documents and data files are not languages, and a repository without such files has no record and no `:not-parsed`. */"""
        self.assertIn("Not in the graph: 2 CMake files, 1 Shell file (no parser for these languages)", self.build.stdout + self.build.stderr)
        record = (self.root / ".wingbox" / "graph-unparsed.sexp").read_text(encoding="utf-8")
        self.assertEqual('(not-parsed-language "CMake" :files 2)\n(not-parsed-language "Shell" :files 1)\n', record)
        answer = _run(self.root, "impact", "depot/crate.py")
        self.assertRegex(answer.stdout, r'\(graph :built \S+ :files 3 :stale 0 :not-parsed \("CMake" 2 "Shell" 1\)\)')
        document = json.loads(_run(self.root, "impact", "depot/crate.py", "--json").stdout)
        self.assertEqual([{"language": "CMake", "files": 2}, {"language": "Shell", "files": 1}], document["graph"]["not_parsed"])
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(os.path.realpath(directory))
            _write(plain, {name: text for name, text in FILES.items() if name.endswith((".py", ".md"))})
            _run(plain, "build", ".")
            self.assertFalse((plain / ".wingbox" / "graph-unparsed.sexp").exists())
            self.assertNotIn("not-parsed", _run(plain, "impact", "depot/crate.py").stdout)
            self.assertNotIn("not_parsed", json.loads(_run(plain, "impact", "depot/crate.py", "--json").stdout)["graph"])

    def test_the_report_names_what_is_not_covered(self) -> None:
        """/* REQ-OUT-020: REPORT.md lists, under the key findings, the languages of the repository Wingbox has no parser for with their file counts, and says "not checked" in place of a count of boundary violations when no part of the repository was checked by a rule: zero violations is written only for parts that were checked. */"""
        report = (self.root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("- Not in this graph: 2 CMake files, 1 Shell file. Wingbox has no parser for these languages", report)
        self.assertIn("| Boundary violations | not checked |", report)
        self.assertNotIn("0 boundary violations", report)
        self.assertIn("- Boundary violations: not checked.", report)

    def test_the_table_of_languages_without_a_parser_never_names_a_parsed_one(self) -> None:
        """/* REQ-CS-150: the table of languages without a parser shares no extension with the languages that are parsed, and the table of parsed extensions is that of the language registry. */"""
        parsed = {extension for language in LANGUAGES for extension in language.source_extensions}
        self.assertEqual(parsed, set(source_kinds.PARSED_EXTENSIONS))
        self.assertEqual({language.display_name: language.name for language in LANGUAGES}, source_kinds.LANGUAGE_NAMES)
        for language in LANGUAGES:
            for extension in language.source_extensions:
                self.assertEqual(language.display_name, source_kinds.PARSED_EXTENSIONS[extension])
        self.assertEqual(set(), parsed & set(source_kinds.NOT_PARSED_EXTENSIONS))
        self.assertEqual("CMake", source_kinds.not_parsed_language("CMakeLists.txt"))
        self.assertEqual("Swift", source_kinds.not_parsed_language("App.swift"))
        self.assertIsNone(source_kinds.not_parsed_language("notes.md"))
        self.assertEqual("C and C++", source_kinds.parsed_language("stage.hpp"))


if __name__ == "__main__":
    unittest.main()
