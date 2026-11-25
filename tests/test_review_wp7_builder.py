"""The builder after the review of 0.19.1: a changed resolution config never leaves old answers behind, one
file that cannot be parsed does not stop the build, and nothing read from a repository adds a line to the graph."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

builder = cli.load_script("build_code_graph")
graph_update = cli.load_script("graph_update")

_HOME = tempfile.TemporaryDirectory()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build(root: Path, *extra: str) -> tuple[int, str]:
    """(exit status, everything printed) of a build of root, in this process."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        status = builder.main(["--root", str(root), *extra])
    return status, output.getvalue()


def _query(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`wingbox <args>` in root, as a person runs it; the graph is read as it is (no refresh before the answer)."""
    environment = dict(os.environ, HOME=_HOME.name, USERPROFILE=_HOME.name, WINGBOX_AUTO_REFRESH="0", WINGBOX_JOBS="1")
    environment.pop("WINGBOX_POSITIONS_FILL", None)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root),
                          timeout=300, text=True)


def _answer(root: Path, *args: str) -> list[str]:
    """The lines of an answer, without the fact that says when the graph was built."""
    return [line for line in _query(root, *args).stdout.splitlines() if not line.startswith("(graph ")]


def _graph(root: Path) -> str:
    return (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")


def _published(root: Path) -> tuple[str, list[str]]:
    """The graph and the lines of its use file (without the header, which carries the key of the build)."""
    uses = (root / ".wingbox" / "index.uses.sexp").read_text(encoding="utf-8").splitlines()
    return _graph(root), [line for line in uses if not line.startswith("(code-graph-uses ")]


class ProjectTestCase(unittest.TestCase):
    def folder(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(os.path.realpath(tmp.name))

    def project(self, files: dict[str, str]) -> Path:
        """A repository named `project` with these files."""
        root = self.folder() / "project"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        _write(root, files)
        return root

    def from_nothing(self, root: Path, rewrite=None) -> Path:
        """A copy of the tree of root under the same folder name, with no .wingbox/ of an earlier build, built once.
        rewrite(copy) gives the files that hold absolute paths, written for the copy."""
        copy = self.folder() / root.name
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".wingbox", ".git"))
        subprocess.run(["git", "init", "-q", str(copy)], check=True)
        _write(copy, rewrite(copy) if rewrite else {})
        self.assertEqual(0, _build(copy)[0])
        return copy


def _alias(folder: str) -> str:
    return json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": [f"src/{folder}/*"]}}})


TYPESCRIPT_ALIAS = {
    "tsconfig.json": _alias("libA"),
    "src/libA/util.ts": "export function foo(): number {\n  return 1;\n}\n",
    "src/libB/util.ts": "export function foo(): number {\n  return 2;\n}\n",
    "src/main.ts": 'import { foo } from "@lib/util";\n\nexport function run(): number {\n  return foo();\n}\n',
    "src/other.ts": "export const unrelated = 1;\n",
}
SERVICE = "<?php\nnamespace App;\n\nclass Service\n{\n    public function run(): int { return %d; }\n}\n"
COMPOSER = {
    "composer.json": json.dumps({"autoload": {"psr-4": {"App\\": "src/"}}}),
    "src/Service.php": SERVICE % 1,
    "lib/Service.php": SERVICE % 2,
    "public/index.php": "<?php\nuse App\\Service;\n\n$service = new Service();\n",
    "tools/other.py": "UNRELATED = 1\n",
}
CPP = {
    "include/a/util.h": "#pragma once\nint util_value();\n",
    "include/b/util.h": "#pragma once\nint util_value();\n",
    "src/main.cpp": '#include "util.h"\n\nint main() {\n  return util_value();\n}\n',
    "tools/other.py": "UNRELATED = 1\n",
}


def _compile_commands(root: Path, folder: str) -> str:
    return json.dumps([{"directory": str(root), "file": str(root / "src/main.cpp"),
                        "arguments": ["c++", f"-I{root / 'include' / folder}", "-c", "src/main.cpp"]}])


class ResolutionConfigChangeTest(ProjectTestCase):
    """A file that decides how imports resolve changed: every later build and partial update publishes the graph
    a build from nothing publishes."""

    def patch(self, root: Path, changed: list[str]) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = graph_update.update_graph(str(root), changed, str(root / ".wingbox" / "graph-fragments.json"))
        self.assertIsInstance(result, graph_update.Updated, result)

    def test_a_remapped_typescript_alias_stays_remapped_after_an_unrelated_edit(self) -> None:
        """/* REQ-FRESH-9700: after tsconfig.json maps an alias to another folder, the incremental build publishes the graph of a build from nothing, and so does the partial update of a later edit of an unrelated file; `usages` of the newly aliased definition gives the same answer in both. */"""
        root = self.project(TYPESCRIPT_ALIAS)
        self.assertEqual(0, _build(root)[0])
        _write(root, {"tsconfig.json": _alias("libB")})
        self.assertEqual(0, _build(root)[0])
        self.assertEqual(_published(self.from_nothing(root)), _published(root))
        _write(root, {"src/other.ts": "export const unrelated = 2;\nexport function extra(): number {\n  return 3;\n}\n"})
        self.patch(root, ["src/other.ts"])
        fresh = self.from_nothing(root)
        self.assertEqual(_published(fresh), _published(root))
        self.assertIn("src/main.ts", "\n".join(_answer(root, "impact", "src/libB/util.ts")))
        expected = _answer(fresh, "usages", "src/libB/util.ts::foo")
        self.assertIn("src/main.ts", "\n".join(expected))
        self.assertEqual(expected, _answer(root, "usages", "src/libB/util.ts::foo"))
        self.assertEqual(_answer(fresh, "usages", "src/libA/util.ts::foo"), _answer(root, "usages", "src/libA/util.ts::foo"))

    def test_usages_asked_before_the_alias_moved_are_not_answered_again(self) -> None:
        """/* REQ-FRESH-9701: `usages` asked before a tsconfig.json change fills the use file and the walk cache; after the change and a build, `usages` of the old and of the new target answer as after a build from nothing. */"""
        root = self.project(TYPESCRIPT_ALIAS)
        self.assertEqual(0, _build(root)[0])
        self.assertIn("src/main.ts", "\n".join(_answer(root, "usages", "src/libA/util.ts::foo")))
        _write(root, {"tsconfig.json": _alias("libB")})
        self.assertEqual(0, _build(root)[0])
        fresh = self.from_nothing(root)
        self.assertEqual(_graph(fresh), _graph(root))
        for target in ("src/libA/util.ts::foo", "src/libB/util.ts::foo"):
            self.assertEqual(_answer(fresh, "usages", target), _answer(root, "usages", target), target)

    def test_a_moved_composer_namespace_root_stays_moved_after_an_unrelated_edit(self) -> None:
        """/* REQ-FRESH-9702: after composer.json moves a PSR-4 namespace to another folder, the incremental build and the partial update of a later edit of an unrelated file publish the graph of a build from nothing, with the same `usages` answer. */"""
        root = self.project(COMPOSER)
        self.assertEqual(0, _build(root)[0])
        _write(root, {"composer.json": json.dumps({"autoload": {"psr-4": {"App\\": "lib/"}}})})
        self.assertEqual(0, _build(root)[0])
        self.assertEqual(_published(self.from_nothing(root)), _published(root))
        _write(root, {"tools/other.py": "UNRELATED = 2\n\n\ndef extra():\n    return 3\n"})
        self.patch(root, ["tools/other.py"])
        fresh = self.from_nothing(root)
        self.assertEqual(_published(fresh), _published(root))
        self.assertIn("public/index.php", "\n".join(_answer(root, "impact", "lib/Service.php")))
        self.assertNotIn("public/index.php", "\n".join(_answer(root, "impact", "src/Service.php")))
        self.assertEqual(_answer(fresh, "usages", "lib/Service.php::Service"), _answer(root, "usages", "lib/Service.php::Service"))

    def test_another_include_folder_of_the_compilation_database_stays_after_an_unrelated_edit(self) -> None:
        """/* REQ-FRESH-9703: after compile_commands.json names another include folder, the incremental build and the partial update of a later edit of an unrelated file publish the graph of a build from nothing, with the same `usages` answer. */"""
        root = self.project(CPP)
        _write(root, {"compile_commands.json": _compile_commands(root, "a")})
        self.assertEqual(0, _build(root)[0])
        self.assertIn("src/main.cpp", "\n".join(_answer(root, "usages", "include/a/util.h::util_value")))
        _write(root, {"compile_commands.json": _compile_commands(root, "b")})
        self.assertEqual(0, _build(root)[0])
        database = lambda copy: {"compile_commands.json": _compile_commands(copy, "b")}  # noqa: E731
        self.assertEqual(_published(self.from_nothing(root, database)), _published(root))
        _write(root, {"tools/other.py": "UNRELATED = 2\n\n\ndef extra():\n    return 3\n"})
        self.patch(root, ["tools/other.py"])
        fresh = self.from_nothing(root, database)
        self.assertEqual(_published(fresh), _published(root))
        expected = _answer(fresh, "usages", "include/b/util.h::util_value")
        self.assertIn("src/main.cpp", "\n".join(expected))
        self.assertEqual(expected, _answer(root, "usages", "include/b/util.h::util_value"))


if __name__ == "__main__":
    unittest.main()
