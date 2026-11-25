"""Source roots of a repository: the folders, besides its root, that Python packages are imported from.

A folder that holds a `pyproject.toml`, `setup.py` or `setup.cfg` is the folder of a distribution. Its
packages lie in that folder, in its `src/` folder, or in a folder one of those files names: `where` and
`package-dir` of setuptools, `pythonpath` of pytest, `from` of a Poetry package, the parent of a package path
of Hatch. No file is run: `setup.py` is read as a syntax tree, and only literal values count.
"""
from __future__ import annotations

import ast
import configparser
import os
import sys
import tomllib
from typing import Any, Callable, Iterable, Optional

from ..discovery import is_excluded_path
from ..interface import DiscoveryExclusions

SOURCE_FOLDER = "src"
_FINDERS = frozenset({"find_packages", "find_namespace_packages"})


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    """A folder or a list of folders as a manifest writes it; a string holds folders separated by blanks."""
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _pyproject_folders(path: str) -> list[str]:
    with open(path, "rb") as manifest:
        tool = _table(tomllib.load(manifest).get("tool"))
    setuptools = _table(tool.get("setuptools"))
    folders = _strings(_table(_table(setuptools.get("packages")).get("find")).get("where"))
    folders += _strings(_table(setuptools.get("package-dir")).get(""))
    folders += _strings(_table(_table(tool.get("pytest")).get("ini_options")).get("pythonpath"))
    packages = _table(tool.get("poetry")).get("packages")
    folders += [name for package in (packages if isinstance(packages, list) else []) for name in _strings(_table(package).get("from"))]
    wheel = _table(_table(_table(_table(tool.get("hatch")).get("build")).get("targets")).get("wheel"))
    folders += [os.path.dirname(package) or "." for package in _strings(wheel.get("packages"))]
    return folders


def _setup_cfg_folders(path: str) -> list[str]:
    parser = configparser.RawConfigParser(strict=False)
    with open(path, "r", encoding="utf-8") as manifest:
        parser.read_file(manifest)
    folders: list[str] = []
    for line in parser.get("options", "package_dir", fallback="").splitlines():
        package, separator, folder = line.partition("=")
        if separator and not package.strip():
            folders += folder.split()
    folders += parser.get("options.packages.find", "where", fallback="").split()
    folders += parser.get("tool:pytest", "pythonpath", fallback="").split()
    return folders


def _literal(node: Optional[ast.AST]) -> list[str]:
    return [node.value] if isinstance(node, ast.Constant) and isinstance(node.value, str) else []


def _setup_py_folders(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as manifest:
        tree = ast.parse(manifest.read(), filename=path)
    folders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "package_dir" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if isinstance(key, ast.Constant) and key.value == "":
                        folders += _literal(value)
        function = node.func
        name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ""
        if name in _FINDERS:
            folders += _literal(node.args[0] if node.args else None)
            folders += [folder for keyword in node.keywords if keyword.arg == "where" for folder in _literal(keyword.value)]
    return folders


_MANIFESTS: tuple[tuple[str, Callable[[str], list[str]]], ...] = (
    ("pyproject.toml", _pyproject_folders), ("setup.cfg", _setup_cfg_folders), ("setup.py", _setup_py_folders),
)


def _folders_with_sources(root: str, paths: Iterable[str]) -> set[str]:
    """Every folder at or below root that holds a source file of the graph, directly or further down."""
    folders: set[str] = set()
    for path in paths:
        folder = os.path.dirname(path)
        while folder not in folders and (folder == root or folder.startswith(root + os.sep)):
            folders.add(folder)
            folder = os.path.dirname(folder)
    return folders


def source_roots(root: str, paths: Iterable[str], exclusions: Optional[DiscoveryExclusions] = None) -> tuple[str, ...]:
    """The source roots below root (absolute, normalised), for the source files at paths.

    Only a folder with source files below it is asked for a manifest, so no folder that discovery
    left out is read. A folder a manifest names outside the repository is not a source root.
    """
    root = os.path.abspath(root)
    found: list[str] = []
    for folder in sorted(_folders_with_sources(root, paths)):
        named: list[str] = []
        is_distribution = False
        for name, read in _MANIFESTS:
            manifest = os.path.join(folder, name)
            if not os.path.isfile(manifest) or (exclusions is not None and is_excluded_path(exclusions, manifest)):
                continue
            is_distribution = True
            try:
                named += read(manifest)
            except (OSError, ValueError, SyntaxError, RecursionError, MemoryError, configparser.Error) as error:
                print(f"[code-graph] WARNING: cannot read the Python source folders of {manifest}: {error}", file=sys.stderr)
        if not is_distribution:
            continue
        for candidate in (".", SOURCE_FOLDER, *named):
            target = os.path.normpath(os.path.join(folder, candidate))
            if (target == root or target.startswith(root + os.sep)) and os.path.isdir(target):
                found.append(target)
    return tuple(dict.fromkeys(found))


def top_level_names(root: str, roots: Iterable[str], paths: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Each top-level name a source root holds (a package folder or a module file) -> the roots that hold it."""
    root = os.path.abspath(root)
    known = frozenset(roots)
    holders: dict[str, dict[str, None]] = {}
    for path in paths:
        name, folder = os.path.basename(path), os.path.dirname(path)
        name = name[:-3] if name.endswith(".py") else name
        while folder == root or folder.startswith(root + os.sep):
            if folder in known:
                holders.setdefault(name, {})[folder] = None
            name, folder = os.path.basename(folder), os.path.dirname(folder)
    return {name: tuple(folders) for name, folders in holders.items()}


__all__ = ["SOURCE_FOLDER", "source_roots", "top_level_names"]
