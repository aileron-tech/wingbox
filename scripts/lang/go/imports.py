from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions, FileResult


@dataclass(frozen=True)
class GoModule:
    import_path: str
    directory: str


def _module_path(content: str) -> str:
    """The path of the `module` directive of a go.mod: `module a/b`, `module "a/b"` or the block form, a
    `//` comment after it left out. Read line by line, so the time is linear in the size of the file."""
    in_block = False
    for line in content.splitlines():
        words = line.split("//", 1)[0].replace("(", " ( ").replace(")", " ) ").split()
        if not words:
            continue
        if not in_block:
            if words[0] != "module":
                continue
            words = words[1:]
        for word in words:
            if word == "(":
                in_block = True
            elif word == ")":
                return ""
            else:
                return word.strip('"`')
        if not in_block:
            return ""
    return ""


def _load_go_modules(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[GoModule, ...]:
    modules: list[GoModule] = []
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = retained_directory_names(
            discovery_exclusions,
            directory_path,
            directory_names,
        )
        if "go.mod" not in file_names:
            continue
        manifest_path = os.path.join(directory_path, "go.mod")
        if is_excluded_path(discovery_exclusions, manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as manifest:
                content = manifest.read()
        except (OSError, ValueError) as error:   # ValueError: a byte that is not UTF-8
            print(
                f"[code-graph] Cannot read Go module identity from {manifest_path}: {error}",
                file=sys.stderr,
            )
            continue
        import_path = _module_path(content)
        if import_path:
            modules.append(
                GoModule(
                    import_path=import_path,
                    directory=os.path.normpath(directory_path),
                )
            )
    return tuple(
        sorted(
            modules,
            key=lambda module: len(module.import_path),
            reverse=True,
        )
    )


def _same_package_ids(path_to_id: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """For every Go file, the files of its directory and package that declare a package-level name it uses.

    No import states these dependencies: the files of a package see each other's names. A name that two files
    of the package declare (one per build constraint, `init`) names no single file and gives no edge. A test
    file sees every file of the package; another file sees the files that are not tests.
    """
    from .parser import go_file_names

    by_directory: dict[str, list[str]] = {}
    for path in path_to_id:
        if path.endswith(".go"):
            by_directory.setdefault(os.path.dirname(path), []).append(path)
    linked: dict[str, tuple[str, ...]] = {}
    for paths in by_directory.values():
        if len(paths) < 2:
            continue
        packages: dict[str, list[tuple[str, frozenset[str], frozenset[str]]]] = {}
        for path in sorted(paths):
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as source:
                    names = go_file_names(source.read())
            except (OSError, RecursionError) as error:
                print(
                    f"[code-graph] Cannot read the package names of {path}: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
                continue
            packages.setdefault(names.package, []).append((path, names.declared, names.used))
        for files in packages.values():
            for path, _declared, used in files:
                is_test = path.endswith("_test.go")
                declaring: dict[str, list[str]] = {}
                for other, declared, _used in files:
                    if other != path and (is_test or not other.endswith("_test.go")):
                        for name in used & declared:
                            declaring.setdefault(name, []).append(other)
                targets = {found[0] for found in declaring.values() if len(found) == 1}
                if targets:
                    linked[path] = tuple(sorted(path_to_id[target] for target in targets))
    return linked


@dataclass(frozen=True)
class BoundGoLanguage:
    path_to_id: Mapping[str, str]
    modules: tuple[GoModule, ...]
    # Non-test Go files of each directory, indexed once: a Go import names a
    # package (a directory), and scanning every file for every import made a
    # 1,200-file build spend nine tenths of its time here.
    package_ids_by_directory: Mapping[str, tuple[str, ...]]
    # The files of its own package whose package-level names a file uses, by the path of the file.
    same_package_ids: Mapping[str, tuple[str, ...]]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundGoLanguage:
        by_directory: dict[str, set[str]] = {}
        for path, node_id in path_to_id.items():
            if path.endswith(".go") and not path.endswith("_test.go"):
                by_directory.setdefault(
                    os.path.normpath(os.path.dirname(path)), set()
                ).add(node_id)
        return cls(
            path_to_id,
            _load_go_modules(root, discovery_exclusions),
            {directory: tuple(sorted(ids)) for directory, ids in by_directory.items()},
            _same_package_ids(path_to_id),
        )

    def _package_ids(self, import_name: str) -> tuple[str, ...]:
        module = next(
            (
                candidate
                for candidate in self.modules
                if import_name == candidate.import_path
                or import_name.startswith(candidate.import_path + "/")
            ),
            None,
        )
        if module is None:
            return ()
        relative_package = import_name.removeprefix(module.import_path).lstrip("/")
        package_directory = os.path.normpath(
            os.path.join(module.directory, *relative_package.split("/"))
        )
        return self.package_ids_by_directory.get(package_directory, ())

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        package_ids = self._package_ids(import_name)
        return package_ids[0] if package_ids else None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    node_id
                    for import_name in result.imports
                    for node_id in self._package_ids(import_name)
                }
                | set(self.same_package_ids.get(result.path, ()))
            )
        )

    def additional_import_lines(self, result: FileResult) -> dict[str, list[int]]:
        """Every file of an imported package, with the lines of the import specs that name the package."""
        lines: dict[str, set[int]] = {}
        for import_name, stated in result.import_lines.items():
            for node_id in self._package_ids(import_name):
                lines.setdefault(node_id, set()).update(stated)
        return {node_id: sorted(found) for node_id, found in lines.items()}
