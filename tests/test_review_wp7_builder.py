"""The builder after the review of 0.19.1: a changed resolution config never leaves old answers behind, one
file that cannot be parsed does not stop the build, and nothing read from a repository adds a line to the graph."""
import contextlib
import io
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

builder = cli.load_script("build_code_graph")
graph_update = cli.load_script("graph_update")
search = cli.load_script("code_graph_search")
source_kinds = cli.load_script("source_kinds")
infra = cli.load_script("build_infra_topology")

_HOME = tempfile.TemporaryDirectory()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build(root: Path, *extra: str) -> tuple[int, str]:
    """(exit status, everything printed) of a build of root, in this process and without worker processes."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
            mock.patch.dict(os.environ, {builder.portable.JOBS_VARIABLE: "1"}):
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
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".wingbox", ".git"), symlinks=True)
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
        """/* REQ-FRESH-058: after tsconfig.json maps an alias to another folder, the incremental build publishes the graph of a build from nothing, and so does the partial update of a later edit of an unrelated file; `usages` of the newly aliased definition gives the same answer in both. */"""
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
        """/* REQ-FRESH-059: `usages` asked before a tsconfig.json change fills the use file and the walk cache; after the change and a build, `usages` of the old and of the new target answer as after a build from nothing. */"""
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
        """/* REQ-FRESH-060: after composer.json moves a PSR-4 namespace to another folder, the incremental build and the partial update of a later edit of an unrelated file publish the graph of a build from nothing, with the same `usages` answer. */"""
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
        """/* REQ-FRESH-061: after compile_commands.json names another include folder, the incremental build and the partial update of a later edit of an unrelated file publish the graph of a build from nothing, with the same `usages` answer. */"""
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


def _unparsed(root: Path) -> str:
    path = root / ".wingbox" / "graph-unparsed.sexp"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _failing_on(name: str, error: BaseException):
    """parse_file that raises error for the file called name and parses every other file."""
    real = builder.parse_file

    def parse(args):
        if os.path.basename(args[0]) == name:
            raise error
        return real(args)

    return parse


SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def order_total(items):\n    return sum(items)\n",
    "shop/cart.py": "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items)\n",
    "shop/generated.py": "TABLE = 1\n",
}


class OneFileCannotStopTheBuildTest(ProjectTestCase):
    """A file that cannot be parsed is left out and recorded; every other file is mapped."""

    def test_a_parser_that_raises_on_one_file_leaves_that_file_out(self) -> None:
        """/* REQ-CS-220: when the parser raises on one file (RecursionError), the build ends with status 0 and publishes the graph of the other files; it prints one notice with the number of files left out, the file and the error; `.wingbox/graph-unparsed.sexp` holds `(not-parsed-file "<file>" :reason parser-failed)`; the file is not in the build cache; `impact` of it answers `(error not-in-graph <file> :reason parser-failed :language "Python")` with status 1, and `--human` says the parser failed. */"""
        root = self.project(SHOP)
        failing = _failing_on("generated.py", RecursionError("maximum recursion depth exceeded"))
        with mock.patch.object(builder, "parse_file", failing):
            status, output = _build(root)
        self.assertEqual(0, status, output)
        notices = [line for line in output.splitlines() if "left out of the graph" in line]
        self.assertEqual(1, len(notices), output)
        self.assertIn("WARNING: 1 source file left out of the graph: shop/generated.py (parser-failed: RecursionError", notices[0])
        self.assertNotIn("Traceback", output)
        self.assertEqual('(not-parsed-file "shop/generated.py" :reason parser-failed)\n', _unparsed(root))
        graph = _graph(root)
        self.assertIn(":nodes 3 ", graph)
        self.assertRegex(graph, r"\(imports \S+ \S+ :line 1\)")
        self.assertNotIn("generated", graph)
        cache = json.loads((root / ".wingbox" / "cache.json").read_text(encoding="utf-8"))
        self.assertEqual(3, len(cache["entries"]))
        answer = _query(root, "impact", "shop/generated.py")
        self.assertEqual(1, answer.returncode)
        self.assertIn('(error not-in-graph shop/generated.py :reason parser-failed :language "Python")', answer.stdout)
        self.assertIn("shop/generated.py is not in the graph: the Python parser failed on it",
                      _query(root, "impact", "shop/generated.py", "--human").stdout)
        self.assertIn("shop/cart.py", _query(root, "impact", "shop/pricing.py").stdout)

    def test_long_expressions_of_generated_code_never_end_the_build(self) -> None:
        """/* REQ-CS-221: a Python file with a `+` chain of 1,000 terms and a Go file with a string concatenation of 1,200 lines do not end the build: status 0, no traceback, the other files are in the graph, and a question about each of the two files is answered either from the graph or with `not-in-graph ... :reason parser-failed`. */"""
        chain = "TOTAL = " + " + ".join(str(number) for number in range(1000)) + "\n"
        concatenation = "package api\n\nvar descriptor = \"\" +\n" + "".join(f'\t"line {number}" +\n' for number in range(1200)) + '\t""\n'
        root = self.project({
            **SHOP,
            "shop/long.py": chain,
            "go.mod": "module example.test/depot\n\ngo 1.22\n",
            "main.go": "package main\n\nfunc main() {}\n",
            "api/svc.pb.go": concatenation,
        })
        status, output = _build(root)
        self.assertEqual(0, status, output[-2000:])
        self.assertNotIn("Traceback", output)
        self.assertIn("shop/cart.py", _query(root, "impact", "shop/pricing.py").stdout)
        left_out = source_kinds.read_unparsed_files(_unparsed(root))
        for name, language in (("shop/long.py", "Python"), ("api/svc.pb.go", "Go")):
            with self.subTest(file=name):
                answer = _query(root, "impact", name).stdout
                if name in left_out:
                    self.assertEqual("parser-failed", left_out[name])
                    self.assertIn(f'(error not-in-graph {name} :reason parser-failed :language "{language}")', answer)
                else:
                    self.assertIn(f"(impact {name} ", answer)

    def test_a_file_that_cannot_be_read_is_left_out(self) -> None:
        """/* REQ-CS-222: a source file that cannot be opened is left out with `:reason unreadable`; the build ends with status 0 and maps the other files. */"""
        root = self.project(SHOP)
        failing = _failing_on("generated.py", PermissionError(13, "Permission denied"))
        with mock.patch.object(builder, "parse_file", failing):
            status, output = _build(root)
        self.assertEqual(0, status, output)
        self.assertEqual('(not-parsed-file "shop/generated.py" :reason unreadable)\n', _unparsed(root))
        self.assertIn(":nodes 3 ", _graph(root))

    def test_the_record_follows_the_next_build_and_no_patch_touches_such_a_file(self) -> None:
        """/* REQ-FRESH-062: a change of a file the build left out is never patched into the graph (the partial update does not apply, the build decides again), a changed file that cannot be parsed now is not patched in either, and a build that parses every file removes the record. */"""
        root = self.project(SHOP)
        failing = _failing_on("generated.py", RecursionError("maximum recursion depth exceeded"))
        with mock.patch.object(builder, "parse_file", failing):
            self.assertEqual(0, _build(root)[0])
            store = str(root / ".wingbox" / "graph-fragments.json")
            _write(root, {"shop/generated.py": "TABLE = 2\n"})
            self.assertIsInstance(graph_update.update_graph(str(root), ["shop/generated.py"], store), graph_update.NotApplicable)
        with mock.patch.object(builder, "parse_file", _failing_on("cart.py", RecursionError("deep"))):
            _write(root, {"shop/cart.py": "from shop.pricing import order_total\n\nTOTAL = order_total([1])\n"})
            self.assertIsInstance(graph_update.update_graph(str(root), ["shop/cart.py"], store), graph_update.NotApplicable)
        self.assertEqual(0, _build(root)[0])
        self.assertFalse((root / ".wingbox" / "graph-unparsed.sexp").exists())
        self.assertIn(":nodes 4 ", _graph(root))

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(),
                         "the stand-in parser reaches the workers only through fork")
    def test_a_worker_process_that_dies_loses_only_its_file(self) -> None:
        """/* REQ-CS-223: when the process that parses one file dies, the files of the other processes and the files not parsed yet are parsed all the same, and only that file comes back as left out (`parser-failed`, the process that parsed it died). */"""
        root = self.project({f"pkg/m{number}.py": f"VALUE = {number}\n" for number in range(8)} | {"pkg/dies.py": "VALUE = 0\n"})
        parent = os.getpid()
        real = builder.parse_file
        items = [(str(path), "python", "project", str(root)) for path in sorted(root.rglob("*.py"))]
        # It dies after the other files were answered, and at once (it is the first file handed out).
        for wait in (0.5, 0.0):
            with self.subTest(wait=wait):
                def parse(args, wait=wait):
                    if os.path.basename(args[0]) == "dies.py":
                        if os.getpid() == parent:
                            raise AssertionError("dies.py must be parsed in a worker process")
                        time.sleep(wait)
                        os._exit(7)
                    return real(args)

                output = io.StringIO()
                with mock.patch.object(builder, "parse_file", parse), contextlib.redirect_stderr(output), \
                        mock.patch.dict(os.environ, {builder.portable.JOBS_VARIABLE: "3", "WINGBOX_START_METHOD": "fork"}):
                    outcomes = builder.parse_files(items)
                parsed = sorted(os.path.basename(item.path) for item in outcomes if isinstance(item, builder.FileResult))
                left_out = [item for item in outcomes if isinstance(item, builder.UnparsedFile)]
                self.assertEqual([f"m{number}.py" for number in range(8)], parsed, output.getvalue())
                self.assertEqual([("dies.py", "parser-failed")], [(os.path.basename(item.path), item.reason) for item in left_out])
                self.assertIn("died", left_out[0].detail)

    def test_a_manifest_that_is_not_utf8_is_named_and_the_build_goes_on(self) -> None:
        """/* REQ-CS-224: a nested composer.json with a byte that is not UTF-8 does not end the build: status 0, a warning that names the file, and the imports of the other manifest are resolved. */"""
        root = self.project({
            "composer.json": json.dumps({"autoload": {"psr-4": {"App\\": "app/"}}}),
            "app/Service.php": SERVICE % 1,
            "public/index.php": "<?php\nuse App\\Service;\n\n$service = new Service();\n",
        })
        (root / "packages" / "old").mkdir(parents=True)
        (root / "packages" / "old" / "composer.json").write_bytes(b'{"name": "caf\xe9/old", "autoload": {"psr-4": {"Old\\\\": "src/"}}}\n')
        status, output = _build(root)
        self.assertEqual(0, status, output[-2000:])
        self.assertNotIn("Traceback", output)
        self.assertIn("packages/old/composer.json", output.replace("\\", "/"))   # a path as the system writes it
        self.assertIn("public/index.php", _query(root, "impact", "app/Service.php").stdout)

    def test_binding_goes_on_without_the_manifest_its_reader_fails_on(self) -> None:
        """/* REQ-CS-225: when binding a language raises while it reads a manifest, the manifest is named in one warning and the language is bound again with that file excluded; an error that names no manifest is raised as it is. */"""
        root = self.folder()
        manifest = root / "packages" / "old" / "composer.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("[]\n", encoding="utf-8")

        class Reader:
            display_name = "PHP"
            calls = 0

            def bind(self, root, path_to_id, exclusions):
                Reader.calls += 1
                path = str(manifest)
                if os.path.normcase(os.path.realpath(path)) not in exclusions.paths:
                    raise AttributeError("'list' object has no attribute 'get'")
                return "bound"

        class Broken:
            display_name = "PHP"

            def bind(self, root, path_to_id, exclusions):
                raise ValueError("no manifest in sight")

        exclusions = builder.DiscoveryExclusions(directory_names=frozenset(), paths=frozenset())
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual("bound", builder.bind_language(Reader(), str(root), {}, exclusions))
            with self.assertRaises(ValueError):
                builder.bind_language(Broken(), str(root), {}, exclusions)
        self.assertEqual(2, Reader.calls)
        self.assertEqual(1, output.getvalue().count("WARNING"))
        self.assertIn("cannot read packages/old/composer.json (AttributeError", output.getvalue())


class BinaryContentTest(ProjectTestCase):
    def test_binary_content_under_a_source_extension_is_not_parsed(self) -> None:
        """/* REQ-CS-226: a file whose first 8 KB hold a NUL byte is not parsed, whatever its extension: the build leaves it out with `:reason binary`, and `impact` of it answers `(error not-in-graph <file> :reason binary :language "TypeScript")`. A text file is parsed whatever its size, and so is a file whose first NUL byte comes after 8 KB. */"""
        root = self.project({
            "src/a.ts": "export function a(): number { return 1; }\n",
            "src/late.ts": "// " + "x" * 9000 + "\nexport const late = '\\0';\n".replace("\\0", "\0"),
            "big/table.py": "# padding\n" * 150_000 + "def big():\n    return 1\n",
        })
        (root / "assets").mkdir()
        (root / "assets" / "blob.ts").write_bytes(bytes(range(256)) * 4096)
        (root / "assets" / "wide.cpp").write_bytes("int main() { return 0; }\n".encode("utf-16"))
        parsed: list[str] = []
        real = builder.parse_file

        def parse(args):
            parsed.append(os.path.basename(args[0]))
            return real(args)

        with mock.patch.object(builder, "parse_file", parse):
            status, output = _build(root)
        self.assertEqual(0, status, output)
        self.assertEqual(["a.ts", "late.ts", "table.py"], sorted(parsed))
        self.assertEqual(
            '(not-parsed-file "assets/blob.ts" :reason binary)\n(not-parsed-file "assets/wide.cpp" :reason binary)\n',
            _unparsed(root),
        )
        self.assertIn("2 source files left out of the graph: assets/blob.ts (binary: ", output)
        graph = _graph(root)
        self.assertIn(":nodes 3 ", graph)
        self.assertIn("(fn ", graph)
        self.assertNotIn("blob", graph)
        self.assertGreater((root / "big" / "table.py").stat().st_size, 1_000_000)
        self.assertRegex(graph, r"\(fn \S+ big 150001 ")
        answer = _query(root, "impact", "assets/blob.ts")
        self.assertEqual(1, answer.returncode)
        self.assertIn('(error not-in-graph assets/blob.ts :reason binary :language "TypeScript")', answer.stdout)


FORGED = '(imports pr2 pr1)'


class NoForgedFactTest(ProjectTestCase):
    """Nothing read from a repository becomes a line of its own in a file of the graph."""

    def test_a_member_name_with_a_line_break_adds_no_fact(self) -> None:
        """/* REQ-SEC-017: a TypeScript member name that holds line breaks and the text of a fact adds no fact to the graph: no line of `.wingbox/index.sexp` starts with the forged text, the graph loads, `impact` of the file the forged fact names lists no dependent, and `analyze` of the file with the name answers. */"""
        root = self.project({
            "src/attack.ts": 'export const table = {\n  [`x\n' + FORGED + '\n(node evil ts "forged")\n`]: 1,\n};\n',
            "src/billing.ts": "export function bill() { return 1; }\n",
            "src/zeta.ts": "export function zeta() { return 2; }\n",
        })
        self.assertEqual(0, _build(root)[0])
        lines = _graph(root).split("\n")
        self.assertEqual([], [line for line in lines if line.startswith((FORGED, "(node evil"))])
        self.assertEqual(lines, _graph(root).splitlines() + [""])
        answer = _query(root, "impact", "src/billing.ts")
        self.assertEqual(0, answer.returncode, answer.stdout + answer.stderr)
        self.assertIn("(impact src/billing.ts :direct 0 :total 0", answer.stdout)
        self.assertNotIn("invalid-map", _query(root, "analyze", "src/attack.ts").stdout)

    def test_no_name_puts_a_control_character_into_a_written_line(self) -> None:
        """/* REQ-SEC-018: whatever control character or line separator (line feed, carriage return, vertical tab, NEL, U+2028, U+2029, NUL) stands in a module path, a purpose, an entity name, a base, a decorator, a method or a variable name, the graph file and the use file hold one fact per line with the character written as an escape, and the reader loads the graph with every node. */"""
        breaks = ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029", "\x00", "\t")
        nodes = []
        for number, character in enumerate(breaks):
            forged = f"x{character}(node evil{number} py \"forged\"){character}y"
            nodes.append({
                "id": f"pkg/m{number}{character}(node odd{number} py \"forged\")",
                "language": "python", "path": f"pkg/m{number}.py", "repo": "project", "purpose": forged,
                "invariant": {"naming": forged, "package": "project"},
                "entities": [
                    {"kind": "class", "name": forged, "line": 1, "bases": [forged], "decorators": [forged], "methods": [forged]},
                    {"kind": "variable", "name": forged, "line": 2},
                ],
                "uses": [(1, 1, "pkg/other", forged, "call")],
            })
        text = builder.render_sexp(nodes, [], "/repository", "syntax", len(nodes))
        uses = builder.render_uses_sexp(nodes, "key", text, positions=True)
        for content in (text, uses):
            self.assertEqual(content.split("\n"), content.splitlines() + [""])
            self.assertFalse(builder.has_control_character(content.replace("\n", "")))
            self.assertEqual([], [line for line in content.split("\n") if line.startswith("(node evil") or line.startswith("(node odd")])
        self.assertEqual(len(nodes), sum(1 for line in text.split("\n") if line.startswith("(node ")))
        self.assertIn('\\n(node odd0 py \\"forged\\")")', text)
        folder = self.folder() / ".wingbox"
        folder.mkdir()
        (folder / "index.sexp").write_text(text, encoding="utf-8")
        graph = search.CodeGraph()
        self.assertIsInstance(graph.load(str(folder / "index.sexp")), search.MapLoadOk)
        self.assertEqual(len(nodes), len(graph.nodes))

    @unittest.skipIf(os.name == "nt", "a file name cannot hold a line break on Windows")
    def test_a_file_whose_name_holds_a_line_break_is_left_out_with_a_notice(self) -> None:
        """/* REQ-SEC-019: a source file whose name holds a line break is left out of the graph with a notice and a `(not-parsed-file ... :reason file-name)` fact that stays on one line; the graph holds no forged line and answers for the other files; a question about the file answers `not-in-graph ... :reason file-name`; and such a file created later is not patched into the graph. */"""
        root = self.project(SHOP)
        odd = 'shop/ev\n(node evil py "forged")\n.py'
        _write(root, {odd: "from shop.pricing import order_total\n"})
        status, output = _build(root)
        self.assertEqual(0, status, output)
        self.assertIn("1 source file left out of the graph: shop/ev\\n(node evil py", output)
        record = _unparsed(root)
        self.assertEqual(1, len(record.splitlines()), record)
        self.assertEqual({odd: "file-name"}, source_kinds.read_unparsed_files(record))
        graph = _graph(root)
        self.assertNotIn("evil", graph)
        self.assertIn(":nodes 4 ", graph)
        self.assertIn("shop/cart.py", _query(root, "impact", "shop/pricing.py").stdout)
        self.assertIn(":reason file-name", _query(root, "impact", odd).stdout)
        later = 'shop/new\n(node late py "forged")\n.py'
        _write(root, {later: "X = 1\n"})
        store = str(root / ".wingbox" / "graph-fragments.json")
        self.assertIsInstance(graph_update.update_graph(str(root), [later], store), graph_update.NotApplicable)


class ShortIdentifierTest(ProjectTestCase):
    def test_two_repositories_never_share_a_short_identifier(self) -> None:
        """/* REQ-CS-227: the short IDs of two repositories of a workspace never coincide (`data-bridge` with 22 modules next to `db`): the build publishes a graph that loads, and `impact` of a module of `db` lists its importer. */"""
        files = {f"data-bridge/m{number:02d}.py": f"def f{number}():\n    return {number}\n" for number in range(22)}
        files["db/core.py"] = "def query():\n    return 1\n"
        files["db/user.py"] = "from db.core import query\n\n\ndef use():\n    return query()\n"
        root = self.project(files)
        (root / "data-bridge" / ".git").mkdir()
        (root / "db" / ".git").mkdir()
        self.assertEqual(0, _build(root)[0])
        identifiers = re.findall(r"^\(node (\S+) ", _graph(root), re.M)
        self.assertEqual(24, len(identifiers))
        self.assertEqual(24, len(set(identifiers)))
        answer = _query(root, "impact", "db/core.py")
        self.assertNotIn("invalid-map", answer.stdout)
        self.assertIn("db/user.py", answer.stdout)
        self.assertIn("(impact data-bridge/m20.py :direct 0 :total 0", _query(root, "impact", "data-bridge/m20.py").stdout)

    def test_a_prefix_never_ends_in_a_digit_and_is_an_atom(self) -> None:
        """/* REQ-CS-228: whatever the repositories are named (a name that ends in a digit, that is the prefix of another, that holds brackets, quotes, spaces or a colon, an empty name), every module has a short ID of its own, made of a prefix that does not end in a digit and a number, and the prefix is an atom; a name that had a usable prefix keeps it (`project` pr, `agent-hub` ah, `proto` next to `project` pro). */"""
        names = ["db", "data-bridge", "d-b", "db2", "db20", "s3", "s", "s30", "shop (copy)", "a b", '"q"', ":k", "", "42",
                 "4", "((", "x;y", "back\\slash", "project", "proto", "pr", "agent-hub", "a\nb"]
        nodes = [{"id": f"{index:02d}/m{number:03d}", "repo": name} for index, name in enumerate(names) for number in range(31)]
        _ordered, identifiers = builder._path_ids(nodes)
        self.assertEqual(len(nodes), len(set(identifiers.values())))
        for identifier in identifiers.values():
            prefix = identifier.rstrip("0123456789")
            self.assertTrue(prefix and len(prefix) < len(identifier), identifier)
            self.assertTrue(all(character.isalnum() or character in "_.@+-" for character in prefix), identifier)
        self.assertEqual("pr", builder._repo_prefix("project", []))
        self.assertEqual("ah", builder._repo_prefix("agent-hub", []))
        self.assertEqual("pro", builder._repo_prefix("proto", ["pr"]))
        self.assertEqual("dba", builder._repo_prefix("db", ["db"]))
        self.assertEqual("sa", builder._repo_prefix("shop (copy)", []))


class EdgeCountTest(ProjectTestCase):
    def test_the_header_counts_the_edge_lines_of_the_file(self) -> None:
        """/* REQ-CS-229: `:edges N` of the graph header is the number of fact lines of the edges section (imports, calls-api, data-flow, refs, calls, http-calls, registry-wires, generated, runs), which is the number of edges of those kinds the reader loads; `:violations` is the number of violation lines. */"""
        root = self.project({
            **SHOP,
            "shop/ports.py": "from abc import ABC, abstractmethod\n\n\nclass Store(ABC):\n    @abstractmethod\n    def save(self, item): ...\n",
            "shop/store.py": "from shop.ports import Store\n\n\nclass MemoryStore(Store):\n    def save(self, item):\n        return item\n",
            "web/src/util.ts": "export function fmt(n: number): string { return String(n); }\n",
            "web/src/index.ts": 'export * from "./util";\n',
            "web/src/app.ts": 'import { fmt } from "./index";\nexport const run = () => fmt(1);\n',
        })
        self.assertEqual(0, _build(root)[0])
        text = _graph(root)
        declared = int(re.search(r"^\(code-graph .*:edges (\d+) ", text, re.M).group(1))
        kinds = "imports|calls-api|data-flow|refs|calls|http-calls|registry-wires|generated|runs"
        written = re.findall(rf"^\(({kinds}) ", text, re.M)
        self.assertGreater(declared, 3)
        self.assertGreater(len(set(written)), 1)
        self.assertEqual(declared, len(written))
        graph = search.CodeGraph()
        self.assertIsInstance(graph.load(str(root / ".wingbox" / "index.sexp")), search.MapLoadOk)
        self.assertEqual(declared, sum(1 for edge in graph.all_edges if edge.etype != "violation"))
        self.assertEqual(int(re.search(r":violations (\d+)\)", text).group(1)), len(re.findall(r"^\(violation ", text, re.M)))


DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: %s
spec:
  template:
    spec:
      containers:
        - name: main
          image: registry.example.invalid/team/%s:1.2
          ports:
            - containerPort: %s
"""
INFRA_FACT = re.compile(r"\((infra-node|helm-depends|k8s-depends|k8s-service|tf-depends|infra-maps) ")


class InfraTopologyTest(ProjectTestCase):
    def run_infra(self, *arguments: str) -> str:
        """Everything `wingbox infra <arguments>` prints."""
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["wingbox infra", *arguments]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            infra.main()
        return output.getvalue()

    def test_a_name_with_a_line_break_adds_no_fact_to_the_graph(self) -> None:
        """/* REQ-SEC-020: a chart name or a dependency name of a Chart.yaml, a workload name, an image, a label or a namespace that holds a line break, a bracket or another control character adds no line to the output of `wingbox infra`: with `--append` every appended line is an infra fact, a comment or empty, IDs and edge ends that are no plain atoms are quoted, and the graph still loads. */"""
        root = self.project(SHOP)
        _write(root, {
            "charts/app/Chart.yaml": 'name: "web)\\n(imports pr1 pr0)\\n(infra-node infra/x"\nversion: 1.0.0\ndependencies:\n'
                                     '  - name: "db)\\r(violation boundary pr0 pr1 forged)\\u2028(helm-depends infra/a infra/b"\n',
            "charts/app/values.yaml": 'image:\n  repository: "registry.example.invalid/web\\n(imports pr2 pr0)"\n  tag: "1\\x0b2"\n',
            "k8s/kustomization.yaml": "resources:\n  - api\n",
            "k8s/api/deployment.yaml": 'kind: Deployment\nmetadata:\n  name: "api\\n(imports pr3 pr0)"\n  labels:\n'
                                       '    app.kubernetes.io/component: "back\\n(imports pr3 pr1)"\nspec:\n  template:\n    spec:\n'
                                       '      containers:\n        - name: main\n          image: "team/api\\n(imports pr3 pr2):1"\n',
            "k8s/api/service.yaml": 'kind: Service\nmetadata:\n  namespace: "prod\\n(imports pr0 pr3)"\nspec:\n  ports:\n    - port: 80\n',
        })
        self.assertEqual(0, _build(root)[0])
        graph_path = root / ".wingbox" / "index.sexp"
        before = _graph(root)
        printed = self.run_infra(str(root), "--append", str(graph_path))
        self.assertIn("[infra-topology] Done: 2 nodes", printed)
        appended = _graph(root)[len(before):]
        self.assertEqual(appended.split("\n"), appended.splitlines() + [""])
        for line in appended.split("\n"):
            self.assertTrue(not line or line.startswith(";") or INFRA_FACT.match(line), line)
        self.assertIn('(infra-node "infra/web)\\n(imports pr1 pr0)\\n(infra-node infra/x" :kind helm-chart', appended)
        self.assertIn('(helm-depends "infra/web)', appended)
        self.assertIn("\\r(violation boundary pr0 pr1 forged)\\u2028(helm-depends infra/a infra/b\" :via chart-dependency)", appended)
        graph = search.CodeGraph()
        self.assertIsInstance(graph.load(str(graph_path)), search.MapLoadOk)
        self.assertEqual(before.count("\n(imports "), sum(1 for edge in graph.all_edges if edge.etype == "imports"))

    def test_a_manifest_of_an_unexpected_shape_is_skipped_with_a_warning(self) -> None:
        """/* REQ-CS-230: a Kubernetes or Helm manifest of a shape `wingbox infra` does not expect (metadata that is a list, a port that is no number, a name that is no text, a dependency that is no mapping) is skipped with a warning that names the file; the other manifests give their facts and the command ends without an error. */"""
        root = self.folder()
        _write(root, {
            "k8s/kustomization.yaml": "resources:\n  - good\n  - listed\n  - named-port\n  - numbered\n  - bad-service\n",
            "k8s/good/deployment.yaml": DEPLOYMENT % ("good", "good", "8080"),
            "k8s/listed/deployment.yaml": "kind: Deployment\nmetadata:\n  - name: listed\n",
            "k8s/named-port/deployment.yaml": DEPLOYMENT % ("named-port", "named", "http"),
            "k8s/numbered/deployment.yaml": DEPLOYMENT % ("12", "numbered", "1"),
            "k8s/bad-service/deployment.yaml": DEPLOYMENT % ("served", "served", "9000"),
            "k8s/bad-service/service.yaml": 'kind: Service\nmetadata: text\nspec:\n  ports:\n    - port: "{{ .Values.port }}"\n',
            "charts/fine/Chart.yaml": "name: fine\nversion: 1.0.0\ndependencies:\n  - name: cache\n",
            "charts/odd/Chart.yaml": "name: odd\nversion: 1.0.0\ndependencies:\n  - 5\n",
            "charts/ported/Chart.yaml": "name: ported\nversion: 1.0.0\n",
            "charts/ported/values.yaml": "service:\n  port: named\n",
            "charts/unnamed/Chart.yaml": "name: [a, b]\nversion: 1.0.0\n",
            "other/kustomization.yaml": "resources: 5\n",
        })
        printed = self.run_infra(str(root), "--stdout")
        facts = [line for line in printed.splitlines() if line.startswith("(")]
        self.assertIn("(infra-node infra/good :kind deployment :image registry.example.invalid/team/good:1.2 :port 8080)", facts)
        self.assertIn("(infra-node infra/served :kind deployment :image registry.example.invalid/team/served:1.2 :port 9000)", facts)
        self.assertIn("(infra-node infra/fine :kind helm-chart)", facts)
        self.assertIn("(helm-depends infra/fine infra/cache :via chart-dependency)", facts)
        self.assertEqual(3, sum(1 for line in facts if line.startswith("(infra-node ")), facts)
        for name in ("k8s/listed/deployment.yaml", "k8s/named-port/deployment.yaml", "k8s/numbered/deployment.yaml",
                     "k8s/bad-service/service.yaml", "charts/odd/Chart.yaml", "charts/ported/Chart.yaml", "charts/unnamed/Chart.yaml",
                     "other/kustomization.yaml"):
            with self.subTest(manifest=name):
                written = re.escape(name).replace("/", r"[\\/]")   # a path as the system writes it
                self.assertRegex(printed, rf"; warning: skipped \S*{written}: unexpected shape")


class LinkedSourceTest(ProjectTestCase):
    @unittest.skipIf(os.name == "nt", "creating a symbolic link needs a privilege on Windows")
    def test_a_link_created_after_the_build_is_not_patched_into_the_graph(self) -> None:
        """/* REQ-FRESH-063: a symbolic link with a source extension, or a source file below a linked folder, that appears after the build is not patched into the graph: discovery follows no link, so the partial update leaves it out and the graph holds the modules a build from nothing holds; a source file replaced by a link leaves the graph. */"""
        root = self.project(SHOP)
        outside = self.folder()
        _write(outside, {"secret.py": "class OutsideService:\n    def rotate(self):\n        return 1\n", "pkg/inner.py": "INNER = 1\n"})
        self.assertEqual(0, _build(root)[0])
        os.symlink(outside / "secret.py", root / "shop" / "link.py")
        os.symlink(outside / "pkg", root / "shop" / "linked")
        store = str(root / ".wingbox" / "graph-fragments.json")
        result = graph_update.update_graph(str(root), ["shop/link.py", "shop/linked/inner.py"], store)
        self.assertIsInstance(result, graph_update.Updated, result)
        self.assertNotIn("OutsideService", _graph(root))
        self.assertNotIn("inner", _graph(root))
        modules = re.compile(r"^\((?:node|fn|cls|imports|refs) .*$", re.M)
        self.assertEqual(modules.findall(_graph(self.from_nothing(root))), modules.findall(_graph(root)))
        (root / "shop" / "generated.py").unlink()
        os.symlink(outside / "secret.py", root / "shop" / "generated.py")
        result = graph_update.update_graph(str(root), ["shop/generated.py"], store)
        self.assertIsInstance(result, graph_update.Updated, result)
        self.assertNotIn("generated", _graph(root))
        self.assertNotIn("OutsideService", _graph(root))

if __name__ == "__main__":
    unittest.main()
