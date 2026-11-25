from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from ..interface import DiscoveryExclusions, FileResult
from .roots import SOURCE_FOLDER, source_roots, top_level_names

# A name of the standard library is never a package of a source root: the library comes first on the path.
_STANDARD_LIBRARY = frozenset(sys.stdlib_module_names)


def _candidate_bases(
    root: str,
    source_path: str,
    import_name: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relative_level = len(import_name) - len(import_name.lstrip("."))
    module_name = import_name[relative_level:]
    parts = tuple(part for part in module_name.split(".") if part)
    source_directory = Path(os.path.abspath(source_path)).parent
    root_path = Path(os.path.abspath(root))

    if relative_level:
        relative_base = source_directory
        for _ in range(relative_level - 1):
            relative_base = relative_base.parent
        return (str(relative_base),), parts

    # The folder of a package is not on the import path: inside a package an absolute name is never the
    # module or the folder beside the importing file (`import logging` next to `logging.py`). The folder
    # of a script is, as the first entry.
    in_package = os.path.isfile(source_directory / "__init__.py")
    bases = [str(root_path)] if in_package else [str(root_path), str(source_directory)]
    first_segment = parts[0] if parts else ""
    current = source_directory
    while current == root_path or root_path in current.parents:
        current_path = str(current)
        if (
            first_segment
            # os.path.isdir answers False for a name the file system refuses (one of a thousand letters).
            and os.path.isdir(current / first_segment)
            and current_path not in bases
            and not (in_package and current == source_directory)
        ):
            bases.append(current_path)
        if current == root_path:
            break
        current = current.parent
    # The src layout: the package lives in `src/` and the files beside it (tests, scripts, docs)
    # import it by its package name. Tried last and nearest first, so that a package beside the
    # importing file or at the root still wins and each part of a monorepo reaches its own `src/`.
    current = source_directory
    while current == root_path or root_path in current.parents:
        source_folder = str(current / SOURCE_FOLDER)
        if source_folder not in bases:
            bases.append(source_folder)
        if current == root_path:
            break
        current = current.parent
    return tuple(bases), parts


@dataclass(frozen=True)
class BoundPythonLanguage:
    root: str
    path_to_id: Mapping[str, str]
    _resolved: dict[tuple[str, str], str | None] = field(default_factory=dict, compare=False, repr=False)
    # The build asks for the submodules of a file twice (the targets, then their lines): one answer per file.
    _additional: dict[tuple[str, str], dict[str, list[int]]] = field(default_factory=dict, compare=False, repr=False)
    # The folders a module name is looked for in, per folder of the importing file (see _bases).
    _base_folders: dict[tuple[str, int, str], tuple[str, ...]] = field(default_factory=dict, compare=False, repr=False)
    exclusions: Optional[DiscoveryExclusions] = field(default=None, compare=False, repr=False)
    # The source roots that hold each top-level name (read on first need), and the answer per dotted name.
    _top_level: list[dict[str, tuple[str, ...]]] = field(default_factory=list, compare=False, repr=False)
    _rooted: dict[tuple[str, ...], str | None] = field(default_factory=dict, compare=False, repr=False)

    def _bases(self, source_path: str, import_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """_candidate_bases(self.root, source_path, import_name), with the folders kept: they depend on the
        folder of the importing file and on the dots of a relative name or the first segment of an absolute
        one, never on the file or on the rest of the name. The files of a folder import the same few packages."""
        level = len(import_name) - len(import_name.lstrip("."))
        parts = tuple(part for part in import_name[level:].split(".") if part)
        key = (os.path.dirname(source_path), level, parts[0] if parts and not level else "")
        folders = self._base_folders.get(key)
        if folders is None:
            folders = self._base_folders[key] = _candidate_bases(self.root, source_path, "." * level + key[2])[0]
        return folders, parts

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports, imported names,
        # relations, use sites); the answer depends only on this bound state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _module_in(self, base: str, parts: tuple[str, ...]) -> str | None:
        # As Python's finder: in one folder the package `name/__init__.py` is taken before the
        # module `name.py`. A name without parts (`from . import x`) is the package folder
        # itself, never a module file that lies beside that folder under the same name.
        package = os.path.join(base, *parts, "__init__.py")
        candidates = (package, os.path.join(base, *parts) + ".py") if parts else (package,)
        for candidate in candidates:
            target_id = self.path_to_id.get(os.path.normpath(candidate))
            if target_id is not None:
                return target_id
        return None

    def _in_bases(self, bases: tuple[str, ...], parts: tuple[str, ...]) -> str | None:
        for base in bases:
            target_id = self._module_in(base, parts)
            if target_id is not None:
                return target_id
        return None

    def _in_source_roots(self, parts: tuple[str, ...]) -> str | None:
        """The module an absolute name means when exactly one source root of the repository holds it (a
        package of another part of a monorepo, a folder a manifest names). Asked after the folders near
        the importing file; a name that several roots hold is ambiguous and stays unlinked."""
        if parts[0] in _STANDARD_LIBRARY:
            return None
        if parts not in self._rooted:
            if not self._top_level:
                roots = source_roots(self.root, self.path_to_id, self.exclusions)
                self._top_level.append(top_level_names(self.root, roots, self.path_to_id))
            found = {self._module_in(folder, parts) for folder in self._top_level[0].get(parts[0], ())} - {None}
            self._rooted[parts] = found.pop() if len(found) == 1 else None
        return self._rooted[parts]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        bases, parts = self._bases(source_path, import_name)
        target_id = self._in_bases(bases, parts)
        if target_id is None and parts and not import_name.startswith("."):
            target_id = self._in_source_roots(parts)
        return target_id

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return tuple(self.additional_import_lines(result))

    def additional_import_lines(self, result: FileResult) -> dict[str, list[int]]:
        """The modules that `from package import module` statements import, each with the lines of those
        statements (ascending; empty when the parse result carries none), in the order they are first named."""
        key = (result.path, result.content_hash)
        if key not in self._additional:
            self._additional[key] = self._additional_import_lines(result)
        return self._additional[key]

    def _additional_import_lines(self, result: FileResult) -> dict[str, list[int]]:
        extra_imports: dict[str, list[int]] = {}
        for imported_name in result.imported_names:
            module = imported_name.get("module", "")
            original_name = imported_name.get("original", "")
            if not module or not original_name:
                continue

            # The submodule `module.name`: a module file or, when `name` is a subpackage, its `__init__.py`.
            bases, parts = self._bases(result.path, module)
            submodule = (*parts, original_name)
            target_id = self._in_bases(bases, submodule)
            # A package that a folder near the importing file holds has its submodules there and nowhere else.
            if target_id is None and parts and not module.startswith(".") and self._in_bases(bases, parts) is None:
                target_id = self._in_source_roots(submodule)
            if target_id is not None:
                lines = extra_imports.setdefault(target_id, [])
                line = imported_name.get("line", 0)
                if line and line not in lines:
                    lines.append(line)
                    lines.sort()

        return extra_imports
