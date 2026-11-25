from __future__ import annotations

import os
import posixpath
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from ..discovery import (
    is_excluded_path,
    retained_directory_names,
)
from ..interface import DiscoveryExclusions, FileResult
from .tsconfig import TsPathOptions, load_ts_path_options, read_json_object


@dataclass(frozen=True)
class TsPathAlias:
    """One validated TypeScript path mapping owned by a tsconfig."""

    pattern: str
    target_patterns: tuple[str, ...]


@dataclass(frozen=True)
class TsPathAliasContext:
    """Path mappings whose scope starts at a tsconfig directory."""

    config_directory: str
    aliases: tuple[TsPathAlias, ...]
    base_directory: Optional[str] = None   # `baseUrl`: bare specifiers that no alias matches are looked up below it


@dataclass(frozen=True)
class TsOutputRoot:
    """A tsconfig `outDir` and the directories its files may be compiled from."""

    output_directory: str
    source_directories: tuple[str, ...]


@dataclass(frozen=True)
class TsPackageExport:
    """One `exports` entry: a subpath such as "." or "./sub/*" and its targets."""

    subpath: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class TsPackage:
    """A local TypeScript package identified by its package.json contract."""

    name: str
    directory: str
    exports: tuple[TsPackageExport, ...] = ()
    entry_targets: tuple[str, ...] = ()


# Manifest fields that name the package entry, the closest to the source first.
_ENTRY_FIELDS = ("source", "types", "typings", "module", "main")
# Suffixes of type declarations emitted next to the compiled JavaScript.
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
# Directories that hold build output compiled from src/ or from the package root,
# and the per-format directories that may sit directly inside them.
_BUILD_OUTPUT_DIRECTORIES = frozenset({"dist", "build", "lib", "out"})
_BUILD_FORMAT_DIRECTORIES = frozenset({"esm", "cjs", "types"})
# Extensions a specifier may leave out; .mjs and .cjs are always written in full.
_IMPLIED_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")
# The files that state the path aliases of a project: TypeScript's, and the one editors read for JavaScript.
_PROJECT_FILE_NAMES = ("tsconfig.json", "jsconfig.json")


WalkedTree = list[tuple[str, list[str]]]  # (folder, its file names) in os.walk order


def walk_tree(root: str, discovery_exclusions: DiscoveryExclusions) -> WalkedTree:
    """The folders under root that discovery keeps, with their file names: one walk for every loader."""
    found: WalkedTree = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = retained_directory_names(
            discovery_exclusions,
            dirpath,
            dirnames,
        )
        found.append((dirpath, filenames))
    return found


def _walk_tree(root: str, discovery_exclusions: DiscoveryExclusions, walked: Optional[WalkedTree]) -> WalkedTree:
    return walk_tree(root, discovery_exclusions) if walked is None else walked


def _load_ts_path_aliases(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
    loaded: Optional[dict[str, TsPathOptions]] = None,
    walked: Optional[WalkedTree] = None,
) -> tuple[TsPathAliasContext, ...]:
    """Load validated path mappings without merging independent projects."""
    contexts: list[TsPathAliasContext] = []
    loaded = {} if loaded is None else loaded
    # The project files of the tree: every tsconfig.json and jsconfig.json, and the files their
    # `references` name (tsconfig.app.json beside a tsconfig.json that only lists its projects).
    project_files: dict[str, None] = {}
    for dirpath, filenames in _walk_tree(root, discovery_exclusions, walked):
        for fname in filenames:
            if fname in _PROJECT_FILE_NAMES:
                project_files[os.path.normpath(os.path.join(dirpath, fname))] = None
    todo = list(reversed(project_files))
    seen: set[str] = set()
    while todo:
        tsconfig_path = todo.pop()
        if tsconfig_path in seen:
            continue
        seen.add(tsconfig_path)
        dirpath = os.path.dirname(tsconfig_path)
        if is_excluded_path(discovery_exclusions, tsconfig_path):
            continue
        options = load_ts_path_options(tsconfig_path, loaded, root=root)
        todo.extend(reference for reference in reversed(options.references) if os.path.isfile(reference))
        if options.paths_directory is None and options.base_directory is None:
            continue
        # TypeScript resolves paths against baseUrl, else next to their tsconfig.
        base_directory = options.base_directory or options.paths_directory

        aliases = [
            TsPathAlias(
                alias_pattern,
                tuple(
                    os.path.normpath(os.path.join(base_directory, target))
                    for target in targets
                ),
            )
            for alias_pattern, targets in options.paths
            if targets
        ]
        if aliases or options.base_directory is not None:
            contexts.append(
                TsPathAliasContext(
                    config_directory=os.path.normpath(dirpath),
                    aliases=tuple(
                        sorted(
                            aliases,
                            key=lambda alias: len(alias.pattern),
                            reverse=True,
                        )
                    ),
                    base_directory=options.base_directory,
                )
            )

    return tuple(
        sorted(
            contexts,
            key=lambda context: len(Path(context.config_directory).parts),
            reverse=True,
        )
    )


def _load_ts_output_roots(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
    loaded: dict[str, TsPathOptions],
    walked: Optional[WalkedTree] = None,
) -> tuple[TsOutputRoot, ...]:
    """Collect the outDir of every tsconfig*.json and where its sources lie."""
    output_roots: list[TsOutputRoot] = []
    for dirpath, filenames in _walk_tree(root, discovery_exclusions, walked):
        for fname in sorted(filenames):
            if not (fname.startswith("tsconfig") and fname.endswith(".json")):
                continue
            tsconfig_path = os.path.normpath(os.path.join(dirpath, fname))
            if is_excluded_path(discovery_exclusions, tsconfig_path):
                continue
            options = load_ts_path_options(tsconfig_path, loaded, root=root)
            if options.output_directory is None:
                continue
            # Without rootDir the compiler emits from the common folder of its inputs:
            # the folder of the tsconfig, or its src when everything lies there.
            directory = os.path.normpath(dirpath)
            source_directories = (
                (directory, os.path.join(directory, "src"))
                if options.source_directory is None
                else (options.source_directory,)
            )
            output_roots.append(
                TsOutputRoot(options.output_directory, source_directories)
            )

    return tuple(
        sorted(
            dict.fromkeys(output_roots),
            key=lambda output_root: len(output_root.output_directory),
            reverse=True,
        )
    )


def _package_relative_parts(target: str) -> Optional[tuple[str, ...]]:
    """Split a package-relative path into segments; None when it leaves the package."""
    normalized = posixpath.normpath(target)
    if normalized.startswith("/") or normalized.split("/")[0] == "..":
        return None
    return () if normalized == "." else tuple(normalized.split("/"))


def _export_targets(value: object) -> tuple[str, ...]:
    """Flatten one `exports` value (path, conditions, fallbacks) in declared order."""
    targets: list[str] = []
    todo = [value]
    while todo:
        current = todo.pop()
        if isinstance(current, str):
            targets.append(current)
        elif isinstance(current, list):
            todo.extend(reversed(current))
        elif isinstance(current, dict):
            todo.extend(reversed(list(current.values())))
    return tuple(targets)


def _package_exports(raw: object) -> tuple[TsPackageExport, ...]:
    """Read `exports`; a value without subpath keys describes the package root."""
    if raw is None:
        return ()
    if isinstance(raw, dict) and raw and all(key.startswith(".") for key in raw):
        entries = tuple(raw.items())
    else:
        entries = ((".", raw),)
    exports = (
        TsPackageExport(subpath, _export_targets(value)) for subpath, value in entries
    )
    return tuple(entry for entry in exports if entry.targets)


def _load_ts_packages(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
    walked: Optional[WalkedTree] = None,
) -> tuple[TsPackage, ...]:
    """Load the packages of the repository and where their manifests point."""
    packages: list[TsPackage] = []
    for dirpath, filenames in _walk_tree(root, discovery_exclusions, walked):
        if "package.json" not in filenames:
            continue

        package_path = os.path.join(dirpath, "package.json")
        if is_excluded_path(discovery_exclusions, package_path):
            continue
        data = read_json_object(package_path, "local package identity")
        if data is None:
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name:
            continue
        packages.append(
            TsPackage(
                name=name,
                directory=os.path.normpath(dirpath),
                exports=_package_exports(data.get("exports")),
                entry_targets=tuple(
                    target
                    for target in (data.get(field) for field in _ENTRY_FIELDS)
                    if isinstance(target, str) and target
                ),
            )
        )

    return tuple(sorted(packages, key=lambda package: len(package.name), reverse=True))


def _typescript_resolution_candidates(resolved: str) -> tuple[str, ...]:
    """List the files a specifier path may name, the most exact reading first."""
    for suffix in (".jsx", ".js"):
        if resolved.endswith(suffix):
            # An emitted .js specifier names x.ts when it exists, else the real file.
            stem = resolved[: -len(suffix)]
            return (
                stem + ".ts",
                stem + ".tsx",
                resolved,
                stem + "/index.ts",
                stem + "/index.tsx",
            )
    for suffix, typescript_suffix in ((".mjs", ".mts"), (".cjs", ".cts")):
        if resolved.endswith(suffix):
            # TypeScript reads ./x.mjs as x.mts and ./x.cjs as x.cts when that file exists.
            stem = resolved[: -len(suffix)]
            return (
                stem + typescript_suffix,
                resolved,
                stem + ".ts",
                stem + ".tsx",
                stem + "/index.ts",
                stem + "/index.tsx",
            )
    return (
        resolved,
        *(resolved + suffix for suffix in _IMPLIED_SUFFIXES),
        *(resolved + "/index" + suffix for suffix in _IMPLIED_SUFFIXES),
    )


def _resolve_typescript_path(
    resolved: str,
    root: str,
    path_to_id: Mapping[str, str],
) -> Optional[str]:
    for candidate in _typescript_resolution_candidates(resolved):
        # A specifier joins with "/"; the file system and path_to_id use the system's separator.
        candidate = os.path.normpath(candidate)
        if candidate in path_to_id:
            return path_to_id[candidate]
        try:
            relative_candidate = os.path.relpath(candidate, root)
        except ValueError as error:
            print(
                f"[code-graph] Cannot relativize {candidate} to {root}: {error}",
                file=sys.stderr,
            )
            relative_candidate = candidate
        if relative_candidate in path_to_id:
            return path_to_id[relative_candidate]
    return None


def _resolve_source_path(
    resolved: str,
    root: str,
    path_to_id: Mapping[str, str],
    output_roots: tuple[TsOutputRoot, ...],
) -> Optional[str]:
    """Find the module of a path: the file itself, else its source behind an outDir."""
    target_id = _resolve_typescript_path(resolved, root, path_to_id)
    if target_id:
        return target_id
    for output_root in output_roots:
        if resolved == output_root.output_directory:
            remainder = ""
        elif resolved.startswith(output_root.output_directory + os.sep):
            remainder = resolved[len(output_root.output_directory) + 1 :]
        else:
            continue
        for source_directory in output_root.source_directories:
            target_id = _resolve_typescript_path(
                os.path.normpath(os.path.join(source_directory, remainder)),
                root,
                path_to_id,
            )
            if target_id:
                return target_id
    return None


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError as error:
        print(
            f"[code-graph] Cannot compare path scopes {path} and {directory}: {error}",
            file=sys.stderr,
        )
        return False


def _match_ts_alias(pattern: str, imp: str) -> Optional[str]:
    if "*" not in pattern:
        return "" if imp == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not imp.startswith(prefix) or not imp.endswith(suffix):
        return None
    remainder_end = len(imp) - len(suffix) if suffix else len(imp)
    return imp[len(prefix) : remainder_end]


def _resolve_ts_path_alias(
    imp: str,
    source_path: str,
    root: str,
    path_to_id: Mapping[str, str],
    alias_contexts: tuple[TsPathAliasContext, ...],
    output_roots: tuple[TsOutputRoot, ...],
) -> Optional[str]:
    """Resolve an alias only through tsconfigs that own the source file."""
    if not alias_contexts:
        return None

    absolute_source = os.path.normpath(
        source_path if os.path.isabs(source_path) else os.path.join(root, source_path)
    )
    for context in alias_contexts:
        if not _is_within(absolute_source, context.config_directory):
            continue
        for alias in context.aliases:
            remainder = _match_ts_alias(alias.pattern, imp)
            if remainder is None:
                continue
            for target_pattern in alias.target_patterns:
                resolved = target_pattern.replace("*", remainder, 1)
                target_id = _resolve_source_path(
                    resolved,
                    root,
                    path_to_id,
                    output_roots,
                )
                if target_id:
                    return target_id
        if context.base_directory is not None:
            # No `paths` entry names it: TypeScript looks the specifier up below baseUrl.
            target_id = _resolve_source_path(
                os.path.normpath(os.path.join(context.base_directory, *imp.split("/"))), root, path_to_id, output_roots
            )
            if target_id:
                return target_id

    return None


def _split_package_specifier(imp: str) -> Optional[tuple[str, str]]:
    """Split a bare specifier into the package name and "." or "./sub/path"."""
    parts = imp.split("/")
    name_length = 2 if imp.startswith("@") else 1
    if len(parts) < name_length or not all(parts[:name_length]):
        return None
    subpath = "/".join(parts[name_length:])
    return "/".join(parts[:name_length]), f"./{subpath}" if subpath else "."


def _select_ts_package(
    name: str,
    absolute_source: str,
    packages: tuple[TsPackage, ...],
) -> Optional[TsPackage]:
    """Pick the package of that name; a duplicated name is never guessed."""
    named = tuple(package for package in packages if package.name == name)
    if len(named) <= 1:
        return named[0] if named else None
    owners = tuple(
        package for package in named if _is_within(absolute_source, package.directory)
    )
    if not owners:
        return None
    return max(owners, key=lambda package: len(Path(package.directory).parts))


def _package_targets(package: TsPackage, subpath: str) -> tuple[str, ...]:
    """Manifest targets of a subpath: `exports` first, then entry fields or the path."""
    matches = [
        (entry, remainder)
        for entry in package.exports
        if (remainder := _match_ts_alias(entry.subpath, subpath)) is not None
    ]
    exported: tuple[str, ...] = ()
    if matches:
        entry, remainder = max(
            matches,
            key=lambda match: ("*" not in match[0].subpath, len(match[0].subpath)),
        )
        exported = tuple(target.replace("*", remainder) for target in entry.targets)
    if subpath == ".":
        return exported + package.entry_targets + ("./src/index", "./index")
    return exported + (subpath,)


def _package_source_paths(package: TsPackage, target: str) -> tuple[str, ...]:
    """List where the source of a package target may live, the declared place first."""
    for suffix in _DECLARATION_SUFFIXES:
        if target.endswith(suffix):
            target = target[: -len(suffix)]
            break
    parts = _package_relative_parts(target)
    if parts is None:
        return ()

    candidates: list[tuple[str, ...]] = [parts]
    if parts and parts[0] in _BUILD_OUTPUT_DIRECTORIES:
        source_parts = parts[1:]
        if source_parts and source_parts[0] in _BUILD_FORMAT_DIRECTORIES:
            source_parts = source_parts[1:]
        candidates.extend((("src",) + source_parts, source_parts))
    return tuple(
        os.path.join(package.directory, *candidate) for candidate in candidates
    )


def _resolve_ts_package(
    imp: str,
    source_path: str,
    root: str,
    path_to_id: Mapping[str, str],
    packages: tuple[TsPackage, ...],
    output_roots: tuple[TsOutputRoot, ...],
) -> Optional[str]:
    """Resolve an import that names a repository package to its source module."""
    specifier = _split_package_specifier(imp)
    if specifier is None:
        return None
    name, subpath = specifier
    absolute_source = os.path.normpath(
        source_path if os.path.isabs(source_path) else os.path.join(root, source_path)
    )
    package = _select_ts_package(name, absolute_source, packages)
    if package is None:
        return None

    for target in _package_targets(package, subpath):
        for candidate in _package_source_paths(package, target):
            target_id = _resolve_source_path(
                candidate,
                root,
                path_to_id,
                output_roots,
            )
            if target_id:
                return target_id
    return None


def _without_query(specifier: str) -> str:
    """The specifier without a Vite query or hash suffix (./x.worker?worker&inline, ./y#part),
    as Vite resolves it; a leading # (a package subpath import) is part of the name."""
    if "?" not in specifier and "#" not in specifier[1:]:
        return specifier
    cut = len(specifier)
    for mark in ("?", "#"):
        position = specifier.find(mark, 1)
        if position != -1:
            cut = min(cut, position)
    return specifier[:cut]


@dataclass(frozen=True)
class BoundTypeScriptLanguage:
    root: str
    path_to_id: Mapping[str, str]
    alias_contexts: tuple[TsPathAliasContext, ...]
    packages: tuple[TsPackage, ...]
    output_roots: tuple[TsOutputRoot, ...]
    _resolved: dict[tuple[str, str], str | None] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundTypeScriptLanguage:
        loaded: dict[str, TsPathOptions] = {}
        walked = walk_tree(root, discovery_exclusions)  # one walk for the three loaders
        alias_contexts = _load_ts_path_aliases(root, discovery_exclusions, loaded, walked)
        if alias_contexts:
            print(
                f"[code-graph] Loaded {len(alias_contexts)} TS alias contexts",
                file=sys.stderr,
            )
        packages = _load_ts_packages(root, discovery_exclusions, walked)
        if packages:
            print(
                f"[code-graph] Loaded {len(packages)} local TS packages",
                file=sys.stderr,
            )
        name_counts = Counter(package.name for package in packages)
        duplicated = sorted(name for name, count in name_counts.items() if count > 1)
        if duplicated:
            print(
                f"[code-graph] {len(duplicated)} TS package names are declared more "
                "than once and resolve only inside their own package: "
                + ", ".join(duplicated[:5]),
                file=sys.stderr,
            )
        output_roots = _load_ts_output_roots(root, discovery_exclusions, loaded, walked)
        if output_roots:
            print(
                f"[code-graph] Loaded {len(output_roots)} TS output directories",
                file=sys.stderr,
            )
        return cls(root, path_to_id, alias_contexts, packages, output_roots)

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports, imported names,
        # relations, use sites); the answer depends only on this bound state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        import_name = _without_query(import_name)
        if not import_name:
            return None
        if not import_name.startswith(".") and not import_name.startswith("/"):
            resolved_via_alias = _resolve_ts_path_alias(
                import_name,
                source_path,
                self.root,
                self.path_to_id,
                self.alias_contexts,
                self.output_roots,
            )
            if resolved_via_alias:
                return resolved_via_alias
            return _resolve_ts_package(
                import_name,
                source_path,
                self.root,
                self.path_to_id,
                self.packages,
                self.output_roots,
            )

        source_directory = os.path.dirname(source_path)
        resolved = os.path.normpath(os.path.join(source_directory, import_name))
        return _resolve_source_path(
            resolved,
            self.root,
            self.path_to_id,
            self.output_roots,
        )

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
