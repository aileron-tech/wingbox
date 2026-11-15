from __future__ import annotations

import os
from collections.abc import Iterable

from .interface import DiscoveryExclusions


def normalize_discovery_path(path: str) -> str:
    """Normalize an existing discovery path consistently across adapters."""
    return os.path.normcase(os.path.realpath(path))


def is_excluded_path(
    exclusions: DiscoveryExclusions,
    path: str,
) -> bool:
    """Return whether a file or directory has an exact configured exclusion."""
    return normalize_discovery_path(path) in exclusions.paths


_DOTNET_MARKERS = (".cs", ".csproj", ".fsproj", ".vbproj", ".sln")


def _is_dotnet_output_directory(parent: str, name: str) -> bool:
    """True for `bin` next to .NET sources or project files: the compiler's output folder.

    Elsewhere `bin` holds real sources (command-line entry points in src/bin of
    TypeScript and Rust projects), so the name alone must not exclude it.
    """
    if name != "bin":
        return False
    try:
        siblings = os.listdir(parent)
    except OSError:
        return True
    return any(sibling.endswith(_DOTNET_MARKERS) for sibling in siblings)


def is_excluded_directory(
    exclusions: DiscoveryExclusions,
    parent: str,
    name: str,
) -> bool:
    """Apply basename and repository-ignore policy before descending."""
    return (
        name in exclusions.directory_names
        or _is_dotnet_output_directory(parent, name)
        or is_excluded_path(exclusions, os.path.join(parent, name))
    )


def retained_directory_names(
    exclusions: DiscoveryExclusions,
    parent: str,
    names: Iterable[str],
) -> list[str]:
    """Return walkable child directory names in their original order."""
    return [
        name for name in names if not is_excluded_directory(exclusions, parent, name)
    ]
