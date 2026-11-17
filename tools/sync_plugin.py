#!/usr/bin/env python3
"""Keep plugins/wingbox in step with the repository.

The plugin folder is what Claude Code and Codex install, and nothing outside
it reaches the user, so it holds a copy of the skill (SKILL.md and
references/) and of LICENSE, and the version of pyproject.toml in both
manifests and the launcher's pinned `uvx --from git+https://github.com/aileron-tech/wingbox@<commit>`.
The plugin follows the latest release: the version and commit of the newest `vX.Y.Z` tag
(the Claude plugin directory accepts a git source pinned to a full commit, not a PyPI version).
Set WINGBOX_RELEASE="<version> <commit>" to override, as the tests do.

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
MANIFESTS = (Path(".claude-plugin") / "plugin.json", Path(".codex-plugin") / "plugin.json")
LAUNCHER = Path("scripts") / "wingbox-run"
SKILL_DIR = Path("skills") / "wingbox"
_PIN = re.compile(r"git\+https://github\.com/aileron-tech/wingbox@[0-9a-f]{40}")
SOURCE = "git+https://github.com/aileron-tech/wingbox@{commit}"


@dataclass(frozen=True)
class Wanted:
    """A plugin file and the bytes it must hold."""

    path: Path  # relative to the plugin folder
    content: bytes
    executable: bool = False


def project_version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def latest_release(repo: Path) -> tuple[str, str]:
    """(version, full commit) of the newest vX.Y.Z tag, or of WINGBOX_RELEASE."""
    override = os.environ.get("WINGBOX_RELEASE", "").split()
    if len(override) == 2:
        return override[0], override[1]
    listed = subprocess.run(["git", "-C", str(repo), "tag", "--list", "v[0-9]*", "--sort=-v:refname"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    tags = listed.stdout.split() if listed.returncode == 0 else []
    if not tags:
        # A source archive or a shallow clone without tags: keep the release the
        # plugin already pins, so --check still compares the copied files.
        pinned = _PIN.search((repo / PLUGIN_PATH / LAUNCHER).read_text(encoding="utf-8"))
        if pinned is None:
            raise SystemExit("no vX.Y.Z tag and no pinned commit: release first, or set WINGBOX_RELEASE")
        manifest = json.loads((repo / PLUGIN_PATH / MANIFESTS[0]).read_text(encoding="utf-8"))
        return manifest["version"], pinned.group(0).rsplit("@", 1)[1]
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{tags[0]}^{{commit}}"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
    return tags[0][1:], commit


def skill_sources(repo: Path) -> list[Path]:
    """SKILL.md and every file of references/, relative to the repository."""
    references = sorted(p.relative_to(repo) for p in (repo / "references").rglob("*") if p.is_file())
    return [Path("SKILL.md")] + references


def with_version(manifest_text: str, version: str) -> bytes:
    manifest = json.loads(manifest_text)
    manifest["version"] = version
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def wanted_files(repo: Path) -> list[Wanted]:
    """Every file the sync owns, with the content it must have."""
    plugin = repo / PLUGIN_PATH
    version, commit = latest_release(repo)
    files = [Wanted(SKILL_DIR / source, (repo / source).read_bytes()) for source in skill_sources(repo)]
    files.append(Wanted(Path("LICENSE"), (repo / "LICENSE").read_bytes()))
    for manifest in MANIFESTS:
        files.append(Wanted(manifest, with_version((plugin / manifest).read_text(encoding="utf-8"), version)))
    launcher = _PIN.sub(SOURCE.format(commit=commit), (plugin / LAUNCHER).read_text(encoding="utf-8"))
    files.append(Wanted(LAUNCHER, launcher.encode("utf-8"), executable=True))
    return files


def stale_files(repo: Path) -> list[Path]:
    """Plugin files whose content differs, files missing, and skill files with no source."""
    plugin = repo / PLUGIN_PATH
    wanted = wanted_files(repo)
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


def sync(repo: Path) -> list[Path]:
    """Rewrite the stale files and remove skill files that have no source; return what changed."""
    plugin = repo / PLUGIN_PATH
    changed = stale_files(repo)
    by_path = {item.path: item for item in wanted_files(repo)}
    for path in changed:
        target = plugin / path
        item = by_path.get(path)
        if item is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
        if item.executable:
            target.chmod(0o755)
    return changed


def main(args: list[str]) -> int:
    check = args == ["--check"]
    if args and not check:
        print(__doc__, file=sys.stderr)
        return 2
    changed = stale_files(REPO) if check else sync(REPO)
    for path in changed:
        print(f"{'stale' if check else 'updated'} {(PLUGIN_PATH / path).as_posix()}")
    if check and changed:
        print("run: python3 tools/sync_plugin.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
