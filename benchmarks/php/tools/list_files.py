"""List the PHP and Blade source files of a checkout, as absolute paths.

Usage: python3 -I list_files.py <root> <out.json> [--exclude-dir NAME ...]

The output is the input of `php_reference.php`. The directories skipped by
default are the ones the graph builder does not discover, so the reference sees
the same tree as the graph it is compared with. `--exclude-dir` adds a name to
that set (the benchmark run passes the names the builder was asked to skip).
"""
from __future__ import annotations

import json
import os
import sys

SKIPPED_DIRECTORIES = {
    "vendor",
    "node_modules",
    "dist",
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    ".tox",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".worktrees",
}


def _published_views(parent: str, name: str) -> bool:
    """`resources/views/vendor` holds published Blade views, not Composer packages."""
    if name != "vendor":
        return False
    parts = os.path.normpath(parent).replace("\\", "/").split("/")
    return parts[-2:] == ["resources", "views"]


def retained(name: str, parent: str, skipped: set[str]) -> bool:
    """Whether to descend into a directory, with the graph builder's policy."""
    if name not in skipped:
        return True
    return _published_views(parent, name)


def source_files(root: str, skipped: set[str]) -> list[str]:
    """Every ``.php`` / ``.blade.php`` file under ``root``, sorted."""
    files: list[str] = []
    for base, directories, names in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if retained(name, base, skipped) and not os.path.islink(os.path.join(base, name))
        )
        for name in sorted(names):
            if name.endswith(".php"):
                files.append(os.path.join(base, name))
    return files


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("usage: list_files.py <root> <out.json> [--exclude-dir NAME ...]", file=sys.stderr)
        return 2
    root = os.path.abspath(arguments[0])
    out_path = arguments[1]
    skipped = set(SKIPPED_DIRECTORIES)
    index = 2
    while index < len(arguments):
        if arguments[index] == "--exclude-dir" and index + 1 < len(arguments):
            skipped.add(arguments[index + 1])
            index += 2
        else:
            index += 1
    files = source_files(root, skipped)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(files, handle, indent=0)
        handle.write("\n")
    blade = sum(1 for name in files if name.endswith(".blade.php"))
    print(f"listed {len(files)} files ({len(files) - blade} php, {blade} blade) under {os.path.basename(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))