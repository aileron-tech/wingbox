from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..interface import FileResult

SOURCE_FOLDER = "src"


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

    bases = [str(root_path), str(source_directory)]
    first_segment = parts[0] if parts else ""
    current = source_directory
    while current == root_path or root_path in current.parents:
        current_path = str(current)
        if (
            first_segment
            and (current / first_segment).is_dir()
            and current_path not in bases
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

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports, imported names,
        # relations, use sites); the answer depends only on this bound state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        bases, parts = _candidate_bases(self.root, source_path, import_name)
        for base in bases:
            for candidate in (
                os.path.join(base, *parts) + ".py",
                os.path.join(base, *parts, "__init__.py"),
            ):
                target_id = self.path_to_id.get(os.path.normpath(candidate))
                if target_id is not None:
                    return target_id
        return None

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

            bases, parts = _candidate_bases(self.root, result.path, module)
            for base in bases:
                submodule_path = os.path.normpath(
                    os.path.join(base, *parts, original_name + ".py")
                )
                target_id = self.path_to_id.get(submodule_path)
                if target_id is not None:
                    lines = extra_imports.setdefault(target_id, [])
                    line = imported_name.get("line", 0)
                    if line and line not in lines:
                        lines.append(line)
                        lines.sort()
                    break

        return extra_imports
