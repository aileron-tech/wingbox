import os
import stat
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

from wingbox import cli, facts, install, update  # noqa: E402

SKILL_FILES = cli.read_skill_files(cli.skill_directory(str(REPO / "src" / "wingbox")))


class StubInstallers:
    """A bin folder that holds stub pipx and uv scripts and nothing else, put first and alone on PATH.

    The stubs place their wingbox environment at <folder>/venvs/wingbox (pipx) and <folder>/tools/wingbox (uv).
    """

    def __init__(self, test: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        self.calls = self.folder / "calls.log"
        self.pipx_env = str(self.folder / "venvs" / "wingbox")
        self.uv_env = str(self.folder / "tools" / "wingbox")
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin)})
        patcher.start()
        test.addCleanup(patcher.stop)

    def add(self, name: str, body: str) -> None:
        """A stub program: a Python script (body: Python with `args`, `out`, `err`, `HERE`) and a launcher
        the system finds on PATH: a .cmd file on Windows, an sh script elsewhere."""
        script = self.bin / f"{name}-stub.py"
        script.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "HERE = os.path.dirname(os.path.abspath(__file__))\n"
            f"with open({str(self.calls)!r}, 'a', encoding='utf-8') as log:\n"
            f"    log.write(' '.join([{name!r}, *args]) + '\\n')\n"
            "def out(*lines): print('\\n'.join(lines))\n"
            "def err(*lines): print('\\n'.join(lines), file=sys.stderr)\n"
            f"{body}\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            (self.bin / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.bin / name
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)

    def remove(self, name: str) -> None:
        for path in self.bin.glob(f"{name}*"):
            path.unlink()

    def called(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []


def environment(installed: list[install.Installed] | None = None, refreshed: list[install.Installed] | None = None,
                origin: update.Origin = update.FromIndex(), python: str = "/envs/wingbox/bin/python",
                prefix: str = "/envs/wingbox") -> update.Environment:
    def refresh(entry: install.Installed) -> list[facts.Fact]:
        if refreshed is not None:
            refreshed.append(entry)
        return [facts.make_fact("update-refreshed", entry.root, what="skill")]

    return update.Environment(
        origin=origin,
        python=python,
        prefix=prefix,
        installed_version="0.2.5",
        installed=installed or [],
        run=update.run_installer,
        new_version=lambda: "0.2.6",
        refresh=refresh,
    )


PIPX_LIST = (
    'if args[:1] == ["list"]: out("wingbox 0.2.5", "black 24.1.0"); sys.exit(0)\n'
    'if args[:1] == ["environment"]: out(os.path.join(HERE, "..", "venvs")); sys.exit(0)'
)
UV_LIST = (
    'if args[:2] == ["tool", "list"]: out("wingbox v0.2.5", "- wingbox"); sys.exit(0)\n'
    'if args[:2] == ["tool", "dir"]: out(os.path.join(HERE, "..", "tools")); sys.exit(0)'
)


class MethodDetectionTest(unittest.TestCase):
    def test_the_installer_that_lists_wingbox_is_chosen(self) -> None:
        """/* REQ-UPDATE-001: `wingbox update` must choose pipx when `pipx list --short` names wingbox and Wingbox runs from pipx's wingbox environment, uv likewise with `uv tool list` and `uv tool dir`, and pip of the running Python otherwise, also when another installer holds a second copy. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", 'if args[:1] == ["list"]: out("black 24.1.0")')
        stubs.add("uv", UV_LIST)
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", stubs.uv_env, update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual(("uv", ("uv", "tool", "upgrade", "wingbox")), (method.name, method.upgrade))

        stubs.add("pipx", PIPX_LIST)
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", stubs.pipx_env, update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual(("pipx", ("pipx", "upgrade", "wingbox")), (method.name, method.upgrade))

        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", "/envs/w", update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual(("/envs/w/bin/python", "-P", "-m", "pip", "install", "--upgrade", "wingbox"), method.upgrade)

        stubs.remove("pipx")
        stubs.remove("uv")
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", stubs.pipx_env, update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual("pip", method.name)

    def test_pipx_is_chosen_when_another_pipx_package_is_broken(self) -> None:
        """/* REQ-UPDATE-008: `wingbox update` must choose pipx when `pipx list --short` names wingbox but exits 1 because another package has a missing interpreter, as pipx does. */"""
        stubs = StubInstallers(self)
        stubs.add(
            "pipx",
            'if args[:1] == ["list"]:\n'
            '    out("poetry 1.8.3", "wingbox 0.2.5", "   package pipenv has invalid interpreter /usr/bin/python3.9")\n'
            '    err("One or more packages have a missing python interpreter."); sys.exit(1)\n'
            'if args[:1] == ["environment"]: out(os.path.join(HERE, "..", "venvs")); sys.exit(0)',
        )
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", stubs.pipx_env, update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual(("pipx", ("pipx", "upgrade", "wingbox")), (method.name, method.upgrade))

    def test_docker_and_source_installs_say_what_to_run_instead(self) -> None:
        """/* REQ-UPDATE-002: in a Docker image or an editable or local install, `wingbox update` must change nothing, exit 1 and name the command to use instead. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST)
        for origin, reason in (
            (update.InDocker(), "docker-image"),
            (update.FromLocal("/src/wingbox", True), "editable-install"),
            (update.FromLocal("/src/wingbox", False), "local-install"),
        ):
            result = update.update(update.Options(False, False, False), environment(origin=origin))
            self.assertEqual(1, update.exit_status(result))
            self.assertEqual(reason, result[-1].get("reason"))
            self.assertTrue(result[-1].get("instead"))
        self.assertEqual([], stubs.called())
        self.assertIsInstance(update.package_origin('{"url": "file:///src/w", "dir_info": {"editable": true}}', False),
                              update.FromLocal)
        self.assertIsInstance(update.package_origin(None, False), update.FromIndex)
        self.assertIsInstance(update.package_origin('{"url": "file:///w/wingbox-0.2.5-py3-none-any.whl", '
                                                    '"archive_info": {}}', False), update.FromIndex)

    def test_a_homebrew_install_is_upgraded_by_homebrew(self) -> None:
        """/* REQ-UPDATE-012: inside a Homebrew keg (the environment folder lies under Cellar/wingbox/<version>/), `wingbox update` must change nothing, run no installer, exit 1 and name `brew upgrade wingbox`, also when uv is on PATH and the environment has no pip; an environment outside a keg is chosen as before. */"""
        stubs = StubInstallers(self)
        stubs.add("uv", 'if args[:1] == ["--version"]: out("uv 0.9.0"); sys.exit(0)\nsys.exit(2)')
        for prefix in ("/opt/homebrew/Cellar/wingbox/0.9.0/libexec", "/home/linuxbrew/.linuxbrew/Cellar/wingbox/0.9.0/libexec"):
            self.assertTrue(update.homebrew_keg(prefix), prefix)
            cannot = update.choose_method(update.FromIndex(), prefix + "/bin/python", prefix, update.run_installer, has_pip=False)
            self.assertIsInstance(cannot, update.CannotUpdate)
            self.assertEqual(("homebrew-install", "brew upgrade wingbox"), (cannot.reason, cannot.instead))
            result = update.update(update.Options(False, False, False),
                                   environment(python=prefix + "/bin/python", prefix=prefix))
            self.assertEqual(1, update.exit_status(result))
            self.assertEqual("brew upgrade wingbox", result[-1].get("instead"))
        self.assertEqual([], stubs.called())
        for prefix in ("/envs/wingbox", "/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks", "/home/me/Cellar/project/.venv"):
            self.assertFalse(update.homebrew_keg(prefix), prefix)


class CheckTest(unittest.TestCase):
    def test_check_reports_the_latest_version_without_installing(self) -> None:
        """/* REQ-UPDATE-003: `wingbox update --check` must report the installed and the latest version through the installer and install nothing. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nif args[:1] == ["runpip"]: out("wingbox (0.3.0)", "Available versions: 0.3.0, 0.2.5"); '
                                      'sys.exit(0)\nsys.exit(9)')
        result = update.update(update.Options(True, False, False), environment(prefix=stubs.pipx_env))
        self.assertEqual(["(update-from 0.2.5)", "(update-method pipx)", "(update-latest 0.3.0)"],
                         [facts.fact_sexp(fact) for fact in result])
        self.assertNotIn("pipx upgrade wingbox", stubs.called())

    def test_check_without_internet_fails_clearly(self) -> None:
        """/* REQ-UPDATE-004: when the installer cannot reach the index, `--check` must end with an `update-failed` fact that quotes the installer and exit 1, without a traceback. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nerr("ERROR: Could not fetch URL: connection refused"); sys.exit(1)')
        result = update.update(update.Options(True, False, False), environment(prefix=stubs.pipx_env))
        self.assertEqual(1, update.exit_status(result))
        self.assertIn("connection refused", str(result[-1].get("reason")))


class UpgradeTest(unittest.TestCase):
    def test_dry_run_runs_no_installer_command_that_changes_anything(self) -> None:
        """/* REQ-UPDATE-005: `wingbox update --dry-run` must print the upgrade command and the installs it would refresh, and not run the upgrade. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST)
        entry = install.Installed("global", "/home/dev", ("claude-code",))
        result = update.update(update.Options(False, True, False), environment(installed=[entry], prefix=stubs.pipx_env))
        self.assertIn("(update-would-run \"pipx upgrade wingbox\")", [facts.fact_sexp(fact) for fact in result])
        self.assertIn("update-would-refresh", [fact.head for fact in result])
        self.assertEqual(["pipx list --short", "pipx environment --value PIPX_LOCAL_VENVS"], stubs.called())

    def test_a_failed_upgrade_refreshes_nothing(self) -> None:
        """/* REQ-UPDATE-006: when the installer's upgrade fails, `wingbox update` must report it, exit 1 and refresh nothing. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nerr("network is unreachable"); sys.exit(1)')
        refreshed: list[install.Installed] = []
        entry = install.Installed("global", "/home/dev", ("claude-code",))
        result = update.update(update.Options(False, False, False), environment([entry], refreshed, prefix=stubs.pipx_env))
        self.assertEqual(1, update.exit_status(result))
        self.assertEqual([], refreshed)

    def test_after_an_upgrade_the_installed_skills_are_refreshed(self) -> None:
        """/* REQ-UPDATE-007: after a successful upgrade, `wingbox update` must run `wingbox install` of the new version for every recorded install, so an outdated skill is rewritten, and report `update-from`, `update-to`, `update-method` and `update-refreshed`. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nif args[:1] == ["upgrade"]: out("upgraded package wingbox"); sys.exit(0)')
        home = stubs.folder / "home"
        (home / ".claude").mkdir(parents=True)
        machine = install.Machine(str(home), str(home), "0.2.4", SKILL_FILES, ("wingbox",))
        options = install.parse_arguments("install", [])
        assert isinstance(options, install.Options)
        install.install(options, machine)
        skill = home / ".claude" / "skills" / "wingbox" / "SKILL.md"
        skill.write_text("outdated skill of 0.2.4\n", encoding="utf-8")
        [entry] = install.read_record((home / ".wingbox" / "installed.sexp").read_text(encoding="utf-8"))

        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            env = update.Environment(
                origin=update.FromIndex(),
                python=sys.executable,
                prefix=stubs.pipx_env,
                installed_version="0.2.4",
                installed=[entry],
                run=update.run_installer,
                new_version=lambda: "0.2.5",
                refresh=lambda item: update.refresh_installed(sys.executable, str(REPO / "src"), item),
            )
            result = update.update(update.Options(False, False, False), env)

        self.assertEqual(0, update.exit_status(result), result)
        self.assertEqual(SKILL_FILES["SKILL.md"], skill.read_bytes())
        lines = [facts.fact_sexp(fact) for fact in result]
        self.assertEqual(["(update-from 0.2.4)", "(update-method pipx)", "(update-to 0.2.5)"], lines[:3])
        self.assertIn(f"(update-refreshed {facts.atom(str(skill.parent))} :what skill :assistant (claude-code))", lines)
        self.assertIn("pipx upgrade wingbox", stubs.called())


class OldLayoutWarningTest(unittest.TestCase):
    """`wingbox update` names files an earlier version left in a project; it deletes nothing."""

    OLD = (".code_graph.sexp", ".code_graph.uses.sexp", ".code_graph.uses-cache.json", ".code_graph_cache.json")

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.project = self.base / "work" / "shop"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "src").mkdir()
        self.recorded = self.base / "other"
        self.recorded.mkdir()

    def leave_old_files(self, folder: Path) -> dict[str, bytes]:
        for name in self.OLD:
            (folder / name).write_text("old\n", encoding="utf-8")
        (folder / "wingbox-out").mkdir()
        (folder / "wingbox-out" / "map.html").write_text("<html></html>\n", encoding="utf-8")
        return {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    def test_old_files_are_named_in_yellow_or_plain_and_kept(self) -> None:
        """/* REQ-UPDATE-9959: `wingbox update` must look in the current folder and its parents up to the project root and in every --project install of ~/.wingbox/installed.sexp for .code_graph.sexp, .code_graph.uses.sexp, .code_graph.uses-cache.json, .code_graph_cache.json and wingbox-out/, print for each project one `! WARNING: <project>: old Wingbox files left: <names> - Wingbox now uses .wingbox/ only; remove them yourself` line (yellow on a terminal without NO_COLOR, plain otherwise), the fact `(update-warning <project> :old-files (...))` with --sexp, and delete nothing. */"""
        kept = self.leave_old_files(self.project)
        kept.update(self.leave_old_files(self.recorded))
        installed = [install.Installed("project", str(self.recorded), ("claude-code",))]
        warnings = update.old_layout_warnings(str(self.project / "src"), installed)
        self.assertEqual([fact.args[0] for fact in warnings], [self.project.as_posix(), self.recorded.as_posix()])
        self.assertEqual(warnings[0].get("old-files"), self.OLD + ("wingbox-out/",))
        line = update.warning_text(warnings[0], color=False)
        self.assertEqual(
            line,
            f"! WARNING: {self.project.as_posix()}: old Wingbox files left: {', '.join(self.OLD + ('wingbox-out/',))}"
            " - Wingbox now uses .wingbox/ only; remove them yourself",
        )
        self.assertEqual(update.warning_text(warnings[0], color=True), "\033[33m" + line + "\033[0m")
        self.assertIn(f"(update-warning {self.project.as_posix()} :old-files (", facts.render_sexp(warnings))
        self.assertFalse(update.use_color(is_terminal=True, environ={"NO_COLOR": "1"}))
        self.assertTrue(update.use_color(is_terminal=True, environ={}))
        self.assertFalse(update.use_color(is_terminal=False, environ={}))
        self.assertEqual(kept, {path: Path(path).read_bytes() for path in kept})

    def test_a_clean_project_gives_no_warning(self) -> None:
        """/* REQ-UPDATE-009: without old-layout files `wingbox update` must print no warning; `--check` runs the same check. */"""
        self.assertEqual(update.old_layout_warnings(str(self.project / "src"), []), [])
        self.leave_old_files(self.project)
        completed = subprocess.run(
            [sys.executable, "-m", "wingbox.cli", "update", "--check"],
            capture_output=True, text=True, cwd=self.project / "src",
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "HOME": str(self.base), "USERPROFILE": str(self.base)}, timeout=300,
        )
        self.assertIn(f"! WARNING: {self.project.as_posix()}: old Wingbox files left:", completed.stdout)
        self.assertNotIn("\033[", completed.stdout)
        self.assertTrue((self.project / ".code_graph.sexp").is_file())


if __name__ == "__main__":
    unittest.main()


class NoPipTest(unittest.TestCase):
    def test_an_environment_without_pip_updates_through_uv_pip(self) -> None:
        """/* REQ-UPDATE-010: in an environment without pip (made by `uv venv`), `wingbox update` upgrades through `uv pip install --python <this interpreter> --upgrade wingbox` when uv is on PATH, reads the latest version from `uv pip install --dry-run`, and without uv says what to run instead of failing on "No module named pip". */"""
        stubs = StubInstallers(self)
        stubs.add("uv", 'if args[:2] == ["pip", "install"] and "--dry-run" in args: err(" - wingbox==0.2.5", " + wingbox==0.2.6"); sys.exit(0)\n'
                        'sys.exit(0)')
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", "/envs/w", update.run_installer, has_pip=False)
        assert isinstance(method, update.Method)
        self.assertEqual("uv pip", method.name)
        self.assertEqual(("uv", "pip", "install", "--python", "/envs/w/bin/python", "--upgrade", "wingbox"), method.upgrade)
        self.assertEqual("0.2.6", update.latest_version(method, update.run_installer))
        stubs.remove("uv")
        cannot = update.choose_method(update.FromIndex(), "/envs/w/bin/python", "/envs/w", update.run_installer, has_pip=False)
        assert isinstance(cannot, update.CannotUpdate)
        self.assertEqual("no-pip", cannot.reason)
        self.assertIn("/envs/w/bin/python -m ensurepip", cannot.instead)

    def test_a_real_uv_venv_has_no_pip_and_is_detected(self) -> None:
        """/* REQ-UPDATE-011: an environment made with `uv venv` has no pip module, and `update.has_pip` of its interpreter says so; the running interpreter of the tests has its own answer from the same function. */"""
        uv = shutil.which("uv")
        if uv is None:
            self.skipTest("uv is not installed here")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([uv, "venv", "--quiet", str(Path(tmp) / "v")], check=True)
            python = Path(tmp) / "v" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            probe = subprocess.run([str(python), "-c", "from importlib.util import find_spec; print(find_spec('pip') is not None)"],
                                   capture_output=True, text=True, check=True)
            self.assertEqual("False", probe.stdout.strip())
        self.assertIsInstance(update.has_pip(), bool)


class RunningLauncherTest(unittest.TestCase):
    def test_the_launcher_is_named_only_on_windows_and_only_for_wingbox_exe(self) -> None:
        """/* REQ-UPDATE-013: on Windows `wingbox update` must name the wingbox.exe it was started through from sys.argv[0], which a console-script launcher sets to its own path with or without the `.exe` ending, and when that file is a link (pipx links the program into its bin folder) also the file it points at; started as `python -m wingbox.cli`, through a file of another name, through a path that does not exist, or on another system it names none. */"""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            (folder / "wingbox.exe").write_bytes(b"launcher")
            (folder / "other.exe").write_bytes(b"launcher")
            (folder / "cli.py").write_text("", encoding="utf-8")
            expected = (str(folder / "wingbox.exe"),)
            self.assertEqual(expected, update.running_launchers(str(folder / "wingbox"), True))
            self.assertEqual(expected, update.running_launchers(str(folder / "wingbox.exe"), True))
            self.assertEqual((), update.running_launchers(str(folder / "wingbox"), False))
            self.assertEqual((), update.running_launchers(str(folder / "cli.py"), True))
            self.assertEqual((), update.running_launchers(str(folder / "other"), True))
            self.assertEqual((), update.running_launchers(str(folder / "missing" / "wingbox"), True))
            link = folder / "bin" / "wingbox.exe"
            link.parent.mkdir()
            try:
                link.symlink_to(folder / "wingbox.exe")
            except OSError as error:
                self.skipTest(f"this account may not create links: {error}")
            self.assertEqual((str(link), str(folder / "wingbox.exe")), update.running_launchers(str(link.with_suffix("")), True))


class UpgradePastLauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.launcher = self.folder / "wingbox.exe"
        self.launcher.write_bytes(b"old launcher")
        self.removed_later: list[str] = []

    def remove_later(self, path: str) -> None:
        self.removed_later.append(path)

    def others(self) -> list[str]:
        return sorted(path.name for path in self.folder.iterdir() if path != self.launcher)

    def test_the_installer_finds_the_launcher_path_free_and_the_old_file_goes(self) -> None:
        """/* REQ-UPDATE-014: while the installer's upgrade runs, the path of the running wingbox.exe must be free (the launcher is renamed beside itself, which Windows allows for a running program), so the installer writes the new launcher there; the old file is then handed to the removal that runs once the command has ended, and the upgrade's own result is returned unchanged. */"""
        seen: list[bool] = []

        def installer(command: tuple[str, ...]) -> update.Ran:
            seen.append(self.launcher.exists())
            self.launcher.write_bytes(b"new launcher")
            return update.Ran(0, "upgraded")

        upgraded = update.upgrade_past_launcher(("uv", "tool", "upgrade", "wingbox"), (str(self.launcher),), installer, self.remove_later)
        self.assertEqual([False], seen)
        self.assertEqual(update.Upgraded(update.Ran(0, "upgraded")), upgraded)
        self.assertEqual(b"new launcher", self.launcher.read_bytes())
        [aside] = self.removed_later
        self.assertEqual(self.folder, Path(aside).parent)
        self.assertEqual(b"old launcher", Path(aside).read_bytes())

    def test_without_a_new_launcher_the_old_one_is_back(self) -> None:
        """/* REQ-UPDATE-015: when the installer wrote no new launcher (nothing to upgrade, or the upgrade failed), `wingbox update` must rename the old wingbox.exe back, leave no other file beside it, start no removal and return the installer's result; without a launcher to move (another system, `python -m wingbox.cli`) the installer runs as it is. */"""
        for status in (0, 1):
            with self.subTest(status=status):
                upgraded = update.upgrade_past_launcher(
                    ("pipx", "upgrade", "wingbox"), (str(self.launcher),), lambda command: update.Ran(status, "said"), self.remove_later)
                self.assertEqual(update.Upgraded(update.Ran(status, "said")), upgraded)
                self.assertEqual(b"old launcher", self.launcher.read_bytes())
                self.assertEqual([], self.others())
                self.assertEqual([], self.removed_later)
        ran = update.upgrade_past_launcher(("pipx", "upgrade", "wingbox"), (), lambda command: update.Ran(0, "said"), self.remove_later)
        self.assertEqual(update.Upgraded(update.Ran(0, "said")), ran)
        self.assertEqual(b"old launcher", self.launcher.read_bytes())

    def test_a_linked_launcher_and_the_file_it_points_at_are_both_replaced(self) -> None:
        """/* REQ-UPDATE-019: when the wingbox.exe on PATH is a link to the launcher inside the environment (pipx, where Windows allows links), both paths must be free while the installer runs: the installer writes the new launcher and the new link, both old files are handed to the removal, and when the installer writes neither, both are renamed back and the link still leads to the launcher. */"""
        link = self.folder / "bin" / "wingbox.exe"
        link.parent.mkdir()
        try:
            link.symlink_to(self.launcher)
        except OSError as error:
            self.skipTest(f"this account may not create links: {error}")
        both = (str(link), str(self.launcher))
        seen: list[bool] = []

        def installer(command: tuple[str, ...]) -> update.Ran:
            seen.extend([os.path.lexists(link), os.path.lexists(self.launcher)])
            self.launcher.write_bytes(b"new launcher")
            link.symlink_to(self.launcher)
            return update.Ran(0, "upgraded")

        untouched = update.upgrade_past_launcher(("pipx", "upgrade", "wingbox"), both, lambda command: update.Ran(0, "nothing to upgrade"), self.remove_later)
        self.assertEqual(update.Upgraded(update.Ran(0, "nothing to upgrade")), untouched)
        self.assertEqual([], self.removed_later)
        self.assertEqual(b"old launcher", link.read_bytes())
        upgraded = update.upgrade_past_launcher(("pipx", "upgrade", "wingbox"), both, installer, self.remove_later)
        self.assertEqual(update.Upgraded(update.Ran(0, "upgraded")), upgraded)
        self.assertEqual([False, False], seen)
        self.assertEqual(b"new launcher", link.read_bytes())
        self.assertEqual(sorted([str(link.parent), str(self.folder)]), sorted(os.path.dirname(path) for path in self.removed_later))

    def test_what_the_launcher_step_reports_is_part_of_the_answer(self) -> None:
        """/* REQ-UPDATE-016: `wingbox update` must print what the launcher step reports: `(update-left <file> :reason ...)` when the removal of the old launcher could not be started, after which the upgrade still counts as done and the installs are refreshed. */"""
        def installer(command: tuple[str, ...]) -> update.Ran:
            self.launcher.write_bytes(b"new launcher")
            return update.Ran(0, "upgraded")

        refreshed: list[install.Installed] = []
        entry = install.Installed("global", "/home/dev", ("claude-code",))
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST)
        env = environment([entry], refreshed, prefix=stubs.pipx_env)
        env = update.Environment(**{**env.__dict__, "upgrade": lambda command: update.upgrade_past_launcher(
            command, (str(self.launcher),), installer, lambda path: "no interpreter")})
        result = update.update(update.Options(False, False, False), env)
        self.assertEqual(0, update.exit_status(result), result)
        heads = [fact.head for fact in result]
        self.assertEqual(["update-from", "update-method", "update-left", "update-to", "update-refreshed"], heads)
        self.assertEqual("no interpreter", result[2].get("reason"))
        self.assertEqual([entry], refreshed)

    def test_the_old_launcher_is_removed_once_it_is_free(self) -> None:
        """/* REQ-UPDATE-017: the removal started by `wingbox update` must delete the old launcher once no process holds it: on Windows the file stays while a handle on it is open and is gone soon after the handle is closed; elsewhere it is gone at once. */"""
        old = self.folder / "wingbox.exe.1.old"
        old.write_bytes(b"old launcher")
        handle = open(old, "rb") if os.name == "nt" else None
        try:
            self.assertIsNone(update.start_removal(sys.executable, str(old), update.detached_options()))
            if handle is not None:
                time.sleep(2)
                self.assertTrue(old.exists())
        finally:
            if handle is not None:
                handle.close()
        deadline = time.monotonic() + 30
        while old.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(old.exists())


# What a console-script launcher makes Python run: the launcher file itself, as a zip archive with __main__.py.
LAUNCHED = """
import importlib.metadata, json, sys
sys.path.insert(1, {src!r})
from wingbox import update

try:
    importlib.metadata.version("wingbox")   # `wingbox update` reads the installed version this way
except importlib.metadata.PackageNotFoundError:
    pass
launchers = update.running_launchers(sys.argv[0], True)
[launcher] = launchers

def installer(command):
    with open(launcher, "wb") as out:
        out.write(b"new launcher")
    return update.Ran(0, "upgraded")

detached = update.detached_options()
upgraded = update.upgrade_past_launcher(("installer", "upgrade"), launchers, installer,
                                        lambda path: update.start_removal(sys.executable, path, detached))
print(json.dumps({{"launcher": launcher, "status": upgraded.ran.status, "notes": [fact.head for fact in upgraded.notes]}}))
"""


class LauncherArchiveTest(unittest.TestCase):
    def test_the_launcher_python_runs_as_an_archive_is_renamed_and_replaced(self) -> None:
        """/* REQ-UPDATE-018: started the way a launcher starts Python (the launcher file itself run as a zip archive, first on sys.path) and after the installed version was read through importlib.metadata, which keeps that archive open, `wingbox update` must still rename the launcher: the installer writes the new one in its place, nothing is reported about the launcher, and the old file is gone soon after the command has ended. On Windows an open file cannot be renamed, so the handle has to be closed first. */"""
        import json
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            launcher = folder / "wingbox.exe"
            with zipfile.ZipFile(launcher, "w") as archive:
                archive.writestr("__main__.py", LAUNCHED.format(src=str(REPO / "src")))
            done = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, timeout=120, check=False)
            self.assertEqual(0, done.returncode, done.stderr)
            answer = json.loads(done.stdout.strip().splitlines()[-1])
            self.assertEqual({"launcher": str(launcher), "status": 0, "notes": []}, answer)
            self.assertEqual(b"new launcher", launcher.read_bytes())
            deadline = time.monotonic() + 30
            while len(list(folder.iterdir())) > 1 and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertEqual(["wingbox.exe"], [path.name for path in folder.iterdir()])
