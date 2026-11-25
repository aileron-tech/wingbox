"""Import resolution for the Dart adapter.

Dart specifiers come in three shapes and this module binds each to a graph
node ID for one repository:

    dart:...                 the SDK: never a project module, no edge
    package:<name>/<rest>    a library of a package: <rest> under the lib/
                             folder of the directory whose pubspec.yaml
                             declares `name: <name>`
    <relative path>          a file next to the importing file
    part / part of           the same two rules; a missing file is silent

Resolution follows the language: `package:example_app/a/b.dart` is the file
lib/a/b.dart of the package named example_app, found by the nearest pubspec
above the importing file first, then anywhere in the repository. A name that
no local pubspec declares is a third-party dependency and creates no edge.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions, FileResult


_PACKAGE_PREFIX = "package:"
_DART_PREFIX = "dart:"
_LIBRARY_PREFIX = "dart-library:"
# `library app.main;` on a line of its own: the name a `part of app.main;` refers to.
_LIBRARY_NAME = re.compile(r"^[ \t]*library[ \t]+([A-Za-z_$][\w$.]*)[ \t]*;", re.MULTILINE)
# The source extension of the adapter; repeated here so that this module, and
# the registry that binds it, never import the tree-sitter package.
DART_EXTENSION = ".dart"


@dataclass(frozen=True)
class DartPackage:
    """A pubspec.yaml that names a package and the lib folder it owns."""

    name: str
    directory: str
    library_directory: str


def _pubspec_name(content: str) -> str:
    """Read the top-level `name:` of a pubspec without a YAML parser.

    pubspec.yaml is a YAML file, but the only key this adapter needs is the
    package name: the first column-0 `name:` pair is it.
    """
    for line in content.splitlines():
        # `name`, a colon and a value, read without a pattern: a pattern that tries every split of a long
        # line between the value and the space after it takes quadratic time.
        if not line.startswith("name"):
            continue
        rest = line[len("name"):].lstrip()
        if not rest.startswith(":") or not rest[1:]:
            continue
        value = rest[1:].strip().split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        return value
    return ""


def _load_dart_packages(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[DartPackage, ...]:
    packages: list[DartPackage] = []
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = retained_directory_names(
            discovery_exclusions,
            directory_path,
            directory_names,
        )
        if "pubspec.yaml" not in file_names:
            continue
        pubspec_path = os.path.join(directory_path, "pubspec.yaml")
        if is_excluded_path(discovery_exclusions, pubspec_path):
            continue
        try:
            with open(pubspec_path, "r", encoding="utf-8-sig") as pubspec:
                content = pubspec.read()
        except (OSError, ValueError) as error:   # ValueError: a byte that is not UTF-8
            print(
                f"[code-graph] Cannot read Dart package identity from {pubspec_path}: {error}",
                file=sys.stderr,
            )
            continue
        name = _pubspec_name(content)
        if not name:
            continue
        directory = os.path.normpath(directory_path)
        packages.append(DartPackage(
            name=name,
            directory=directory,
            library_directory=os.path.join(directory, "lib"),
        ))
    return tuple(
        sorted(
            packages,
            key=lambda package: len(package.directory),
            reverse=True,
        )
    )


def _within(path: str, directory: str) -> bool:
    """True when path is directory or below it."""
    return path == directory or path.startswith(directory + os.sep)


@dataclass(frozen=True)
class BoundDartLanguage:
    root: str
    path_to_id: Mapping[str, str]
    packages: tuple[DartPackage, ...]
    # Repository-relative paths of the parsed files, one spelling each.
    relative_ids: Mapping[str, str]
    _resolved: dict[tuple[str, str], str | None] = field(
        default_factory=dict, compare=False, repr=False
    )
    # Library name -> the files that declare it (`library app.main;`); filled when a part first asks.
    _libraries: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False, repr=False)
    _libraries_read: list[bool] = field(default_factory=list, compare=False, repr=False)

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundDartLanguage:
        relative_ids: dict[str, str] = {}
        for path, node_id in path_to_id.items():
            if not path.endswith(DART_EXTENSION):
                continue
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            relative_ids[relative] = node_id
        return cls(
            os.path.normpath(root),
            path_to_id,
            _load_dart_packages(root, discovery_exclusions),
            relative_ids,
        )

    def _relative_id(self, path: str) -> str | None:
        """The node ID of a parsed file, given an absolute or repository-relative path.

        A specifier may leave the .dart extension out
        (`package:example_app/core/models/user`); the file it would name
        decides, as it would for a URI with the extension written.
        """
        # normpath before the scope test: a joined specifier still holds "..".
        normalized = os.path.normpath(
            path if os.path.isabs(path) else os.path.join(self.root, path)
        )
        if not _within(normalized, self.root):
            return None
        relative = os.path.relpath(normalized, self.root).replace(os.sep, "/")
        if relative.startswith("../"):
            return None
        if relative in self.relative_ids:
            return self.relative_ids[relative]
        with_extension = relative + DART_EXTENSION
        return self.relative_ids.get(with_extension)

    def _package_id(
        self,
        import_name: str,
        source_path: str,
    ) -> str | None:
        """Resolve package:<name>/<rest> through the pubspec that declares name."""
        specifier = import_name[len(_PACKAGE_PREFIX):]
        name, _, rest = specifier.partition("/")
        if not name or not rest:
            return None
        candidates = [
            package for package in self.packages if package.name == name
        ]
        if not candidates:
            return None
        source = os.path.normpath(os.path.abspath(source_path))
        # packages are sorted deepest first: the nearest owning pubspec wins
        # when the importing file lies inside one of them.
        for package in candidates:
            if _within(source, package.directory):
                target = self._relative_id(
                    os.path.join(package.library_directory, *rest.split("/"))
                )
                if target is not None:
                    return target
        for package in candidates:
            target = self._relative_id(
                os.path.join(package.library_directory, *rest.split("/"))
            )
            if target is not None:
                return target
        return None

    def _relative_specifier_id(
        self,
        import_name: str,
        source_path: str,
    ) -> str | None:
        # Relative specifiers resolve against the importing file's folder. A
        # relative source_path is read against the graph root, never the CWD.
        source = source_path
        if not os.path.isabs(source):
            source = os.path.join(self.root, source)
        source_directory = os.path.dirname(os.path.normpath(source))
        return self._relative_id(
            os.path.join(source_directory, *import_name.split("/"))
        )

    def _library_id(self, name: str, source_path: str) -> str | None:
        """The file that declares `library <name>;`, for a part written `part of <name>;`. When several
        files declare the name, the one of the package of the part; no answer without a single one."""
        if not self._libraries_read:
            self._libraries_read.append(True)
            declared: dict[str, list[str]] = {}
            for path in sorted(self.path_to_id):
                if not path.endswith(DART_EXTENSION):
                    continue
                try:
                    with open(path, "r", encoding="utf-8-sig", errors="replace") as source:
                        content = source.read()
                except OSError as error:
                    print(f"[code-graph] Cannot read the Dart library name of {path}: {error}", file=sys.stderr)
                    continue
                for library in _LIBRARY_NAME.findall(content) if "library" in content else ():
                    declared.setdefault(library, []).append(os.path.normpath(path))
            self._libraries.update({library: tuple(paths) for library, paths in declared.items()})
        candidates = self._libraries.get(name, ())
        if len(candidates) > 1:
            source = os.path.normpath(os.path.abspath(source_path))
            package = next((package for package in self.packages if _within(source, package.directory)), None)
            if package is not None:
                candidates = tuple(path for path in candidates if _within(path, package.directory))
        return self._relative_id(candidates[0]) if len(candidates) == 1 else None

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        # One build asks the same (specifier, file) pairs many times (imports,
        # imported names, relations); the answer depends only on this state.
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        if not import_name:
            return None
        if import_name.startswith(_LIBRARY_PREFIX):
            return self._library_id(import_name[len(_LIBRARY_PREFIX):], source_path)
        if import_name.startswith(_DART_PREFIX):
            return None
        if import_name.startswith(_PACKAGE_PREFIX):
            return self._package_id(import_name, source_path)
        if import_name.startswith("/"):
            return None
        return self._relative_specifier_id(import_name, source_path)

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()


__all__ = ["BoundDartLanguage", "DartPackage"]