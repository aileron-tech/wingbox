"""`wingbox add <language>`: install the parser of a language on demand.

    wingbox add LANGUAGE [LANGUAGE ...] [--from PATH] [--sexp]
    wingbox add --list [--sexp]

The package carries the parsers of TypeScript and Rust (Python and Pascal need none). The parsers of
C#, Java, Go, Ruby, PHP and Dart are added by this command, and only by it: a build that meets such files
without their parser skips them, says how many, and names `wingbox add <language>`. Nothing is ever
downloaded on its own.

What `wingbox add csharp` does:

1. Nothing when the parser is already there: in the environment that runs Wingbox (`pip install
   "wingbox[csharp]"`) or in the per-user folder.
2. Picks, from the catalog inside the package (assets/parsers.json: package, exact version, and for each
   wheel of that version its file name, URL and sha256), the wheel that fits this interpreter and platform.
3. Downloads that one file over https from files.pythonhosted.org (wingbox/fetch.py) into a temporary
   folder inside the per-user folder; with --from PATH it reads a wheel file, or a folder of wheel
   files, from disk instead and opens no connection.
4. Refuses the file unless its sha256 is the pinned one, then unpacks it (no member may leave the target
   folder) and renames the finished folder into place:

       ~/.wingbox/parsers/<platform>/<package>-<version>/     (WINGBOX_PARSERS_DIR names another folder)

   A lock in that folder lets two runs at once install once. The folder is keyed by package and version,
   so every Wingbox on the machine that pins the same parser (pipx, uv tool, uvx, Homebrew, pip) uses it.
5. When the project of the current folder has a graph that skipped the language, marks the skipped files
   changed: the next use of the graph rebuilds it with them. Other projects: `wingbox build`.

No pip is needed (a uv tool environment has none): the download and the unpacking use the standard
library. Where no pinned wheel fits the platform, the command says so and names the pip extra.

`add` is the pure core over a Machine (catalog, folders, the interpreter, the download); `this_machine`
builds the real one. Results are facts (wingbox/facts.py): --sexp prints them, the default is text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Optional, Union

from wingbox.facts import Fact, make_fact, parse_fact

USAGE = """usage: wingbox add LANGUAGE [LANGUAGE ...] [--from PATH] [--sexp]
       wingbox add --list [--sexp]

Install the parser of a language that Wingbox does not carry: csharp, java, go, ruby, php, dart.
The pinned wheel is downloaded from the Python Package Index, checked against the sha256 recorded
in the package, and unpacked into ~/.wingbox/parsers (WINGBOX_PARSERS_DIR names another folder).
This is the only Wingbox command that downloads a parser; a build never does.

  --from PATH   Take the wheel from PATH (a wheel file, or a folder of wheel files) instead of
                the network: for a machine without network access. The same sha256 check applies.
  --list        Each on-demand language: whether its parser is installed, and from where
  --sexp        Print the facts as S-expressions"""
LOCK_NAME = ".lock"
TEMPORARY_PREFIX = ".tmp-"
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
_CHUNK = 1 << 16


@dataclass(frozen=True)
class Wheel:
    filename: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Parser:
    language: str
    package: str
    version: str
    module: str
    sdist: bool   # the package index also has a source package of this version
    wheels: tuple[Wheel, ...]

    @property
    def requirement(self) -> str:
        return f"{self.package}=={self.version}"


@dataclass(frozen=True)
class Interpreter:
    """What decides which wheel fits: the Python that runs Wingbox and the system under it."""

    implementation: str           # cpython, pypy
    version: tuple[int, int]
    free_threaded: bool
    system: str                   # darwin, linux, windows
    machine: str                  # x86_64, arm64, aarch64, amd64, ...
    libc: str                     # glibc or musl on Linux, "" elsewhere
    os_version: tuple[int, int]   # macOS version, or the C library version on Linux; (0, 0) on Windows


@dataclass(frozen=True)
class Options:
    languages: tuple[str, ...]
    listing: bool
    source: str   # --from: a wheel file or a folder of wheel files; "" downloads
    sexp: bool


@dataclass(frozen=True)
class UsageError:
    message: str


@dataclass(frozen=True)
class Machine:
    home: str                                               # the per-user folder of the parsers
    platform: str                                           # its sub-folder for this platform
    interpreter: Interpreter
    catalog: Mapping[str, Parser]
    builtin: tuple[str, ...]                                # languages that need nothing added
    fetch: Callable[[str, str, int], None]                  # (url, target file, size): OSError on failure
    environment_version: Callable[[str], Optional[str]]     # version of a distribution in the environment


# ---------------------------------------------------------------------------
# Catalog and wheel choice (pure)
# ---------------------------------------------------------------------------


def read_catalog(text: str) -> dict[str, Parser]:
    """The parsers of assets/parsers.json, in the order of the file."""
    languages = json.loads(text)["languages"]
    return {
        name: Parser(
            name, entry["package"], entry["version"], entry["module"], bool(entry.get("sdist")),
            tuple(Wheel(wheel["filename"], wheel["url"], wheel["sha256"], int(wheel["size"])) for wheel in entry["wheels"]),
        )
        for name, entry in languages.items()
    }


_WHEEL_NAME = re.compile(
    r"^[^-]+-[^-]+(?:-\d[^-]*)?-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)
_CPYTHON_TAG = re.compile(r"cp(\d)(\d+)")
_LEGACY_MANYLINUX = {"1": (2, 5), "2010": (2, 12), "2014": (2, 17)}
_WINDOWS_TAGS = {"amd64": "win_amd64", "arm64": "win_arm64", "x86": "win32"}


def _python_fits(python: str, abi: str, interpreter: Interpreter) -> bool:
    if abi == "none":
        return python.startswith("py3") or python == "py2"
    match = _CPYTHON_TAG.fullmatch(python)
    if match is None or interpreter.implementation != "cpython" or interpreter.free_threaded:
        return False
    tagged = (int(match.group(1)), int(match.group(2)))
    if abi == "abi3":   # the stable ABI: this version of CPython and every later one
        return tagged <= interpreter.version
    return abi == python and tagged == interpreter.version


def _platform_fits(tag: str, interpreter: Interpreter) -> bool:
    if tag == "any":
        return True
    match = re.fullmatch(r"macosx_(\d+)_(\d+)_(\w+)", tag)
    if match:
        return (
            interpreter.system == "darwin" and match.group(3) in (interpreter.machine, "universal2")
            and (int(match.group(1)), int(match.group(2))) <= interpreter.os_version
        )
    match = re.fullmatch(r"(manylinux|musllinux)_(\d+)_(\d+)_(\w+)", tag)
    if match:
        return (
            interpreter.system == "linux" and interpreter.libc == ("glibc" if match.group(1) == "manylinux" else "musl")
            and match.group(4) == interpreter.machine
            and (int(match.group(2)), int(match.group(3))) <= interpreter.os_version
        )
    match = re.fullmatch(r"manylinux(1|2010|2014)_(\w+)", tag)
    if match:
        return (
            interpreter.system == "linux" and interpreter.libc == "glibc" and match.group(2) == interpreter.machine
            and _LEGACY_MANYLINUX[match.group(1)] <= interpreter.os_version
        )
    return interpreter.system == "windows" and tag == _WINDOWS_TAGS.get(interpreter.machine)


def wheel_fits(filename: str, interpreter: Interpreter) -> bool:
    """Whether a wheel, by the tags in its file name, runs on this interpreter and platform."""
    match = _WHEEL_NAME.match(filename)
    if match is None:
        return False
    return any(
        _python_fits(python, abi, interpreter)
        for python in match.group("python").split(".") for abi in match.group("abi").split(".")
    ) and any(_platform_fits(tag, interpreter) for tag in match.group("platform").split("."))


def choose_wheel(parser: Parser, interpreter: Interpreter) -> Optional[Wheel]:
    for wheel in parser.wheels:
        if wheel_fits(wheel.filename, interpreter):
            return wheel
    return None


def describe(interpreter: Interpreter) -> str:
    system = interpreter.system + (f" {interpreter.libc}" if interpreter.libc else "")
    python = f"{interpreter.implementation} {interpreter.version[0]}.{interpreter.version[1]}"
    return f"{system} {interpreter.machine}, {python}" + (" free-threaded" if interpreter.free_threaded else "")


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def _usage(problem: str) -> UsageError:
    return UsageError(f"{problem}\n{USAGE}")


def parse_arguments(args: list[str]) -> Union[Options, UsageError]:
    languages: list[str] = []
    listing = sexp = False
    source = ""
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--list":
            listing = True
        elif argument == "--sexp":
            sexp = True
        elif argument == "--from":
            if index + 1 >= len(args):
                return _usage("--from needs a wheel file or a folder of wheel files")
            source = args[index + 1]
            index += 1
        elif argument.startswith("--from="):
            source = argument[len("--from="):]
        elif argument.startswith("-"):
            return _usage(f"unknown option {argument}")
        else:
            languages.append(argument.lower())
        index += 1
    if listing and (languages or source):
        return _usage("--list takes no language and no --from")
    if not listing and not languages:
        return _usage("name at least one language")
    return Options(tuple(dict.fromkeys(languages)), listing, source, sexp)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _folder(parser: Parser, machine: Machine) -> str:
    return _ondemand().parser_folder(machine.home, machine.platform, parser.package, parser.version)


def _extra(name: str) -> str:
    return f'pip install "wingbox[{name}]"'


def _no_wheel(parser: Parser, machine: Machine) -> str:
    source = "it has a source package, which pip builds with a C compiler" if parser.sdist else "it has no source package either"
    return (
        f"the package index has no wheel of {parser.requirement} for this platform "
        f"({describe(machine.interpreter)}); {source}"
    )


def _other_version(parser: Parser, installed: str) -> str:
    return f"{parser.package} {installed} is installed in the environment that runs Wingbox; Wingbox needs {parser.version}"


def _listed(parser: Parser, machine: Machine) -> Fact:
    name = parser.language
    installed = machine.environment_version(parser.package)
    if installed == parser.version:
        return make_fact("parser-installed", name, package=parser.requirement, **{"from": "environment"})
    if installed is not None:
        return make_fact("parser-missing", name, package=parser.requirement, reason=_other_version(parser, installed),
                         instead=f"pip install {parser.requirement}")
    folder = _folder(parser, machine)
    if os.path.isdir(folder):
        return make_fact("parser-installed", name, package=parser.requirement, **{"from": "per-user-folder"}, at=folder)
    if choose_wheel(parser, machine.interpreter) is None:
        return make_fact("parser-missing", name, package=parser.requirement, reason=_no_wheel(parser, machine),
                         instead=_extra(name))
    return make_fact("parser-missing", name, package=parser.requirement, add=f"wingbox add {name}")


def add(options: Options, machine: Machine) -> Union[list[Fact], UsageError]:
    """Install the parsers of options.languages (or list them all); one fact per language."""
    if options.listing:
        return [_listed(parser, machine) for parser in machine.catalog.values()]
    unknown = [name for name in options.languages if name not in machine.catalog and name not in machine.builtin]
    if unknown:
        return UsageError(
            f"unknown language{'' if len(unknown) == 1 else 's'} {', '.join(unknown)}; "
            f"languages added on demand: {', '.join(sorted(machine.catalog))}"
        )
    return [_add_one(name, options.source, machine) for name in options.languages]


def _add_one(name: str, source: str, machine: Machine) -> Fact:
    if name in machine.builtin:
        return make_fact("parser-present", name, **{"from": "built-in"})
    parser = machine.catalog[name]
    installed = machine.environment_version(parser.package)
    if installed == parser.version:
        return make_fact("parser-present", name, package=parser.requirement, **{"from": "environment"})
    if installed is not None:
        return _failed(parser, _other_version(parser, installed), f"pip install {parser.requirement}")
    folder = _folder(parser, machine)
    if os.path.isdir(folder):
        return _present(parser, folder)
    wheel = choose_wheel(parser, machine.interpreter)
    if wheel is None:
        return _failed(parser, _no_wheel(parser, machine), _extra(name))
    try:
        return _install(parser, wheel, folder, source, machine)
    except OSError as error:
        return _failed(parser, f"cannot write {folder}: {error}")


def _present(parser: Parser, folder: str) -> Fact:
    return make_fact("parser-present", parser.language, package=parser.requirement, **{"from": "per-user-folder"}, at=folder)


def _failed(parser: Parser, reason: str, instead: str = "") -> Fact:
    return make_fact("parser-failed", parser.language, package=parser.requirement, reason=reason, instead=instead)


def _install(parser: Parser, wheel: Wheel, folder: str, source: str, machine: Machine) -> Fact:
    """Under the lock: obtain the pinned wheel, check its sha256, unpack it aside, rename it into place."""
    platform_folder = os.path.dirname(folder)
    with _locked(machine.home):
        if os.path.isdir(folder):   # another run installed it while this one waited for the lock
            return _present(parser, folder)
        os.makedirs(platform_folder, exist_ok=True)
        _remove_leftovers(platform_folder)
        work = tempfile.mkdtemp(prefix=TEMPORARY_PREFIX, dir=platform_folder)
        try:
            if source:
                source = os.path.expanduser(source)
                archive = os.path.join(source, wheel.filename) if os.path.isdir(source) else source
                if not os.path.isfile(archive):
                    return _failed(parser, f"no file {archive}; expected the wheel {wheel.filename} ({wheel.url})")
            else:
                archive = os.path.join(work, wheel.filename)
                try:
                    machine.fetch(wheel.url, archive, wheel.size)
                except OSError as error:
                    return _failed(
                        parser, f"cannot download {wheel.url}: {error}",
                        f"wingbox add {parser.language} --from {wheel.filename}  (the file {wheel.url}, "
                        "fetched where the network is reachable)",
                    )
            digest = _sha256(archive)
            if digest != wheel.sha256:
                return _failed(
                    parser, f"the sha256 of {archive} is {digest}; the pinned wheel {wheel.filename} has {wheel.sha256}"
                )
            unpacked = os.path.join(work, "unpacked")
            problem = unpack(archive, unpacked)
            if problem is not None:
                return _failed(parser, problem)
            helpers = _portable()
            helpers.replace(unpacked, folder)
            helpers.fsync_directory(platform_folder)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return make_fact("parser-added", parser.language, package=parser.requirement, into=folder)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_member(member: zipfile.ZipInfo) -> bool:
    """A member that could land outside the target folder: an absolute or parent path, a drive, a backslash, a link."""
    name = member.filename
    if not name or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name:
        return True
    if any(part in ("", ".", "..") for part in name.rstrip("/").split("/")):
        return True
    kind = stat.S_IFMT(member.external_attr >> 16)
    return kind not in (0, stat.S_IFREG, stat.S_IFDIR)


def unpack(archive: str, target: str) -> Optional[str]:
    """Unpack a wheel into the new folder target; None, or why nothing was unpacked."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            for member in members:
                if _unsafe_member(member):
                    return f"unsafe member {member.filename!r} in {os.path.basename(archive)}: nothing was unpacked"
            if sum(member.file_size for member in members) > MAX_UNPACKED_BYTES:
                return f"unsafe size of {os.path.basename(archive)} when unpacked: nothing was unpacked"
            os.makedirs(target)
            base = os.path.realpath(target)
            for member in members:
                destination = os.path.realpath(os.path.join(base, *member.filename.rstrip("/").split("/")))
                if not destination.startswith(base + os.sep):
                    return f"unsafe member {member.filename!r} in {os.path.basename(archive)}"
                if member.is_dir():
                    os.makedirs(destination, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with bundle.open(member) as packed, open(destination, "wb") as output:
                    shutil.copyfileobj(packed, output)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, RuntimeError, NotImplementedError) as error:
        return f"{os.path.basename(archive)} is not a readable wheel: {error}"
    return None


def _remove_leftovers(platform_folder: str) -> None:
    """Temporary folders of an interrupted run; the caller holds the lock, so none is in use."""
    for name in os.listdir(platform_folder):
        if name.startswith(TEMPORARY_PREFIX):
            shutil.rmtree(os.path.join(platform_folder, name), ignore_errors=True)


@contextmanager
def _locked(home: str) -> Iterator[None]:
    """One `wingbox add` at a time writes into the per-user folder."""
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, LOCK_NAME), "a+", encoding="utf-8") as handle:
        helpers = _portable()
        helpers.lock(handle)
        try:
            yield
        finally:
            helpers.unlock(handle)


def exit_status(facts: list[Fact]) -> int:
    return 1 if any(fact.head.endswith("-failed") for fact in facts) else 0


# ---------------------------------------------------------------------------
# The graph of the current project
# ---------------------------------------------------------------------------


def ready_languages(facts: list[Fact]) -> tuple[str, ...]:
    """The on-demand languages these facts say are installed now."""
    return tuple(
        fact.args[0] for fact in facts
        if fact.head in ("parser-added", "parser-present") and fact.args and fact.get("from") != "built-in"
    )


def mark_skipped(root: str, languages: tuple[str, ...], extensions: Mapping[str, tuple[str, ...]]) -> int:
    """Mark changed the files of root that its last build skipped for want of one of these parsers, the way
    the edit hook marks a file: the next use of the graph rebuilds it. The number of files marked."""
    from wingbox import freshness

    skipped = {fact.args[0] for fact in map(parse_fact, freshness.skipped_languages_line(root).splitlines()) if fact.args}
    suffixes = tuple(suffix for name in languages if name in skipped for suffix in extensions.get(name, ()))
    snapshot = freshness.read_snapshot(root)
    if not suffixes or snapshot is None:
        return 0
    paths = sorted(path for path in snapshot.files if path.endswith(suffixes))
    if paths:
        freshness.mark_changed(root, paths, "parser_marks")
    return len(paths)


def project_facts(root: Optional[str], facts: list[Fact], extensions: Mapping[str, tuple[str, ...]]) -> list[Fact]:
    """What happens to the graphs: the project at root is marked when it skipped an installed language."""
    ready = ready_languages(facts)
    if not ready:
        return []
    marked = mark_skipped(root, ready, extensions) if root is not None else 0
    if marked and root is not None:
        return [make_fact("graph-marked", root, files=str(marked), next="wingbox build")]
    if any(fact.head == "parser-added" for fact in facts):
        return [make_fact("graph-next", "wingbox build")]
    return []


# ---------------------------------------------------------------------------
# Text for people
# ---------------------------------------------------------------------------

_STATES = {
    "parser-added": "added", "parser-present": "present", "parser-installed": "installed",
    "parser-missing": "not installed", "parser-failed": "failed",
}
_SOURCES = {"environment": "in the environment that runs Wingbox", "built-in": "built in, nothing to add"}


def _short(path: str, home: str) -> str:
    """A path under the home folder as ~/..., written with /."""
    shown, base = path.replace(os.sep, "/"), home.replace(os.sep, "/").rstrip("/")
    return "~" + shown[len(base):] if base and (shown == base or shown.startswith(base + "/")) else path


def render_text(facts: list[Fact], home: str = "") -> str:
    """One line per language (state, package, where), then what to do about the graphs."""
    lines: list[str] = []
    for fact in facts:
        name = fact.args[0] if fact.args else ""
        if fact.head == "graph-marked":
            lines.append(
                f"The graph of {_short(name, home)} left out {fact.get('files')} files for want of a parser that is "
                "installed now: it is marked and rebuilt at its next use. In other projects run wingbox build."
            )
            continue
        if fact.head == "graph-next":
            lines.append("A graph includes the files of the added language from its next build: run wingbox build in the project.")
            continue
        where = str(fact.get("into") or fact.get("at"))
        detail = _short(where, home) if where else _SOURCES.get(str(fact.get("from")), "")
        if fact.get("add"):
            detail = f"run: {fact.get('add')}"
        line = f"{name:<8} {_STATES.get(fact.head, fact.head):<14} {str(fact.get('package')):<28} {detail}".rstrip()
        lines.append(line)
        if fact.get("reason"):
            lines.append(f"         {fact.get('reason')}")
        if fact.get("instead"):
            lines.append(f"         instead run: {fact.get('instead')}")
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------------------
# The real machine (adapters)
# ---------------------------------------------------------------------------


def _portable():
    from wingbox.cli import load_script

    return load_script("portable")


def _ondemand():
    from wingbox.cli import load_script

    return load_script("lang.ondemand")


def catalog_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "parsers.json")


def installed_in_environment(distribution: str, home: str) -> Optional[str]:
    """The version of a distribution in the environment that runs Wingbox; None when it is absent or is the
    copy of the per-user folder under home."""
    import importlib.metadata as metadata

    try:
        found = metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        return None
    location = os.path.normcase(os.path.realpath(str(found.locate_file(""))))
    base = os.path.normcase(os.path.realpath(home))
    if location == base or location.startswith(base + os.sep):
        return None
    return found.version


def _two(text: str) -> tuple[int, int]:
    numbers = [int(part) for part in re.findall(r"\d+", text)[:2]]
    return (numbers + [0, 0])[0], (numbers + [0, 0])[1]


def _musl_version() -> tuple[int, int]:
    """The version the musl loader prints (`Version 1.2.4` on stderr when run without arguments)."""
    import glob
    import subprocess

    for loader in sorted(glob.glob("/lib/ld-musl-*.so.1")):
        try:
            printed = subprocess.run([loader], capture_output=True, text=True, timeout=10, check=False).stderr
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r"Version (\d+)\.(\d+)", printed)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 0, 0


def this_interpreter() -> Interpreter:
    import sysconfig

    system, machine, glibc = _ondemand().this_platform()
    implementation = sys.implementation.name
    version = (sys.version_info[0], sys.version_info[1])
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if system == "darwin":
        import platform

        return Interpreter(implementation, version, free_threaded, "darwin", machine, "", _two(platform.mac_ver()[0]))
    if system == "win32":
        return Interpreter(implementation, version, free_threaded, "windows", machine, "", (0, 0))
    if system.startswith("linux"):
        if glibc:
            return Interpreter(
                implementation, version, free_threaded, "linux", machine, "glibc", _two(os.confstr("CS_GNU_LIBC_VERSION") or "")
            )
        return Interpreter(implementation, version, free_threaded, "linux", machine, "musl", _musl_version())
    return Interpreter(implementation, version, free_threaded, system, machine, "", (0, 0))


def _download(url: str, target: str, size: int) -> None:
    """The one call that opens a network connection: imported here so that only a download loads it."""
    from wingbox import fetch

    fetch.download(url, target, size)


def this_machine() -> tuple[Machine, dict[str, tuple[str, ...]]]:
    """The machine `wingbox add` runs on, and the source extensions of every language."""
    from wingbox.cli import load_script

    registry = load_script("lang")
    ondemand = _ondemand()
    home = ondemand.parsers_home()
    with open(catalog_path(), "r", encoding="utf-8") as handle:
        catalog = read_catalog(handle.read())
    machine = Machine(
        home=home,
        platform=ondemand.platform_key(),
        interpreter=this_interpreter(),
        catalog=catalog,
        builtin=tuple(language.name for language in registry.LANGUAGES if language.name not in catalog),
        fetch=_download,
        environment_version=lambda distribution: installed_in_environment(distribution, home),
    )
    return machine, {language.name: tuple(language.source_extensions) for language in registry.LANGUAGES}
