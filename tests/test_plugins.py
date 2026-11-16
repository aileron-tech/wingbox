"""The Claude Code plugin under plugins/wingbox and its marketplace."""
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "wingbox"
LAUNCHER = PLUGIN / "scripts" / "wingbox-run"
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
SKILL_SOURCES = ["SKILL.md"] + sorted(f"references/{p.name}" for p in (REPO / "references").iterdir() if p.is_file())


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def fake_command(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


class ClaudePluginTest(unittest.TestCase):
    def test_manifest(self) -> None:
        """/* REQ-PLUGIN-001: the Claude Code manifest must name the plugin wingbox at the version of pyproject.toml, carry the listing metadata, an icon that is a square PNG of at least 512 pixels, a PreToolUse hook on Grep|Glob|Read that runs the launcher with `hook pre-search`, and an MCP server that runs the launcher with `mcp`, every path under ${CLAUDE_PLUGIN_ROOT} existing in the plugin. */"""
        manifest = load(PLUGIN / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "wingbox")
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(manifest["author"]["name"], "Aileron Technologies")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["homepage"], "https://wingbox.dev")
        self.assertEqual(manifest["repository"], "https://github.com/aileron-tech/wingbox")
        self.assertTrue(manifest["description"] and manifest["keywords"])
        for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl"):
            self.assertTrue(manifest[key].startswith("https://"), key)
        width, height = png_size(PLUGIN / manifest["icon"])
        self.assertEqual(width, height)
        self.assertGreaterEqual(width, 512)
        (group,) = manifest["hooks"]["PreToolUse"]
        self.assertEqual(group["matcher"], "Grep|Glob|Read")
        (hook,) = group["hooks"]
        self.assertEqual(hook["command"], '"${CLAUDE_PLUGIN_ROOT}/scripts/wingbox-run" hook pre-search')
        server = manifest["mcpServers"]["wingbox"]
        self.assertEqual(server["command"], "${CLAUDE_PLUGIN_ROOT}/scripts/wingbox-run")
        self.assertEqual(server["args"], ["mcp"])
        for path in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\\ ]+)", json.dumps(manifest)):
            self.assertTrue((PLUGIN / path).is_file(), path)

    def test_marketplace(self) -> None:
        """/* REQ-PLUGIN-002: `.claude-plugin/marketplace.json` at the repository root must list the plugin wingbox from ./plugins/wingbox, so that `/plugin marketplace add aileron-tech/wingbox` then `/plugin install wingbox@aileron-tech` installs it. */"""
        marketplace = load(REPO / ".claude-plugin" / "marketplace.json")
        self.assertEqual(marketplace["name"], "aileron-tech")
        self.assertEqual(marketplace["owner"]["name"], "Aileron Technologies")
        (entry,) = marketplace["plugins"]
        self.assertEqual(entry["name"], "wingbox")
        self.assertEqual(entry["source"], "./plugins/wingbox")
        self.assertTrue((REPO / entry["source"] / ".claude-plugin" / "plugin.json").is_file())


class SkillCopyTest(unittest.TestCase):
    def test_copy_is_identical(self) -> None:
        """/* REQ-PLUGIN-003: the plugin's skill must be byte for byte SKILL.md and references/ of the repository, and its LICENSE the repository's; `tools/sync_plugin.py --check` must pass on the repository. */"""
        skill = PLUGIN / "skills" / "wingbox"
        copied = sorted(str(p.relative_to(skill)) for p in skill.rglob("*") if p.is_file())
        self.assertEqual(copied, sorted(SKILL_SOURCES))
        for name in SKILL_SOURCES:
            self.assertEqual((skill / name).read_bytes(), (REPO / name).read_bytes(), name)
        self.assertEqual((PLUGIN / "LICENSE").read_bytes(), (REPO / "LICENSE").read_bytes())
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "sync_plugin.py"), "--check"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sync_repairs_drift(self) -> None:
        """/* REQ-PLUGIN-004: after SKILL.md or the version in pyproject.toml changes, `tools/sync_plugin.py --check` must exit 1 and name each stale file, and `tools/sync_plugin.py` must rewrite the skill copy and the version in the manifest and the launcher. */"""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            for name in ("SKILL.md", "LICENSE", "pyproject.toml"):
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / name, copy / name)
            for folder in ("references", "plugins", "tools"):
                shutil.copytree(REPO / folder, copy / folder)
            (copy / "SKILL.md").write_text((REPO / "SKILL.md").read_text(encoding="utf-8") + "\nNew line.\n", encoding="utf-8")
            pyproject = (copy / "pyproject.toml").read_text(encoding="utf-8")
            (copy / "pyproject.toml").write_text(pyproject.replace(f'version = "{VERSION}"', 'version = "9.8.7"', 1), encoding="utf-8")
            script = str(copy / "tools" / "sync_plugin.py")
            check = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
            self.assertEqual(check.returncode, 1)
            for stale in ("skills/wingbox/SKILL.md", ".claude-plugin/plugin.json", "scripts/wingbox-run"):
                self.assertIn(stale, check.stdout)
            subprocess.run([sys.executable, script], check=True, capture_output=True)
            self.assertEqual(subprocess.run([sys.executable, script, "--check"], capture_output=True).returncode, 0)
            plugin = copy / "plugins" / "wingbox"
            self.assertTrue((plugin / "skills" / "wingbox" / "SKILL.md").read_text(encoding="utf-8").endswith("New line.\n"))
            self.assertEqual(load(plugin / ".claude-plugin" / "plugin.json")["version"], "9.8.7")
            self.assertIn("wingbox==9.8.7", (plugin / "scripts" / "wingbox-run").read_text(encoding="utf-8"))
            self.assertTrue(os.access(plugin / "scripts" / "wingbox-run", os.X_OK))


class LauncherTest(unittest.TestCase):
    def run_launcher(self, path: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/sh", str(LAUNCHER), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env={"PATH": path, "HOME": os.environ.get("HOME", "/tmp")},
        )

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        self.path = f"{self.bin}:/usr/bin:/bin"

    def test_version_and_mode(self) -> None:
        """/* REQ-PLUGIN-005: the launcher must be executable and pin uvx to the version of pyproject.toml. */"""
        self.assertTrue(LAUNCHER.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(set(re.findall(r"wingbox==([0-9][^\"' ]*)", LAUNCHER.read_text(encoding="utf-8"))), {VERSION})

    def test_installed_command_first(self) -> None:
        """/* REQ-PLUGIN-006: with `wingbox` on PATH the launcher must run it with the given arguments and pass its output and exit code through, even when uvx is present. */"""
        fake_command(self.bin, "wingbox", 'echo "wingbox $*"; exit 3')
        fake_command(self.bin, "uvx", 'echo "uvx $*"')
        result = self.run_launcher(self.path, "mcp", "--root", "x")
        self.assertEqual((result.stdout, result.returncode), ("wingbox mcp --root x\n", 3))

    def test_uvx_fallback(self) -> None:
        """/* REQ-PLUGIN-007: without `wingbox` on PATH the launcher must run `uvx --from wingbox==<version> wingbox <arguments>`; the hook goes through uvx offline only. */"""
        fake_command(self.bin, "uvx", 'echo "uvx $*"')
        result = self.run_launcher(self.path, "mcp")
        self.assertEqual(result.stdout, f"uvx --quiet --from wingbox=={VERSION} wingbox mcp\n")
        result = self.run_launcher(self.path, "hook", "pre-search")
        self.assertEqual(result.stdout, f"uvx --offline --quiet --from wingbox=={VERSION} wingbox hook pre-search\n")

    def test_neither_available(self) -> None:
        """/* REQ-PLUGIN-008: without `wingbox` and uvx the launcher must exit 127 with a message naming `pipx install wingbox`, and the hook must print nothing and exit 0. */"""
        result = self.run_launcher(self.path, "mcp")
        self.assertEqual(result.returncode, 127)
        self.assertIn("pipx install wingbox", result.stderr)
        self.assertEqual(result.stdout, "")
        result = self.run_launcher(self.path, "hook", "pre-search", stdin='{"tool_name": "Grep"}')
        self.assertEqual((result.stdout, result.stderr, result.returncode), ("", "", 0))

    def test_hook_never_blocks(self) -> None:
        """/* REQ-PLUGIN-009: when uvx fails in the hook (exit code 2 would block the tool call in Claude Code) the launcher must print nothing and exit 0. */"""
        fake_command(self.bin, "uvx", 'echo "error: offline" >&2; exit 2')
        result = self.run_launcher(self.path, "hook", "pre-search")
        self.assertEqual((result.stdout, result.stderr, result.returncode), ("", "", 0))


class PluginFolderTest(unittest.TestCase):
    def test_directory_rules(self) -> None:
        """/* REQ-PLUGIN-010: the plugin folder must hold no symbolic link, no top-level bin/, no CLAUDE.md, no system file, at most 512 files, no text file over 256 KiB, a README of at least 40 words and the LICENSE. */"""
        files = [p for p in PLUGIN.rglob("*")]
        self.assertFalse([p for p in files if p.is_symlink()])
        self.assertFalse((PLUGIN / "bin").exists())
        self.assertFalse((PLUGIN / "CLAUDE.md").exists())
        self.assertFalse([p for p in files if p.name in (".DS_Store", "Thumbs.db", "desktop.ini")])
        regular = [p for p in files if p.is_file()]
        self.assertLessEqual(len(regular), 512)
        for path in regular:
            if path.suffix not in (".png", ".svg", ".webp", ".jpg"):
                self.assertLess(path.stat().st_size, 256 * 1024, path)
        words = re.sub(r"```.*?```", "", (PLUGIN / "README.md").read_text(encoding="utf-8"), flags=re.S).split()
        self.assertGreaterEqual(len(words), 40)
        self.assertTrue((PLUGIN / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
