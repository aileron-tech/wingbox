"""`wingbox update [--check] [--dry-run] [--sexp]`: upgrade Wingbox with the installer that installed it.

Wingbox opens no network connection. `wingbox update` runs the package
installer the user already has (pipx, uv or pip) as a subprocess, and only
when asked; that installer reaches the package index.

- pipx when `pipx list --short` names wingbox and the running Python is
  pipx's wingbox environment: `pipx upgrade wingbox`;
- uv when `uv tool list` names wingbox and the running Python is uv's wingbox
  tool environment: `uv tool upgrade wingbox`;
- otherwise pip of the Python environment Wingbox runs in; in an environment without pip (made by
  `uv venv`) `uv pip install --python <this interpreter>` when uv is on PATH, else a message saying what to
  run.

On Windows the `wingbox` program is wingbox.exe, a launcher that keeps running
until Python ends, and Windows lets nobody overwrite or delete a running
program. For the time of the upgrade `wingbox update` closes its own handle on
the launcher it was started through and renames it, so the installer can put the new one in its place; the old
file is removed when the command has ended, and renamed back when the installer
wrote no new launcher (nothing to upgrade, or the installer failed).

A Docker image, a Homebrew install (`brew upgrade wingbox`) and an editable or
local install cannot update themselves: the command says so and prints what to
run instead. After an upgrade the new
version refreshes what `wingbox install` put in place, as recorded in
~/.wingbox/installed.sexp.

It also names the files an earlier version left in a project (.code_graph.sexp,
.code_graph.uses.sexp, .code_graph.uses-cache.json, .code_graph_cache.json,
wingbox-out/): Wingbox now writes only into .wingbox/ and deletes nothing, so
they stay until you remove them. It looks in the current folder and its parents
up to the project root, and in every project of a `--project` install of an earlier version.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Optional, Union

from wingbox.facts import Fact, make_fact, parse_fact
from wingbox.install import Installed

PACKAGE = "wingbox"
_INSTALLER_TIMEOUT_SECONDS = 600
_LATEST_LINE = re.compile(r"^wingbox \(([^)]+)\)", re.M)
_UV_LATEST_LINE = re.compile(r"^\s*\+ wingbox==(\S+)", re.M)   # `uv pip install --dry-run`: " + wingbox==0.8.2"


@dataclass(frozen=True)
class Options:
    check: bool
    dry_run: bool
    sexp: bool


@dataclass(frozen=True)
class UsageError:
    message: str


# Where the running package comes from (read by the composition root).
@dataclass(frozen=True)
class FromIndex:
    pass


@dataclass(frozen=True)
class FromLocal:
    location: str
    editable: bool


@dataclass(frozen=True)
class InDocker:
    pass


Origin = Union[FromIndex, FromLocal, InDocker]


@dataclass(frozen=True)
class Method:
    name: Literal["pipx", "uv", "pip", "uv pip"]
    upgrade: tuple[str, ...]
    latest: tuple[str, ...]


@dataclass(frozen=True)
class CannotUpdate:
    reason: str
    instead: str


@dataclass(frozen=True)
class Ran:
    status: int
    output: str


Runner = Callable[[tuple[str, ...]], Ran]


@dataclass(frozen=True)
class Upgraded:
    ran: Ran
    notes: tuple[Fact, ...] = ()  # what the adapter has to tell about the launcher it moved


Upgrader = Callable[[tuple[str, ...]], Upgraded]


@dataclass(frozen=True)
class Environment:
    origin: Origin
    python: str  # interpreter of this installation
    prefix: str  # its environment folder (sys.prefix)
    installed_version: str
    installed: list[Installed]  # from the install record
    run: Runner  # runs an installer command
    new_version: Callable[[], str]  # version installed after an upgrade, read by a new process
    refresh: Callable[[Installed], list[Fact]]  # `wingbox install` of the new version for one record entry
    has_pip: bool = True  # pip can be imported in this environment (an environment made by `uv venv` has none)
    upgrade: Optional[Upgrader] = None  # runs the upgrade command; None: `run` as it is


def has_pip() -> bool:
    """Whether the pip module exists in the running interpreter's environment."""
    from importlib.util import find_spec

    return find_spec("pip") is not None



def _hidden() -> dict:
    """Options that start a child without a console window on Windows (scripts/portable.py)."""
    from wingbox import freshness

    return freshness.portable().hidden_process_options()


def parse_arguments(args: list[str]) -> Union[Options, UsageError]:
    check = dry_run = sexp = False
    for argument in args:
        if argument == "--check":
            check = True
        elif argument == "--dry-run":
            dry_run = True
        elif argument == "--sexp":
            sexp = True
        else:
            return UsageError(f"unknown argument {argument}")
    return Options(check, dry_run, sexp)


def _lists_wingbox(output: str) -> bool:
    return any(line.split()[:1] == [PACKAGE] for line in output.splitlines())


def _runs_from(prefix: str, run: Runner, command: tuple[str, ...]) -> bool:
    """True when prefix is the wingbox environment inside the folder the installer prints."""
    ran = run(command)
    folder = ran.output.strip().splitlines()[-1].strip() if ran.status == 0 and ran.output.strip() else ""
    return bool(folder) and os.path.realpath(prefix) == os.path.realpath(os.path.join(folder, PACKAGE))


def homebrew_keg(prefix: str) -> bool:
    """True when the environment folder lies in a Homebrew keg of Wingbox: .../Cellar/wingbox/<version>/..."""
    parts = Path(os.path.realpath(prefix)).parts
    return any(pair == ("Cellar", PACKAGE) for pair in zip(parts, parts[1:]))


def choose_method(
    origin: Origin, python: str, prefix: str, run: Runner, has_pip: bool = True
) -> Union[Method, CannotUpdate]:
    if isinstance(origin, InDocker):
        return CannotUpdate("docker-image", "git -C wingbox pull && docker build -t wingbox wingbox")
    if homebrew_keg(prefix):
        # Homebrew owns the keg: an upgrade by pip or uv inside it would change files behind its back.
        return CannotUpdate("homebrew-install", "brew upgrade wingbox")
    if isinstance(origin, FromLocal):
        if origin.editable:
            return CannotUpdate("editable-install", f"git -C {origin.location} pull")
        return CannotUpdate("local-install", f"{python} -m pip install --upgrade {origin.location}")
    # pipx exits 1 when any other package has a missing interpreter, yet still lists wingbox: trust the listing.
    pipx = run(("pipx", "list", "--short"))
    if (
        _lists_wingbox(pipx.output)
        and _runs_from(prefix, run, ("pipx", "environment", "--value", "PIPX_LOCAL_VENVS"))
    ):
        return Method("pipx", ("pipx", "upgrade", PACKAGE), ("pipx", "runpip", PACKAGE, "index", "versions", PACKAGE))
    uv = run(("uv", "tool", "list"))
    if uv.status == 0 and _lists_wingbox(uv.output) and _runs_from(prefix, run, ("uv", "tool", "dir")):
        return Method("uv", ("uv", "tool", "upgrade", PACKAGE), ("uv", "tool", "run", "pip", "index", "versions", PACKAGE))
    if not has_pip:
        if run(("uv", "--version")).status == 0:
            return Method(
                "uv pip",
                ("uv", "pip", "install", "--python", python, "--upgrade", PACKAGE),
                ("uv", "pip", "install", "--python", python, "--dry-run", "--upgrade", "--reinstall-package", PACKAGE, PACKAGE),
            )
        return CannotUpdate(
            "no-pip",
            f"{python} -m ensurepip && {python} -m pip install --upgrade {PACKAGE}"
            f" (or with uv: uv pip install --python {python} --upgrade {PACKAGE})",
        )
    return Method(
        "pip",
        (python, "-m", "pip", "install", "--upgrade", PACKAGE),
        (python, "-m", "pip", "index", "versions", PACKAGE),
    )


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"


def _failed(command: tuple[str, ...], ran: Ran) -> Fact:
    return make_fact("update-failed", reason=f"{' '.join(command)} exited with {ran.status}: {_last_line(ran.output)}")


def latest_version(method: Method, run: Runner) -> Union[str, Fact]:
    ran = run(method.latest)
    if ran.status != 0:
        return _failed(method.latest, ran)
    found = (_UV_LATEST_LINE if method.name == "uv pip" else _LATEST_LINE).search(ran.output)
    if found is None:
        return make_fact("update-failed", reason=f"{' '.join(method.latest)} named no version of wingbox")
    return found.group(1)


def _refresh_target(entry: Installed) -> Fact:
    return make_fact("update-would-refresh", entry.root, scope=entry.scope, assistant=entry.assistants)


def project_notes(installed: list[Installed]) -> list[Fact]:
    """One note per repository where a version before 0.9.0 installed into the project: its files are still there,
    and update no longer refreshes them (Wingbox writes only to .wingbox/ and to your own settings)."""
    return [
        make_fact("update-project-files-left", entry.root, assistant=entry.assistants,
                  instead=f"cd {entry.root} && wingbox uninstall --project")
        for entry in installed if entry.scope == "project"
    ]


def update(options: Options, env: Environment) -> list[Fact]:
    method = choose_method(env.origin, env.python, env.prefix, env.run, env.has_pip)
    facts = [make_fact("update-from", env.installed_version)] + project_notes(env.installed)
    if isinstance(method, CannotUpdate):
        return facts + [make_fact("update-failed", reason=method.reason, instead=method.instead)]
    facts.append(make_fact("update-method", method.name))
    if options.check:
        latest = latest_version(method, env.run)
        if isinstance(latest, Fact):
            return facts + [latest]
        return facts + [make_fact("update-latest", latest)]
    if options.dry_run:
        return facts + [make_fact("update-would-run", " ".join(method.upgrade))] + [
            _refresh_target(entry) for entry in env.installed if entry.scope == "global"
        ]
    upgraded = env.upgrade(method.upgrade) if env.upgrade is not None else Upgraded(env.run(method.upgrade))
    facts.extend(upgraded.notes)
    if upgraded.ran.status != 0:
        return facts + [_failed(method.upgrade, upgraded.ran)]
    facts.append(make_fact("update-to", env.new_version()))
    for entry in env.installed:
        if entry.scope == "global":
            facts.extend(env.refresh(entry))
    return facts


OLD_LAYOUT_FILES = (".code_graph.sexp", ".code_graph.uses.sexp", ".code_graph.uses-cache.json", ".code_graph_cache.json")
OLD_LAYOUT_FOLDER = "wingbox-out"
_YELLOW, _RESET = "\033[33m", "\033[0m"


def _old_files(folder: str) -> tuple[str, ...]:
    names = tuple(name for name in OLD_LAYOUT_FILES if os.path.isfile(os.path.join(folder, name)))
    return names + ((OLD_LAYOUT_FOLDER + "/",) if os.path.isdir(os.path.join(folder, OLD_LAYOUT_FOLDER)) else ())


def _folders_up_to_root(cwd: str) -> list[str]:
    """cwd and its parents up to the project root (a folder holding .wingbox/ or .git); cwd alone without one."""
    folders: list[str] = []
    folder = os.path.abspath(cwd)
    while True:
        folders.append(folder)
        if os.path.isdir(os.path.join(folder, ".wingbox")) or os.path.exists(os.path.join(folder, ".git")):
            return folders
        parent = os.path.dirname(folder)
        if parent == folder:
            return folders[:1]
        folder = parent


def old_layout_warnings(cwd: str, installed: list[Installed]) -> list[Fact]:
    """One `update-warning` fact per project folder that still holds files of the old layout."""
    folders = _folders_up_to_root(cwd) + [entry.root for entry in installed if entry.scope == "project"]
    facts: list[Fact] = []
    for folder in dict.fromkeys(os.path.abspath(item) for item in folders):
        names = _old_files(folder)
        if names:
            facts.append(make_fact("update-warning", Path(folder).as_posix(), old_files=names))   # POSIX on every system
    return facts


def use_color(is_terminal: bool, environ: Mapping[str, str]) -> bool:
    return is_terminal and "NO_COLOR" not in environ


def warning_text(fact: Fact, color: bool) -> str:
    names = fact.get("old-files")
    listed = ", ".join(names) if isinstance(names, tuple) else names
    line = (
        f"! WARNING: {fact.args[0]}: old Wingbox files left: {listed}"
        " - Wingbox now uses .wingbox/ only; remove them yourself"
    )
    return _YELLOW + line + _RESET if color else line


def exit_status(facts: list[Fact]) -> int:
    return 1 if any(fact.head == "update-failed" for fact in facts) else 0


# ---------------------------------------------------------------------------
# Adapters used by the composition root
# ---------------------------------------------------------------------------

def run_installer(command: tuple[str, ...]) -> Ran:
    """Run an installer; a missing program or a time-out is a failed run, never an exception."""
    # shutil.which finds the program as the system does (PATHEXT on Windows: pipx.exe, uv.exe, a .cmd shim).
    program = shutil.which(command[0]) or command[0]
    try:
        completed = subprocess.run(
            [program, *command[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_INSTALLER_TIMEOUT_SECONDS, check=False, **_hidden(),
        )
    except FileNotFoundError:
        return Ran(127, f"{command[0]}: not found")
    except subprocess.TimeoutExpired:
        return Ran(124, f"{command[0]}: no answer after {_INSTALLER_TIMEOUT_SECONDS} s")
    except OSError as error:
        return Ran(126, f"{command[0]}: {error}")
    return Ran(completed.returncode, (completed.stdout or "") + (completed.stderr or ""))


LAUNCHER = "wingbox.exe"
# Removes the old launcher once the process that runs it has ended (until then Windows refuses).
_REMOVE_WHEN_FREE = (
    "import os, sys, time\n"
    "deadline = time.monotonic() + 600\n"
    "while time.monotonic() < deadline:\n"
    "    try:\n"
    "        os.remove(sys.argv[1])\n"
    "        break\n"
    "    except FileNotFoundError:\n"
    "        break\n"
    "    except OSError:\n"
    "        time.sleep(0.5)\n"
)


# The removal outlives this process: its handle is kept until then instead of being dropped while it runs.
_REMOVALS: list["subprocess.Popen"] = []


def running_launcher(argv0: str, windows: bool) -> Optional[str]:
    """The wingbox.exe this process was started through, on Windows; None elsewhere and for `python -m wingbox.cli`.

    A console-script launcher (pip, pipx, uv) runs Python with its own path as the script, and the script
    drops the `.exe` ending from sys.argv[0]."""
    if not windows:
        return None
    path = argv0 if argv0.lower().endswith(".exe") else argv0 + ".exe"
    if os.path.basename(path).lower() != LAUNCHER or not os.path.isfile(path):
        return None
    return os.path.abspath(path)


def close_archive_handles() -> None:
    """Close the files importlib.metadata keeps open.

    Python runs the launcher as a zip archive, so it is the first entry of sys.path, and importlib.metadata
    (asked for the installed version) keeps every archive of sys.path open in a cache. Windows renames no
    file that is open this way. Dropping the caches and collecting what they held closes the file."""
    import gc
    import importlib

    importlib.invalidate_caches()
    gc.collect()


def detached_options() -> dict:
    """Options for a child that outlives this process, without a console window (scripts/portable.py).
    Read before the upgrade: afterwards the package files on disk belong to the new version."""
    from wingbox import freshness

    return freshness.portable().detached_process_options()


def start_removal(python: str, path: str, detached: dict) -> Optional[str]:
    """Start a process without a window that removes path once it is free; the error text when it cannot start."""
    try:
        process = subprocess.Popen(
            [python, "-I", "-c", _REMOVE_WHEN_FREE, path], cwd=os.path.dirname(python) or None,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **detached,
        )
    except OSError as error:
        return str(error)
    _REMOVALS.append(process)
    return None


def upgrade_past_launcher(
    command: tuple[str, ...], launcher: Optional[str], run: Runner,
    remove_later: Callable[[str], Optional[str]],
) -> Upgraded:
    """Run the upgrade with the running launcher renamed, so the installer can write the new one in its place.

    Windows refuses to overwrite or delete a running program and allows renaming it, once this process holds
    the file open no more (close_archive_handles). After the run: a new
    launcher in place means the old file goes (remove_later removes it when this command has ended); no
    launcher in place (nothing to upgrade, or the installer failed) means the old one is renamed back."""
    if launcher is None:
        return Upgraded(run(command))
    aside = f"{launcher}.{os.getpid()}.old"
    close_archive_handles()
    try:
        os.rename(launcher, aside)
    except OSError as error:
        # The installer runs as before and reports what it could not replace.
        return Upgraded(run(command), (make_fact("update-launcher-in-place", launcher, reason=str(error)),))
    ran = run(command)
    if os.path.exists(launcher):
        failure = remove_later(aside)
        return Upgraded(ran, () if failure is None else (make_fact("update-left", aside, reason=failure),))
    try:
        os.rename(aside, launcher)
    except OSError as error:
        return Upgraded(Ran(ran.status or 1, f"{ran.output}\n{launcher} is now {aside}: could not rename it back: {error}"))
    return Upgraded(ran)


def read_new_version(python: str) -> str:
    ran = run_installer((python, "-c", "import importlib.metadata as m; print(m.version('wingbox'))"))
    return ran.output.strip().splitlines()[-1] if ran.status == 0 and ran.output.strip() else "unknown"


def refresh_installed(python: str, package_parent: str, entry: Installed) -> list[Fact]:
    """Run `wingbox install` of the upgraded package for one record entry; report what it rewrote."""
    command = [python, "-m", "wingbox.cli", "install", "--yes", "--sexp"]
    for name in entry.assistants:
        command += ["--assistant", name]
    # The items the person left out stay out (an empty --without would put them back).
    command += ["--without", ",".join(entry.without) if entry.without else "none"]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (package_parent, environment.get("PYTHONPATH", "")) if part
    )
    cwd = entry.root if entry.scope == "project" else None
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, env=environment,
            timeout=_INSTALLER_TIMEOUT_SECONDS, check=False, **_hidden(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [make_fact("update-failed", entry.root, reason=f"refresh: {error}")]
    facts: list[Fact] = []
    for line in completed.stdout.splitlines():
        fact = parse_fact(line)
        if fact.head == "install-wrote" and fact.get("what") != "record":
            facts.append(make_fact("update-refreshed", *fact.args, what=fact.get("what"),
                                   assistant=fact.get("assistant")))
        elif fact.head == "install-failed":
            facts.append(make_fact("update-failed", *fact.args, reason=f"refresh: {fact.get('reason')}"))
    if completed.returncode != 0 and not any(fact.head == "update-failed" for fact in facts):
        facts.append(make_fact("update-failed", entry.root, reason=f"refresh: {_last_line(completed.stderr)}"))
    return facts


def package_origin(direct_url_json: Optional[str], in_docker: bool) -> Origin:
    """Origin from the distribution's direct_url.json (PEP 610); none means installed from an index."""
    if in_docker:
        return InDocker()
    if not direct_url_json:
        return FromIndex()
    try:
        data = json.loads(direct_url_json)
    except json.JSONDecodeError:
        return FromIndex()
    if not isinstance(data, dict):
        return FromIndex()
    url, dir_info = data.get("url", ""), data.get("dir_info")
    # A local folder (editable or not) is a source install; a wheel or archive file is upgraded from the index.
    if not isinstance(url, str) or not url.startswith("file://") or not isinstance(dir_info, dict):
        return FromIndex()
    return FromLocal(url[len("file://"):], bool(dir_info.get("editable")))
