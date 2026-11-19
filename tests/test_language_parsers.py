"""The language parsers are dependencies of the package: what the package pins, where a build imports a
grammar from, and what a build does when a parser is missing anyway."""
from __future__ import annotations

import contextlib
import fnmatch
import importlib.metadata
import io
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_code_graph
from scripts.lang import LANGUAGES

REPO = Path(__file__).resolve().parents[1]
# The languages whose parser is also a pip extra of the package, kept for the commands written for 0.12.
EXTRAS = ("csharp", "java", "go", "ruby", "php", "dart")
DART_MARKER = 'sys_platform != "win32" or platform_machine != "ARM64"'
# One source file of each of these languages: (file, text, display name, grammar distribution).
SOURCES = {
    "csharp": ("storefront/Service.cs", "namespace Shop { public class Service { } }\n", "C#", "tree-sitter-c-sharp"),
    "java": ("storefront/Main.java", "package shop;\npublic class Main { }\n", "Java", "tree-sitter-java"),
    "go": ("storefront/main.go", "package main\n\nfunc main() {}\n", "Go", "tree-sitter-go"),
    "ruby": ("storefront/worker.rb", "class Worker\nend\n", "Ruby", "tree-sitter-ruby"),
    "php": ("storefront/index.php", "<?php\nclass Index {}\n", "PHP and Laravel", "tree-sitter-php"),
    "dart": ("storefront/main.dart", "void main() {}\n", "Dart", "tree-sitter-dart"),
}
# The folders 0.12 kept a parser in: <per-user folder>/<platform>/<distribution>-<version>.
OLD_PLATFORM_FOLDERS = (
    "darwin-arm64", "darwin-x86_64", "linux-x86_64", "linux-aarch64", "linux-x86_64-musl", "linux-aarch64-musl",
    "windows-amd64", "windows-arm64", "windows-x86",
)


def _without(distribution: str):
    """importlib.metadata.version with one distribution reported as not installed."""
    real_version = importlib.metadata.version

    def version(name: str) -> str:
        if name == distribution:
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    return mock.patch.object(build_code_graph.importlib.metadata, "version", version)


def _build(root: Path) -> tuple[int, str]:
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
        status = build_code_graph.main(["--root", str(root)])
    return status, errors.getvalue()


def _project(directory: str, language: str) -> Path:
    root = Path(directory).resolve()
    name, text, _display, _distribution = SOURCES[language]
    for file_name, content in ((name, text), ("tool.py", "import helper\n"), ("helper.py", "X = 1\n")):
        path = root / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class SkippedLanguageTest(unittest.TestCase):
    def test_a_build_without_the_parser_skips_the_language_and_names_the_pip_extra(self) -> None:
        """/* REQ-DEPS-007: the parsers of C#, Java, Go, Ruby, PHP and Dart are installed with the package; a build that meets files of one of them without its parser all the same (a broken install, Dart on Windows arm64) goes on with the other languages and warns with the number of skipped files and the pip extra, `pip install "wingbox[<language>]"`. */"""
        for language, (_name, _text, display, distribution) in SOURCES.items():
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                root = _project(directory, language)
                with _without(distribution):
                    status, errors = _build(root)
                self.assertEqual(0, status, errors)
                self.assertIn(f"{display} files are skipped (1 file)", errors)
                self.assertIn(f'pip install "wingbox[{language}]"', errors)
                self.assertNotIn("wingbox add", errors)
                graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
                self.assertIn("helper", graph)
                self.assertNotIn("storefront", graph)

    def test_a_project_of_one_language_without_its_parser_stops_with_the_command(self) -> None:
        """/* REQ-DEPS-008: a project whose only source files need a parser that is not installed publishes no graph; the error names the pip extra, `pip install "wingbox[<language>]"`, first. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Service.cs").write_text("public class Service { }\n", encoding="utf-8")
            with _without("tree-sitter-c-sharp"):
                status, errors = _build(root)
            self.assertEqual(2, status)
            self.assertIn('pip install "wingbox[csharp]"', errors)
            self.assertNotIn("wingbox add", errors)
            self.assertFalse((root / ".wingbox" / "index.sexp").exists())

    def test_the_skipped_languages_are_recorded_beside_the_graph_and_cleared_by_a_complete_build(self) -> None:
        """/* REQ-DEPS-009: a build that skips a language writes one fact per language to .wingbox/graph-skipped.sexp, `(skipped-language csharp :files N :add "pip install \\"wingbox[csharp]\\"")`; the file is covered by .wingbox/.gitignore and a build that skips nothing removes it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = _project(directory, "csharp")
            (root / "storefront" / "Order.cs").write_text("namespace Shop { public class Order { } }\n", encoding="utf-8")
            record = root / ".wingbox" / build_code_graph.SKIPPED_FILE
            with _without("tree-sitter-c-sharp"):
                status, errors = _build(root)
            self.assertEqual(0, status, errors)
            self.assertEqual(
                '(skipped-language csharp :files 2 :add "pip install \\"wingbox[csharp]\\"")\n',
                record.read_text(encoding="utf-8"),
            )
            ignored = [line for line in build_code_graph.GITIGNORE_TEXT.splitlines() if line and not line.startswith("#")]
            self.assertTrue(any(fnmatch.fnmatch(record.name, pattern) for pattern in ignored), ignored)
            status, errors = _build(root)
            self.assertEqual(0, status, errors)
            self.assertFalse(record.exists())
            self.assertIn("storefront", (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"))


class EnvironmentOnlyTest(unittest.TestCase):
    def test_a_parser_in_the_per_user_folder_of_0_12_is_not_read(self) -> None:
        """/* REQ-DEPS-014: a grammar is imported only from the environment that runs Wingbox: a parser that 0.12 put into ~/.wingbox/parsers (or the folder WINGBOX_PARSERS_DIR names) is not importable after the languages are loaded, and no folder under it is on the module search path. */"""
        script = (
            "import os, sys\n"
            "import scripts.lang\n"
            "try:\n"
            "    import tree_sitter_c_sharp\n"
            "    print('imported', tree_sitter_c_sharp.MARK)\n"
            "except ImportError:\n"
            "    print('absent')\n"
            "print([entry for entry in sys.path if os.sep + 'parsers' + os.sep in entry])\n"
        )
        pinned = next(
            dependency.version for language in LANGUAGES if language.name == "csharp"
            for dependency in language.dependencies if dependency.distribution == "tree-sitter-c-sharp"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "home"
            named = Path(directory).resolve() / "named" / "parsers"
            for parsers in (home / ".wingbox" / "parsers", named):
                for platform in OLD_PLATFORM_FOLDERS:
                    module = parsers / platform / f"tree-sitter-c-sharp-{pinned}" / "tree_sitter_c_sharp"
                    module.mkdir(parents=True)
                    (module / "__init__.py").write_text("MARK = 'per-user'\n", encoding="utf-8")
            environment = {name: value for name, value in os.environ.items() if name not in ("PYTHONPATH", "WINGBOX_PARSERS_DIR")}
            environment.update(HOME=str(home), USERPROFILE=str(home))
            for setting in ({}, {"WINGBOX_PARSERS_DIR": str(named)}):
                with self.subTest(setting=sorted(setting)):
                    done = subprocess.run([sys.executable, "-S", "-B", "-c", script], cwd=REPO, env={**environment, **setting},
                                          capture_output=True, text=True, check=False)
                    self.assertEqual(0, done.returncode, done.stderr)
                    self.assertEqual(["absent", "[]"], done.stdout.splitlines())


class PinnedParsersTest(unittest.TestCase):
    def test_the_package_depends_on_every_parser_with_exact_pins(self) -> None:
        """/* REQ-DEPS-012: the package depends on tree-sitter, PyYAML and the grammars of TypeScript, Rust, C#, Java, Go, Ruby, PHP and Dart, each with an exact pin; the Dart grammar alone carries an environment marker, which leaves it out on Windows arm64, where it has no wheel; the extras csharp, java, go, ruby, php, dart and all stay defined with the same pins for the commands written for 0.12, and `all` holds these parsers and nothing else; the one other extra is `http`, the transport package; every pin equals the one of the language adapter and of the requirements file. */"""
        project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        dependencies = {}
        for requirement in project["dependencies"]:
            pin, _, marker = requirement.partition(";")
            name, _, version = pin.strip().partition("==")
            self.assertTrue(name and version, requirement)
            dependencies[name] = (version, marker.strip())
        grammars = {
            dependency.distribution: dependency.version
            for language in LANGUAGES for dependency in language.dependencies
        }
        self.assertEqual(sorted(["PyYAML", *grammars]), sorted(dependencies))
        self.assertEqual(grammars, {name: dependencies[name][0] for name in grammars})
        self.assertEqual(
            {"tree-sitter-dart": DART_MARKER}, {name: marker for name, (_version, marker) in dependencies.items() if marker}
        )
        # A marker of comparisons joined by `or` reads the same as a Python expression.
        installs = {
            (system, machine): eval(DART_MARKER, {"__builtins__": {}}, {"sys_platform": system, "platform_machine": machine})
            for system, machine in (("win32", "ARM64"), ("win32", "AMD64"), ("darwin", "arm64"), ("linux", "aarch64"))
        }
        self.assertEqual([("win32", "ARM64")], [where for where, installed in installs.items() if not installed])
        extras = project["optional-dependencies"]
        # `http` is the one extra that is not a parser: the HTTP transport of the MCP server, a distribution
        # of its own (REQ-HTTP-021). `all` means all parsers and does not install it.
        self.assertEqual(sorted(EXTRAS + ("all", "http")), sorted(extras))
        adapters = {language.name: language for language in LANGUAGES}
        for name in EXTRAS:
            requirement = [dep.requirement for dep in adapters[name].dependencies if dep.distribution != "tree-sitter"]
            self.assertEqual(1, len(requirement))
            self.assertEqual(requirement, extras[name])
            self.assertIn(requirement[0], extras["all"])
            self.assertIn(requirement[0], (REPO / "requirements" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(EXTRAS), len(extras["all"]))


if __name__ == "__main__":
    unittest.main()
