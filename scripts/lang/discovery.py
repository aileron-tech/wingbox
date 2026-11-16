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

# Dart and Flutter tool output, excluded by basename in every repository.
# `.dart_tool` holds the package config, kernel snapshots and generated
# sources of `dart pub get` and `dart build`; Flutter writes its compiled
# output to `build`, which is a SKIP_DIRS name like `vendor` or `dist`.
DART_OUTPUT_DIRECTORIES = frozenset({".dart_tool"})


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


def _is_published_view_directory(parent: str, name: str) -> bool:
    """True for `resources/views/vendor`: published Blade views, not Composer packages.

    Laravel publishes a package's templates under the application's views folder,
    so the `vendor` basename there holds source, not dependencies.
    """
    if name != "vendor":
        return False
    parts = os.path.normpath(parent).replace("\\", "/").split("/")
    return parts[-2:] == ["resources", "views"]


def is_excluded_directory(
    exclusions: DiscoveryExclusions,
    parent: str,
    name: str,
) -> bool:
    """Apply basename and repository-ignore policy before descending."""
    return (
        (name in exclusions.directory_names and not _is_published_view_directory(parent, name))
        or name in DART_OUTPUT_DIRECTORIES
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
