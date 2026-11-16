#!/usr/bin/env python3
"""Build the ZIP packages for the OpenAI plugin directory (ChatGPT and Codex).

    python3 tools/build_openai_plugin.py [--out DIR]    (default DIR: dist)

writes two archives from plugins/wingbox, in the Codex layout that
`codex plugin add` installs (.codex-plugin/plugin.json at the archive root):

- wingbox-openai-plugin-<version>.zip: skills only. The wingbox skill and the
  listing; `requires_local_executor` marks it Desktop only. No MCP server, no
  hook, no launcher. This is the package for the public submission.
- wingbox-openai-plugin-<version>-local-mcp.zip: the same plus .mcp.json, the
  local stdio server `wingbox mcp`. Local MCP submissions are open only to
  allowlisted organizations; keep it for that.

Neither archive holds the Claude Code manifest, the hook or the launcher
script; their README.md is docs/store/openai-package-README.md. The
archives are not committed. The build refuses a stale plugin folder: run
`python3 tools/sync_plugin.py` first.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_plugin  # noqa: E402

REPO = sync_plugin.REPO
PLUGIN = REPO / sync_plugin.PLUGIN_PATH
CODEX_MANIFEST = Path(".codex-plugin") / "plugin.json"
SHARED = ("LICENSE", "assets", "skills")
PACKAGE_README = REPO / "docs" / "store" / "openai-package-README.md"  # the plugin README describes the Claude Code hook too
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # same bytes for the same files


def package_files(plugin: Path) -> list[Path]:
    """The files both archives take from the plugin folder, relative to it, sorted."""
    files: list[Path] = []
    for name in SHARED:
        path = plugin / name
        files.extend([path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()])
    return sorted(p.relative_to(plugin) for p in files if p.name != ".DS_Store")


def manifest_for(plugin: Path, with_mcp: bool) -> bytes:
    manifest = json.loads((plugin / CODEX_MANIFEST).read_text(encoding="utf-8"))
    if not with_mcp:
        manifest.pop("mcpServers", None)
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _add(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content)


def build(plugin: Path, target: Path, with_mcp: bool) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        _add(archive, CODEX_MANIFEST.as_posix(), manifest_for(plugin, with_mcp))
        if with_mcp:
            _add(archive, ".mcp.json", (plugin / ".mcp.json").read_bytes())
        _add(archive, "README.md", PACKAGE_README.read_bytes())
        for path in package_files(plugin):
            _add(archive, path.as_posix(), (plugin / path).read_bytes())
    return target


def main(args: list[str]) -> int:
    out = REPO / "dist"
    if args[:1] == ["--out"] and len(args) == 2:
        out = Path(args[1]).resolve()
    elif args:
        print(__doc__, file=sys.stderr)
        return 2
    stale = sync_plugin.stale_files(REPO)
    if stale:
        for path in stale:
            print(f"stale {sync_plugin.PLUGIN_PATH / path}", file=sys.stderr)
        print("run: python3 tools/sync_plugin.py", file=sys.stderr)
        return 1
    version = sync_plugin.project_version(REPO)
    for suffix, with_mcp in (("", False), ("-local-mcp", True)):
        path = build(PLUGIN, out / f"wingbox-openai-plugin-{version}{suffix}.zip", with_mcp)
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
