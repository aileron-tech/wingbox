import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli, facts, install  # noqa: E402

SKILL_FILES = cli.read_skill_files(cli.skill_directory(str(REPO / "src" / "wingbox")))
HOOK_COMMAND = "/opt/tools/bin/wingbox hook pre-search"


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under root with its bytes, and every folder (value b'<dir>')."""
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = b"<link>" + os.readlink(path).encode()
        elif path.is_dir():
            result[relative] = b"<dir>"
        else:
            result[relative] = path.read_bytes()
    return result


class InstallTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.project = self.base / "work" / "shop"
        (self.project / ".git").mkdir(parents=True)

    def machine(self, cwd: Path | None = None) -> install.Machine:
        return install.Machine(
            home=str(self.home),
            cwd=str(cwd or self.project),
            version="9.9.9",
            skill_files=SKILL_FILES,
            hook_command=HOOK_COMMAND,
        )

    def run_command(self, command: str, *args: str, cwd: Path | None = None) -> list[facts.Fact]:
        options = install.parse_arguments("install" if command == "install" else "uninstall", list(args))
        assert isinstance(options, install.Options), options
        machine = self.machine(cwd)
        return install.install(options, machine) if command == "install" else install.uninstall(options, machine)

    def heads(self, result: list[facts.Fact]) -> list[str]:
        return [fact.head for fact in result]


class GlobalInstallTest(InstallTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()

    def test_fresh_install_writes_skill_block_and_hook_for_detected_assistants(self) -> None:
        """/* REQ-INSTALL-001: `wingbox install` must detect the assistants by their configuration folders and write, for each, the stamped skill, the always-on block and (Claude Code) the PreToolUse hook; assistants without a folder get nothing. */"""
        result = self.run_command("install")

        skill = self.home / ".claude" / "skills" / "wingbox"
        self.assertEqual(SKILL_FILES["SKILL.md"], (skill / "SKILL.md").read_bytes())
        self.assertTrue((skill / "references" / "sexp-notation.md").is_file())
        self.assertEqual("9.9.9\n", (skill / ".wingbox-version").read_text())
        self.assertTrue((self.home / ".agents" / "skills" / "wingbox" / "SKILL.md").is_file())  # Codex
        for block_file in (self.home / ".claude" / "CLAUDE.md", self.home / ".codex" / "AGENTS.md"):
            text = block_file.read_text()
            self.assertEqual(1, text.count(install.BEGIN_MARKER))
            self.assertIn("wingbox impact <file>", text)
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        # The entry is a shell command, or on Windows the program and its arguments apart (exec form).
        self.assertEqual(
            [install.hook_group(HOOK_COMMAND)],
            settings["hooks"]["PreToolUse"],
        )
        self.assertFalse((self.home / ".gemini").exists())
        self.assertFalse((self.home / ".cursor").exists())
        self.assertEqual({"install-wrote"}, set(self.heads(result)))
        [entry] = install.read_record((self.home / ".wingbox" / "installed.sexp").read_text())
        self.assertEqual(("global", str(self.home), ("claude-code", "codex")), (entry.scope, entry.root, entry.assistants))
        self.assertIn(str(self.home / ".agents"), entry.folders)

    def test_install_adds_the_edit_hook_and_reinstall_upgrades_an_old_install_without_duplicates(self) -> None:
        """/* REQ-FRESH-011: install must add one PostToolUse group on Edit|Write|MultiEdit running `<same program> hook dirty`; installing over a settings file from an older install (search hook only, timeout 5) must leave exactly one Wingbox group per event, and uninstall must remove both. */"""
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"matcher": "Grep|Glob|Read", "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}],
            "PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "fmt.sh"}]}],
        }}) + "\n")
        self.run_command("install")
        self.run_command("install")
        hooks = json.loads(settings_path.read_text())["hooks"]
        self.assertEqual([install.hook_group(HOOK_COMMAND)], hooks["PreToolUse"])
        self.assertEqual(
            [
                {"matcher": "Write", "hooks": [{"type": "command", "command": "fmt.sh"}]},
                install.dirty_group(HOOK_COMMAND),
            ],
            hooks["PostToolUse"],
        )
        self.run_command("uninstall")
        remaining = json.loads(settings_path.read_text()).get("hooks", {})
        self.assertNotIn("PreToolUse", remaining)
        self.assertEqual([{"matcher": "Write", "hooks": [{"type": "command", "command": "fmt.sh"}]}], remaining["PostToolUse"])

    def test_second_install_changes_nothing(self) -> None:
        """/* REQ-INSTALL-002: a second `wingbox install` must change no file and report every target as kept and unchanged. */"""
        self.run_command("install")
        before = snapshot(self.base)
        result = self.run_command("install")
        self.assertEqual(before, snapshot(self.base))
        self.assertEqual({"install-kept"}, set(self.heads(result)))
        self.assertEqual({"unchanged"}, {fact.get("reason") for fact in result})

    def test_existing_files_and_other_hooks_are_kept_and_uninstall_restores_them(self) -> None:
        """/* REQ-INSTALL-003: install must keep the text of CLAUDE.md and AGENTS.md outside its markers and every other setting and hook, back up settings.json once, add its hook once; uninstall must restore the files byte for byte and leave only the backup. */"""
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# My rules\n\nAlways run the tests.\n")
        agents_md = self.home / ".codex" / "AGENTS.md"
        agents_md.write_text("Use tabs.\n")
        settings_path = self.home / ".claude" / "settings.json"
        original_settings = {
            "model": "opus",
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}],
            },
        }
        settings_path.write_text(json.dumps(original_settings) + "\n")  # one line: not the format install writes
        before = snapshot(self.base)

        self.run_command("install")
        self.run_command("install")

        self.assertTrue(claude_md.read_text().startswith("# My rules\n\nAlways run the tests.\n\n<!-- wingbox:begin -->"))
        self.assertTrue(agents_md.read_text().startswith("Use tabs.\n\n<!-- wingbox:begin -->"))
        settings = json.loads(settings_path.read_text())
        self.assertEqual("opus", settings["model"])
        self.assertEqual(original_settings["hooks"]["Stop"], settings["hooks"]["Stop"])
        self.assertEqual(2, len(settings["hooks"]["PreToolUse"]))
        self.assertEqual(original_settings["hooks"]["PreToolUse"][0], settings["hooks"]["PreToolUse"][0])
        backup = settings_path.with_name("settings.json.wingbox-bak")
        self.assertEqual(json.loads(backup.read_text()), original_settings)

        result = self.run_command("uninstall")

        after = snapshot(self.base)
        self.assertEqual(["home/.claude/settings.json.wingbox-bak"], sorted(set(after) - set(before)))
        del after["home/.claude/settings.json.wingbox-bak"]
        self.assertEqual(before, after)
        self.assertIn("uninstall-removed", self.heads(result))

    def test_uninstall_after_a_fresh_install_leaves_no_trace(self) -> None:
        """/* REQ-INSTALL-004: uninstall after an install into empty configuration folders must delete every file and folder install created, the record included. */"""
        before = snapshot(self.base)
        self.run_command("install")
        self.run_command("uninstall")
        self.assertEqual(before, snapshot(self.base))

    def test_dry_run_writes_nothing_and_names_every_file(self) -> None:
        """/* REQ-INSTALL-005: `wingbox install --dry-run` must write nothing and name every file it would write or change. */"""
        before = snapshot(self.base)
        result = self.run_command("install", "--dry-run")
        self.assertEqual(before, snapshot(self.base))
        self.assertEqual({"install-would-write"}, set(self.heads(result)))
        named = {fact.args[0] for fact in result}
        self.assertEqual(
            {
                str(self.home / ".claude" / "skills" / "wingbox"),
                str(self.home / ".agents" / "skills" / "wingbox"),
                str(self.home / ".claude" / "CLAUDE.md"),
                str(self.home / ".codex" / "AGENTS.md"),
                str(self.home / ".claude" / "settings.json"),
                str(self.home / ".wingbox" / "installed.sexp"),
            },
            named,
        )

    def test_assistant_option_limits_the_install_and_rejects_unknown_names(self) -> None:
        """/* REQ-INSTALL-006: `--assistant` must limit the install to the named assistants, even ones without a folder; an unknown name is a usage error. */"""
        result = self.run_command("install", "--assistant", "gemini", "--assistant", "cursor")
        self.assertTrue((self.home / ".gemini" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertIn(install.BEGIN_MARKER, (self.home / ".gemini" / "GEMINI.md").read_text())
        self.assertTrue((self.home / ".cursor" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertFalse((self.home / ".claude" / "skills").exists())
        self.assertEqual(
            [("skill", ("gemini",)), ("skill", ("cursor",)), ("block", ("gemini",)), ("record", "")],
            [(fact.get("what"), fact.get("assistant")) for fact in result],
        )
        self.assertIsInstance(install.parse_arguments("install", ["--assistant", "vim"]), install.UsageError)

    def test_a_skill_folder_written_by_another_tool_is_kept(self) -> None:
        """/* REQ-INSTALL-007: a wingbox skill folder without the install stamp (for example from `npx skills add`) must be neither overwritten nor removed. */"""
        other = self.home / ".agents" / "skills" / "wingbox"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("from another installer\n")
        install_result = self.run_command("install")
        uninstall_result = self.run_command("uninstall")
        self.assertEqual("from another installer\n", (other / "SKILL.md").read_text())
        reasons = {fact.get("reason") for fact in install_result + uninstall_result if fact.args == (str(other),)}
        self.assertEqual({"installed-by-another-tool"}, reasons)

    def test_invalid_settings_json_is_left_untouched(self) -> None:
        """/* REQ-INSTALL-008: a settings.json that is not a JSON object must not be rewritten; install reports the failure and exits 1. */"""
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.write_text("{ not json")
        result = self.run_command("install")
        self.assertEqual("{ not json", settings_path.read_text())
        self.assertEqual(1, install.exit_status(result))
        self.assertIn("install-failed", self.heads(result))

    def test_uninstalling_one_assistant_keeps_a_folder_another_one_uses(self) -> None:
        """/* REQ-INSTALL-009: uninstalling Codex must keep ~/.agents/skills/wingbox while the shared `agents` target is still installed. */"""
        (self.home / ".agents").mkdir()
        self.run_command("install")
        result = self.run_command("uninstall", "--assistant", "codex")
        self.assertTrue((self.home / ".agents" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertNotIn(install.BEGIN_MARKER, (self.home / ".codex" / "AGENTS.md").read_text()
                         if (self.home / ".codex" / "AGENTS.md").exists() else "")
        self.assertIn("shared", {fact.get("reason") for fact in result})
        record = install.read_record((self.home / ".wingbox" / "installed.sexp").read_text())
        self.assertEqual(("claude-code", "agents"), record[0].assistants)


class ProjectInstallTest(InstallTestCase):
    def test_project_install_writes_into_the_repository(self) -> None:
        """/* REQ-INSTALL-010: `wingbox install --project` must write .claude/skills/wingbox, .claude/settings.json with the bare `wingbox hook pre-search` command, and the block in CLAUDE.md and AGENTS.md of the repository root, also from a subfolder. */"""
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        sub = self.project / "src" / "app"
        sub.mkdir(parents=True)
        self.run_command("install", "--project", cwd=sub)
        self.assertTrue((self.project / ".claude" / "skills" / "wingbox" / "SKILL.md").is_file())
        self.assertTrue((self.project / ".agents" / "skills" / "wingbox" / "SKILL.md").is_file())
        settings = json.loads((self.project / ".claude" / "settings.json").read_text())
        self.assertEqual("wingbox hook pre-search", settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        for name in ("CLAUDE.md", "AGENTS.md"):
            self.assertIn(install.BEGIN_MARKER, (self.project / name).read_text())
        self.assertFalse((self.home / ".claude" / "skills").exists())

    def test_a_claude_md_that_links_to_agents_md_gets_one_block(self) -> None:
        """/* REQ-INSTALL-011: when CLAUDE.md is a link to AGENTS.md, the project install must write one block, keep the link, and uninstall must restore AGENTS.md. */"""
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.project / "AGENTS.md").write_text("# Project rules\n")
        (self.project / "CLAUDE.md").symlink_to("AGENTS.md")
        before = snapshot(self.base)
        self.run_command("install", "--project")
        self.assertTrue((self.project / "CLAUDE.md").is_symlink())
        self.assertEqual(1, (self.project / "AGENTS.md").read_text().count(install.BEGIN_MARKER))
        self.run_command("uninstall", "--project")
        self.assertEqual(before, snapshot(self.base))


class BlockEditTest(unittest.TestCase):
    def test_the_block_is_replaced_between_markers_and_nothing_else_changes(self) -> None:
        """/* REQ-INSTALL-012: install must replace an outdated block between the markers in place, never add a second one, and keep the text before and after; unbalanced markers are left alone. */"""
        old = f"intro\n\n{install.BEGIN_MARKER}\nold advice\n{install.END_MARKER}\noutro\n"
        edited = install.upsert_block(old)
        assert isinstance(edited, install.Edited)
        self.assertEqual(f"intro\n\n{install.ALWAYS_ON_BLOCK}outro\n", edited.text)
        again = install.upsert_block(edited.text)
        assert isinstance(again, install.Edited)
        self.assertEqual(edited.text, again.text)
        self.assertIsInstance(install.upsert_block(f"x\n{install.BEGIN_MARKER}\nno end\n"), install.Unbalanced)
        self.assertIsInstance(install.remove_block(f"{install.END_MARKER}\n{install.BEGIN_MARKER}\n"), install.Unbalanced)
        appended = install.upsert_block("keep me\n")
        assert isinstance(appended, install.Edited)
        self.assertEqual(install.Edited("keep me\n"), install.remove_block(appended.text))


class InstallOutputTest(InstallTestCase):
    def test_install_prints_sexp_with_sexp_and_a_pretty_print_by_default(self) -> None:
        """/* REQ-INSTALL-013: `wingbox install --sexp` must print one `(install-wrote <path> :what ... :assistant ...)` fact per target; the default output is the same facts as aligned text, with the home folder shown as ~. */"""
        (self.home / ".claude").mkdir()
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}), redirect_stdout(output):
            os.chdir(self.project)
            self.addCleanup(os.chdir, str(REPO))
            status = cli.main(["install", "--sexp"])
        self.assertEqual(0, status)
        lines = output.getvalue().splitlines()
        skill_path = self.home / ".claude" / "skills" / "wingbox"
        # facts.atom quotes a path that holds backslashes (Windows), so the expectation is built the same way.
        self.assertIn(f"(install-wrote {facts.atom(str(skill_path))} :what skill :assistant (claude-code) :version {cli._version()})", lines)
        self.assertIn(f"(install-wrote {facts.atom(str(self.home / '.claude' / 'settings.json'))} :what hook :assistant (claude-code))", lines)

        text = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}), redirect_stdout(text):
            cli.main(["install"])
        first = text.getvalue().splitlines()[0].split()
        self.assertEqual(["kept", "skill", "claude-code", "~/.claude/skills/wingbox", "(unchanged)"], first)

    def test_usage_errors_exit_with_2(self) -> None:
        """/* REQ-INSTALL-014: an unknown option or assistant must exit with status 2 and a message on stderr. */"""
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = cli.main(["install", "--assistant", "notepad"])
        self.assertEqual(2, status)
        self.assertIn("unknown assistant notepad", errors.getvalue())


class PackagedSkillTest(unittest.TestCase):
    def test_the_packaged_skill_holds_skill_md_references_and_the_slash_command(self) -> None:
        """/* REQ-INSTALL-015: the skill that install copies must be SKILL.md named wingbox with its references, and SKILL.md must describe `/wingbox` with no argument, with a file and with a command. */"""
        self.assertEqual({"SKILL.md", "references/scheme-queries.md", "references/sexp-notation.md"}, set(SKILL_FILES))
        text = SKILL_FILES["SKILL.md"].decode()
        self.assertIn("name: wingbox", text)
        for usage in ("/wingbox`", "/wingbox <file>", "/wingbox <command>"):
            self.assertIn(usage, text)
        pyproject = (REPO / "pyproject.toml").read_text()
        self.assertIn('"SKILL.md" = "wingbox/_skill/SKILL.md"', pyproject)
        self.assertIn('"references" = "wingbox/_skill/references"', pyproject)


if __name__ == "__main__":
    unittest.main()
