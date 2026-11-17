#!/usr/bin/env python3
"""Build the Claude Desktop extension: an MCP Bundle (.mcpb) around `wingbox mcp`.

    python3 tools/build_mcpb.py [--out DIR]    (default DIR: dist)

writes DIR/wingbox-<version>.mcpb, the version of pyproject.toml. The bundle uses manifest
version 0.4 and the server type `uv` (MCPB spec, github.com/modelcontextprotocol/mcpb,
MANIFEST.md "UV Runtime"): it holds no Python packages, only a pyproject.toml that pins
`wingbox==<version>` from PyPI; the host installs Python and the dependencies (tree-sitter and
its parsers ship wheels for macOS, Windows and Linux) with uv and runs `src/server.py`, which
starts `wingbox mcp --root <the folder the user picked> --build-missing`.

The tool list of the manifest is generated from the server's own definitions
(src/wingbox/mcp_server.py), and the archive is deterministic: sorted entries, fixed times
and modes. The bundle is not committed.
"""
from __future__ import annotations

import json
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ICON = REPO / "plugins" / "wingbox" / "assets" / "icon.png"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # same bytes for the same files
MANIFEST_VERSION = "0.4"           # the first version with the `uv` server type
SLOGAN = "Give your coding agent a map of your codebase. Half the cost, no missed files."
SHORT_DESCRIPTION = "A map of your codebase for coding agents: what a change touches, where a symbol is used."
LONG_DESCRIPTION = f"""{SLOGAN}

Wingbox builds a code graph of the repository you choose and answers from it before Claude reads
source files: what a change will break, two hops away and across packages (blast radius); who uses
a file, function or type; where a feature is implemented; which modules depend on each other and
which dependencies cross an architecture layer; and which few files to read first.

Pick the repository in the extension settings. The first use builds its graph (the `.wingbox/`
folder in the repository); later answers bring it up to date with the files you change.

Everything runs on your computer. Wingbox parses source files with tree-sitter and never runs
your code; building and querying the graph open no network connection, and nothing is sent to a
model or a service. Installing the extension downloads the open-source `wingbox` package (MIT)
and its parsers from PyPI once.
"""

SERVER_PY = '''"""Entry point of the Wingbox extension for Claude Desktop: `wingbox mcp` with the bundle's arguments."""
import sys

from wingbox import cli

sys.exit(cli.main(["mcp", *sys.argv[1:]]))
'''


def project_version(repo: Path = REPO) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def server_tools(repo: Path = REPO) -> list[dict[str, str]]:
    """Name and description of every tool of `wingbox mcp`, from the server's own definitions."""
    sys.path.insert(0, str(repo / "src"))
    from wingbox import mcp_server

    return [{"name": tool.name, "description": tool.description} for tool in mcp_server.TOOLS]


def manifest(version: str, tools: list[dict[str, str]]) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "name": "wingbox",
        "display_name": "Wingbox",
        "version": version,
        "description": SHORT_DESCRIPTION,
        "long_description": LONG_DESCRIPTION,
        "author": {"name": "Aileron Technologies", "url": "https://wingbox.dev"},
        "repository": {"type": "git", "url": "https://github.com/aileron-tech/wingbox"},
        "homepage": "https://wingbox.dev",
        "documentation": "https://github.com/aileron-tech/wingbox#readme",
        "support": "https://github.com/aileron-tech/wingbox/issues",
        "icon": "icon.png",
        "server": {
            "type": "uv",
            "entry_point": "src/server.py",
            "mcp_config": {
                "command": "uv",
                "args": [
                    "run", "--directory", "${__dirname}", "--quiet", "src/server.py",
                    "--root", "${user_config.repository}", "--build-missing",
                ],
            },
        },
        "tools": tools,
        "keywords": ["code-graph", "blast-radius", "impact-analysis", "dependency-graph", "architecture",
                     "monorepo", "local-first"],
        "license": "MIT",
        "compatibility": {
            "platforms": ["darwin", "win32", "linux"],
            "runtimes": {"python": ">=3.11"},
        },
        "user_config": {
            "repository": {
                "type": "directory",
                "title": "Repository",
                "description": "The repository Wingbox maps. The first use builds its code graph in the .wingbox folder there.",
                "required": True,
            }
        },
    }


def bundle_pyproject(version: str) -> str:
    return (
        "[project]\n"
        'name = "wingbox-claude-desktop"\n'
        f'version = "{version}"\n'
        'description = "Claude Desktop extension: the Wingbox MCP server"\n'
        'requires-python = ">=3.11"\n'
        f'dependencies = ["wingbox=={version}"]\n'
    )


def bundle_files(repo: Path = REPO) -> dict[str, bytes]:
    """Every file of the bundle, by its path in the archive."""
    version = project_version(repo)
    return {
        "manifest.json": (json.dumps(manifest(version, server_tools(repo)), indent=2, ensure_ascii=True) + "\n").encode(),
        "pyproject.toml": bundle_pyproject(version).encode(),
        "src/server.py": SERVER_PY.encode(),
        "icon.png": ICON.read_bytes(),
        "LICENSE": (repo / "LICENSE").read_bytes(),
    }


def write_bundle(files: dict[str, bytes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])


def main(args: list[str]) -> int:
    out = REPO / "dist"
    if args[:1] == ["--out"] and len(args) == 2:
        out = Path(args[1])
    elif args:
        print(__doc__, file=sys.stderr)
        return 2
    path = out / f"wingbox-{project_version()}.mcpb"
    write_bundle(bundle_files(), path)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
