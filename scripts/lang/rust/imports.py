from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions, FileResult


@dataclass(frozen=True)
class RustPackage:
    """One local Cargo package and its Rust crate identifier."""

    crate_name: str
    directory: str


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError as error:
        print(
            f"[code-graph] Cannot compare Rust path scopes {path} and {directory}: {error}",
            file=sys.stderr,
        )
        return False


def _load_rust_packages(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[RustPackage, ...]:
    packages: list[RustPackage] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = retained_directory_names(
            discovery_exclusions,
            dirpath,
            dirnames,
        )
        if "Cargo.toml" not in filenames:
            continue

        manifest_path = os.path.join(dirpath, "Cargo.toml")
        if is_excluded_path(discovery_exclusions, manifest_path):
            continue
        try:
            with open(manifest_path, "rb") as manifest_file:
                manifest = tomllib.load(manifest_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            print(
                f"[code-graph] Cannot read Rust package identity from {manifest_path}: {error}",
                file=sys.stderr,
            )
            continue

        package = manifest.get("package")
        if not isinstance(package, dict):
            continue
        package_name = package.get("name")
        if not isinstance(package_name, str) or not package_name:
            continue
        packages.append(
            RustPackage(
                crate_name=package_name.replace("-", "_"),
                directory=os.path.normpath(dirpath),
            )
        )

    return tuple(
        sorted(
            packages,
            key=lambda package: len(Path(package.directory).parts),
            reverse=True,
        )
    )


def _candidate_id(
    candidate: str,
    path_to_id: Mapping[str, str],
) -> str | None:
    return path_to_id.get(os.path.normpath(candidate))


def _resolve_from_directory(
    directory: str,
    segments: tuple[str, ...],
    path_to_id: Mapping[str, str],
    fallback_files: tuple[str, ...] = (),
) -> str | None:
    for segment_count in range(len(segments), 0, -1):
        module_path = os.path.join(directory, *segments[:segment_count])
        for candidate in (f"{module_path}.rs", os.path.join(module_path, "mod.rs")):
            target_id = _candidate_id(candidate, path_to_id)
            if target_id is not None:
                return target_id

    for fallback_file in fallback_files:
        target_id = _candidate_id(fallback_file, path_to_id)
        if target_id is not None:
            return target_id
    return None


def _package_entrypoints(package: RustPackage) -> tuple[str, ...]:
    source_directory = os.path.join(package.directory, "src")
    return (
        os.path.join(source_directory, "lib.rs"),
        os.path.join(source_directory, "main.rs"),
    )


def _logical_module_files(module_directory: str) -> tuple[str, ...]:
    """Return the physical Rust files that can define one logical module."""
    return (
        f"{module_directory}.rs",
        os.path.join(module_directory, "mod.rs"),
    )


def _owning_package(
    source_path: str,
    packages: tuple[RustPackage, ...],
) -> RustPackage | None:
    absolute_source = os.path.normpath(source_path)
    return next(
        (
            package
            for package in packages
            if _is_within(absolute_source, package.directory)
        ),
        None,
    )


def _logical_module_directory(source_path: str) -> str:
    source_path = os.path.normpath(source_path)
    filename = os.path.basename(source_path)
    if filename in ("lib.rs", "main.rs", "mod.rs"):
        return os.path.dirname(source_path)
    return os.path.splitext(source_path)[0]


# Folders whose subfolders Cargo builds as crates of their own: tests/<name>/main.rs and the like.
_SEPARATE_CRATE_FOLDERS = ("tests", "benches", "examples")


def _separate_crate_directory(source_path: str, owner: RustPackage) -> str | None:
    """Root folder of the test, bench or example crate a file belongs to, if any."""
    relative = os.path.relpath(source_path, owner.directory).split(os.sep)
    if len(relative) >= 3 and relative[0] in _SEPARATE_CRATE_FOLDERS:
        return os.path.join(owner.directory, relative[0], relative[1])
    return None


def _crate_source_directory(
    source_path: str,
    owner: RustPackage | None,
) -> str:
    if owner is not None:
        separate = _separate_crate_directory(source_path, owner)
        if separate is not None:
            return separate
        return os.path.join(owner.directory, "src")

    source = Path(source_path)
    for parent in source.parents:
        if parent.name == "src":
            return str(parent)
    return os.path.dirname(source_path)


_EXPORTED_MACRO = re.compile(
    r"#\[macro_export[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*macro_rules!\s*([A-Za-z_][A-Za-z0-9_]*)"
)


def _load_exported_macros(
    path_to_id: Mapping[str, str],
    packages: tuple[RustPackage, ...],
) -> dict[str, dict[str, str]]:
    """Files that define #[macro_export] macros, by package directory and macro name.

    An exported macro lives at the crate root by name (use crate::emit), wherever
    it is written; without this index such a use would point at lib.rs.
    """
    macros: dict[str, dict[str, str]] = {}
    for path in sorted(path_to_id):
        if not path.endswith(".rs"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as source:
                text = source.read()
        except (OSError, UnicodeDecodeError) as error:
            print(f"[code-graph] Cannot read Rust macros from {path}: {error}", file=sys.stderr)
            continue
        if "macro_export" not in text:
            continue
        owner = _owning_package(path, packages)
        by_name = macros.setdefault(owner.directory if owner is not None else "", {})
        for match in _EXPORTED_MACRO.finditer(text):
            by_name.setdefault(match.group(1), path)
    return macros


@dataclass(frozen=True)
class BoundRustLanguage:
    root: str
    path_to_id: Mapping[str, str]
    packages: tuple[RustPackage, ...]
    exported_macros: Mapping[str, Mapping[str, str]]
    _resolved: dict[tuple[str, str], str | None] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundRustLanguage:
        packages = _load_rust_packages(root, discovery_exclusions)
        if packages:
            print(
                f"[code-graph] Loaded {len(packages)} local Rust packages",
                file=sys.stderr,
            )
        return cls(
            os.path.normpath(root),
            path_to_id,
            packages,
            _load_exported_macros(path_to_id, packages),
        )

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports, imported names,
        # relations, use sites); the answer depends only on this bound state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        segments = tuple(segment for segment in import_name.split("::") if segment)
        if not segments:
            return None

        absolute_source = os.path.normpath(source_path)
        owner = _owning_package(absolute_source, self.packages)
        first = segments[0]

        if first == "crate":
            source_directory = _crate_source_directory(absolute_source, owner)
            separate = (
                _separate_crate_directory(absolute_source, owner) if owner is not None else None
            )
            if separate is not None:
                fallbacks = (os.path.join(separate, "main.rs"),)
            elif owner is not None:
                fallbacks = _package_entrypoints(owner)
            else:
                fallbacks = (absolute_source,)
            module_target = _resolve_from_directory(
                source_directory,
                segments[1:],
                self.path_to_id,
            )
            if module_target is not None:
                return module_target
            if len(segments) == 2:
                macro_file = self.exported_macros.get(
                    owner.directory if owner is not None else "", {}
                ).get(segments[1])
                if macro_file is not None:
                    macro_target = _candidate_id(macro_file, self.path_to_id)
                    if macro_target is not None:
                        return macro_target
            return _resolve_from_directory(
                source_directory,
                (),
                self.path_to_id,
                fallbacks,
            )

        if first in ("self", "super"):
            module_directory = _logical_module_directory(absolute_source)
            index = 0
            traversed_parent = False
            while index < len(segments) and segments[index] in ("self", "super"):
                if segments[index] == "super":
                    module_directory = os.path.dirname(module_directory)
                    traversed_parent = True
                index += 1
            remaining_segments = segments[index:]
            fallbacks: tuple[str, ...] = ()
            if traversed_parent:
                # `super::Item` depends on the parent module wherever Item is defined:
                # the parent may only re-export it.
                fallbacks = _logical_module_files(module_directory)
                crate_source = _crate_source_directory(absolute_source, owner)
                if module_directory == crate_source and owner is not None:
                    fallbacks = (*fallbacks, *_package_entrypoints(owner))
            elif not remaining_segments:
                fallbacks = (absolute_source,)
            return _resolve_from_directory(
                module_directory,
                remaining_segments,
                self.path_to_id,
                fallbacks,
            )

        package = next(
            (package for package in self.packages if package.crate_name == first),
            None,
        )
        if package is None:
            # A relative path that starts at a child module: use codegen::emit::X.
            # An external crate has no file below this module, so it resolves to nothing.
            return _resolve_from_directory(
                _logical_module_directory(absolute_source),
                segments,
                self.path_to_id,
            )
        return _resolve_from_directory(
            os.path.join(package.directory, "src"),
            segments[1:],
            self.path_to_id,
            _package_entrypoints(package),
        )

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
