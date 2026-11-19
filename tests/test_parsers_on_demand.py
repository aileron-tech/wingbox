"""Parsers installed on demand: what a build does without one, where it looks for one, and what the package pins."""
from __future__ import annotations

import contextlib
import fnmatch
import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_code_graph
from scripts.lang import LANGUAGES, ondemand

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "src" / "wingbox" / "assets" / "parsers.json"
ON_DEMAND = ("csharp", "java", "go", "ruby", "php", "dart")
# One source file of each on-demand language: (file, text, display name, grammar distribution).
SOURCES = {
    "csharp": ("storefront/Service.cs", "namespace Shop { public class Service { } }\n", "C#", "tree-sitter-c-sharp"),
    "java": ("storefront/Main.java", "package shop;\npublic class Main { }\n", "Java", "tree-sitter-java"),
    "go": ("storefront/main.go", "package main\n\nfunc main() {}\n", "Go", "tree-sitter-go"),
    "ruby": ("storefront/worker.rb", "class Worker\nend\n", "Ruby", "tree-sitter-ruby"),
    "php": ("storefront/index.php", "<?php\nclass Index {}\n", "PHP and Laravel", "tree-sitter-php"),
    "dart": ("storefront/main.dart", "void main() {}\n", "Dart", "tree-sitter-dart"),
}


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
    def test_a_build_without_the_parser_skips_the_language_and_names_wingbox_add(self) -> None:
        """/* REQ-DEPS-007: the parsers of C#, Java, Go, Ruby, PHP and Dart are installed on demand; a build that meets files of one of them without its parser goes on with the other languages and warns with the number of skipped files and the command `wingbox add <language>`. */"""
        self.assertEqual(ON_DEMAND, ondemand.ON_DEMAND_LANGUAGES)
        for language, (_name, _text, display, distribution) in SOURCES.items():
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                root = _project(directory, language)
                with _without(distribution):
                    status, errors = _build(root)
                self.assertEqual(0, status, errors)
                self.assertIn(f"{display} files are skipped (1 file)", errors)
                self.assertIn(f"wingbox add {language}", errors)
                graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
                self.assertIn("helper", graph)
                self.assertNotIn("storefront", graph)

    def test_a_project_of_one_on_demand_language_alone_stops_with_the_command(self) -> None:
        """/* REQ-DEPS-008: a project whose only source files need a parser that is not installed publishes no graph; the error names `wingbox add <language>` first. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Service.cs").write_text("public class Service { }\n", encoding="utf-8")
            with _without("tree-sitter-c-sharp"):
                status, errors = _build(root)
            self.assertEqual(2, status)
            self.assertIn("wingbox add csharp", errors)
            self.assertFalse((root / ".wingbox" / "index.sexp").exists())

    def test_the_skipped_languages_are_recorded_beside_the_graph_and_cleared_by_a_complete_build(self) -> None:
        """/* REQ-DEPS-009: a build that skips a language writes one fact per language to .wingbox/graph-skipped.sexp, `(skipped-language csharp :files N :add "wingbox add csharp")`; the file is covered by .wingbox/.gitignore and a build that skips nothing removes it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = _project(directory, "csharp")
            (root / "storefront" / "Order.cs").write_text("namespace Shop { public class Order { } }\n", encoding="utf-8")
            record = root / ".wingbox" / build_code_graph.SKIPPED_FILE
            with _without("tree-sitter-c-sharp"):
                status, errors = _build(root)
            self.assertEqual(0, status, errors)
            self.assertEqual(
                '(skipped-language csharp :files 2 :add "wingbox add csharp")\n', record.read_text(encoding="utf-8")
            )
            ignored = [line for line in build_code_graph.GITIGNORE_TEXT.splitlines() if line and not line.startswith("#")]
            self.assertTrue(any(fnmatch.fnmatch(record.name, pattern) for pattern in ignored), ignored)
            status, errors = _build(root)
            self.assertEqual(0, status, errors)
            self.assertFalse(record.exists())
            self.assertIn("storefront", (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"))


class PerUserFolderTest(unittest.TestCase):
    def test_the_folder_is_under_the_home_folder_unless_the_setting_names_another(self) -> None:
        """/* REQ-DEPS-010: parsers added on demand live in ~/.wingbox/parsers (WINGBOX_PARSERS_DIR names another folder), one folder per platform and per package and version: <home>/<platform>/<distribution>-<version>. */"""
        home = os.path.join(os.path.expanduser("~"), ".wingbox", "parsers")
        self.assertEqual(home, ondemand.parsers_home({}))
        self.assertEqual(home, ondemand.parsers_home({"WINGBOX_PARSERS_DIR": ""}))
        other = os.path.abspath(os.path.join(os.sep, "srv", "parsers"))   # with the drive on Windows
        self.assertEqual(other, ondemand.parsers_home({"WINGBOX_PARSERS_DIR": other}))
        self.assertEqual("darwin-arm64", ondemand.platform_key("darwin", "arm64", True))
        self.assertEqual("darwin-x86_64", ondemand.platform_key("darwin", "x86_64", True))
        self.assertEqual("linux-x86_64", ondemand.platform_key("linux", "x86_64", True))
        self.assertEqual("linux-aarch64-musl", ondemand.platform_key("linux", "aarch64", False))
        self.assertEqual("windows-amd64", ondemand.platform_key("win32", "amd64", True))
        self.assertEqual(
            os.path.join(other, "linux-x86_64", "tree-sitter-go-0.25.0"),
            ondemand.parser_folder(other, "linux-x86_64", "tree-sitter-go", "0.25.0"),
        )
        self.assertEqual(ondemand.platform_key(), ondemand.platform_key(*ondemand.this_platform()))

    def _run(self, parsers: Path, environment_folder: Path | None = None) -> str:
        """What a fresh interpreter without site-packages sees after the language registry is imported."""
        script = (
            "import importlib.metadata as metadata\n"
            "import scripts.lang\n"
            "try:\n"
            "    import tree_sitter_c_sharp\n"
            "    print(tree_sitter_c_sharp.MARK, metadata.version('tree-sitter-c-sharp'))\n"
            "except ImportError:\n"
            "    print('absent')\n"
        )
        environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
        environment["WINGBOX_PARSERS_DIR"] = str(parsers)
        if environment_folder is not None:
            environment["PYTHONPATH"] = str(environment_folder)
        done = subprocess.run([sys.executable, "-S", "-B", "-c", script], cwd=REPO, env=environment,
                              capture_output=True, text=True, check=False)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout.strip()

    @staticmethod
    def _unpacked(folder: Path, mark: str, version: str) -> None:
        (folder / "tree_sitter_c_sharp").mkdir(parents=True)
        (folder / "tree_sitter_c_sharp" / "__init__.py").write_text(f"MARK = {mark!r}\n", encoding="utf-8")
        info = folder / f"tree_sitter_c_sharp-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: tree-sitter-c-sharp\nVersion: {version}\n", encoding="utf-8"
        )

    def test_a_build_finds_the_pinned_parser_in_the_per_user_folder_after_the_environment(self) -> None:
        """/* REQ-DEPS-011: a grammar is imported from the environment that runs Wingbox when it is there; otherwise from the per-user folder of exactly the pinned version and this platform, where its version is read too; a folder of another version, another platform or an unfinished download is never importable. */"""
        pinned = next(
            dependency.version for language in LANGUAGES if language.name == "csharp"
            for dependency in language.dependencies if dependency.distribution == "tree-sitter-c-sharp"
        )
        with tempfile.TemporaryDirectory() as directory:
            parsers = Path(directory) / "parsers"
            platform = parsers / ondemand.platform_key()
            self._unpacked(platform / "tree-sitter-c-sharp-0.0.1", "other-version", "0.0.1")
            self._unpacked(platform / ".tmp-abc" / "tree-sitter-c-sharp-" f"{pinned}", "unfinished", pinned)
            self._unpacked(parsers / "another-platform" / f"tree-sitter-c-sharp-{pinned}", "other-platform", pinned)
            self.assertEqual("absent", self._run(parsers))
            self._unpacked(platform / f"tree-sitter-c-sharp-{pinned}", "per-user", pinned)
            self.assertEqual(f"per-user {pinned}", self._run(parsers))
            environment = Path(directory) / "environment"
            self._unpacked(environment, "environment", pinned)
            self.assertEqual(f"environment {pinned}", self._run(parsers, environment))

    def test_a_build_finds_a_parser_added_while_its_process_was_running(self) -> None:
        """/* REQ-DEPS-013: every build looks at the per-user folder again, so a process that lives long (the MCP server, which builds in its own process) uses a parser that `wingbox add` installed after it started: the second build of the same process no longer reports the grammar missing. */"""
        script = (
            "import contextlib, io, os, sys\n"
            "from scripts import build_code_graph\n"
            "root, folder = sys.argv[1], sys.argv[2]\n"
            "def build():\n"
            "    errors = io.StringIO()\n"
            "    with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):\n"
            "        build_code_graph.main(['--root', root])\n"
            "    return 'tree-sitter-c-sharp' in errors.getvalue(), 'C# files are skipped' in errors.getvalue()\n"
            "print(build())\n"
            "os.makedirs(os.path.join(folder, 'tree_sitter_c_sharp-0.23.5.dist-info'))\n"
            "with open(os.path.join(folder, 'tree_sitter_c_sharp-0.23.5.dist-info', 'METADATA'), 'w') as handle:\n"
            "    handle.write('Metadata-Version: 2.1\\nName: tree-sitter-c-sharp\\nVersion: 0.23.5\\n')\n"
            "print(build())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = _project(os.path.join(directory, "project"), "csharp")
            parsers = Path(directory) / "parsers"
            folder = parsers / ondemand.platform_key() / "tree-sitter-c-sharp-0.23.5"
            environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
            environment["WINGBOX_PARSERS_DIR"] = str(parsers)
            done = subprocess.run([sys.executable, "-S", "-B", "-c", script, str(root), str(folder)], cwd=REPO,
                                  env=environment, capture_output=True, text=True, check=False)
            # Without site-packages the parser library itself stays missing, so C# is skipped both times;
            # what changes is that the grammar is found.
            self.assertEqual(["(True, True)", "(False, True)"], done.stdout.split("\n")[:2], done.stderr)


class PinnedCatalogTest(unittest.TestCase):
    def test_the_package_pins_only_the_built_in_parsers_and_the_catalog_agrees_with_the_extras(self) -> None:
        """/* REQ-DEPS-012: the package depends on tree-sitter, the TypeScript and Rust grammars and PyYAML only; every on-demand language has a pip extra and `all` has them all and nothing else, with exact pins; the one other extra is `http`, the transport package; the catalog `wingbox add` downloads from (assets/parsers.json) names the same package and version as the extra, the language adapter and the requirements file, and for each wheel a file name, an https URL on files.pythonhosted.org and a sha256. */"""
        project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(
            ["PyYAML", "tree-sitter", "tree-sitter-rust", "tree-sitter-typescript"],
            sorted(requirement.split("==")[0] for requirement in project["dependencies"]),
        )
        self.assertTrue(all("==" in requirement for requirement in project["dependencies"]))
        extras = project["optional-dependencies"]
        # `http` is the one extra that is not a parser: the HTTP transport of the MCP server, a distribution
        # of its own (REQ-HTTP-021). `all` means all parsers and does not install it.
        self.assertEqual(sorted(ON_DEMAND + ("all", "http")), sorted(extras))
        self.assertEqual(len(ON_DEMAND), len(extras["all"]))
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(sorted(ON_DEMAND), sorted(catalog["languages"]))
        adapters = {language.name: language for language in LANGUAGES}
        for name in ON_DEMAND:
            entry = catalog["languages"][name]
            requirement = f"{entry['package']}=={entry['version']}"
            self.assertEqual([requirement], extras[name])
            self.assertIn(requirement, extras["all"])
            self.assertEqual(
                [requirement],
                [dep.requirement for dep in adapters[name].dependencies if dep.distribution != "tree-sitter"],
            )
            self.assertIn(requirement, (REPO / "requirements" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
            self.assertEqual(entry["package"].replace("-", "_"), entry["module"])
            self.assertTrue(entry["wheels"])
            for wheel in entry["wheels"]:
                self.assertTrue(wheel["filename"].startswith(f"{entry['module']}-{entry['version']}-"), wheel)
                self.assertTrue(wheel["filename"].endswith(".whl"), wheel)
                self.assertTrue(wheel["url"].startswith("https://files.pythonhosted.org/packages/"), wheel)
                self.assertTrue(wheel["url"].endswith("/" + wheel["filename"]), wheel)
                self.assertRegex(wheel["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(wheel["size"], 0)
        self.assertEqual(len(ON_DEMAND), len(extras["all"]))


if __name__ == "__main__":
    unittest.main()
