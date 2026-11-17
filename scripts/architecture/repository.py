"""File system adapter: the files of a repository and a reader of small text files."""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

MAX_TEXT_BYTES = 2_000_000


def list_files(root: str, skip_directories: frozenset[str], ignored_paths: frozenset[str]) -> tuple[str, ...]:
    """Every file below root as a POSIX path relative to it, sorted; skipped folders and
    ignored paths (absolute, as the git ignore loader returns them) are left out."""
    found: list[str] = []
    root = os.path.realpath(root)
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(
            name for name in folders
            if name not in skip_directories and os.path.join(directory, name) not in ignored_paths
            and not os.path.islink(os.path.join(directory, name))
        )
        for name in files:
            path = os.path.join(directory, name)
            if path in ignored_paths:
                continue
            found.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return tuple(sorted(found))


def text_reader(root: str) -> Callable[[str], Optional[str]]:
    """Reader of repository-relative text files; None for a missing, large or binary file."""

    def read(relative: str) -> Optional[str]:
        path = os.path.join(root, *relative.split("/"))
        try:
            if os.path.getsize(path) > MAX_TEXT_BYTES:
                print(f"[wingbox] WARNING: {relative} is larger than {MAX_TEXT_BYTES} bytes, not read", file=sys.stderr)
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as error:
            print(f"[wingbox] WARNING: cannot read {relative}: {error}", file=sys.stderr)
            return None

    return read
