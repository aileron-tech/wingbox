from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from ..interface import DiscoveryExclusions, FileResult
from .cargo import RustPackage, load_rust_packages as _load_rust_packages
from .definers import Definers, ItemRoute
from .modules import ModuleLayout, PathModule, path_modules


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError as error:
        print(
            f"[code-graph] Cannot compare Rust path scopes {path} and {directory}: {error}",
            file=sys.stderr,
        )
        return False


def _candidate_id(
    candidate: str,
    path_to_id: Mapping[str, str],
) -> str | None:
    return path_to_id.get(os.path.normpath(candidate))


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


def _crate_root(source_path: str, owner: RustPackage | None) -> str | None:
    """The root file of the crate a file belongs to: the target root in the nearest folder at or above the file
    (the library before a binary of the same folder)."""
    if owner is None:
        return None
    if source_path in owner.crate_roots:
        return source_path
    containing = [root for root in owner.crate_roots if _is_within(source_path, os.path.dirname(root))]
    return max(containing, key=lambda root: len(os.path.dirname(root)), default=None)


def _source_directory_without_package(source_path: str) -> str:
    source = Path(source_path)
    for parent in source.parents:
        if parent.name == "src":
            return str(parent)
    return os.path.dirname(source_path)


_EXPORTED_MACRO = re.compile(
    r"#\[macro_export[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*macro_rules!\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_MACRO = re.compile(r"(?<![A-Za-z0-9_])macro_rules!\s*([A-Za-z_][A-Za-z0-9_]*)")
_EXTERN_CRATE_ALIAS = re.compile(
    r"^\s*(?:pub\s+)?extern\s+crate\s+([A-Za-z_][A-Za-z0-9_]*)\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.M
)


@dataclass(frozen=True)
class RustSources:
    """What resolving a path reads of the sources of a tree."""

    exported_macros: Mapping[str, Mapping[str, str]]  # package directory -> macro name -> defining file
    macros: Mapping[str, frozenset[str]]  # file -> the macro_rules! names it defines
    path_modules: tuple[PathModule, ...]
    extern_aliases: Mapping[str, Mapping[str, str]]  # folder of a crate root -> alias -> crate (`extern crate a as b;`)


def _scan_sources(
    path_to_id: Mapping[str, str],
    packages: tuple[RustPackage, ...],
) -> RustSources:
    """One reading of every Rust file: the files that define #[macro_export] macros, the macros of each file,
    the modules declared with a `#[path]` attribute and the `extern crate .. as ..;` names of the crate roots.

    An exported macro lives at the crate root by name (use crate::emit), wherever
    it is written; without this index such a use would point at lib.rs.
    """
    exported: dict[str, dict[str, str]] = {}
    macros: dict[str, frozenset[str]] = {}
    declarations: list[PathModule] = []
    aliases: dict[str, dict[str, str]] = {}
    crate_roots = {root for package in packages for root in package.crate_roots}
    for path in sorted(path_to_id):
        if not path.endswith(".rs"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as source:
                text = source.read()
        except (OSError, UnicodeDecodeError) as error:
            print(f"[code-graph] Cannot read Rust macros from {path}: {error}", file=sys.stderr)
            continue
        declarations.extend(path_modules(path, text))
        if path in crate_roots and "extern" in text:
            for match in _EXTERN_CRATE_ALIAS.finditer(text):
                aliases.setdefault(os.path.dirname(path), {}).setdefault(match.group(2), match.group(1))
        if "macro_rules!" not in text:
            continue
        macros[path] = frozenset(match.group(1) for match in _MACRO.finditer(text))
        if "macro_export" not in text:
            continue
        owner = _owning_package(path, packages)
        by_name = exported.setdefault(owner.directory if owner is not None else "", {})
        for match in _EXPORTED_MACRO.finditer(text):
            by_name.setdefault(match.group(1), path)
    return RustSources(exported, macros, tuple(declarations), aliases)


@dataclass(frozen=True)
class BoundRustLanguage:
    root: str
    path_to_id: Mapping[str, str]
    packages: tuple[RustPackage, ...]
    exported_macros: Mapping[str, Mapping[str, str]]
    layout: ModuleLayout = field(default_factory=lambda: ModuleLayout(frozenset()))
    macros: Mapping[str, frozenset[str]] = field(default_factory=dict)
    extern_aliases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    _resolved: dict[tuple[str, str], str | None] = field(default_factory=dict, compare=False, repr=False)
    _definers: list[Definers] = field(default_factory=list, compare=False, repr=False)

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
        sources = _scan_sources(path_to_id, packages)
        layout = ModuleLayout.create(
            (crate_root for package in packages for crate_root in package.crate_roots), sources.path_modules)
        return cls(os.path.normpath(root), path_to_id, packages, sources.exported_macros, layout, sources.macros,
                   sources.extern_aliases)

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports, imported names,
        # relations, use sites); the answer depends only on this bound state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_below(
        self,
        directory: str,
        segments: tuple[str, ...],
        fallback_files: tuple[str, ...] = (),
    ) -> str | None:
        """The file of the deepest module a path names below a module directory, else the first fallback."""
        for segment_count in range(len(segments), 0, -1):
            for candidate in self.layout.candidates(directory, segments[:segment_count]):
                target_id = _candidate_id(candidate, self.path_to_id)
                if target_id is not None:
                    return target_id
        for fallback_file in fallback_files:
            target_id = _candidate_id(fallback_file, self.path_to_id)
            if target_id is not None:
                return target_id
        return None

    def _exported_macro(self, package: RustPackage | None, name: str) -> str | None:
        macro_file = self.exported_macros.get(package.directory if package is not None else "", {}).get(name)
        return _candidate_id(macro_file, self.path_to_id) if macro_file is not None else None

    def _crate_named(self, name: str, owner: RustPackage | None) -> RustPackage | None:
        """The local package code of owner names by a crate name: a renamed dependency first."""
        renamed = dict(owner.renamed_dependencies).get(name) if owner is not None else None
        if renamed is not None:
            return next((package for package in self.packages if package.package_name == renamed), None)
        return next((package for package in self.packages if package.crate_name == name), None)

    def _resolve_in_crate(self, source: str, owner: RustPackage | None, segments: tuple[str, ...],
                          root_fallback: bool = True) -> str | None:
        """A path below the root of the crate the source file belongs to."""
        root_file = _crate_root(source, owner)
        if root_file is not None and owner is not None:
            directory = os.path.dirname(root_file)
            fallbacks = (root_file, *(root for root in owner.crate_roots if os.path.dirname(root) == directory))
        elif owner is not None:
            directory = os.path.join(owner.directory, "src")
            fallbacks = (os.path.join(directory, "lib.rs"), os.path.join(directory, "main.rs"))
        else:
            directory, fallbacks = _source_directory_without_package(source), (source,)
        module_target = self._resolve_below(directory, segments)
        if module_target is not None or not root_fallback:
            return module_target
        if len(segments) == 1:
            macro_target = self._exported_macro(owner, segments[0])
            if macro_target is not None:
                return macro_target
        return self._resolve_below(directory, (), fallbacks)

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        segments = tuple(segment.removeprefix("r#") for segment in import_name.split("::") if segment)
        if not segments:
            return None

        absolute_source = os.path.normpath(source_path)
        owner = _owning_package(absolute_source, self.packages)
        first = segments[0]

        if first == "crate":
            return self._resolve_in_crate(absolute_source, owner, segments[1:])

        if first in ("self", "super"):
            module_directory = self.layout.module_directory(absolute_source)
            index = 0
            traversed_parent = False
            while index < len(segments) and segments[index] in ("self", "super"):
                if segments[index] == "super":
                    module_directory = (os.path.dirname(module_directory) if traversed_parent
                                        else self.layout.parent_directory(absolute_source))
                    traversed_parent = True
                index += 1
            remaining_segments = segments[index:]
            fallbacks: tuple[str, ...] = ()
            if traversed_parent:
                # `super::Item` depends on the parent module wherever Item is defined:
                # the parent may only re-export it.
                roots = owner.crate_roots if owner is not None else ()
                fallbacks = (f"{module_directory}.rs", os.path.join(module_directory, "mod.rs"),
                             *(root for root in roots if os.path.dirname(root) == module_directory))
            elif not remaining_segments:
                fallbacks = (absolute_source,)
            return self._resolve_below(module_directory, remaining_segments, fallbacks)

        package = self._crate_named(first, owner)
        if package is None:
            # `extern crate app as other;` in the crate root: other names the crate app, or this crate for self.
            root_file = _crate_root(absolute_source, owner)
            crate = self.extern_aliases.get(os.path.dirname(root_file), {}).get(first) if root_file is not None else None
            if crate == "self":
                return self._resolve_in_crate(absolute_source, owner, segments[1:])
            package = self._crate_named(crate, owner) if crate is not None else None
        if package is None and import_name.startswith("::"):
            # `::name` is a crate; in edition 2015 it is the root of this crate.
            if owner is not None and owner.edition_2015:
                return self._resolve_in_crate(absolute_source, owner, segments, root_fallback=False)
            return None
        if package is None:
            # A relative path that starts at a child module: use codegen::emit::X.
            # An external crate has no file below this module, so it resolves to nothing.
            relative = self._resolve_below(self.layout.module_directory(absolute_source), segments)
            if relative is None and owner is not None and owner.edition_2015:
                # Edition 2015: a `use` path starts at the crate root.
                return self._resolve_in_crate(absolute_source, owner, segments, root_fallback=False)
            return relative
        if package.library_root:
            directory, fallbacks = os.path.dirname(package.library_root), (package.library_root,)
        else:
            directory = os.path.join(package.directory, "src")
            fallbacks = (os.path.join(directory, "lib.rs"), os.path.join(directory, "main.rs"))
        module_target = self._resolve_below(directory, segments[1:])
        if module_target is None and len(segments) == 2:
            module_target = self._exported_macro(package, segments[1])
        return module_target if module_target is not None else self._resolve_below(directory, (), fallbacks)

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        """The files the `#[path]` declarations of a file load: every candidate of a cfg-dependent path."""
        declared = self.layout.declared.get(os.path.normpath(result.path), ())
        return tuple(target for target in (_candidate_id(path, self.path_to_id) for path in declared) if target is not None)

    def resolve_named_imports(
        self,
        result: FileResult,
        results: Mapping[str, FileResult],
        resolve: Callable[[str, str], str | None] | None = None,
    ) -> tuple[dict[str, frozenset[str]], int, dict[str, tuple[ItemRoute, ...]]]:
        """Files that define the names this file imports through a module facade or names as members of a type,
        how many names could not be followed, and the names followed to each module (see Definers.named_imports). `resolve` is the resolver to ask
        (a traced one during a graph update); results are the parse results by module ID."""
        resolve = resolve or self.resolve_import
        # One Definers per set of results: the index of type members is built once for a build.
        if not self._definers or self._definers[0].results is not results or self._definers[0].resolve != resolve:
            self._definers[:] = [Definers(results, resolve, self.layout, self.macros, self.path_to_id)]
        return self._definers[0].named_imports(result)
