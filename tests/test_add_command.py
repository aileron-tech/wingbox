"""`wingbox add <language>`: the one command that downloads a parser. No test here opens a network connection:
the wheels are made in a temporary folder and reach the command through --from or a stand-in for the download."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import add, cli, freshness  # noqa: E402
from wingbox.facts import Fact, fact_sexp  # noqa: E402

ondemand = cli.load_script("lang.ondemand")
languages = cli.load_script("lang").LANGUAGES

MAC = add.Interpreter("cpython", (3, 12), False, "darwin", "arm64", "", (15, 6))
MAC_TAG = "cp310-abi3-macosx_11_0_arm64"
BUILT_IN = ("python", "typescript", "rust", "pascal")
PACKAGES = {"csharp": ("tree-sitter-c-sharp", "0.23.5"), "go": ("tree-sitter-go", "0.25.0")}


def make_wheel(folder: Path, language: str, extra: dict[str, bytes] | None = None, tag: str = MAC_TAG) -> Path:
    """A wheel of the layout the grammars ship: the module, its metadata, and any extra members."""
    package, version = PACKAGES[language]
    module = package.replace("-", "_")
    path = folder / f"{module}-{version}-{tag}.whl"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{module}/__init__.py", f"MARK = {language!r}\n")
        archive.writestr(f"{module}/_binding.abi3.so", b"\x7fELF not a real library")
        archive.writestr(f"{module}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n")
        archive.writestr(f"{module}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: false\n")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def run(options: add.Options, machine: add.Machine) -> list[Fact]:
    """The facts of a run whose arguments are valid."""
    facts = add.add(options, machine)
    assert isinstance(facts, list), facts
    return facts


def parser_of(language: str, wheel: Path, sdist: bool = True) -> add.Parser:
    package, version = PACKAGES[language]
    data = wheel.read_bytes()
    return add.Parser(
        language, package, version, package.replace("-", "_"), sdist,
        (add.Wheel(wheel.name, f"https://files.pythonhosted.org/packages/aa/bb/{wheel.name}", hashlib.sha256(data).hexdigest(), len(data)),),
    )


class AddTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.home = self.base / "parsers"
        self.wheels = self.base / "wheels"
        self.fetched: list[tuple[str, str]] = []
        self.catalog = {name: parser_of(name, make_wheel(self.wheels, name)) for name in PACKAGES}

    def served(self, url: str, target: str, size: int) -> None:
        """The download, served from the temporary folder: the same call the command makes for PyPI."""
        self.fetched.append((url, target))
        shutil.copyfile(self.wheels / url.rsplit("/", 1)[1], target)

    def no_network(self, url: str, target: str, size: int) -> None:
        raise AssertionError(f"the network was asked for {url}")

    def machine(self, fetch=None, environment: dict[str, str] | None = None, interpreter=MAC, catalog=None) -> add.Machine:
        return add.Machine(
            home=str(self.home), platform="darwin-arm64", interpreter=interpreter,
            catalog=self.catalog if catalog is None else catalog, builtin=BUILT_IN,
            fetch=fetch or self.no_network, environment_version=lambda name: (environment or {}).get(name),
        )

    def folder(self, language: str) -> Path:
        package, version = PACKAGES[language]
        return self.home / "darwin-arm64" / f"{package}-{version}"

    def leftovers(self) -> list[str]:
        """Everything in the per-user folder except the lock file."""
        return sorted(
            path.relative_to(self.home).as_posix() for path in self.home.rglob("*") if path.name != add.LOCK_NAME
        ) if self.home.exists() else []


class ArgumentsTest(unittest.TestCase):
    def test_the_arguments_of_add(self) -> None:
        """/* REQ-CLI-026: `wingbox add LANGUAGE [LANGUAGE ...] [--from PATH] [--sexp]` and `wingbox add --list [--sexp]`; no language, an unknown option, --from without a path or --list with a language is a usage error that shows the usage line. */"""
        self.assertEqual(add.Options(("csharp", "php"), False, "", False), add.parse_arguments(["csharp", "php"]))
        self.assertEqual(add.Options(("csharp",), False, "w.whl", True), add.parse_arguments(["CSharp", "--from", "w.whl", "--sexp"]))
        self.assertEqual(add.Options((), True, "", False), add.parse_arguments(["--list"]))
        for arguments in ([], ["--sexp"], ["csharp", "--from"], ["--list", "csharp"], ["csharp", "--force"]):
            with self.subTest(arguments=arguments):
                problem = add.parse_arguments(arguments)
                assert isinstance(problem, add.UsageError), problem
                self.assertIn("usage: wingbox add", problem.message)


class AddFromFileTest(AddTestCase):
    def test_add_from_a_wheel_file_checks_the_hash_unpacks_it_and_is_idempotent(self) -> None:
        """/* REQ-CLI-027: `wingbox add csharp --from FILE` installs the pinned wheel from a local file without the network: the file's sha256 must be the pinned one of the wheel for this platform, then it is unpacked into <per-user folder>/<platform>/<package>-<version>, where the loader finds it; a second run changes nothing and says the parser is present; a folder of wheels works as FILE too. */"""
        wheel = self.wheels / self.catalog["csharp"].wheels[0].filename
        facts = run(add.Options(("csharp",), False, str(wheel), False), self.machine())
        self.assertEqual(["parser-added"], [fact.head for fact in facts], facts)
        self.assertEqual(("csharp",), facts[0].args)
        self.assertEqual("tree-sitter-c-sharp==0.23.5", facts[0].get("package"))
        self.assertEqual(str(self.folder("csharp")), facts[0].get("into"))
        self.assertEqual("MARK = 'csharp'\n", (self.folder("csharp") / "tree_sitter_c_sharp" / "__init__.py").read_text(encoding="utf-8"))
        self.assertTrue((self.folder("csharp") / "tree_sitter_c_sharp-0.23.5.dist-info" / "METADATA").is_file())
        self.assertEqual(
            (str(self.folder("csharp")),), ondemand.user_parser_folders(languages, str(self.home), "darwin-arm64")
        )
        self.assertEqual(["darwin-arm64", "darwin-arm64/tree-sitter-c-sharp-0.23.5"], self.leftovers()[:2])
        before = {path: path.stat().st_mtime_ns for path in self.folder("csharp").rglob("*")}
        again = run(add.Options(("csharp",), False, str(wheel), False), self.machine())
        self.assertEqual(["parser-present"], [fact.head for fact in again])
        self.assertEqual("per-user-folder", again[0].get("from"))
        self.assertEqual(before, {path: path.stat().st_mtime_ns for path in self.folder("csharp").rglob("*")})
        both = run(add.Options(("csharp", "go"), False, str(self.wheels), False), self.machine())
        self.assertEqual(["parser-present", "parser-added"], [fact.head for fact in both])
        self.assertTrue((self.folder("go") / "tree_sitter_go" / "__init__.py").is_file())
        self.assertEqual(0, add.exit_status(both))

    def test_a_file_with_another_hash_is_refused_and_nothing_is_unpacked(self) -> None:
        """/* REQ-CLI-028: a file whose sha256 is not the pinned one is refused before it is opened as an archive, by --from and after a download alike: the fact says which file and hash were expected, the status is 1 and the per-user folder holds no parser and no temporary file. */"""
        tampered = self.base / "other" / self.catalog["csharp"].wheels[0].filename
        make_wheel(tampered.parent, "csharp", {"tree_sitter_c_sharp/extra.py": b"print('not the pinned file')\n"})
        facts = run(add.Options(("csharp",), False, str(tampered), False), self.machine())
        self.assertEqual(["parser-failed"], [fact.head for fact in facts])
        self.assertIn("sha256", facts[0].get("reason"))
        self.assertIn(self.catalog["csharp"].wheels[0].filename, facts[0].get("reason"))
        self.assertEqual(1, add.exit_status(facts))
        self.assertEqual([], [name for name in self.leftovers() if name != "darwin-arm64"])

        def corrupted(url: str, target: str, size: int) -> None:
            shutil.copyfile(tampered, target)

        facts = run(add.Options(("csharp",), False, "", False), self.machine(fetch=corrupted))
        self.assertEqual(["parser-failed"], [fact.head for fact in facts])
        self.assertIn("sha256", facts[0].get("reason"))
        self.assertEqual([], [name for name in self.leftovers() if name != "darwin-arm64"])
        missing = run(add.Options(("csharp",), False, str(self.base / "nowhere.whl"), False), self.machine())
        self.assertEqual(["parser-failed"], [fact.head for fact in missing])
        self.assertIn("nowhere.whl", missing[0].get("reason"))


class AddDownloadTest(AddTestCase):
    def test_add_downloads_only_the_pinned_file_of_each_language(self) -> None:
        """/* REQ-CLI-029: `wingbox add csharp go` asks the download for exactly the catalog URL of the wheel that fits this interpreter and platform, one per language, into a temporary folder inside the per-user folder, and installs both; a parser the environment already has (a pip extra) is reported present and not downloaded. */"""
        facts = run(add.Options(("csharp", "go"), False, "", False), self.machine(fetch=self.served))
        self.assertEqual(["parser-added", "parser-added"], [fact.head for fact in facts])
        self.assertEqual(
            [wheel.url for name in ("csharp", "go") for wheel in self.catalog[name].wheels],
            [url for url, _target in self.fetched],
        )
        for _url, target in self.fetched:
            self.assertTrue(target.startswith(str(self.home) + os.sep), target)
        self.assertEqual(
            ["darwin-arm64", "darwin-arm64/tree-sitter-c-sharp-0.23.5", "darwin-arm64/tree-sitter-go-0.25.0"],
            [name for name in self.leftovers() if name.count("/") < 2],
        )
        shutil.rmtree(self.home)
        present = run(
            add.Options(("csharp",), False, "", False), self.machine(environment={"tree-sitter-c-sharp": "0.23.5"})
        )
        self.assertEqual(["parser-present"], [fact.head for fact in present])
        self.assertEqual("environment", present[0].get("from"))
        self.assertEqual([], self.leftovers())

    def test_errors_say_what_to_do(self) -> None:
        """/* REQ-CLI-030: an unknown language is a usage error that lists the languages added on demand and installs nothing; a built-in language is reported present; a failed download says so and names --from with the file to fetch; a platform or interpreter no pinned wheel fits names the pip extra as the way out; another version of the grammar in the environment is reported with the pinned one. */"""
        unknown = add.add(add.Options(("csharp", "kotlin"), False, "", False), self.machine(fetch=self.served))
        assert isinstance(unknown, add.UsageError), unknown
        self.assertIn("kotlin", unknown.message)
        self.assertIn("csharp, go", unknown.message)
        self.assertEqual([], self.leftovers())
        built_in = run(add.Options(("typescript",), False, "", False), self.machine())
        self.assertEqual([("parser-present", "built-in")], [(fact.head, fact.get("from")) for fact in built_in])

        def offline(url: str, target: str, size: int) -> None:
            raise OSError("no route to host")

        failed = run(add.Options(("csharp",), False, "", False), self.machine(fetch=offline))
        self.assertEqual(["parser-failed"], [fact.head for fact in failed])
        self.assertIn("no route to host", failed[0].get("reason"))
        self.assertIn("--from", failed[0].get("instead"))
        self.assertIn(self.catalog["csharp"].wheels[0].url, failed[0].get("instead"))
        self.assertEqual([], [name for name in self.leftovers() if name != "darwin-arm64"])
        riscv = add.Interpreter("cpython", (3, 12), False, "linux", "riscv64", "glibc", (2, 39))
        no_wheel = run(add.Options(("csharp",), False, "", False), self.machine(fetch=self.served, interpreter=riscv))
        self.assertEqual(["parser-failed"], [fact.head for fact in no_wheel])
        self.assertIn("no wheel", no_wheel[0].get("reason"))
        self.assertIn('pip install "wingbox[csharp]"', no_wheel[0].get("instead"))
        self.assertEqual([], self.fetched)
        other = run(add.Options(("csharp",), False, "", False), self.machine(environment={"tree-sitter-c-sharp": "0.21.0"}))
        self.assertEqual(["parser-failed"], [fact.head for fact in other])
        self.assertIn("0.21.0", other[0].get("reason"))
        self.assertIn("tree-sitter-c-sharp==0.23.5", other[0].get("instead"))


class SafeUnpackTest(AddTestCase):
    def test_an_archive_member_outside_the_target_folder_is_refused(self) -> None:
        """/* REQ-CLI-031: a wheel is unpacked only when every member stays inside the target folder: a parent path, an absolute path, a drive, a backslash or a link is refused, the parser folder does not appear (it is written in a temporary folder and renamed once complete) and nothing is left outside it; leftovers of an interrupted run are removed by the next one. */"""
        link = zipfile.ZipInfo("tree_sitter_c_sharp/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases: dict[str, str | zipfile.ZipInfo] = {
            "parent": "../escaped.py", "deep parent": "tree_sitter_c_sharp/../../escaped.py",
            "absolute": "/escaped.py", "drive": "C:/escaped.py", "backslash": "..\\escaped.py", "link": link,
        }
        for label, member in cases.items():
            with self.subTest(member=label):
                wheel = self.base / label.replace(" ", "-") / self.catalog["csharp"].wheels[0].filename
                make_wheel(wheel.parent, "csharp")
                with zipfile.ZipFile(wheel, "a") as archive:
                    archive.writestr(member, b"escaped")
                catalog = {"csharp": parser_of("csharp", wheel)}
                facts = run(add.Options(("csharp",), False, str(wheel), False), self.machine(catalog=catalog))
                self.assertEqual(["parser-failed"], [fact.head for fact in facts], facts)
                self.assertIn("unsafe", facts[0].get("reason"))
                self.assertEqual([], [name for name in self.leftovers() if name != "darwin-arm64"])
                self.assertEqual([], sorted(path.name for path in self.base.rglob("escaped*")))
        stale = self.home / "darwin-arm64" / ".tmp-interrupted" / "tree_sitter_c_sharp"
        stale.mkdir(parents=True)
        (stale / "__init__.py").write_text("half\n", encoding="utf-8")
        self.assertEqual((), ondemand.user_parser_folders(languages, str(self.home), "darwin-arm64"))
        wheel = self.wheels / self.catalog["csharp"].wheels[0].filename
        facts = run(add.Options(("csharp",), False, str(wheel), False), self.machine())
        self.assertEqual(["parser-added"], [fact.head for fact in facts])
        self.assertFalse(stale.parent.exists())

    def test_two_adds_at_once_install_once(self) -> None:
        """/* REQ-CLI-032: two `wingbox add` of the same language at the same time do not collide: a lock in the per-user folder lets one download and unpack, and the other finds the parser present. */"""
        started = threading.Event()

        def slow(url: str, target: str, size: int) -> None:
            started.set()
            time.sleep(0.4)
            self.served(url, target, size)

        results: list[list[Fact]] = []
        options = add.Options(("csharp",), False, "", False)
        first = threading.Thread(target=lambda: results.append(run(options, self.machine(fetch=slow))))
        second = threading.Thread(target=lambda: results.append(run(options, self.machine(fetch=slow))))
        first.start()
        self.assertTrue(started.wait(30))
        second.start()
        first.join(60)
        second.join(60)
        self.assertEqual(["parser-added", "parser-present"], sorted(facts[0].head for facts in results))
        self.assertEqual(1, len(self.fetched))


class WheelChoiceTest(unittest.TestCase):
    def test_the_pinned_wheels_fit_the_platforms_they_were_built_for(self) -> None:
        """/* REQ-CLI-033: from the pinned catalog the command picks the wheel of the running interpreter and platform (the grammars ship abi3 wheels for CPython): macOS x86_64 and arm64 by system version, Linux glibc and musl by C library version and processor, Windows amd64 and arm64; no wheel for an older system, another processor, a Python older than the wheel's, PyPy or a free-threaded build, and none where a pinned version ships no wheel (PHP, Ruby and Java on musl aarch64, Dart on Windows arm64). */"""
        catalog = add.read_catalog((REPO / "src" / "wingbox" / "assets" / "parsers.json").read_text(encoding="utf-8"))
        every: tuple[str, ...] = ("csharp", "java", "go", "ruby", "php", "dart")

        def chosen(interpreter: add.Interpreter) -> dict[str, str]:
            picks = {name: add.choose_wheel(catalog[name], interpreter) for name in every}
            return {name: wheel.filename.rsplit("-", 1)[1][:-len(".whl")] for name, wheel in picks.items() if wheel}

        def cpython(system: str, machine: str, libc: str, version: tuple[int, int], python=(3, 12)) -> add.Interpreter:
            return add.Interpreter("cpython", python, False, system, machine, libc, version)

        without_dart = {name for name in every if name != "dart"}
        self.assertEqual({name: "macosx_11_0_arm64" for name in every}, chosen(cpython("darwin", "arm64", "", (15, 6))))
        intel = chosen(cpython("darwin", "x86_64", "", (10, 15)))
        self.assertEqual(set(every), set(intel))
        self.assertEqual("macosx_10_13_x86_64", intel["dart"])
        self.assertEqual("macosx_10_9_x86_64", intel["go"])
        self.assertEqual(without_dart, set(chosen(cpython("darwin", "x86_64", "", (10, 12)))))
        glibc = chosen(cpython("linux", "x86_64", "glibc", (2, 31)))
        self.assertEqual(set(every), set(glibc))
        self.assertTrue(all("manylinux" in tag and tag.endswith("x86_64") for tag in glibc.values()), glibc)
        self.assertEqual(set(every), set(chosen(cpython("linux", "aarch64", "glibc", (2, 17)))))
        self.assertEqual({}, chosen(cpython("linux", "x86_64", "glibc", (2, 4))))
        self.assertEqual({name: "musllinux_1_2_x86_64" for name in every}, chosen(cpython("linux", "x86_64", "musl", (1, 2))))
        self.assertEqual({}, chosen(cpython("linux", "x86_64", "musl", (1, 1))))
        self.assertEqual({"csharp", "go", "dart"}, set(chosen(cpython("linux", "aarch64", "musl", (1, 2)))))
        self.assertEqual({name: "win_amd64" for name in every}, chosen(cpython("windows", "amd64", "", (0, 0))))
        self.assertEqual(without_dart, set(chosen(cpython("windows", "arm64", "", (0, 0)))))
        self.assertEqual({}, chosen(cpython("linux", "riscv64", "glibc", (2, 39))))
        self.assertEqual({"ruby", "java", "dart"}, set(chosen(cpython("darwin", "arm64", "", (15, 6), python=(3, 9)))))
        self.assertEqual({}, chosen(add.Interpreter("pypy", (3, 11), False, "darwin", "arm64", "", (15, 6))))
        self.assertEqual({}, chosen(add.Interpreter("cpython", (3, 13), True, "darwin", "arm64", "", (15, 6))))
        here = add.this_interpreter()
        self.assertEqual(sys.version_info[:2], here.version)
        self.assertIn(here.system, ("darwin", "linux", "windows"))


class ListTest(AddTestCase):
    def test_list_says_for_each_language_whether_its_parser_is_installed_and_from_where(self) -> None:
        """/* REQ-CLI-034: `wingbox add --list` prints one fact per on-demand language: installed from the environment or from the per-user folder (with its path), or missing with the command that adds it; where no pinned wheel fits the platform it names the pip extra instead. It downloads nothing. */"""
        wheel = self.wheels / self.catalog["csharp"].wheels[0].filename
        run(add.Options(("csharp",), False, str(wheel), False), self.machine())
        facts = run(add.Options((), True, "", False), self.machine())
        self.assertEqual(["parser-installed", "parser-missing"], [fact.head for fact in facts])
        self.assertEqual(
            (("csharp",), "tree-sitter-c-sharp==0.23.5", "per-user-folder", str(self.folder("csharp"))),
            (facts[0].args, facts[0].get("package"), facts[0].get("from"), facts[0].get("at")),
        )
        self.assertEqual('(parser-missing go :package tree-sitter-go==0.25.0 :add "wingbox add go")', fact_sexp(facts[1]))
        self.assertEqual(0, add.exit_status(facts))
        in_environment = run(add.Options((), True, "", False), self.machine(environment={"tree-sitter-go": "0.25.0"}))
        self.assertEqual("(parser-installed go :package tree-sitter-go==0.25.0 :from environment)", fact_sexp(in_environment[1]))
        riscv = add.Interpreter("cpython", (3, 12), False, "linux", "riscv64", "glibc", (2, 39))
        elsewhere = run(add.Options((), True, "", False), self.machine(interpreter=riscv))
        self.assertIn('pip install "wingbox[go]"', elsewhere[1].get("instead"))


class CommandTest(unittest.TestCase):
    """The command as a person runs it, with the pinned catalog of the package."""

    def run_add(self, *arguments: str, cwd: str | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), contextlib.ExitStack() as stack:
            if cwd is not None:
                stack.enter_context(mock.patch.object(os, "getcwd", lambda: cwd))
            status = cli.main(["add", *arguments])
        return status, out.getvalue(), err.getvalue()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        patch = mock.patch.dict(os.environ, {"WINGBOX_PARSERS_DIR": str(self.base / "parsers")})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_command_lists_refuses_and_explains_without_the_network(self) -> None:
        """/* REQ-CLI-035: `wingbox add --list` names the six on-demand languages; `wingbox add kotlin` exits 2 and lists them; `wingbox add csharp --from FILE` with a file that is not the pinned wheel exits 1 and writes no parser; `wingbox add --help` prints the usage; `wingbox --help` names the command. */"""
        status, out, _err = self.run_add("--list")
        self.assertEqual(0, status)
        for name in ("csharp", "java", "go", "ruby", "php", "dart"):
            self.assertIn(name, out)
        status, out, _err = self.run_add("--list", "--sexp")
        self.assertEqual(6, len([line for line in out.splitlines() if line.startswith("(parser-")]), out)
        status, _out, err = self.run_add("kotlin")
        self.assertEqual(2, status)
        self.assertIn("csharp, dart, go, java, php, ruby", err)
        wrong = self.base / "wrong.whl"
        wrong.write_bytes(b"not the pinned wheel")
        with mock.patch.object(add, "installed_in_environment", lambda distribution, home: None):
            status, out, err = self.run_add("csharp", "--from", str(wrong))
        self.assertEqual(1, status, out + err)
        self.assertIn("sha256", out + err)
        self.assertFalse(any((self.base / "parsers").rglob("tree_sitter_c_sharp")))
        status, out, _err = self.run_add("--help")
        self.assertEqual(0, status)
        self.assertIn("usage: wingbox add", out)
        self.assertIn("wingbox add LANGUAGE", cli.__doc__ or "")

    def test_add_marks_the_graph_of_the_project_that_skipped_the_language(self) -> None:
        """/* REQ-CLI-036: when the project of the current folder has a graph whose last build skipped a language that `wingbox add` now finds installed, the command marks the skipped files changed, so the next use of the graph rebuilds it with them; the output says so, and names `wingbox build` for other projects. */"""
        root = self.base / "project"
        (root / "storefront").mkdir(parents=True)
        (root / "storefront" / "Service.cs").write_text("namespace Shop { public class Service { } }\n", encoding="utf-8")
        (root / "tool.py").write_text("X = 1\n", encoding="utf-8")
        builder = cli.load_script("build_code_graph")
        real_version = builder.importlib.metadata.version

        def version(name: str) -> str:
            if name == "tree-sitter-c-sharp":
                raise builder.importlib.metadata.PackageNotFoundError(name)
            return real_version(name)

        with mock.patch.object(builder.importlib.metadata, "version", version), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, cli.main(["build", str(root)]))
        self.assertTrue(freshness.is_fresh(freshness.read_state(str(root))))
        self.assertEqual(
            '(skipped-language csharp :files 1 :add "wingbox add csharp")', freshness.skipped_languages_line(str(root))
        )
        marked = add.mark_skipped(str(root), ("csharp", "go"), {"csharp": (".cs",), "go": (".go",)})
        self.assertEqual(1, marked)
        state = freshness.read_state(str(root))
        self.assertEqual(["storefront/Service.cs"], sorted(state.changed_files))
        self.assertFalse(freshness.is_fresh(state))
        self.assertEqual(0, add.mark_skipped(str(root), ("go",), {"go": (".go",)}))
        with mock.patch.object(add, "installed_in_environment", lambda distribution, home: "0.23.5"):
            status, out, _err = self.run_add("csharp", cwd=str(root))
        self.assertEqual(0, status)
        self.assertIn("wingbox build", out)
        self.assertIn("project", out)
        self.assertGreater(freshness.read_state(str(root)).generation, state.generation)


if __name__ == "__main__":
    unittest.main()
