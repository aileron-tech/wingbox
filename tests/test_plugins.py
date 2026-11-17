"""The Claude Code and Codex plugins under plugins/wingbox, their marketplaces and the OpenAI packages."""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "wingbox"
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
# The plugin follows the latest release tag (tools/sync_plugin.py), which lags pyproject.toml
# between a version bump and its tag.
_spec = __import__("importlib.util").util.spec_from_file_location("sync_plugin", REPO / "tools" / "sync_plugin.py")
_sync = __import__("importlib.util").util.module_from_spec(_spec)
sys.modules["sync_plugin"] = _sync
_spec.loader.exec_module(_sync)
PLUGIN_VERSION = _sync.latest_release(REPO)[0]
PIN = re.compile(r"git\+https://github\.com/aileron-tech/wingbox@([0-9a-f]{40})")
SKILL_SOURCES = ["SKILL.md"] + sorted(f"references/{p.name}" for p in (REPO / "references").iterdir() if p.is_file())


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


class ClaudePluginTest(unittest.TestCase):
    def test_manifest(self) -> None:
        """/* REQ-PLUGIN-001: the Claude Code manifest must name the plugin wingbox at the version of pyproject.toml, carry the listing metadata, an icon that is a square PNG of at least 512 pixels, a PreToolUse hook on Grep|Glob|Read that runs `uvx --offline --quiet --from <pin> wingbox hook pre-search` and an MCP server that runs `uvx --quiet --from <pin> wingbox mcp`, both in exec form (`command` uvx, a real executable on every system, and the arguments in `args`: no shell, so no sh, Git Bash or PowerShell is needed), the hooks with `"onFailure": "continue"` so a missing uvx never blocks a tool call, and no file of the plugin started as a script. */"""
        manifest = load(PLUGIN / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "wingbox")
        self.assertEqual(manifest["version"], PLUGIN_VERSION)
        self.assertEqual(manifest["author"]["name"], "Aileron Technologies")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["homepage"], "https://wingbox.dev")
        self.assertEqual(manifest["repository"], "https://github.com/aileron-tech/wingbox")
        self.assertTrue(manifest["description"] and manifest["keywords"])
        for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"):
            self.assertTrue(manifest[key].startswith("https://"), key)
        width, height = png_size(PLUGIN / manifest["icon"])
        self.assertEqual(width, height)
        self.assertGreaterEqual(width, 512)
        (group,) = manifest["hooks"]["PreToolUse"]
        self.assertEqual(group["matcher"], "Grep|Glob|Read")
        (hook,) = group["hooks"]
        source = "git+https://github.com/aileron-tech/wingbox@" + PIN.findall(json.dumps(manifest))[0]
        self.assertEqual(
            hook,
            {"type": "command", "command": "uvx", "args": ["--offline", "--quiet", "--from", source, "wingbox", "hook", "pre-search"],
             "timeout": 10, "onFailure": "continue"},
        )
        server = manifest["mcpServers"]["wingbox"]
        self.assertEqual(server, {"command": "uvx", "args": ["--quiet", "--from", source, "wingbox", "mcp"]})
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", json.dumps(manifest))
        self.assertFalse((PLUGIN / "scripts").exists())

    def test_edit_hook(self) -> None:
        """/* REQ-FRESH-012: the Claude Code manifest must carry one PostToolUse hook on Edit|Write|MultiEdit that runs `uvx --offline --quiet --from <pin> wingbox hook dirty` in exec form with `"onFailure": "continue"`, and the search hook a timeout of 10 seconds (it may wait for a refresh of the graph). */"""
        manifest = load(PLUGIN / ".claude-plugin" / "plugin.json")
        (group,) = manifest["hooks"]["PostToolUse"]
        self.assertEqual(group["matcher"], "Edit|Write|MultiEdit")
        (hook,) = group["hooks"]
        self.assertEqual(hook["command"], "uvx")
        self.assertEqual(hook["args"][:3], ["--offline", "--quiet", "--from"])
        self.assertEqual(hook["args"][4:], ["wingbox", "hook", "dirty"])
        self.assertEqual((hook["timeout"], hook["onFailure"]), (5, "continue"))
        (search,) = manifest["hooks"]["PreToolUse"]
        self.assertEqual(search["hooks"][0]["timeout"], 10)

    def test_marketplace(self) -> None:
        """/* REQ-PLUGIN-002: `.claude-plugin/marketplace.json` at the repository root must list the plugin wingbox from ./plugins/wingbox, so that `/plugin marketplace add aileron-tech/wingbox` then `/plugin install wingbox@aileron-tech` installs it. */"""
        marketplace = load(REPO / ".claude-plugin" / "marketplace.json")
        self.assertEqual(marketplace["name"], "aileron-tech")
        self.assertEqual(marketplace["owner"]["name"], "Aileron Technologies")
        (entry,) = marketplace["plugins"]
        self.assertEqual(entry["name"], "wingbox")
        self.assertEqual(entry["source"], "./plugins/wingbox")
        self.assertTrue((REPO / entry["source"] / ".claude-plugin" / "plugin.json").is_file())


class CodexPluginTest(unittest.TestCase):
    def test_manifest(self) -> None:
        """/* REQ-PLUGIN-011: the Codex manifest `.codex-plugin/plugin.json` must name the plugin wingbox at the version of pyproject.toml, declare ./skills/ and ./codex.mcp.json (not .mcp.json, which the Claude plugin directory reads as a second, shadowed server), require a local executor, and give the listing fields within the directory limits (display name and subtitle at most 30 characters, at most three starter prompts of 128 characters, square icons that exist); `codex.mcp.json` must start `wingbox mcp` from PATH so that it runs in the session folder. */"""
        manifest = load(PLUGIN / ".codex-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "wingbox")
        self.assertEqual(manifest["version"], PLUGIN_VERSION)
        self.assertEqual(manifest["author"]["name"], "Aileron Technologies")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./codex.mcp.json")
        self.assertFalse((PLUGIN / ".mcp.json").exists())
        self.assertIs(manifest["requires_local_executor"], True)
        interface = manifest["interface"]
        self.assertLessEqual(len(interface["displayName"]), 30)
        self.assertLessEqual(len(interface["shortDescription"]), 30)
        self.assertLessEqual(len(interface["longDescription"]), 4000)
        self.assertLessEqual(len(interface["developerName"]), 80)
        self.assertTrue(interface["category"])
        self.assertLessEqual(len(interface["capabilities"]), 20)
        self.assertLessEqual(len(interface["defaultPrompt"]), 3)
        for prompt in interface["defaultPrompt"]:
            self.assertLessEqual(len(prompt), 128)
        for key in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
            self.assertTrue(interface[key].startswith("https://"), key)
        for key in ("composerIcon", "logo"):
            width, height = png_size(PLUGIN / interface[key])
            self.assertEqual(width, height)
        self.assertFalse((PLUGIN / "hooks" / "hooks.json").exists(), "Codex loads hooks/hooks.json by default")
        servers = load(PLUGIN / "codex.mcp.json")["mcpServers"]
        self.assertEqual(servers, {"wingbox": {"command": "wingbox", "args": ["mcp"]}})

    def test_marketplace(self) -> None:
        """/* REQ-PLUGIN-012: `.agents/plugins/marketplace.json` must list wingbox from the local path ./plugins/wingbox with an install policy, an authentication policy and a category, so that `codex plugin marketplace add aileron-tech/wingbox` offers it. */"""
        marketplace = load(REPO / ".agents" / "plugins" / "marketplace.json")
        self.assertEqual(marketplace["name"], "aileron-tech")
        (entry,) = marketplace["plugins"]
        self.assertEqual(entry["name"], "wingbox")
        self.assertEqual(entry["source"], {"source": "local", "path": "./plugins/wingbox"})
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        self.assertIn(entry["policy"]["authentication"], ("ON_INSTALL", "ON_USE"))
        self.assertTrue(entry["category"])
        self.assertTrue((REPO / "plugins" / "wingbox" / ".codex-plugin" / "plugin.json").is_file())


class SkillCopyTest(unittest.TestCase):
    def test_copy_is_identical(self) -> None:
        """/* REQ-PLUGIN-003: the plugin's skill must be byte for byte SKILL.md and references/ of the repository, and its LICENSE the repository's; `tools/sync_plugin.py --check` must pass on the repository. */"""
        skill = PLUGIN / "skills" / "wingbox"
        copied = sorted(p.relative_to(skill).as_posix() for p in skill.rglob("*") if p.is_file())
        self.assertEqual(copied, sorted(SKILL_SOURCES))
        for name in SKILL_SOURCES:
            self.assertEqual((skill / name).read_bytes(), (REPO / name).read_bytes(), name)
        self.assertEqual((PLUGIN / "LICENSE").read_bytes(), (REPO / "LICENSE").read_bytes())
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "sync_plugin.py"), "--check"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sync_repairs_drift(self) -> None:
        """/* REQ-PLUGIN-004: after SKILL.md or the version in pyproject.toml changes, `tools/sync_plugin.py --check` must exit 1 and name each stale file, and `tools/sync_plugin.py` must rewrite the skill copy, the version in both manifests and the pinned commit of every uvx command. */"""
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
            fake = "f" * 40
            os.environ["WINGBOX_RELEASE"] = f"9.8.7 {fake}"
            self.addCleanup(os.environ.pop, "WINGBOX_RELEASE", None)
            check = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
            self.assertEqual(check.returncode, 1)
            for stale in ("skills/wingbox/SKILL.md", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
                self.assertIn(stale, check.stdout)
            subprocess.run([sys.executable, script], check=True, capture_output=True)
            self.assertEqual(subprocess.run([sys.executable, script, "--check"], capture_output=True).returncode, 0)
            plugin = copy / "plugins" / "wingbox"
            self.assertTrue((plugin / "skills" / "wingbox" / "SKILL.md").read_text(encoding="utf-8").endswith("New line.\n"))
            self.assertEqual(load(plugin / ".claude-plugin" / "plugin.json")["version"], "9.8.7")
            self.assertEqual(load(plugin / ".codex-plugin" / "plugin.json")["version"], "9.8.7")
            text = (plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
            self.assertEqual(PIN.findall(text), [fake] * 3)   # both hooks and the MCP server


class NoGitTest(unittest.TestCase):
    def test_check_without_git(self) -> None:
        """/* REQ-PLUGIN-016: in a copy of the repository without .git or tags (a source archive, a shallow CI clone) `tools/sync_plugin.py --check` must pass, keeping the release the manifest already pins. */"""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            copy.mkdir()
            for name in ("pyproject.toml", "SKILL.md", "LICENSE"):
                shutil.copy(REPO / name, copy / name)
            for folder in ("references", "plugins", "tools"):
                shutil.copytree(REPO / folder, copy / folder)
            env = {k: v for k, v in os.environ.items() if k != "WINGBOX_RELEASE"}
            env["GIT_CEILING_DIRECTORIES"] = tmp
            result = subprocess.run([sys.executable, str(copy / "tools" / "sync_plugin.py"), "--check"], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ReleaseTagTest(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def test_the_release_is_the_newest_tag_that_is_an_ancestor_of_head(self) -> None:
        """/* REQ-PLUGIN-017: the plugin follows the newest vX.Y.Z tag that is an ancestor of HEAD, so a branch cut before a release keeps passing when the release tag lands on main; with tags present but none reachable (a shallow clone) the sync stops with a message that says to fetch the tags and the history. */"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            os.environ.pop("WINGBOX_RELEASE", None)
            repo = Path(tmp)
            self.git(repo, "init", "-q")
            for name in ("user.email", "user.name"):
                self.git(repo, "config", name, "tests@example.invalid")
            self.git(repo, "commit", "-q", "--allow-empty", "-m", "one")
            self.git(repo, "tag", "v1.0.0")
            first = self.git(repo, "rev-parse", "HEAD")
            self.git(repo, "switch", "-q", "-c", "feature")
            self.git(repo, "commit", "-q", "--allow-empty", "-m", "feature work")
            self.git(repo, "switch", "-q", "-")
            self.git(repo, "commit", "-q", "--allow-empty", "-m", "release")
            self.git(repo, "tag", "v1.1.0")
            self.git(repo, "switch", "-q", "feature")
            self.assertEqual(("1.0.0", first), _sync.latest_release(repo))
            self.git(repo, "switch", "-q", "--orphan", "lonely")
            self.git(repo, "commit", "-q", "--allow-empty", "-m", "no tag reachable")
            with self.assertRaises(SystemExit) as stopped:
                _sync.latest_release(repo)
            self.assertIn("fetch the tags", str(stopped.exception))


class PinTest(unittest.TestCase):
    def test_every_uvx_command_is_pinned_to_a_release_commit(self) -> None:
        """/* REQ-PLUGIN-005: every uvx command of the Claude Code manifest (both hooks and the MCP server) must name the source as a literal git URL pinned to the same full 40-character commit (the Claude plugin directory refuses a PyPI version, a variable or a short commit as a pin), the commit of a vX.Y.Z tag when the tags are present. */"""
        text = CLAUDE_MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("wingbox==", text)
        self.assertNotIn("$", text.replace("${CLAUDE_PLUGIN_ROOT}", ""))
        commits = PIN.findall(text)
        self.assertEqual(len(commits), 3)
        self.assertEqual(len(set(commits)), 1)
        tags = subprocess.run(["git", "-C", str(REPO), "tag", "--points-at", commits[0]], capture_output=True, text=True).stdout.split()
        if subprocess.run(["git", "-C", str(REPO), "tag", "--list", "v*"], capture_output=True, text=True).stdout.strip():
            self.assertTrue(any(re.fullmatch(r"v\d+\.\d+\.\d+", t) for t in tags), tags)


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


class PurposeTest(unittest.TestCase):
    """The plugin is found by its purpose: code graphs of large repositories and monorepos."""

    PURPOSE = "Code graph for large repositories and monorepos"
    KEYWORDS = {
        "code-graph", "blast-radius", "impact-analysis", "dependency-graph", "code-search", "architecture",
        "local-first", "mcp", "monorepo", "large-codebase", "codebase-map", "call-graph", "refactoring",
        "code-navigation",
    }

    def skill_description(self) -> str:
        front = (REPO / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        folded = front.split("description: >", 1)[1]
        return " ".join(line.strip() for line in folded.splitlines() if line.startswith("  "))

    def test_descriptions_and_keywords(self) -> None:
        """/* REQ-PLUGIN-014: the descriptions of both manifests and of the Claude Code marketplace entry must start with "Code graph for large repositories and monorepos", the Codex subtitle and long description must name monorepos, and the keywords of both manifests must include monorepo, large-codebase, codebase-map, call-graph, refactoring and code-navigation besides the earlier ones. */"""
        claude = load(PLUGIN / ".claude-plugin" / "plugin.json")
        codex = load(PLUGIN / ".codex-plugin" / "plugin.json")
        (entry,) = load(REPO / ".claude-plugin" / "marketplace.json")["plugins"]
        for description in (claude["description"], codex["description"], entry["description"]):
            self.assertTrue(description.startswith(self.PURPOSE), description)
        self.assertIn("monorepo", codex["interface"]["shortDescription"])
        self.assertTrue(codex["interface"]["longDescription"].startswith(self.PURPOSE))
        for manifest in (claude, codex):
            self.assertLessEqual(self.KEYWORDS, set(manifest["keywords"]))

    def test_skill_description(self) -> None:
        """/* REQ-PLUGIN-015: the description of SKILL.md must name large repositories and monorepos and the questions an agent hears (what a change will break, who uses a file or function, where a feature is implemented, which files to read first) and the moments (a refactor, a cross-package change), within the 1,024 characters of the Agent Skills format. */"""
        description = self.skill_description()
        self.assertLessEqual(len(description), 1024)
        for phrase in (
            "large repositories and monorepos",
            "what a change will break",
            "who uses",
            "where a feature is implemented",
            "which files to read first",
            "refactor",
            "cross-package change",
        ):
            self.assertIn(phrase, description)


class OpenAIPackageTest(unittest.TestCase):
    def test_zip_packages(self) -> None:
        """/* REQ-PLUGIN-013: `tools/build_openai_plugin.py --out DIR` must write the skills-only ZIP wingbox-openai-plugin-<version>.zip (Codex manifest with requires_local_executor and no MCP, skill, icon, the package README of docs/store, LICENSE; no .mcp.json, hooks, .claude-plugin, scripts or bin) and wingbox-openai-plugin-<version>-local-mcp.zip that also holds .mcp.json and declares it. */"""
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(REPO / "tools" / "build_openai_plugin.py"), "--out", tmp],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            skills_only = Path(tmp) / f"wingbox-openai-plugin-{PLUGIN_VERSION}.zip"
            local_mcp = Path(tmp) / f"wingbox-openai-plugin-{PLUGIN_VERSION}-local-mcp.zip"
            # The tool prints resolved paths; Windows may name the temporary folder in its 8.3 form (RUNNER~1).
            self.assertIn(os.path.realpath(skills_only), result.stdout)
            with zipfile.ZipFile(skills_only) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read(".codex-plugin/plugin.json"))
                self.assertEqual(archive.read("skills/wingbox/SKILL.md"), (REPO / "SKILL.md").read_bytes())
                readme = archive.read("README.md")
            self.assertEqual(readme, (REPO / "docs" / "store" / "openai-package-README.md").read_bytes())
            self.assertTrue({"README.md", "LICENSE", "assets/icon.png", "skills/wingbox/SKILL.md"} <= names)
            self.assertFalse([n for n in names if n.startswith((".mcp.json", "hooks", ".claude-plugin", "scripts", "bin/"))])
            self.assertIs(manifest["requires_local_executor"], True)
            self.assertNotIn("mcpServers", manifest)
            self.assertEqual(manifest["version"], PLUGIN_VERSION)
            with zipfile.ZipFile(local_mcp) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read(".codex-plugin/plugin.json"))
            self.assertIn(".mcp.json", names)
            self.assertEqual(manifest["mcpServers"], "./.mcp.json")


if __name__ == "__main__":
    unittest.main()
