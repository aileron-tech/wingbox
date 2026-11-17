"""The install list: every global item with a checkbox, unticked in a terminal, left out with --without elsewhere,
remembered for update; and the two hooks that refresh the graph (session start, after a git command)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import checklist, cli, freshness, install, update  # noqa: E402

SKILL_FILES = cli.read_skill_files(cli.skill_directory(str(REPO / "src" / "wingbox")))
HOOK_PROGRAM = ("/opt/tools/bin/wingbox",)


def _child_environment(home: Path) -> dict[str, str]:
    """A person's terminal: no CI, no agent variables."""
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("CI", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CURSOR_AGENT")}
    environment.update(HOME=str(home), USERPROFILE=str(home), NO_COLOR="1")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    return environment


class ListTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(os.path.realpath(tmp.name)) / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir()
        self.machine = install.Machine(str(self.home), str(self.home), "9.9.9", SKILL_FILES, HOOK_PROGRAM)

    def run_install(self, *args: str) -> list:
        options = install.parse_arguments("install", list(args))
        assert isinstance(options, install.Options), options
        return install.install(options, self.machine)

    def settings_hooks(self) -> list[str]:
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        return sorted(install.hook_kind(entry) for groups in settings.get("hooks", {}).values()
                      for group in groups for entry in group["hooks"] if install.is_wingbox_hook(entry))

    def recorded(self) -> install.Installed:
        [entry] = install.read_record((self.home / ".wingbox" / "installed.sexp").read_text())
        return entry


class ItemsTest(ListTestCase):
    def test_the_list_holds_the_global_items_of_each_assistant(self) -> None:
        """/* REQ-INSTALL-030: the install list names, per detected assistant, only items in the home folder: the skill, the instructions block (with its file), and for Claude Code the search, edit, session-start and git hooks in ~/.claude/settings.json; all ticked by default. */"""
        items = install.install_items(str(self.home), ("claude-code", "codex"), ())
        self.assertEqual(
            [("claude-code", "skill"), ("claude-code", "block"), ("claude-code", "search-hook"), ("claude-code", "edit-hook"),
             ("claude-code", "session-hook"), ("claude-code", "git-hook"), ("codex", "skill"), ("codex", "block")],
            [(item.assistant, item.key) for item in items],
        )
        self.assertTrue(all(item.ticked and item.path.startswith(str(self.home)) for item in items))
        self.assertIn(str(self.home / ".codex" / "AGENTS.md"), [item.path for item in items])
        text = "\n".join(checklist.lines(items, home=str(self.home)))
        self.assertIn(f"claude-code:\n   1. [x] skill (/wingbox)  ({os.path.join('~', '.claude', 'skills', 'wingbox')})", text)
        self.assertIn(f"[x] always-on instructions block  ({os.path.join('~', '.codex', 'AGENTS.md')})", text)

    def test_without_names_an_item_or_an_item_of_one_assistant(self) -> None:
        """/* REQ-INSTALL-031: `--without` takes ITEM or ASSISTANT:ITEM, comma separated or repeated (skill, block, search-hook, edit-hook, session-hook, git-hook); `none` leaves nothing out; an unknown item or assistant is a usage error that names the known ones. */"""
        options = install.parse_arguments("install", ["--without", "git-hook,codex:block", "--without", "session-hook"])
        assert isinstance(options, install.Options)
        self.assertEqual(("git-hook", "codex:block", "session-hook"), options.without)
        self.assertEqual((), install.parse_arguments("install", ["--without", "none"]).without)  # type: ignore[union-attr]
        self.assertIsNone(install.parse_arguments("install", []).without)  # type: ignore[union-attr]
        for wrong in ("hooks", "vim:skill"):
            error = install.parse_arguments("install", ["--without", wrong])
            self.assertIsInstance(error, install.UsageError)
            self.assertIn("skill, block, search-hook" if wrong == "hooks" else "claude-code", error.message)  # type: ignore[union-attr]
        unticked = [item if (item.assistant, item.key) not in {("claude-code", "git-hook"), ("codex", "block")}
                    else install.Item(item.assistant, item.key, item.path, False)
                    for item in install.install_items(str(self.home), ("claude-code", "codex"), ())]
        self.assertEqual(("git-hook", "codex:block"), install.without_from_items(unticked))


class WithoutTest(ListTestCase):
    def test_left_out_items_are_not_written_and_the_choice_is_kept(self) -> None:
        """/* REQ-INSTALL-032: `wingbox install --without git-hook,codex:block` writes every other item and not these, reports them as install-left-out, records the choice in ~/.wingbox/installed.sexp; a later install without --without keeps the choice; `--without none` puts everything back. */"""
        result = self.run_install("--without", "git-hook,codex:block")
        self.assertEqual(["edit-hook", "search-hook", "session-hook"], self.settings_hooks())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertTrue((self.home / ".agents" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertIn(install.BEGIN_MARKER, (self.home / ".claude" / "CLAUDE.md").read_text())
        self.assertEqual({"git-hook", "block"}, {fact.get("what") for fact in result if fact.head == "install-left-out"})
        self.assertEqual(("git-hook", "codex:block"), self.recorded().without)
        self.run_install()
        self.assertEqual(["edit-hook", "search-hook", "session-hook"], self.settings_hooks())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.run_install("--without", "none")
        self.assertEqual(["edit-hook", "git-hook", "search-hook", "session-hook"], self.settings_hooks())
        self.assertTrue((self.home / ".codex" / "AGENTS.md").is_file())
        self.assertEqual((), self.recorded().without)

    def test_unticking_an_installed_hook_removes_it(self) -> None:
        """/* REQ-INSTALL-033: installing with --without search-hook over a full install removes that hook entry and keeps the others; with every hook left out no Wingbox entry stays in settings.json. */"""
        self.run_install()
        self.run_install("--without", "search-hook")
        self.assertEqual(["edit-hook", "git-hook", "session-hook"], self.settings_hooks())
        self.run_install("--without", "search-hook,edit-hook,session-hook,git-hook")
        self.assertEqual([], self.settings_hooks())

    def test_uninstall_keeps_the_items_named_by_without(self) -> None:
        """/* REQ-INSTALL-034: `wingbox uninstall --without skill,session-hook` removes everything else install wrote and keeps the skills and the session-start hook. */"""
        self.run_install()
        options = install.parse_arguments("uninstall", ["--without", "skill,session-hook"])
        assert isinstance(options, install.Options)
        install.uninstall(options, self.machine)
        self.assertTrue((self.home / ".claude" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertTrue((self.home / ".agents" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertEqual(["session-hook"], self.settings_hooks())
        self.assertNotIn(install.BEGIN_MARKER, (self.home / ".claude" / "CLAUDE.md").read_text() if (self.home / ".claude" / "CLAUDE.md").exists() else "")

    def test_update_refreshes_only_the_installed_items(self) -> None:
        """/* REQ-INSTALL-035: `wingbox update` refreshes a global install with the recorded --without, so an item the person left out is never added back; it refreshes no project install, and says once per recorded project install that its files are still there and that `wingbox uninstall --project` in that folder removes them. */"""
        self.run_install("--without", "git-hook,codex:skill")
        entry = self.recorded()
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}):
            result = update.refresh_installed(sys.executable, str(REPO / "src"), entry)
        self.assertNotIn("update-failed", [fact.head for fact in result], result)
        self.assertNotIn("git-hook", self.settings_hooks())
        self.assertFalse((self.home / ".agents" / "skills" / "wingbox").exists())
        project = install.Installed("project", "/work/shop", ("claude-code",))
        notes = update.project_notes([entry, project])
        self.assertEqual(1, len(notes))
        self.assertEqual("update-project-files-left", notes[0].head)
        self.assertIn("wingbox uninstall --project", str(notes[0].get("instead")))


class TerminalTest(ListTestCase):
    def test_the_numbered_list_toggles_items_and_quits(self) -> None:
        """/* REQ-INSTALL-036: without single-key input the list is numbered: typing numbers toggles those items (a second time ticks again), Enter installs the list as shown, q quits and installs nothing; a word that is not a number of the list is named. */"""
        items = install.install_items(str(self.home), ("claude-code", "codex"), ())
        answers = iter(["6 8", "8 x", ""])
        shown: list[str] = []
        result = checklist.numbered(items, lambda _prompt: next(answers), shown.append, str(self.home))
        assert result is not None
        self.assertEqual(("git-hook",), install.without_from_items(result))
        self.assertIn("not a number of the list: x\n", shown)
        self.assertIsNone(checklist.numbered(items, lambda _prompt: "q", shown.append))

    def test_nothing_is_asked_outside_a_terminal(self) -> None:
        """/* REQ-INSTALL-037: install asks only a person at a terminal (stdin and stdout are terminals, no CI, no agent variable, no --yes, --dry-run or --sexp); `wingbox install` with stdin from a pipe asks nothing and installs every item. */"""
        self.assertTrue(checklist.asks(True, True, {}, False, False, False))
        for case in ((False, True, {}, False), (True, False, {}, False), (True, True, {"CI": "true"}, False),
                     (True, True, {"CLAUDECODE": "1"}, False), (True, True, {}, True)):
            self.assertFalse(checklist.asks(case[0], case[1], case[2], case[3], False, False), case)
        self.assertFalse(checklist.asks(True, True, {}, False, True, False))
        done = subprocess.run([sys.executable, "-m", "wingbox.cli", "install"], input="6\nq\n", capture_output=True, text=True,
                              env=_child_environment(self.home), timeout=300)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("Enter to install", done.stdout)
        self.assertEqual(["edit-hook", "git-hook", "search-hook", "session-hook"], self.settings_hooks())

    def test_dry_run_prints_the_list_and_writes_nothing(self) -> None:
        """/* REQ-INSTALL-038: `wingbox install --dry-run --without git-hook` prints the same list with the git hook unticked and writes nothing. */"""
        done = subprocess.run([sys.executable, "-m", "wingbox.cli", "install", "--dry-run", "--without", "git-hook"],
                              capture_output=True, text=True, env=_child_environment(self.home), timeout=300)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("[ ] git hook", done.stdout)
        self.assertIn("[x] session-start hook", done.stdout)
        self.assertFalse((self.home / ".claude" / "settings.json").exists())

    @unittest.skipIf(os.name == "nt", "pseudo-terminals are POSIX only; the numbered list is tested on every system")
    def test_a_person_unticks_items_with_the_arrow_keys(self) -> None:
        """/* REQ-INSTALL-039: in a terminal `wingbox install` shows the list; arrow down and space untick the item under the cursor, Enter installs the rest and records the choice; q installs nothing. */"""
        import pty
        import select

        def run(keys: list[bytes]) -> str:
            leader, follower = pty.openpty()
            process = subprocess.Popen([sys.executable, "-m", "wingbox.cli", "install"], stdin=follower, stdout=follower,
                                       stderr=follower, env=_child_environment(self.home), close_fds=True)
            os.close(follower)
            output = b""

            def read_until(text: bytes, seconds: float = 120) -> None:
                nonlocal output
                deadline = time.monotonic() + seconds
                while text not in output and time.monotonic() < deadline:
                    if select.select([leader], [], [], 0.2)[0]:
                        try:
                            chunk = os.read(leader, 65536)
                        except OSError:
                            return
                        if not chunk:
                            return
                        output += chunk

            read_until(b"Enter: install")
            for key in keys:
                before = len(output)
                os.write(leader, key)
                deadline = time.monotonic() + 30
                while len(output) == before and time.monotonic() < deadline and process.poll() is None:
                    if select.select([leader], [], [], 0.2)[0]:
                        try:
                            output += os.read(leader, 65536)
                        except OSError:
                            break
            process.wait(timeout=300)
            read_until(b"\x00", 2)   # what the process printed last
            os.close(leader)
            return output.decode("utf-8", "replace")

        down = b"\x1b[B"
        shown = run([b"q"])
        self.assertIn("nothing installed", shown)
        self.assertFalse((self.home / ".claude" / "settings.json").exists())
        run([down] * 5 + [b" ", b"\r"])   # the sixth item: the git hook of Claude Code
        self.assertEqual(["edit-hook", "search-hook", "session-hook"], self.settings_hooks())
        self.assertEqual(("git-hook",), self.recorded().without)


def _cleanup_when_released(tmp: tempfile.TemporaryDirectory) -> None:
    """Remove the folder once the background refresh has exited: on Windows its log stays open until the process ends."""
    deadline = time.monotonic() + 60
    while True:
        try:
            tmp.cleanup()
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


class RefreshHooksTest(unittest.TestCase):
    """The session-start hook and the hook after a Bash call, as Claude Code runs them."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(_cleanup_when_released, tmp)
        self.root = Path(os.path.realpath(tmp.name)) / "project"
        (self.root / "shop").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "shop" / "__init__.py").write_text("")
        (self.root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n")
        (self.root / "shop" / "cart.py").write_text("def pay(items):\n    return items\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(0, cli.main(["build", str(self.root)]))
        self.environment = _child_environment(Path(tmp.name) / "home")

    def hook(self, subcommand: str, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", "hook", subcommand], input=json.dumps(payload),
                              capture_output=True, text=True, cwd=self.root, env=self.environment, timeout=300)

    def wait_until_fresh(self) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if freshness.is_fresh(freshness.read_state(str(self.root))) and not freshness.refresh_running(str(self.root)):
                return
            time.sleep(0.1)
        self.fail("the refresh did not finish")

    def changed_like_a_switch(self) -> None:
        (self.root / "shop" / "cart.py").write_text("from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n")

    def test_a_git_command_of_the_agent_starts_a_refresh_and_other_commands_do_not(self) -> None:
        """/* REQ-FRESH-050: `wingbox hook git` after a Bash call that ran git switch, checkout, pull, merge, rebase, reset or stash pop finds the changed sources and starts a background refresh, printing nothing; after any other command (ls, git status, git log) it starts nothing. */"""
        self.changed_like_a_switch()
        for command in ("ls -la", "git status", "git log --oneline -3", "echo switch"):
            done = self.hook("git", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(self.root),
                                     "tool_input": {"command": command}})
            self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))
            self.assertFalse(freshness.refresh_running(str(self.root)))
            self.assertFalse(freshness.read_state(str(self.root)).changed_files)
        done = self.hook("git", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(self.root),
                                 "tool_input": {"command": "cd x && git switch -c feature"}})
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))
        self.wait_until_fresh()
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn("(imports pr1 pr2)", graph)

    def test_session_start_refreshes_a_stale_graph_and_nothing_else(self) -> None:
        """/* REQ-FRESH-051: `wingbox hook session-start` refreshes a graph that misses changes in the background and prints nothing; in a fresh project or a folder without a graph it does nothing and writes nothing. */"""
        done = self.hook("session-start", {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.root)})
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))
        self.assertFalse(freshness.refresh_running(str(self.root)))
        self.changed_like_a_switch()
        done = self.hook("session-start", {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.root)})
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        self.wait_until_fresh()
        self.assertIn("(imports pr1 pr2)", (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as empty:
            done = subprocess.run([sys.executable, "-m", "wingbox.cli", "hook", "session-start"],
                                  input=json.dumps({"cwd": empty}), capture_output=True, text=True, cwd=empty,
                                  env=self.environment, timeout=300)
            self.assertEqual((0, "", "", []), (done.returncode, done.stdout, done.stderr, os.listdir(empty)))


if __name__ == "__main__":
    unittest.main()
