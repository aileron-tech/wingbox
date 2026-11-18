#!/usr/bin/env python3
"""Keep plugins/wingbox and plugins/wingbox-skill in step with the repository.

The plugin folder is what Claude Code and Codex install, and nothing outside
it reaches the user, so it holds a copy of the skill (SKILL.md and
references/) and of LICENSE, the version in both manifests, and in the Claude Code manifest the
pinned `uvx --from git+https://github.com/aileron-tech/wingbox@<commit>` of every hook and of the MCP
server (a literal in each command: the plugin directory reads the pin from it).
The plugin follows the latest release: the version and commit of the newest `vX.Y.Z` tag that is an ancestor of HEAD
(the Claude plugin directory accepts a git source pinned to a full commit, not a PyPI version).
Set WINGBOX_RELEASE="<version> <commit>" to override, as the tests do.

plugins/wingbox-skill is the same skill alone, for the Claude plugin directory, which takes hooks, a local
MCP server and scripts only from organizations it has verified: one manifest at the same version, the skill,
LICENSE, and nothing that is not Markdown, text, JSON, YAML or an image. One run keeps both folders current.

    python3 tools/sync_plugin.py           rewrite what is stale
    python3 tools/sync_plugin.py --check   list what is stale, exit 1 if anything is

Run it after a change to SKILL.md, references/ or the version.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_PATH = Path("plugins") / "wingbox"
SKILL_PLUGIN_PATH = Path("plugins") / "wingbox-skill"
MANIFESTS = (Path(".claude-plugin") / "plugin.json", Path(".codex-plugin") / "plugin.json")
# Files that hold the pinned uvx source but no version: the MCP server of the Codex plugin.
PINNED = (Path("codex.mcp.json"),)
SKILL_DIR = Path("skills") / "wingbox"
# What the Claude plugin directory takes from an organization it has not verified.
DIRECTORY_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")


@dataclass(frozen=True)
class Bundle:
    """A plugin folder the sync keeps current."""

    path: Path  # relative to the repository
    manifests: tuple[Path, ...]
    pinned: tuple[Path, ...]
    directory_rules: bool  # skill files outside DIRECTORY_SUFFIXES, or starting with #!, are left out


FULL = Bundle(PLUGIN_PATH, MANIFESTS, PINNED, False)
SKILL_ONLY = Bundle(SKILL_PLUGIN_PATH, MANIFESTS[:1], (), True)
BUNDLES = (FULL, SKILL_ONLY)
_PIN = re.compile(r"git\+https://github\.com/aileron-tech/wingbox@[0-9a-f]{40}")
SOURCE = "git+https://github.com/aileron-tech/wingbox@{commit}"


@dataclass(frozen=True)
class Wanted:
    """A plugin file and the bytes it must hold."""

    path: Path  # relative to the plugin folder
    content: bytes


def project_version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def latest_release(repo: Path) -> tuple[str, str]:
    """(version, full commit) of the newest vX.Y.Z tag that is an ancestor of HEAD, or of WINGBOX_RELEASE.

    An ancestor, not the newest tag of the repository: a branch cut before a release keeps the
    release it was cut from when the next tag lands on main."""
    override = os.environ.get("WINGBOX_RELEASE", "").split()
    if len(override) == 2:
        return override[0], override[1]
    listed = _git(repo, "tag", "--list", "v[0-9]*")
    tags = listed.stdout.split() if listed.returncode == 0 else []
    if tags:
        described = _git(repo, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD")
        if described.returncode != 0:
            raise SystemExit("no vX.Y.Z tag is an ancestor of HEAD (a shallow clone?): fetch the tags and the "
                             "history (git fetch --tags --unshallow), or set WINGBOX_RELEASE")
        tags = [described.stdout.strip()]
    if not tags:
        # A source archive or a shallow clone without tags: keep the release the
        # plugin already pins, so --check still compares the copied files.
        pinned = _PIN.search((repo / PLUGIN_PATH / MANIFESTS[0]).read_text(encoding="utf-8"))
        if pinned is None:
            raise SystemExit("no vX.Y.Z tag and no pinned commit: release first, or set WINGBOX_RELEASE")
        manifest = json.loads((repo / PLUGIN_PATH / MANIFESTS[0]).read_text(encoding="utf-8"))
        return manifest["version"], pinned.group(0).rsplit("@", 1)[1]
    commit = _git(repo, "rev-parse", f"{tags[0]}^{{commit}}").stdout.strip()
    return tags[0][1:], commit


def skill_sources(repo: Path) -> list[Path]:
    """SKILL.md and every file of references/, relative to the repository."""
    references = sorted(p.relative_to(repo) for p in (repo / "references").rglob("*") if p.is_file())
    return [Path("SKILL.md")] + references


def with_release(manifest_text: str, version: str, commit: str) -> bytes:
    """The manifest at version, every pinned uvx source pointing at commit."""
    manifest = json.loads(_PIN.sub(SOURCE.format(commit=commit), manifest_text))
    manifest["version"] = version
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def directory_takes(path: Path, content: bytes) -> bool:
    """Whether the plugin directory takes this file from an organization it has not verified."""
    return path.suffix.lower() in DIRECTORY_SUFFIXES and not content.startswith(b"#!")


def wanted_files(repo: Path, bundle: Bundle = FULL) -> list[Wanted]:
    """Every file the sync owns in the bundle, with the content it must have."""
    plugin = repo / bundle.path
    version, commit = latest_release(repo)
    files = [Wanted(SKILL_DIR / source, (repo / source).read_bytes()) for source in skill_sources(repo)]
    if bundle.directory_rules:
        files = [item for item in files if directory_takes(item.path, item.content)]
    files.append(Wanted(Path("LICENSE"), (repo / "LICENSE").read_bytes()))
    for manifest in bundle.manifests:
        files.append(Wanted(manifest, with_release((plugin / manifest).read_text(encoding="utf-8"), version, commit)))
    for pinned in bundle.pinned:
        text = _PIN.sub(SOURCE.format(commit=commit), (plugin / pinned).read_text(encoding="utf-8"))
        files.append(Wanted(pinned, text.encode("utf-8")))
    return files


def stale_files(repo: Path, bundle: Bundle = FULL) -> list[Path]:
    """Bundle files whose content differs, files missing, and skill files with no source."""
    plugin = repo / bundle.path
    wanted = wanted_files(repo, bundle)
    stale = [
        item.path
        for item in wanted
        if not (plugin / item.path).is_file() or (plugin / item.path).read_bytes() != item.content
    ]
    owned = {item.path for item in wanted}
    extra = sorted(
        p.relative_to(plugin) for p in (plugin / SKILL_DIR).rglob("*") if p.is_file() and p.relative_to(plugin) not in owned
    )
    return stale + extra


def sync(repo: Path, bundle: Bundle = FULL) -> list[Path]:
    """Rewrite the stale files and remove skill files that have no source; return what changed."""
    plugin = repo / bundle.path
    changed = stale_files(repo, bundle)
    by_path = {item.path: item for item in wanted_files(repo, bundle)}
    for path in changed:
        target = plugin / path
        item = by_path.get(path)
        if item is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
    return changed


def main(args: list[str]) -> int:
    check = args == ["--check"]
    if args and not check:
        print(__doc__, file=sys.stderr)
        return 2
    changed: list[Path] = []
    for bundle in BUNDLES:
        if not (REPO / bundle.path).is_dir():
            continue   # a copy of the repository that holds one plugin folder only
        paths = stale_files(REPO, bundle) if check else sync(REPO, bundle)
        changed.extend(bundle.path / path for path in paths)
    for path in changed:
        print(f"{'stale' if check else 'updated'} {path.as_posix()}")
    if check and changed:
        print("run: python3 tools/sync_plugin.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
