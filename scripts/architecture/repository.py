"""File system adapter: the files of a repository and a reader of small text files."""
from __future__ import annotations

import os
import stat
import sys
from typing import Callable, Optional

from .declared import NotRead

MAX_TEXT_BYTES = 2_000_000


def _excluded(skip_directories: frozenset[str], parent: str, name: str) -> bool:
    """The folder policy of discovery: a skipped name, except a first-party `vendor` folder and the like."""
    try:
        from ..lang.discovery import is_excluded_directory
        from ..lang.interface import DiscoveryExclusions
    except ImportError:  # loaded as a top-level script module
        from lang.discovery import is_excluded_directory
        from lang.interface import DiscoveryExclusions
    return is_excluded_directory(DiscoveryExclusions(directory_names=skip_directories, paths=frozenset()), parent, name)


def list_files(root: str, skip_directories: frozenset[str], ignored_paths: frozenset[str]) -> tuple[str, ...]:
    """Every file below root as a POSIX path relative to it, sorted; skipped folders and
    ignored paths (absolute, as the git ignore loader returns them) are left out."""
    found: list[str] = []
    root = os.path.realpath(root)
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(
            name for name in folders
            if not _excluded(skip_directories, directory, name) and os.path.join(directory, name) not in ignored_paths
            and not os.path.islink(os.path.join(directory, name))
        )
        for name in files:
            path = os.path.join(directory, name)
            if path in ignored_paths:
                continue
            found.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return tuple(sorted(found))


def read_text_file(root: str, relative: str) -> "str | None | NotRead":
    """The text of a repository-relative file; None when there is no such file; NotRead with the reason
    when it is there and cannot be read.

    Only a regular file inside the repository is opened: the real path decides, so a link that leaves the
    repository is refused, and a pipe or a device is never opened (reading one would block the command).
    """
    path = os.path.join(root, *relative.split("/"))
    try:
        status = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        if os.path.islink(path):
            return NotRead(relative, "it is a link to a file that does not exist")
        return None
    except (OSError, ValueError) as error:   # ValueError: a name with a null character
        return NotRead(relative, str(error))
    if not stat.S_ISREG(status.st_mode):
        return NotRead(relative, "it is not a regular file")
    real_root, real = os.path.normcase(os.path.realpath(root)), os.path.normcase(os.path.realpath(path))
    if real != real_root and not real.startswith(real_root.rstrip(os.sep) + os.sep):
        return NotRead(relative, "it is outside the repository")
    if status.st_size > MAX_TEXT_BYTES:
        return NotRead(relative, f"it is larger than {MAX_TEXT_BYTES} bytes")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        return NotRead(relative, str(error))


def file_reader(root: str) -> Callable[[str], "str | None | NotRead"]:
    """Reader of repository-relative text files that says why a file that is there was not read."""
    return lambda relative: read_text_file(root, relative)


def text_reader(root: str) -> Callable[[str], Optional[str]]:
    """Reader of repository-relative text files; None for a missing file, and for one that cannot be
    read (not a regular file inside the repository, large, not UTF-8), with a warning."""

    def read(relative: str) -> Optional[str]:
        text = read_text_file(root, relative)
        if isinstance(text, NotRead):
            print(f"[wingbox] WARNING: cannot read {relative}: {text.reason}", file=sys.stderr)
            return None
        return text

    return read
