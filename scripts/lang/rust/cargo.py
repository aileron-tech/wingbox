"""Cargo packages of a tree: the crate each package's library is named by, and the root file of every target.

The rules are Cargo's own (target auto-discovery and the `[lib]`, `[[bin]]`, `[[test]]`, `[[example]]`,
`[[bench]]` and `build` keys of a manifest): the library is `src/lib.rs` unless `[lib] path` names another
file, and is named by `[lib] name` or the package name with `-` replaced by `_`; binaries are `src/main.rs`,
`src/bin/*.rs` and `src/bin/<name>/main.rs`; tests, examples and benches are the `.rs` files and the
`<name>/main.rs` files of `tests/`, `examples/` and `benches/`; `build.rs` is the build script. A manifest
without `edition` is edition 2015, where a `use` path starts at the crate root.
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions


@dataclass(frozen=True)
class RustPackage:
    """One local Cargo package."""

    crate_name: str  # the name code uses for the library of the package
    directory: str
    package_name: str = ""
    library_root: str = ""  # the root file of the library, "" when the package has none
    crate_roots: tuple[str, ...] = ()  # the root file of every target, the library first
    edition_2015: bool = False
    # (name in code, folder of the package) of every dependency the manifest takes from a folder of the
    # tree: `name = { path = ".." }`, or `name.workspace = true` with a path in `[workspace.dependencies]`.
    local_dependencies: tuple[tuple[str, str], ...] = ()
    # The names in code of the other dependencies (a registry, git): never a package of this tree.
    foreign_dependencies: frozenset[str] = frozenset()


_TARGET_FOLDERS = (("bin", os.path.join("src", "bin"), "autobins"), ("test", "tests", "autotests"),
                   ("example", "examples", "autoexamples"), ("bench", "benches", "autobenches"))


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _folder_targets(directory: str) -> list[str]:
    """Root files Cargo finds in a target folder: every `.rs` file and every `<name>/main.rs`."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    roots = []
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(directory, name)
        if name.endswith(".rs") and os.path.isfile(path):
            roots.append(path)
        elif os.path.isfile(os.path.join(path, "main.rs")):
            roots.append(os.path.join(path, "main.rs"))
    return roots


def _crate_roots(directory: str, manifest: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """(library root or "", root files of all targets) of the package in directory."""
    package = _table(manifest.get("package"))

    def explicit(table: Any) -> str:
        path = _table(table).get("path")
        return os.path.normpath(os.path.join(directory, path)) if isinstance(path, str) and path else ""

    library = explicit(manifest.get("lib")) or os.path.join(directory, "src", "lib.rs")
    library = library if os.path.isfile(library) else ""
    roots = [library] if library else []
    main = os.path.join(directory, "src", "main.rs")
    for key, folder, auto in _TARGET_FOLDERS:
        declared = manifest.get(key)
        for target in declared if isinstance(declared, list) else []:
            path = explicit(target)
            if path:
                roots.append(path)
        if package.get(auto) is not False:
            if key == "bin" and os.path.isfile(main):
                roots.append(main)
            roots.extend(_folder_targets(os.path.join(directory, folder)))
    build = package.get("build")
    if isinstance(build, str) and build:
        roots.append(os.path.normpath(os.path.join(directory, build)))
    elif build is not False and os.path.isfile(os.path.join(directory, "build.rs")):
        roots.append(os.path.join(directory, "build.rs"))
    return library, tuple(dict.fromkeys(roots))


def _workspace_edition(directory: str, root: str, manifests: Mapping[str, Mapping[str, Any]]) -> Any:
    """`[workspace.package] edition` of the nearest manifest above (or at) directory that has one."""
    current = directory
    while True:
        workspace = _table(manifests.get(current, {}).get("workspace"))
        if workspace:
            return _table(workspace.get("package")).get("edition")
        parent = os.path.dirname(current)
        if current == root or parent == current:
            return None
        current = parent


_DEPENDENCY_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _dependency_tables(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    tables = [_table(manifest.get(key)) for key in _DEPENDENCY_TABLES]
    tables.extend(_table(_table(target).get(key)) for target in _table(manifest.get("target")).values()
                  for key in _DEPENDENCY_TABLES)
    return tables


def _workspace_dependencies(directory: str, root: str, manifests: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    """(folder, `[workspace.dependencies]`) of the nearest manifest at or above directory with a `[workspace]`."""
    current = directory
    while True:
        workspace = _table(manifests.get(current, {}).get("workspace"))
        if workspace:
            return current, _table(workspace.get("dependencies"))
        parent = os.path.dirname(current)
        if current == root or parent == current:
            return directory, {}
        current = parent


def _dependencies(
    directory: str, root: str, manifests: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[tuple[str, str], ...], frozenset[str]]:
    """The dependencies of the package in directory by their name in code: (those taken from a folder
    of the tree, with that folder; the names of the others)."""
    local: dict[str, str] = {}
    foreign: set[str] = set()
    workspace_directory, shared = _workspace_dependencies(directory, root, manifests)
    for table in _dependency_tables(manifests[directory]):
        for name, value in table.items():
            base, entry = directory, _table(value)
            if entry.get("workspace") is True:
                base, entry = workspace_directory, _table(shared.get(name))
            path = entry.get("path")
            if isinstance(path, str) and path:
                local.setdefault(name.replace("-", "_"), os.path.normpath(os.path.join(base, path)))
            else:
                foreign.add(name.replace("-", "_"))
    return tuple(sorted(local.items())), frozenset(foreign - set(local))


def load_rust_packages(root: str, discovery_exclusions: DiscoveryExclusions) -> tuple[RustPackage, ...]:
    """Every Cargo package below root, the deepest directory first."""
    manifests: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = retained_directory_names(discovery_exclusions, dirpath, dirnames)
        if "Cargo.toml" not in filenames:
            continue
        manifest_path = os.path.join(dirpath, "Cargo.toml")
        if is_excluded_path(discovery_exclusions, manifest_path):
            continue
        try:
            # utf-8-sig: a manifest saved with a byte order mark is the same manifest (tomllib refuses the mark).
            with open(manifest_path, "r", encoding="utf-8-sig") as manifest_file:
                manifests[os.path.normpath(dirpath)] = tomllib.loads(manifest_file.read())
        except (OSError, ValueError, RecursionError) as error:
            # ValueError: not UTF-8, or not TOML; RecursionError: tables or arrays nested too deep to read.
            print(f"[code-graph] Cannot read Rust package identity from {manifest_path}: {error}", file=sys.stderr)

    packages: list[RustPackage] = []
    for directory, manifest in manifests.items():
        package = manifest.get("package")
        if not isinstance(package, dict):
            continue
        package_name = package.get("name")
        if not isinstance(package_name, str) or not package_name:
            continue
        library_name = _table(manifest.get("lib")).get("name")
        edition = package.get("edition")
        if isinstance(edition, dict) and edition.get("workspace") is True:
            edition = _workspace_edition(directory, os.path.normpath(root), manifests)
        library, roots = _crate_roots(directory, manifest)
        local_dependencies, foreign_dependencies = _dependencies(directory, os.path.normpath(root), manifests)
        packages.append(RustPackage(
            crate_name=(library_name if isinstance(library_name, str) and library_name else package_name).replace("-", "_"),
            directory=directory,
            package_name=package_name,
            library_root=library,
            crate_roots=roots,
            edition_2015=edition is None or str(edition) == "2015",
            local_dependencies=local_dependencies,
            foreign_dependencies=foreign_dependencies,
        ))
    return tuple(sorted(packages, key=lambda package: len(Path(package.directory).parts), reverse=True))


__all__ = ["RustPackage", "load_rust_packages"]
