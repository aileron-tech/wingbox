"""How Wingbox calls Git and reads `git diff`: no program of a repository's configuration is started, a ref
is never an option, and `impact --diff` and `diff_functions` name every change Git reports."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts import code_graph_search

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
WINDOWS = os.name == "nt"

APP = {
    ".gitignore": ".wingbox/\n",
    "app/f.py": "def load():\n    return 1\n\n\ndef keep():\n    return 2\n",
    "app/g.py": "from app.f import load\n\n\ndef use():\n    return load()\n",
    "tests/test_g.py": "from app.g import use\n\n\ndef test_use():\n    assert use() == 1\n",
}
LOAD_CHANGED = "def load():\n    return 10\n\n\ndef keep():\n    return 2\n"
THREE = "def first():\n    return 1\n\n\ndef second():\n    return 2\n\n\ndef third():\n    return 3\n"


class _Graph:
    """What reading a diff needs of a loaded graph: its root."""

    def __init__(self, root: str) -> None:
        self._root = root


class _Repositories(unittest.TestCase):
    """Git repositories in a temporary folder, with a home folder of their own: the Git configuration of the
    person who runs the tests is not read."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(os.path.realpath(folder.name))
        self.home, self.marks = self.base / "home", self.base / "marks"
        self.home.mkdir()
        self.marks.mkdir()
        self.environment = dict(
            os.environ, HOME=str(self.home), USERPROFILE=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
            GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"), GIT_CONFIG_NOSYSTEM="1", WINGBOX_JOBS="1",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=os.pathsep.join(filter(None, (str(SOURCE), os.environ.get("PYTHONPATH", "")))),
        )
        self.environment.pop("CLAUDECODE", None)
        self._clock = 0

    def git(self, root: Path, *args: str, check: bool = True, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        done = subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            capture_output=True, check=check, env=self.environment, input=stdin,
        )
        return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode("utf-8", "replace"),
                                           done.stderr.decode("utf-8", "replace"))

    def write(self, root: Path, files: dict[str, str]) -> None:
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8", newline="\n")

    def repository(self, name: str, files: dict[str, str]) -> Path:
        root = self.base / name
        root.mkdir()
        self.git(root, "init", "-q")
        self.write(root, files)
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "first")
        return root

    def wingbox(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-P", "-m", "wingbox.cli", *args], cwd=str(root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=self.environment, timeout=600)

    def built(self, name: str, files: dict[str, str]) -> Path:
        root = self.repository(name, files)
        done = self.wingbox(root, "build", ".")
        self.assertEqual(0, done.returncode, done.stderr)
        return root

    def built_from(self, root: Path) -> str:
        """The fact of the graph that says what it was built from."""
        lines = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8").splitlines()
        return next(line for line in lines if line.startswith("(built-from "))

    def answer(self, root: Path, *args: str) -> tuple[int, str]:
        """A query command run in this process on the graph of root, as `wingbox <command>` runs it after its
        refresh: (exit status, standard output)."""
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.environment, clear=True), contextlib.redirect_stdout(out):
            status = code_graph_search.main([*args, "--root", str(root)])
        return status, out.getvalue()

    def program(self, name: str) -> str:
        """A program that leaves the mark RAN-<name> when it runs; the path with /, as Git takes it everywhere."""
        path = self.marks / f"mark-{name}"
        path.write_text(f"#!/bin/sh\n: >> '{(self.marks / ('RAN-' + name)).as_posix()}'\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path.as_posix()

    def ran(self) -> list[str]:
        return sorted(path.name[4:] for path in self.marks.iterdir() if path.name.startswith("RAN-"))

    def touch(self, *paths: Path) -> None:
        """Give the files a time the index does not hold, so that Git reads them again."""
        self._clock += 10
        moment = time.time() - 100000 + self._clock
        for path in paths:
            os.utime(path, (moment, moment))


class GitProgramsNeverRunTest(_Repositories):
    def test_programs_of_the_repository_configuration_are_not_started(self) -> None:
        """/* REQ-SEC-9100: `wingbox build`, `wingbox impact --diff` and `wingbox diff_functions` in a repository whose own Git configuration names programs start none of them: `core.fsmonitor`, a clean and a smudge filter that `.gitattributes` applies (with `required = true`), a filter process, `diff.external`, a textconv driver and a `post-index-change` hook under `.git/hooks`. The commands still answer: the build names the commit of a clean tree and both diff commands name the changed function. Plain `git status` and `git diff` in the same repository start every one of these programs (checked where the test runs on POSIX; on Windows the programs are written the same way and only their absence is asserted). */"""
        root = self.repository("project", {
            **APP,
            ".gitattributes": "*.dat filter=evil\n*.raw filter=proc\n*.bin diff=conv\n",
            "data/one.dat": "payload\n", "data/two.raw": "payload\n", "data/three.bin": "payload\n",
        })
        data = [root / "data" / name for name in ("one.dat", "two.raw", "three.bin")]
        for key, name in (("core.fsmonitor", "fsmonitor"), ("filter.evil.clean", "clean"), ("filter.evil.smudge", "smudge"),
                          ("filter.proc.process", "process"), ("diff.external", "external"), ("diff.conv.textconv", "textconv")):
            self.git(root, "config", key, f'"{self.program(name)}"')
        self.git(root, "config", "filter.evil.required", "true")
        hook = root / ".git" / "hooks" / "post-index-change"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(Path(self.program("hook")).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        hook.chmod(0o755)

        self.touch(*data, root / "app" / "g.py")
        build = self.wingbox(root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        self.assertEqual([], self.ran())
        commit = self.git(root, "rev-parse", "HEAD").stdout.strip()
        self.assertIn(f":commit {commit}", self.built_from(root))

        self.write(root, {"app/f.py": LOAD_CHANGED, "data/one.dat": "changed\n", "data/three.bin": "changed\n"})
        self.touch(*data, root / "app" / "g.py")
        impact = self.wingbox(root, "impact", "--diff")
        self.assertIn("(changed app/f.py::load :line 1 :used-by (app/g.py))", impact.stdout, impact.stderr)
        self.assertEqual([], self.ran())
        self.touch(*data, root / "app" / "g.py")
        functions = self.wingbox(root, "diff_functions")
        self.assertIn("(diff-fn app/f::load ", functions.stdout, functions.stderr)
        self.assertEqual([], self.ran())

        if WINDOWS:
            return
        self.touch(*data, root / "app" / "g.py")
        self.git(root, "status", "--porcelain", check=False)   # ends at the filter process, which does not answer
        self.git(root, "config", "--unset", "filter.proc.process")
        self.touch(*data, root / "app" / "g.py")
        self.git(root, "status", "--porcelain", check=False)
        self.git(root, "diff", "HEAD", check=False)
        self.git(root, "diff", "--no-ext-diff", "HEAD", check=False)
        for name in ("clean", "external", "fsmonitor", "hook", "process", "textconv"):
            with self.subTest(program=name):
                self.assertIn(name, self.ran())

    def test_git_is_not_started_inside_a_submodule(self) -> None:
        """/* REQ-SEC-9101: in a repository with a submodule whose own Git configuration sets `core.fsmonitor` to a program, `wingbox build`, `wingbox impact --diff` and `wingbox diff_functions` do not start that program (Git is not run inside the submodule), and the change of the repository itself is still named. Plain `git status` starts it (checked on POSIX). */"""
        library = self.repository("library", {"lib/s.py": "def s():\n    return 1\n"})
        root = self.repository("project", APP)
        self.git(root, "-c", "protocol.file.allow=always", "submodule", "--quiet", "add", library.as_posix(), "deps/library")
        self.git(root, "commit", "-q", "-m", "submodule")
        self.git(root / "deps" / "library", "config", "core.fsmonitor", f'"{self.program("submodule")}"')
        self.write(root, {"deps/library/lib/s.py": "def s():\n    return 2\n"})

        build = self.wingbox(root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        self.write(root, {"app/f.py": LOAD_CHANGED})
        self.assertIn("(changed app/f.py::load :line 1 :used-by (app/g.py))", self.wingbox(root, "impact", "--diff").stdout)
        self.assertIn("(diff-fn app/f::load ", self.wingbox(root, "diff_functions").stdout)
        self.assertEqual([], self.ran())
        if not WINDOWS:
            self.git(root, "status", "--porcelain", check=False)
            self.assertEqual(["submodule"], self.ran())

    def test_missing_objects_are_not_fetched(self) -> None:
        """/* REQ-SEC-9102: in a repository whose configuration declares a promisor remote (a partial clone) with a program as its transport, objects that are missing are not fetched: `wingbox build` finishes and `wingbox impact --diff <ref>` answers `(error git-diff-failed ...)` with exit status 1, and the program is not started. Plain `git diff <ref>` starts it (checked on POSIX). */"""
        root = self.repository("project", APP)
        elsewhere = self.repository("elsewhere", {"x.py": "X = 1\n"})
        self.write(root, {"app/f.py": LOAD_CHANGED})
        self.git(root, "commit", "-q", "-am", "second")
        for name in ("HEAD~1:app/f.py", "HEAD^{tree}"):
            digest = self.git(root, "rev-parse", name).stdout.strip()
            stored = root / ".git" / "objects" / digest[:2] / digest[2:]
            stored.chmod(0o644)
            stored.unlink()
        for key, value in (("remote.origin.url", elsewhere.as_posix()), ("remote.origin.promisor", "true"),
                           ("remote.origin.partialclonefilter", "blob:none"), ("extensions.partialClone", "origin"),
                           ("remote.origin.uploadpack", f'"{self.program("fetch")}"')):
            self.git(root, "config", key, value)

        build = self.wingbox(root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        self.write(root, {"app/f.py": "def load():\n    return 11\n"})
        impact = self.wingbox(root, "impact", "--diff", "HEAD~1")
        self.assertTrue(impact.stdout.startswith("(error git-diff-failed "), impact.stdout + impact.stderr)
        self.assertEqual(1, impact.returncode)
        self.assertEqual([], self.ran())
        if not WINDOWS:
            self.git(root, "diff", "HEAD~1", check=False)
            self.assertEqual(["fetch"], self.ran())

    def test_a_filter_that_cannot_be_switched_off_stops_the_call(self) -> None:
        """/* REQ-SEC-9103: a filter driver of the repository's configuration whose name holds `=` cannot be overridden on Git's command line, so Git is not started on the work tree at all: `git_safe.run` raises `GitRefused` for `status` and `diff`, `wingbox build` finishes and names no commit, `wingbox impact --diff` answers `(error git-diff-failed ...)` that names the driver, with exit status 1, and the program is not started. Plain `git status` starts it (checked on POSIX). */"""
        from scripts import git_safe

        root = self.repository("project", {**APP, ".gitattributes": "*.dat filter=a=b\n", "data/one.dat": "payload\n"})
        with open(root / ".git" / "config", "a", encoding="utf-8", newline="\n") as config:
            config.write(f'[filter "a=b"]\n\tclean = \\"{self.program("clean")}\\"\n')
        self.touch(root / "data" / "one.dat")
        with mock.patch.dict(os.environ, self.environment, clear=True):
            for subcommand, arguments in (("status", ("--porcelain",)), ("diff", ("HEAD",))):
                with self.assertRaises(git_safe.GitRefused):
                    git_safe.run(str(root), subcommand, arguments, capture_output=True)
            self.assertEqual(0, git_safe.run(str(root), "rev-parse", ("HEAD",), capture_output=True).returncode)

        build = self.wingbox(root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        self.assertNotIn(":commit", self.built_from(root))
        self.write(root, {"app/f.py": LOAD_CHANGED})
        impact = self.wingbox(root, "impact", "--diff")
        self.assertTrue(impact.stdout.startswith("(error git-diff-failed "), impact.stdout + impact.stderr)
        self.assertIn("a=b", impact.stdout)
        self.assertEqual(1, impact.returncode)
        self.assertEqual([], self.ran())
        if not WINDOWS:
            self.touch(root / "data" / "one.dat")
            self.git(root, "status", "--porcelain", check=False)
            self.assertEqual(["clean"], self.ran())

    def test_the_descendants_script_refuses_an_option_and_starts_no_program(self) -> None:
        """/* REQ-SEC-9104: `scripts/git_descendants.py <ref>` refuses a ref that starts with `-` with a message on standard error and exit status 1 before Git is started, and lists the descendants of a commit without starting the signature program of the repository's configuration (`log.showSignature` with `gpg.program`) for a signed descendant. Plain `git log` starts it (checked on POSIX). */"""
        root = self.repository("project", APP)
        first = self.git(root, "rev-parse", "HEAD").stdout.strip()
        tree = self.git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
        signed = self.git(root, "hash-object", "-t", "commit", "-w", "--stdin", stdin=(
            f"tree {tree}\nparent {first}\nauthor t <t@example.invalid> 1700000000 +0000\n"
            "committer t <t@example.invalid> 1700000000 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n \n invented\n -----END PGP SIGNATURE-----\n\nsigned work\n"
        ).encode("ascii")).stdout.strip()
        self.git(root, "update-ref", "refs/heads/signed", signed)
        self.git(root, "config", "log.showSignature", "true")
        self.git(root, "config", "gpg.program", self.program("gpg"))
        script = [sys.executable, str(ROOT / "scripts" / "git_descendants.py")]
        options = dict(cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.environment, timeout=120)

        listed = subprocess.run([*script, first], **options)
        self.assertEqual(0, listed.returncode, listed.stderr)
        self.assertEqual(f"{signed[:7]} signed work", listed.stdout.strip())
        self.assertEqual([], self.ran())
        refused = subprocess.run([*script, "--all"], **options)
        self.assertEqual(1, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn("starts with '-'", refused.stderr)
        self.assertEqual("", refused.stdout)
        if not WINDOWS:
            self.git(root, "log", "-1", "--format=%h", signed, check=False)
            self.assertEqual(["gpg"], self.ran())


class RefIsNeverAnOptionTest(_Repositories):
    def _mcp(self, root: Path, calls: list[tuple[int, str, dict]]) -> dict[int, dict]:
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})]
        lines += [json.dumps({"jsonrpc": "2.0", "id": identifier, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                  for identifier, name, arguments in calls]
        done = subprocess.run([sys.executable, "-P", "-m", "wingbox", "mcp", "--root", str(root)], cwd=str(root),
                              input="".join(line + "\n" for line in lines).encode("utf-8"), capture_output=True,
                              env=self.environment, timeout=300)
        messages = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines() if line.strip()]
        return {message["id"]: message for message in messages if "id" in message}

    def test_a_ref_that_starts_with_a_dash_is_refused_before_git_starts(self) -> None:
        """/* REQ-CS-9105: a git ref that starts with `-` is refused with `(error invalid-argument ...)` and a non-zero exit status before Git is started, by `wingbox diff_functions <ref>`, by `wingbox query '(diff-functions "<ref>")'` and by `wingbox impact --diff <ref>` (also a one-dash word such as `-R`); the MCP tools `list_changes` and `query_graph` answer the same text as a tool error. `--output=<file>` as the ref leaves that file as it was: Git does not create or empty it. */"""
        root = self.built("project", APP)
        self.write(root, {"app/f.py": LOAD_CHANGED})
        victim = self.base / "victim.txt"
        victim.write_text("keep\n", encoding="utf-8")
        ref = f"--output={victim.as_posix()}"
        cases = [
            ("diff_functions", ref),
            ("query", f'(diff-functions "{ref}")'),
            ("impact", "--diff", "-R"),
            ("impact", "--diff", f"-O{victim.as_posix()}"),
        ]
        for args in cases:
            with self.subTest(args=args):
                done = self.wingbox(root, *args)
                self.assertTrue(done.stdout.startswith("(error invalid-argument "), done.stdout + done.stderr)
                self.assertEqual(1, len(done.stdout.strip().splitlines()), done.stdout)
                self.assertNotEqual(0, done.returncode)
                self.assertEqual("keep\n", victim.read_text(encoding="utf-8"))
        document = json.loads(self.wingbox(root, "diff_functions", ref, "--json").stdout)
        self.assertEqual("invalid-argument", document["error"]["code"])
        replies = self._mcp(root, [(2, "list_changes", {"ref": ref}), (3, "query_graph", {"expression": f'(diff-functions "{ref}")'}),
                                   (4, "list_changes", {})])
        for identifier in (2, 3):
            with self.subTest(call=identifier):
                result = replies[identifier]["result"]
                self.assertTrue(result["isError"], result)
                self.assertTrue(result["content"][0]["text"].startswith("(error invalid-argument "), result)
        self.assertFalse(replies[4]["result"]["isError"], replies[4])
        self.assertIn("(diff-fn app/f::load ", replies[4]["result"]["content"][0]["text"])
        self.assertEqual("keep\n", victim.read_text(encoding="utf-8"))

    def test_a_failed_git_diff_is_one_error_line_and_a_failed_command(self) -> None:
        """/* REQ-CS-9106: when Git cannot give the diff (a ref that does not exist), `wingbox diff_functions <ref>` prints one line, `(error git-diff-failed "<the message of Git on one line>")`, and exits with status 1, as `impact --diff <ref>` does; with `--json` the error object carries the code and the message. A ref that names a file as well as a branch is read as the ref. */"""
        root = self.built("project", APP)
        for args in (("diff_functions", "no-such-ref"), ("impact", "--diff", "no-such-ref")):
            with self.subTest(args=args):
                done = self.wingbox(root, *args)
                self.assertEqual(1, done.returncode, done.stdout + done.stderr)
                self.assertEqual(1, len(done.stdout.strip().splitlines()), done.stdout)
                self.assertTrue(done.stdout.startswith('(error git-diff-failed "'), done.stdout)
                self.assertIn("no-such-ref", done.stdout)
        failed = self.wingbox(root, "diff_functions", "no-such-ref", "--json")
        self.assertEqual(1, failed.returncode)
        self.assertEqual("git-diff-failed", json.loads(failed.stdout)["error"]["code"])
        self.git(root, "branch", "app")
        self.write(root, {"app/f.py": LOAD_CHANGED})
        named = self.wingbox(root, "diff_functions", "app")
        self.assertIn("(diff-fn app/f::load ", named.stdout, named.stderr)


class DiffIsReadExactlyTest(_Repositories):
    def test_the_git_configuration_does_not_change_what_is_read(self) -> None:
        """/* REQ-CS-9107: `impact --diff` and `diff_functions` name the changed function whatever the Git configuration says about the look of a diff, in the repository's own configuration and in the user's global one: `diff.noprefix`, `diff.mnemonicPrefix`, `diff.srcPrefix`, `color.diff = always`, `color.ui = always`, `diff.external`, `diff.renames = false`, `diff.relative`, `diff.interHunkContext`; the external diff program is not started. */"""
        root = self.built("project", {**APP, "app/three.py": THREE})
        self.write(root, {"app/f.py": LOAD_CHANGED})
        settings = [("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"), ("diff.srcPrefix", "old/"), ("color.diff", "always"),
                    ("color.ui", "always"), ("diff.external", f'"{self.program("external")}"'), ("diff.renames", "false"),
                    ("diff.relative", "true"), ("diff.interHunkContext", "9")]
        for level in ("--local", "--global"):
            for key, value in settings:
                with self.subTest(level=level, key=key):
                    self.git(root, "config", level, key, value)
                    try:
                        status, functions = self.answer(root, "diff_functions")
                        self.assertEqual(0, status, functions)
                        self.assertIn("(diff-fn app/f::load ", functions)
                        self.assertIn("(diff-functions-summary :changed 1 :files 1 :ref HEAD)", functions)
                        status, impact = self.answer(root, "impact", "--diff")
                        self.assertEqual(0, status, impact)
                        self.assertIn("(impact app/f.py :direct 1 :total 2", impact)
                        self.assertIn("(changed app/f.py::load :line 1 :used-by (app/g.py))", impact)
                    finally:
                        self.git(root, "config", level, "--unset", key)
        self.assertEqual([], self.ran())
        # Hunks that Git would join with the unchanged lines between them stay apart.
        self.git(root, "checkout", "-q", "--", ".")
        self.write(root, {"app/three.py": THREE.replace("return 1", "return 10").replace("return 3", "return 30")})
        self.git(root, "config", "diff.interHunkContext", "9")
        status, functions = self.answer(root, "diff_functions")
        self.assertEqual(0, status, functions)
        self.assertEqual(["first", "third"], sorted(line.split("::")[1].split()[0] for line in functions.splitlines() if line.startswith("(diff-fn ")))

    def test_a_path_with_a_space_and_a_header_that_holds_a_plus_sign(self) -> None:
        """/* REQ-CS-9108: a changed file whose path holds a space is named as it is (Git ends its `+++` line with a tab, which is no part of the name) and gets its `impact --diff` answer; a change under a hunk header whose context text holds `+1,` (`@@ -6 +6 @@ export function move(dx = +1, dy = 0) {`) is read from the part between the two `@@` alone and names the function. */"""
        root = self.built("project", {
            **APP,
            "my dir/my mod.py": "def spaced():\n    return 1\n",
            "web/move.ts": "export function move(dx = +1, dy = 0) {\n  const a = 1;\n  const b = 2;\n  const c = 3;\n  const d = 4;\n"
                           "  const e = 5;\n  return dx + dy + a + b + c + d + e;\n}\n",
        })
        self.write(root, {"my dir/my mod.py": "def spaced():\n    return 2\n"})
        status, functions = self.answer(root, "diff_functions")
        self.assertEqual(0, status, functions)
        self.assertNotIn("\t", functions)
        self.assertIn('(diff-fn "my dir/my mod::spaced" :kind fn :line 1 :lang py', functions)
        document = json.loads(self.wingbox(root, "impact", "--diff", "--json").stdout)
        self.assertEqual(["my dir/my mod.py"], [item["file"] for item in document["impact"]])
        self.assertEqual(["my dir/my mod.py::spaced"], [item["symbol"] for item in document["changed"]])
        self.git(root, "checkout", "-q", "--", ".")

        self.write(root, {"web/move.ts": (root / "web" / "move.ts").read_text(encoding="utf-8").replace("const e = 5;", "const e = 6;")})
        self.assertIn(" export function move(dx = +1, dy = 0) {", self.git(root, "diff", "--unified=0").stdout)
        status, functions = self.answer(root, "diff_functions")
        self.assertEqual(0, status, functions)
        self.assertIn("(diff-fn web/move::move ", functions)
        status, impact = self.answer(root, "impact", "--diff")
        self.assertEqual(0, status, impact)
        self.assertIn("(changed web/move.ts::move :line 1 :used-by ())", impact)

    def test_a_patch_is_read_by_its_structure(self) -> None:
        """/* REQ-CS-9109: the text of a patch is read exactly: the lines of a hunk are counted by its header and never read as headers (a removed line `-- a/x` and an added line `++ b/x` look like file headers; a line may hold a form feed or a carriage return); a quoted name is unquoted (`\\t`, `\\"`, `\\\\`, an octal escape); the tab after a name with a space is dropped; a file without a text patch (a mode change, a binary file, a rename without a change, an empty new file) is named from its `diff --git` or `rename` lines; a removed file is `deleted`, a renamed one carries the path it had, and a file removed and created again in one diff (a type change) is one changed file. Removed lines count for the line above them; a change before line 1 gives no range. */"""
        patch = "\n".join([
            'diff --git "a/back\\\\slash.py" "b/back\\\\slash.py"', "index c77028a..e6bfff5 100644",
            '--- "a/back\\\\slash.py"', '+++ "b/back\\\\slash.py"', "@@ -1 +1 @@", "-old", "+new",
            "diff --git a/bin.dat b/bin.dat", "index bdc955b..8835708 100644", "Binary files a/bin.dat and b/bin.dat differ",
            "diff --git a/gone.py b/gone.py", "deleted file mode 100644", "index abaddc0..0000000", "--- a/gone.py", "+++ /dev/null",
            "@@ -1,2 +0,0 @@", "-del", "-diff --git a/fake.py b/fake.py",
            "diff --git a/mode dir/run me.sh b/mode dir/run me.sh", "old mode 100644", "new mode 100755",
            "diff --git a/empty.py b/empty.py", "new file mode 100644", "index 0000000..e69de29",
            "diff --git a/q.sql b/q.sql", "index a404ddb..fc3971e 100644", "--- a/q.sql", "+++ b/q.sql",
            "@@ -1 +0,0 @@", "--- a/sql comment", "@@ -3,0 +3,2 @@ select 1;", "+++ b/added", "+page\x0cbreak\rhere",
            "@@ -9,2 +10,0 @@ select 2;", "-x", "-y",
            'diff --git "a/quo\\"te\\tand\\001.py" "b/quo\\"te\\tand\\001.py"', "index bca70f3..d169a2f 100644",
            '--- "a/quo\\"te\\tand\\001.py"', '+++ "b/quo\\"te\\tand\\001.py"', "@@ -1 +1 @@", "-q", "\\ No newline at end of file", "+q2",
            "\\ No newline at end of file",
            "diff --git a/ren.py b/sp ace/ren two.py", "similarity index 57%", "rename from ren.py", "rename to sp ace/ren two.py",
            "index 4cb29ea..2f43848 100644", "--- a/ren.py", "+++ b/sp ace/ren two.py\t", "@@ -3 +3 @@ two", "-three", "+THREE",
            "diff --git a/pure.py b/moved/pure.py", "similarity index 100%", "rename from pure.py", "rename to moved/pure.py",
            "diff --git a/link.py b/link.py", "deleted file mode 100644", "index 367805c..0000000", "--- a/link.py", "+++ /dev/null",
            "@@ -1 +0,0 @@", "-a = 3",
            "diff --git a/link.py b/link.py", "new file mode 120000", "index 0000000..5425ec0", "--- /dev/null", "+++ b/link.py",
            "@@ -0,0 +1 @@", "+nowhere", "\\ No newline at end of file", "",
        ])
        files = code_graph_search._parse_diff(patch)
        self.assertIsInstance(files, list, files)
        read = {item.path: (item.status, item.old_path, item.hunks, item.ranges) for item in files}
        self.assertEqual({
            "back\\slash.py": ("modified", "", 1, ((1, 1),)),
            "bin.dat": ("modified", "", 0, ()),
            "gone.py": ("deleted", "", 1, ()),
            "mode dir/run me.sh": ("modified", "", 0, ()),
            "empty.py": ("added", "", 0, ()),
            "q.sql": ("modified", "", 3, ((3, 4), (10, 10))),
            'quo"te\tand\x01.py': ("modified", "", 1, ((1, 1),)),
            "sp ace/ren two.py": ("renamed", "ren.py", 1, ((3, 3),)),
            "moved/pure.py": ("renamed", "pure.py", 0, ()),
            "link.py": ("modified", "", 2, ((1, 1),)),
        }, read)
        self.assertEqual(len(read), len(files))
        self.assertEqual([], code_graph_search._parse_diff(""))
        unreadable = code_graph_search._parse_diff("diff --git a/one.py b/two.py\nold mode 100644\nnew mode 100755\n")
        self.assertIsInstance(unreadable, str)

    def test_the_files_read_from_a_patch_are_those_git_names(self) -> None:
        """/* REQ-CS-9115: for a diff with awkward names and changes (a space, letters outside ASCII, a rename with and without a change, a removed file, a binary file, a new file, a removed line that looks like a file header and, where the file system takes them, a tab, a double quote and a backslash in a name), the files and the status read from the patch are exactly those `git diff --raw -z` lists, which Git writes without quoting. */"""
        from scripts import git_safe

        names = {"plain.py": "a = 1\nb = 2\n", "sp ace/my mod.py": "x = 1\n", "\u00fcn\u00ef.py": "u = 1\n", "q.sql": "-- a/q.sql\nselect 1;\n",
                 "ren.py": "one\ntwo\nthree\n", "pure.py": "p = 1\nq = 2\n", "gone.py": "g = 1\n", "blob.dat": "\0\1\n"}
        if not WINDOWS:
            names.update({"tab\tname.py": "t = 1\n", 'quo"te.py': "q = 1\n", "back\\slash.py": "s = 1\n"})
        root = self.repository("project", names)
        self.git(root, "mv", "ren.py", "ren two.py")
        (root / "moved").mkdir()
        self.git(root, "mv", "pure.py", "moved/pure.py")
        self.git(root, "rm", "-q", "gone.py")
        unchanged = ("ren.py", "pure.py", "gone.py")
        self.write(root, {name: text.replace("1", "7") for name, text in names.items() if name not in unchanged})
        self.write(root, {"q.sql": "select 1;\n++ b/q.sql\n", "new file.py": "n = 1\n", "ren two.py": "one\ntwo\nTHREE\n",
                          "blob.dat": "\0\2\n"})
        self.git(root, "add", "new file.py")
        with mock.patch.dict(os.environ, self.environment, clear=True):
            listed = git_safe.run(str(root), "diff", ("--raw", "-z", "--find-renames", "HEAD"), capture_output=True)
            read = code_graph_search._diff_files(_Graph(str(root)), "HEAD")
        self.assertEqual(0, listed.returncode, listed.stderr)
        fields = listed.stdout.decode("utf-8").split("\0")
        expected: dict[str, tuple[str, str]] = {}
        index = 0
        while fields[index]:
            letter = fields[index].split(" ")[4][0]
            if letter == "R":
                expected[fields[index + 2]] = ("renamed", fields[index + 1])
                index += 3
            else:
                expected[fields[index + 1]] = ({"A": "added", "D": "deleted"}.get(letter, "modified"), "")
                index += 2
        self.assertIsInstance(read, list, read)
        self.assertEqual(expected, {item.path: (item.status, item.old_path) for item in read})
        self.assertEqual({"renamed": 2, "deleted": 1, "added": 1}, {status: sum(1 for item in read if item.status == status)
                                                                  for status in ("renamed", "deleted", "added")})
        self.assertEqual(len(names) + 1, len(read))

    def test_a_graph_below_the_top_of_the_repository_and_an_exact_path(self) -> None:
        """/* REQ-CS-9110: with the graph built in a folder below the top level of the Git repository, `impact --diff` and `diff_functions` answer for the changed files of that folder, by their paths relative to the graph root; a change of a file outside that folder is no change of the graph (`impact-diff-empty`, `diff-functions-empty`) and is never mapped to a file of the graph whose path ends the same way (a changed `util.py` at the top level is not `a/util.py` of the graph). */"""
        top = self.repository("project", {
            ".gitignore": ".wingbox/\n",
            "util.py": "def helper():\n    return 1\n",
            "web/a/util.py": "def helper():\n    return 1\n",
            "web/src/area.py": "def area(w, h):\n    return w * h\n",
            "web/src/room.py": "from src.area import area\n\n\ndef room():\n    return area(2, 3)\n",
        })
        root = top / "web"
        self.assertEqual(0, self.wingbox(root, "build", ".").returncode)
        self.write(top, {"util.py": "def helper():\n    return 2\n"})
        self.assertEqual((0, "(diff-functions-empty HEAD)\n"), self.answer(root, "diff_functions"))
        self.assertEqual((0, "(impact-diff-empty HEAD)\n"), self.answer(root, "impact", "--diff"))
        self.write(top, {"web/src/area.py": "def area(w, h):\n    return h * w\n"})
        status, functions = self.answer(root, "diff_functions")
        self.assertEqual(0, status)
        self.assertIn("(diff-fn src/area::area ", functions)
        self.assertNotIn("a/util", functions)
        self.assertIn(":changed 1 :files 1 ", functions)
        status, impact = self.answer(root, "impact", "--diff")
        self.assertEqual(0, status, impact)
        self.assertIn("(impact src/area.py :direct 1 :total 1", impact)
        self.assertIn("(changed src/area.py::area :line 1 :used-by (src/room.py))", impact)

    def test_the_last_definition_of_a_file_runs_to_its_end(self) -> None:
        """/* REQ-CS-9111: a changed line is mapped to the definition that starts above it, and the last definition of a file runs to the end of the file: a change at line 251 of a file with one class names that class, not `diff-functions-no-match`. */"""
        body = "".join(f"    v{number} = {number}\n" for number in range(1, 300))
        root = self.built("project", {".gitignore": ".wingbox/\n", "big.py": "class Big:\n" + body})
        self.write(root, {"big.py": "class Big:\n" + body.replace("    v250 = 250\n", "    v250 = 0\n")})
        status, functions = self.answer(root, "diff_functions")
        self.assertEqual(0, status)
        self.assertIn("(diff-fn big::Big :kind cls :line 1 ", functions)
        self.assertIn("(changed big.py::Big :line 1 :used-by ())", self.answer(root, "impact", "--diff")[1])


class RemovedAndRenamedTest(_Repositories):
    def test_removed_lines_are_a_change_of_their_file(self) -> None:
        """/* REQ-CS-9112: a file from which lines were only removed is a changed file: `impact --diff` gives its impact answer and `diff_functions` names it, never the empty form. The removed lines count for the definition that holds the line above them (a statement removed inside `load` names `load`); lines removed above the first definition name the file alone (`diff-file`, `diff-functions-no-match`). */"""
        root = self.built("project", {**APP, "app/f.py": "def load():\n    value = 1\n    value += 0\n    return value\n\n\ndef keep():\n    return 2\n"})
        self.write(root, {"app/f.py": "def load():\n    value = 1\n    return value\n\n\ndef keep():\n    return 2\n"})
        self.assertIn("@@ -3 +2,0 @@", self.git(root, "diff", "--unified=0").stdout)
        impact = self.wingbox(root, "impact", "--diff")
        self.assertEqual(0, impact.returncode, impact.stdout + impact.stderr)
        self.assertIn("(impact app/f.py :direct 1 :total 2", impact.stdout)
        self.assertIn("(changed app/f.py::load :line 1 :used-by (app/g.py))", impact.stdout)
        functions = self.wingbox(root, "diff_functions")
        self.assertEqual(0, functions.returncode)
        self.assertIn("(diff-fn app/f::load ", functions.stdout)
        self.assertNotIn("::keep", functions.stdout)

        self.git(root, "commit", "-q", "-am", "second")
        self.write(root, {"app/f.py": "def keep():\n    return 2\n"})
        self.assertIn("+0,0 @@", self.git(root, "diff", "--unified=0").stdout)
        impact = self.wingbox(root, "impact", "--diff")
        self.assertIn("(impact app/f.py ", impact.stdout, impact.stderr)
        self.assertNotIn("impact-diff-empty", impact.stdout)
        self.assertNotIn("(changed ", impact.stdout)
        functions = self.wingbox(root, "diff_functions")
        self.assertEqual("(diff-file app/f.py :hunks 1)\n(diff-functions-no-match :files 1 :ref HEAD)\n", functions.stdout)

    def test_a_removed_file_and_a_renamed_file_are_named(self) -> None:
        """/* REQ-CS-9113: a diff that removes a file (`git rm`) or renames one (`git mv`) never answers the empty form. `impact --diff` names the path that is gone with `(changed-file <path> :status deleted :dependents unknown)` or `(changed-file <old path> :status renamed :to <new path> :dependents unknown)` after the impact answers (`changed_files` in JSON, a block in the text for people, the same fact under `--summary`), answers for the new path of a rename, and exits with status 0; a diff that only removes files prints these facts alone. `diff_functions` names the same paths with `(diff-file <path> :hunks N :status deleted)` and `(diff-file <old path> :hunks N :status renamed :to <new path>)`. */"""
        root = self.built("project", {**APP, "util.py": "def helper():\n    return 1\n"})
        self.git(root, "rm", "-q", "util.py")
        only_removed = self.wingbox(root, "impact", "--diff")
        self.assertEqual((0, "(changed-file util.py :status deleted :dependents unknown)\n"), (only_removed.returncode, only_removed.stdout))
        document = json.loads(self.wingbox(root, "impact", "--diff", "--json").stdout)
        self.assertEqual(([], [], [{"file": "util.py", "status": "deleted", "dependents": "unknown"}]),
                         (document["impact"], document["changed"], document["changed_files"]))
        functions = self.wingbox(root, "diff_functions")
        self.assertEqual((0, "(diff-file util.py :hunks 1 :status deleted)\n(diff-functions-no-match :files 1 :ref HEAD)\n"),
                         (functions.returncode, functions.stdout))

        self.git(root, "mv", "app/f.py", "app/f2.py")
        impact = self.wingbox(root, "impact", "--diff")
        self.assertEqual(0, impact.returncode, impact.stdout + impact.stderr)
        self.assertIn("(impact app/f2.py ", impact.stdout)
        self.assertTrue(impact.stdout.endswith(
            "(changed-file app/f.py :status renamed :to app/f2.py :dependents unknown)\n"
            "(changed-file util.py :status deleted :dependents unknown)\n"), impact.stdout)
        document = json.loads(self.wingbox(root, "impact", "--diff", "--json").stdout)
        self.assertEqual(["app/f2.py"], [item["file"] for item in document["impact"]])
        self.assertEqual([{"file": "app/f.py", "status": "renamed", "to": "app/f2.py", "dependents": "unknown"},
                          {"file": "util.py", "status": "deleted", "dependents": "unknown"}], document["changed_files"])
        summary = self.wingbox(root, "impact", "--diff", "--summary")
        self.assertIn("(impact-summary app/f2.py ", summary.stdout)
        self.assertIn("(changed-file util.py :status deleted :dependents unknown)", summary.stdout)
        self.assertIn("changed_files", json.loads(self.wingbox(root, "impact", "--diff", "--summary", "--json").stdout))
        people = self.wingbox(root, "impact", "--diff", "--human").stdout
        self.assertIn("util.py", people)
        self.assertIn("deleted", people)
        self.assertIn("renamed to app/f2.py", people)
        functions = self.wingbox(root, "diff_functions")
        self.assertEqual((0, "(diff-file app/f.py :hunks 0 :status renamed :to app/f2.py)\n"
                             "(diff-file util.py :hunks 1 :status deleted)\n(diff-functions-no-match :files 2 :ref HEAD)\n"),
                         (functions.returncode, functions.stdout))

        self.write(root, {"app/f2.py": LOAD_CHANGED})
        functions = self.wingbox(root, "diff_functions")
        self.assertEqual(
            ["(diff-fn app/f2::load ", "(diff-file app/f.py :hunks 1 :status renamed :to app/f2.py)",
             "(diff-file util.py :hunks 1 :status deleted)", "(diff-functions-summary :changed 1 :files 2 :ref HEAD)"],
            [line if not line.startswith("(diff-fn ") else line[:22] for line in functions.stdout.splitlines()])
        self.assertEqual(json.loads(self.wingbox(root, "impact", "--diff", "--json").stdout)["changed"][0]["symbol"], "app/f2.py::load")


class ChangedUnderOnlyTestsTest(_Repositories):
    def test_a_function_only_other_files_use_says_so(self) -> None:
        """/* REQ-CS-9114: under `impact --diff --only tests` a changed function that no test file uses directly and that other files do use carries `:other-users N`, the number of those files, after `:used-by ()` (`other_users` in JSON, the same key on `changed-summary`, and the text for people says it), so that the empty list does not read as "nothing uses it". A function a test file uses, a function nothing uses and every answer without `--only tests` are printed as before. */"""
        root = self.built("project", {**APP, "app/lone.py": "def lone():\n    return 0\n"})
        self.write(root, {"app/f.py": LOAD_CHANGED, "app/lone.py": "def lone():\n    return 1\n",
                          "app/g.py": "from app.f import load\n\n\ndef use():\n    return load() + 0\n"})
        compact = self.wingbox(root, "impact", "--diff", "--only", "tests").stdout
        self.assertIn("(changed app/f.py::load :line 1 :used-by () :other-users 1)", compact)
        self.assertIn("(changed app/g.py::use :line 4 :used-by (tests/test_g.py))", compact)
        self.assertIn("(changed app/lone.py::lone :line 1 :used-by ())", compact)
        changed = {item["symbol"]: item for item in json.loads(self.wingbox(root, "impact", "--diff", "--only", "tests", "--json").stdout)["changed"]}
        self.assertEqual({"symbol": "app/f.py::load", "line": 1, "used_by": [], "other_users": 1}, changed["app/f.py::load"])
        self.assertNotIn("other_users", changed["app/g.py::use"])
        self.assertNotIn("other_users", changed["app/lone.py::lone"])
        summary = self.wingbox(root, "impact", "--diff", "--only", "tests", "--summary").stdout
        self.assertIn("(changed-summary app/f.py::load :line 1 :used-by 0 :other-users 1)", summary)
        self.assertIn("(changed-summary app/lone.py::lone :line 1 :used-by 0)", summary)
        counted = json.loads(self.wingbox(root, "impact", "--diff", "--only", "tests", "--summary", "--json").stdout)["changed"]
        self.assertEqual([1], [item["other_users"] for item in counted if "other_users" in item])
        people = self.wingbox(root, "impact", "--diff", "--only", "tests", "--human").stdout
        self.assertIn("no test file; 1 other file uses it", people)
        people = self.wingbox(root, "impact", "--diff", "--only", "tests", "--summary", "--human").stdout
        self.assertIn("used by 0 test files; 1 other file uses it", people)
        plain = self.wingbox(root, "impact", "--diff").stdout
        self.assertIn("(changed app/f.py::load :line 1 :used-by (app/g.py))", plain)
        self.assertNotIn("other-users", plain)


if __name__ == "__main__":
    unittest.main()
