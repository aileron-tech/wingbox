"""The file an `#include` names.

A compiler looks for an included file beside the including file (a quoted name only) and then in
its include folders. Those folders are read, in this order of trust:

1. from the compilation database (`compile_commands.json`): the folders of the entry of the file;
   a header has no entry and takes the folders of the entries of the nearest folder;
2. without a database, from the include folders the CMake files of the repository name literally;
3. last, for a quoted name, the one file of the repository whose path ends with the written name.

A name that none of them finds (a system header, a library outside the repository, a path two files
end with) is no dependency: the graph never guesses between two files. A name in angle brackets and
the name of a standard header are found in an include folder or not at all: `<time.h>` is not the
`time.h` that happens to lie somewhere in the repository.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions, FileResult

DATABASE_NAME = "compile_commands.json"
# Folders at the root that are never searched for a compilation database.
_NO_DATABASE_FOLDERS = frozenset({".git", "node_modules", ".wingbox", ".venv", "venv"})
# Options that name an include folder: the folder follows as the next argument or is joined to the option.
_QUOTE_OPTIONS = ("-iquote",)
_FOLDER_OPTIONS = ("-isystem", "-idirafter", "--include-directory=", "--include-directory", "-external:I", "-I", "/I")
_CMAKE_CALL = re.compile(r"\b(target_include_directories|include_directories)\s*\(([^()]*)\)", re.IGNORECASE)
_CMAKE_KEYWORDS = frozenset({"PUBLIC", "PRIVATE", "INTERFACE", "SYSTEM", "BEFORE", "AFTER"})
_CMAKE_HERE = ("${CMAKE_CURRENT_SOURCE_DIR}", "${CMAKE_CURRENT_LIST_DIR}")
_CMAKE_TOP = ("${PROJECT_SOURCE_DIR}", "${CMAKE_SOURCE_DIR}")
# The headers of the C and C++ standard libraries and of POSIX that are written with `.h`: a quoted
# include of one of them means the system header unless an include folder holds a file of that name.
_STANDARD_HEADERS = frozenset(
    name + ".h" for name in (
        "assert", "complex", "ctype", "errno", "fenv", "float", "inttypes", "iso646", "limits", "locale", "math",
        "setjmp", "signal", "stdalign", "stdarg", "stdatomic", "stdbit", "stdbool", "stdckdint", "stddef", "stdint",
        "stdio", "stdlib", "stdnoreturn", "string", "tgmath", "threads", "time", "uchar", "wchar", "wctype",
        "dirent", "dlfcn", "fcntl", "getopt", "glob", "grp", "libgen", "netdb", "poll", "pthread", "pwd", "regex",
        "sched", "semaphore", "strings", "syslog", "termios", "unistd", "utime", "malloc", "memory", "io", "process",
        "direct", "conio", "windows",
    )
)
_GENERATOR_EXPRESSION = re.compile(r"^\$<(?:BUILD_INTERFACE|INSTALL_INTERFACE):(.*)>$")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _arguments(entry: Mapping[str, object]) -> list[str]:
    arguments = entry.get("arguments")
    if isinstance(arguments, list):
        return [str(argument) for argument in arguments]
    command = entry.get("command")
    if not isinstance(command, str):
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _option_folders(arguments: list[str]) -> tuple[list[str], list[str]]:
    """(folders for quoted names only, folders for every name) as the command line names them, in its order."""
    quoted: list[str] = []
    every: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        for options, target in ((_QUOTE_OPTIONS, quoted), (_FOLDER_OPTIONS, every)):
            option = next((option for option in options if argument.startswith(option)), None)
            if option is None:
                continue
            value = argument[len(option):]
            if not value and not option.endswith("=") and index < len(arguments):
                value = arguments[index]
                index += 1
            if value:
                target.append(value)
            break
    return quoted, every


@dataclass(frozen=True)
class _Folders:
    """The include folders of one translation unit, inside the repository, in search order."""

    quoted: tuple[str, ...] = ()
    every: tuple[str, ...] = ()

    def of(self, is_quoted: bool) -> tuple[str, ...]:
        return self.quoted + self.every if is_quoted else self.every


def _merged(items: list[_Folders]) -> _Folders:
    quoted: dict[str, None] = {}
    every: dict[str, None] = {}
    for item in items:
        quoted.update(dict.fromkeys(item.quoted))
        every.update(dict.fromkeys(item.every))
    return _Folders(tuple(quoted), tuple(every))


def _database_paths(root: str) -> list[str]:
    """`compile_commands.json` at the root, in a folder of the root (`build/`) or one level below (`build/release/`)."""
    found = []
    here = os.path.join(root, DATABASE_NAME)
    if os.path.isfile(here):
        found.append(here)
    try:
        first_level = sorted(os.listdir(root))
    except OSError:
        return found
    for name in first_level:
        folder = os.path.join(root, name)
        if name in _NO_DATABASE_FOLDERS or not os.path.isdir(folder):
            continue
        candidate = os.path.join(folder, DATABASE_NAME)
        if os.path.isfile(candidate):
            found.append(candidate)
        try:
            second_level = sorted(os.listdir(folder))
        except OSError:
            continue
        for inner in second_level:
            candidate = os.path.join(folder, inner, DATABASE_NAME)
            if os.path.isfile(candidate):
                found.append(candidate)
    return found


def _load_databases(root: str) -> dict[str, _Folders]:
    """Source file -> its include folders, for every entry of every compilation database of the repository.
    Paths are those of the database; a folder outside the repository is left out."""
    by_file: dict[str, _Folders] = {}
    for database in _database_paths(root):
        try:
            with open(database, "r", encoding="utf-8-sig") as handle:
                entries = json.load(handle)
        except (OSError, ValueError, RecursionError) as error:
            # ValueError: text that is not JSON or not UTF-8; RecursionError: nested deeper than the reader goes.
            print(
                f"[code-graph] Cannot read the compilation database {database}: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                continue
            directory = entry.get("directory")
            base = os.path.join(os.path.dirname(database), directory) if isinstance(directory, str) else os.path.dirname(database)
            source = os.path.normpath(os.path.join(base, entry["file"]))
            if source in by_file or not _inside(source, root):
                continue
            quoted, every = _option_folders(_arguments(entry))
            by_file[source] = _Folders(
                tuple(dict.fromkeys(path for path in (os.path.normpath(os.path.join(base, folder)) for folder in quoted) if _inside(path, root))),
                tuple(dict.fromkeys(path for path in (os.path.normpath(os.path.join(base, folder)) for folder in every) if _inside(path, root))),
            )
    return by_file


def _cmake_folders(root: str, discovery_exclusions: DiscoveryExclusions) -> tuple[str, ...]:
    """The include folders the CMake files name literally, inside the repository, in the order they are read.

    `${CMAKE_CURRENT_SOURCE_DIR}` and a relative path are taken from the folder of the nearest
    `CMakeLists.txt` at or above the file, then from the folder of the file itself (a `.cmake` file
    is read in the scope of the list file that includes it); `${PROJECT_SOURCE_DIR}` and
    `${CMAKE_SOURCE_DIR}` from every folder above that holds a `CMakeLists.txt`, the top one first.
    A folder that does not exist, or a path with any other variable, is left out.
    """
    list_folders: set[str] = set()
    cmake_files: list[str] = []
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(retained_directory_names(discovery_exclusions, directory_path, directory_names))
        for name in sorted(file_names):
            if name == "CMakeLists.txt" or name.endswith(".cmake"):
                path = os.path.join(directory_path, name)
                if not is_excluded_path(discovery_exclusions, path):
                    cmake_files.append(path)
                    if name == "CMakeLists.txt":
                        list_folders.add(os.path.normpath(directory_path))
    folders: dict[str, None] = {}
    for path in cmake_files:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        if "include_directories" not in text.lower():
            continue
        here = os.path.normpath(os.path.dirname(path))
        above = []   # the folders with a list file at or above this file, the nearest first
        current = here
        while _inside(current, root):
            if current in list_folders:
                above.append(current)
            if current == root:
                break
            current = os.path.dirname(current)
        scopes = list(dict.fromkeys([*above[:1], here]))
        tops = list(reversed(above)) or [root]
        for call in _CMAKE_CALL.finditer(text):
            tokens = [token.strip('"') for token in call.group(2).split()]
            if call.group(1).lower() == "target_include_directories":
                tokens = tokens[1:]
            for token in tokens:
                if not token or token in _CMAKE_KEYWORDS:
                    continue
                expression = _GENERATOR_EXPRESSION.match(token)
                if expression:
                    token = expression.group(1)
                bases = scopes
                for variables, variable_bases in ((_CMAKE_HERE, scopes), (_CMAKE_TOP, tops)):
                    variable = next((variable for variable in variables if token.startswith(variable)), None)
                    if variable is not None:
                        token = token[len(variable):].lstrip("/\\")
                        bases = variable_bases
                        break
                if "$" in token or os.path.isabs(token):
                    continue
                for base in bases:
                    candidate = os.path.normpath(os.path.join(base, token))
                    if _inside(candidate, root) and os.path.isdir(candidate):
                        folders.setdefault(candidate, None)
                        break
    return tuple(folders)


@dataclass(frozen=True)
class BoundCppLanguage:
    root: str
    path_to_id: Mapping[str, str]
    # Compilation database: the folders of each translation unit, and of each folder that holds some.
    unit_folders: Mapping[str, _Folders]
    folder_folders: Mapping[str, _Folders]
    database_folders: _Folders
    # Without a database for the file: what the CMake files name, then the tree itself.
    cmake_folders: tuple[str, ...]
    by_name: Mapping[str, tuple[str, ...]]
    _resolved: dict[tuple[str, str], str | None] = field(default_factory=dict, compare=False, repr=False)
    # The includes found without the folders a compiler was given: through the CMake files or by the end of a path.
    _inferred: set[tuple[str, str]] = field(default_factory=set, compare=False, repr=False)
    _nearest: dict[str, _Folders] = field(default_factory=dict, compare=False, repr=False)
    _ordered_cmake: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundCppLanguage:
        from . import CPP_LANGUAGE

        root = os.path.normpath(os.path.abspath(root))
        units = _load_databases(root)
        in_folder: dict[str, list[_Folders]] = {}
        for source in sorted(units):
            in_folder.setdefault(os.path.dirname(source), []).append(units[source])
        by_name: dict[str, list[str]] = {}
        for path in sorted(path_to_id):
            if CPP_LANGUAGE.accepts_file(path):
                by_name.setdefault(os.path.basename(path), []).append(path)
        return cls(
            root=root,
            path_to_id=path_to_id,
            unit_folders=units,
            folder_folders={folder: _merged(items) for folder, items in in_folder.items()},
            database_folders=_merged([units[source] for source in sorted(units)]),
            # The CMake files are read only when no database says what the compiler was given.
            cmake_folders=() if units else _cmake_folders(root, discovery_exclusions),
            by_name={name: tuple(paths) for name, paths in by_name.items()},
        )

    def _database_folders_for(self, source: str) -> _Folders:
        """The include folders of a file: its own entry, else the entries of the nearest folder at or
        above it that holds a translation unit, else every folder of the database."""
        own = self.unit_folders.get(source)
        if own is not None:
            return own
        start = os.path.dirname(source)
        if start not in self._nearest:
            current = start
            found = self.database_folders
            while _inside(current, self.root):
                if current in self.folder_folders:
                    found = self.folder_folders[current]
                    break
                if current == self.root:
                    break
                current = os.path.dirname(current)
            self._nearest[start] = found
        return self._nearest[start]

    def _cmake_folders_for(self, folder: str) -> tuple[str, ...]:
        """The include folders of the CMake files, those that share the longest path with the including file first."""
        if folder not in self._ordered_cmake:
            def shared(candidate: str) -> int:
                return len(os.path.commonpath([candidate, folder]))

            self._ordered_cmake[folder] = tuple(sorted(self.cmake_folders, key=lambda candidate: (-shared(candidate), candidate)))
        return self._ordered_cmake[folder]

    def _by_path_end(self, name: str) -> str | None:
        """The one file of the repository whose path ends with the written name."""
        if name.startswith(("../", "./")) or "/../" in name or name in _STANDARD_HEADERS:
            return None
        ending = os.sep + os.path.normpath(name)
        matches = [path for path in self.by_name.get(os.path.basename(name), ()) if path.endswith(ending)]
        return self.path_to_id.get(matches[0]) if len(matches) == 1 else None

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        key = (import_name, source_path)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_import(import_name, source_path)
        return self._resolved[key]

    def _resolve_import(self, import_name: str, source_path: str) -> str | None:
        is_quoted = not import_name.startswith("<")
        name = import_name.strip("<>")
        if not name or os.path.isabs(name):
            return None
        source = os.path.normpath(os.path.abspath(source_path))
        folder = os.path.dirname(source)
        stated: list[str] = [folder] if is_quoted else []
        stated.extend(self._database_folders_for(source).of(is_quoted))
        for base in stated:
            target_id = self.path_to_id.get(os.path.normpath(os.path.join(base, name)))
            if target_id is not None:
                return target_id
        for base in self._cmake_folders_for(folder):
            target_id = self.path_to_id.get(os.path.normpath(os.path.join(base, name)))
            if target_id is not None:
                self._inferred.add((import_name, source_path))
                return target_id
        target_id = self._by_path_end(name) if is_quoted else None
        if target_id is not None:
            self._inferred.add((import_name, source_path))
        return target_id

    def import_basis(self, import_name: str, source_path: str) -> str:
        """`inferred` for an include that was found through the CMake files or by the end of its path, ""
        for one found beside the including file or in an include folder of the compilation database."""
        self.resolve_import(import_name, source_path)
        return "inferred" if (import_name, source_path) in self._inferred else ""

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
