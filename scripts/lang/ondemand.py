"""Parsers installed on demand: which languages, where their parsers live, how a build finds them.

The grammars of C#, Java, Go, Ruby, PHP and Dart are not dependencies of the package. A build imports a
grammar from the environment that runs it when it is there (`pip install "wingbox[csharp]"`, a requirements
file); otherwise from the per-user folder, where `wingbox add <language>` unpacked the pinned wheel:

    <parsers home>/<platform>/<distribution>-<version>/

<parsers home> is WINGBOX_PARSERS_DIR, else ~/.wingbox/parsers. <platform> keeps apart the parsers of two
processor types or C libraries that share one home folder (darwin-arm64, linux-x86_64, linux-x86_64-musl,
windows-amd64). A folder exists only once its parser is complete: `wingbox add` renames it into place.

`activate` appends the folders that exist to sys.path, after everything the environment offers, so an
installed grammar always wins and its version is read the same way (importlib.metadata) from either place.

Standard library only. Nothing here opens a network connection or writes a file.
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Iterable, Mapping, Optional

from .interface import Language, LanguageDependency

ON_DEMAND_LANGUAGES: tuple[str, ...] = ("csharp", "java", "go", "ruby", "php", "dart")
PARSERS_VARIABLE = "WINGBOX_PARSERS_DIR"
CORE_DISTRIBUTION = "tree-sitter"   # the parser library itself: a dependency of the package, never added on demand


def parsers_home(environ: Optional[Mapping[str, str]] = None) -> str:
    """The per-user folder of the parsers added on demand: WINGBOX_PARSERS_DIR, else ~/.wingbox/parsers."""
    chosen = (os.environ if environ is None else environ).get(PARSERS_VARIABLE, "")
    if chosen:
        return os.path.abspath(os.path.expanduser(chosen))
    return os.path.join(os.path.expanduser("~"), ".wingbox", "parsers")


def this_platform() -> tuple[str, str, bool]:
    """(sys.platform, the processor type this interpreter runs as, whether the C library is glibc)."""
    if sys.platform == "win32":
        import sysconfig

        return "win32", sysconfig.get_platform().lower().replace("win-", "").replace("win32", "x86"), True
    machine = os.uname().machine.lower()
    if sys.maxsize <= 2 ** 32:   # a 32-bit interpreter on a 64-bit kernel
        machine = {"x86_64": "i686", "aarch64": "armv8l"}.get(machine, machine)
    glibc = True
    if sys.platform.startswith("linux"):
        try:
            glibc = (os.confstr("CS_GNU_LIBC_VERSION") or "").startswith("glibc")
        except (AttributeError, OSError, ValueError):
            glibc = False
    return sys.platform, machine, glibc


def platform_key(system: Optional[str] = None, machine: Optional[str] = None, glibc: Optional[bool] = None) -> str:
    """The folder name of a platform: darwin-arm64, linux-x86_64, linux-aarch64-musl, windows-amd64."""
    if system is None or machine is None or glibc is None:
        system, machine, glibc = this_platform()
    name = "windows" if system == "win32" else "linux" if system.startswith("linux") else system
    return f"{name}-{machine}" + ("-musl" if name == "linux" and not glibc else "")


def parser_folder(home: str, platform: str, distribution: str, version: str) -> str:
    return os.path.join(home, platform, f"{distribution}-{version}")


def grammar(language: Language) -> Optional[LanguageDependency]:
    """The grammar package of an on-demand language (its dependency other than the parser library)."""
    if language.name not in ON_DEMAND_LANGUAGES:
        return None
    for dependency in language.dependencies:
        if dependency.distribution != CORE_DISTRIBUTION:
            return dependency
    return None


def user_parser_folders(languages: Iterable[Language], home: str, platform: str) -> tuple[str, ...]:
    """The per-user folders that hold the pinned grammar of an on-demand language, for this platform."""
    folders = []
    for language in languages:
        dependency = grammar(language)
        if dependency is None:
            continue
        folder = parser_folder(home, platform, dependency.distribution, dependency.version)
        if os.path.isdir(folder):
            folders.append(folder)
    return tuple(folders)


def activate(languages: Iterable[Language]) -> tuple[str, ...]:
    """Make the parsers of the per-user folder importable, after those of the environment; the folders added."""
    added = []
    for folder in user_parser_folders(languages, parsers_home(), platform_key()):
        if folder not in sys.path:
            sys.path.append(folder)
            added.append(folder)
    if added:
        importlib.invalidate_caches()
    return tuple(added)
