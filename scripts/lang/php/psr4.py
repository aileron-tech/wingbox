"""PSR-4 namespace prefixes read from the composer.json files of a repository.

Composer merges `autoload.psr-4` and `autoload-dev.psr-4` into one prefix map,
the longest matching prefix winning. A repository can carry several manifests
(the root one and one per module), so nested files contribute their own
prefixes and directories.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions


# Composer loads both sections for local development; resolution must see both.
_AUTOLOAD_SECTIONS = ("autoload", "autoload-dev")


def _read_composer(path: str) -> dict[str, object] | None:
    """Read one composer.json; a broken manifest is reported and skipped."""
    try:
        # utf-8-sig also reads plain UTF-8 and drops a byte order mark.
        with open(path, "r", encoding="utf-8-sig") as composer_file:
            data = json.loads(composer_file.read())
    except (OSError, ValueError, RecursionError) as error:
        # ValueError: text that is not JSON or not UTF-8; RecursionError: nested deeper than the reader goes.
        print(
            f"[code-graph] Cannot read composer autoload from {path}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return None
    return data if isinstance(data, dict) else None


def _psr4_directories(composer_directory: str, value: object) -> tuple[Path, ...]:
    """List the directories one `psr-4` entry maps its prefix to.

    Composer accepts one directory or a list of directories per prefix; a value
    of another type is malformed and contributes nothing.
    """
    if isinstance(value, str):
        declared = (value,)
    elif isinstance(value, list):
        declared = tuple(item for item in value if isinstance(item, str))
    else:
        return ()
    directories = (
        Path(os.path.normpath(os.path.join(composer_directory, relative)))
        for relative in declared
        if relative
    )
    return tuple(dict.fromkeys(directories))


def _psr4_entries(
    composer_directory: str,
    data: dict[str, object],
) -> tuple[tuple[str, tuple[Path, ...]], ...]:
    """Read the prefix entries of one manifest, both autoload sections."""
    entries: list[tuple[str, tuple[Path, ...]]] = []
    for section in _AUTOLOAD_SECTIONS:
        autoload = data.get(section)
        if not isinstance(autoload, dict):
            continue
        psr4 = autoload.get("psr-4")
        if not isinstance(psr4, dict):
            continue
        for raw_prefix, value in psr4.items():
            if not isinstance(raw_prefix, str):
                continue
            prefix = raw_prefix.strip().lstrip("\\")
            directories = _psr4_directories(composer_directory, value)
            if directories:
                entries.append((prefix, directories))
    return tuple(entries)


@dataclass(frozen=True)
class Psr4Map:
    """Every PSR-4 prefix of the repository, longest prefix first."""

    prefixes: tuple[tuple[str, tuple[Path, ...]], ...]

    @classmethod
    def create(cls, root: Path, exclusions: DiscoveryExclusions) -> Psr4Map:
        root = Path(root).resolve()
        roots_by_prefix: dict[str, dict[str, Path]] = {}
        for directory, directory_names, file_names in os.walk(root):
            directory_names[:] = retained_directory_names(
                exclusions,
                directory,
                directory_names,
            )
            if "composer.json" not in file_names:
                continue
            composer_path = os.path.join(directory, "composer.json")
            if is_excluded_path(exclusions, composer_path):
                continue
            data = _read_composer(composer_path)
            if data is None:
                continue
            for prefix, directories in _psr4_entries(directory, data):
                known = roots_by_prefix.setdefault(prefix, {})
                for path in directories:
                    known[str(path)] = path

        ordered = sorted(
            roots_by_prefix.items(),
            key=lambda entry: (-len(entry[0]), entry[0]),
        )
        return cls(
            tuple(
                (prefix, tuple(paths[key] for key in sorted(paths)))
                for prefix, paths in ordered
            )
        )

    def file_for_class(self, fqcn: str) -> tuple[Path, ...]:
        """List the existing PHP files a class name maps to through PSR-4.

        Prefixes are tried longest first, as Composer does, and a prefix only
        answers when at least one of its directories holds the file.
        """
        name = fqcn.strip().lstrip("\\")
        if not name:
            return ()
        for prefix, directories in self.prefixes:
            if prefix and not name.startswith(prefix):
                continue
            remainder = name[len(prefix):].lstrip("\\").replace("\\", "/")
            if not remainder:
                continue
            files = tuple(
                dict.fromkeys(
                    directory / f"{remainder}.php"
                    for directory in directories
                    if (directory / f"{remainder}.php").is_file()
                )
            )
            if files:
                return files
        return ()