import os
import stat
import sys
import tempfile
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
        script = self.bin / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n{body}\n', encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def called(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


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
    'if [ "$1" = list ]; then echo "wingbox 0.2.5"; echo "black 24.1.0"; exit 0; fi\n'
    'if [ "$1" = environment ]; then echo "${0%/*}/../venvs"; exit 0; fi'
)
UV_LIST = (
    'if [ "$1 $2" = "tool list" ]; then echo "wingbox v0.2.5"; echo "- wingbox"; exit 0; fi\n'
    'if [ "$1 $2" = "tool dir" ]; then echo "${0%/*}/../tools"; exit 0; fi'
)


class MethodDetectionTest(unittest.TestCase):
    def test_the_installer_that_lists_wingbox_is_chosen(self) -> None:
        """/* REQ-UPDATE-001: `wingbox update` must choose pipx when `pipx list --short` names wingbox and Wingbox runs from pipx's wingbox environment, uv likewise with `uv tool list` and `uv tool dir`, and pip of the running Python otherwise, also when another installer holds a second copy. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", 'if [ "$1" = list ]; then echo "black 24.1.0"; fi')
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
        self.assertEqual(("/envs/w/bin/python", "-m", "pip", "install", "--upgrade", "wingbox"), method.upgrade)

        (stubs.bin / "pipx").unlink()
        (stubs.bin / "uv").unlink()
        method = update.choose_method(update.FromIndex(), "/envs/w/bin/python", stubs.pipx_env, update.run_installer)
        assert isinstance(method, update.Method)
        self.assertEqual("pip", method.name)

    def test_pipx_is_chosen_when_another_pipx_package_is_broken(self) -> None:
        """/* REQ-UPDATE-008: `wingbox update` must choose pipx when `pipx list --short` names wingbox but exits 1 because another package has a missing interpreter, as pipx does. */"""
        stubs = StubInstallers(self)
        stubs.add(
            "pipx",
            'if [ "$1" = list ]; then echo "poetry 1.8.3"; echo "wingbox 0.2.5"; '
            'echo "   package pipenv has invalid interpreter /usr/bin/python3.9"; '
            'echo "One or more packages have a missing python interpreter." >&2; exit 1; fi\n'
            'if [ "$1" = environment ]; then echo "${0%/*}/../venvs"; exit 0; fi',
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


class CheckTest(unittest.TestCase):
    def test_check_reports_the_latest_version_without_installing(self) -> None:
        """/* REQ-UPDATE-003: `wingbox update --check` must report the installed and the latest version through the installer and install nothing. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nif [ "$1" = runpip ]; then echo "wingbox (0.3.0)"; '
                                      'echo "Available versions: 0.3.0, 0.2.5"; exit 0; fi\nexit 9')
        result = update.update(update.Options(True, False, False), environment(prefix=stubs.pipx_env))
        self.assertEqual(["(update-from 0.2.5)", "(update-method pipx)", "(update-latest 0.3.0)"],
                         [facts.fact_sexp(fact) for fact in result])
        self.assertNotIn("pipx upgrade wingbox", stubs.called())

    def test_check_without_internet_fails_clearly(self) -> None:
        """/* REQ-UPDATE-004: when the installer cannot reach the index, `--check` must end with an `update-failed` fact that quotes the installer and exit 1, without a traceback. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\necho "ERROR: Could not fetch URL: connection refused" >&2; exit 1')
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
        stubs.add("pipx", PIPX_LIST + '\necho "network is unreachable" >&2; exit 1')
        refreshed: list[install.Installed] = []
        entry = install.Installed("global", "/home/dev", ("claude-code",))
        result = update.update(update.Options(False, False, False), environment([entry], refreshed, prefix=stubs.pipx_env))
        self.assertEqual(1, update.exit_status(result))
        self.assertEqual([], refreshed)

    def test_after_an_upgrade_the_installed_skills_are_refreshed(self) -> None:
        """/* REQ-UPDATE-007: after a successful upgrade, `wingbox update` must run `wingbox install` of the new version for every recorded install, so an outdated skill is rewritten, and report `update-from`, `update-to`, `update-method` and `update-refreshed`. */"""
        stubs = StubInstallers(self)
        stubs.add("pipx", PIPX_LIST + '\nif [ "$1" = upgrade ]; then echo "upgraded package wingbox"; exit 0; fi')
        home = stubs.folder / "home"
        (home / ".claude").mkdir(parents=True)
        machine = install.Machine(str(home), str(home), "0.2.4", SKILL_FILES, "wingbox hook pre-search")
        options = install.parse_arguments("install", [])
        assert isinstance(options, install.Options)
        install.install(options, machine)
        skill = home / ".claude" / "skills" / "wingbox" / "SKILL.md"
        skill.write_text("outdated skill of 0.2.4\n")
        [entry] = install.read_record((home / ".wingbox" / "installed.sexp").read_text())

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
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
        self.assertIn(f"(update-refreshed {skill.parent} :what skill :assistant (claude-code))", lines)
        self.assertIn("pipx upgrade wingbox", stubs.called())


if __name__ == "__main__":
    unittest.main()
