from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping

from ..interface import FileResult


def _project_folders(paths: list[str], root: str) -> dict[str, str]:
    """For every path, the nearest folder above it, not above root, that holds a project file (*.csproj);
    "" when there is none. The files of one project folder are compiled together."""
    root = os.path.normpath(root) if root else ""
    holds_project: dict[str, bool] = {}
    folder_of: dict[str, str] = {}
    projects: dict[str, str] = {}
    for path in paths:
        visited: list[str] = []
        folder = os.path.dirname(path)
        found = ""
        while True:
            if folder in folder_of:
                found = folder_of[folder]
                break
            visited.append(folder)
            if folder not in holds_project:
                try:
                    holds_project[folder] = any(name.endswith(".csproj") for name in os.listdir(folder))
                except OSError:
                    holds_project[folder] = False
            if holds_project[folder]:
                found = folder
                break
            parent = os.path.dirname(folder)
            if not root or folder == root or parent == folder:
                break
            folder = parent
        for folder in visited:
            folder_of[folder] = found
        projects[path] = found
    return projects


@dataclass(frozen=True)
class BoundCSharpLanguage:
    # full type name -> (node id, every declaration of the file is `partial`, project folder) per declaring file
    declarations: Mapping[str, tuple[tuple[str, bool, str], ...]]
    projects: Mapping[str, str]   # path -> project folder
    # path -> simple name -> the full type names it binds to in that file; () for a name two namespaces of
    # one level declare (the compiler rejects it as ambiguous)
    bindings: Mapping[str, Mapping[str, tuple[str, ...]]]
    namespaces: Mapping[str, frozenset[str]]   # path -> the namespaces searched for its simple names
    explicit: Mapping[str, frozenset[str]]   # path -> the targets of its alias and static usings

    @classmethod
    def create(cls, path_to_id: Mapping[str, str], root: str = "") -> BoundCSharpLanguage:
        from .parser import CSharpFileScope, csharp_file_scope

        scopes: dict[str, CSharpFileScope] = {}
        for path in sorted(path_to_id):
            if not path.endswith(".cs"):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as source:
                    content = source.read()
                scopes[path] = csharp_file_scope(content)
            except (OSError, RecursionError) as error:
                print(
                    f"[code-graph] Cannot index C# declarations from {path}: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
        projects = _project_folders(list(scopes), root)
        declared: dict[str, dict[str, tuple[bool, str]]] = {}
        simple_names: dict[str, set[str]] = {}
        global_usings: dict[str, list[str]] = {}
        for path, scope in scopes.items():
            for full_name, partial in scope.declared:
                by_file = declared.setdefault(full_name, {})
                was_partial = by_file.get(path_to_id[path], (True, ""))[0]
                by_file[path_to_id[path]] = (was_partial and partial, projects[path])
                simple_names.setdefault(full_name.rpartition(".")[2], set()).add(full_name)
            for namespace in scope.global_usings:
                if namespace not in global_usings.setdefault(projects[path], []):
                    global_usings[projects[path]].append(namespace)
        bindings: dict[str, dict[str, tuple[str, ...]]] = {}
        namespaces: dict[str, frozenset[str]] = {}
        for path, scope in scopes.items():
            levels = [*scope.levels, tuple(global_usings.get(projects[path], ()))]
            namespaces[path] = frozenset(namespace for level in levels for namespace in level)
            bound: dict[str, tuple[str, ...]] = {}
            for name in scope.referenced & simple_names.keys():
                matches = [
                    sorted({
                        full_name
                        for namespace in level
                        if (full_name := f"{namespace}.{name}" if namespace else name) in simple_names[name]
                    })
                    for level in levels
                ]
                if scope.ordered:
                    first = next((found for found in matches if found), [])
                    if first:
                        bound[name] = tuple(first) if len(first) == 1 else ()
                elif any(matches):
                    bound[name] = tuple(sorted({full_name for found in matches for full_name in found}))
            bindings[path] = bound
        return cls(
            {
                full_name: tuple(sorted((node_id, partial, project) for node_id, (partial, project) in by_file.items()))
                for full_name, by_file in declared.items()
            },
            projects,
            bindings,
            namespaces,
            {path: scope.explicit for path, scope in scopes.items()},
        )

    def _targets(self, full_name: str, project: str) -> tuple[str, ...]:
        """The files that declare the type as the project of the reader sees it: one file, or every file
        when all declare it `partial`; none when the name stands for several types."""
        declarations = self.declarations.get(full_name, ())
        if not declarations:
            return ()
        own = [declaration for declaration in declarations if declaration[2] == project]
        if not own and len({declaration[2] for declaration in declarations}) > 1:
            return ()
        group = own or declarations
        if len(group) == 1 or all(partial for _node_id, partial, _project in group):
            return tuple(node_id for node_id, _partial, _project in group)
        return ()

    def _resolve(self, import_name: str, source_path: str) -> tuple[str, ...]:
        project = self.projects.get(source_path, "")
        if import_name not in self.explicit.get(source_path, frozenset()):
            namespace, _, name = import_name.rpartition(".")
            bound = self.bindings.get(source_path, {}).get(name)
            if (
                bound is not None
                and import_name not in bound
                and namespace in self.namespaces.get(source_path, frozenset())
            ):
                return ()   # the name binds to the type of a nearer namespace, or to none
        candidate = import_name
        while candidate:
            targets = self._targets(candidate, project)
            if targets:
                return targets
            candidate, separator, _ = candidate.rpartition(".")
            if not separator:
                break
        return ()

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        targets = self._resolve(import_name, source_path)
        return targets[0] if targets else None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        """The types the names of the file bind to that no import of the file states alone: a type of an
        enclosing or the global namespace, one a global using of the project brings in, and every file of a
        partial type."""
        project = self.projects.get(result.path, "")
        found: set[str] = set()
        for bound in self.bindings.get(result.path, {}).values():
            for full_name in bound:
                found.update(self._targets(full_name, project))
        for import_name in result.imports:
            targets = self._resolve(import_name, result.path)
            if len(targets) > 1:
                found.update(targets)
        return tuple(sorted(found))

    def additional_import_lines(self, result: FileResult) -> dict[str, list[int]]:
        """Every file of a partial type, with the lines of the using directives that bring the type in."""
        lines: dict[str, set[int]] = {}
        for import_name, stated in result.import_lines.items():
            targets = self._resolve(import_name, result.path)
            if len(targets) > 1:
                for node_id in targets:
                    lines.setdefault(node_id, set()).update(stated)
        return {node_id: sorted(found) for node_id, found in lines.items()}
