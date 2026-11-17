"""`wingbox update [--check] [--dry-run] [--sexp]`: upgrade Wingbox with the installer that installed it.

Wingbox opens no network connection. `wingbox update` runs the package
installer the user already has (pipx, uv or pip) as a subprocess, and only
when asked; that installer reaches the package index.

- pipx when `pipx list --short` names wingbox and the running Python is
  pipx's wingbox environment: `pipx upgrade wingbox`;
- uv when `uv tool list` names wingbox and the running Python is uv's wingbox
  tool environment: `uv tool upgrade wingbox`;
- otherwise pip of the Python environment Wingbox runs in.

A Docker image and an editable or local install cannot update themselves: the
command says so and prints what to run instead. After an upgrade the new
version refreshes what `wingbox install` put in place, as recorded in
~/.wingbox/installed.sexp.

It also names the files an earlier version left in a project (.code_graph.sexp,
.code_graph.uses.sexp, .code_graph.uses-cache.json, .code_graph_cache.json,
wingbox-out/): Wingbox now writes only into .wingbox/ and deletes nothing, so
they stay until you remove them. It looks in the current folder and its parents
up to the project root, and in every project of a `--project` install.
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
    name: Literal["pipx", "uv", "pip"]
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
class Environment:
    origin: Origin
    python: str  # interpreter of this installation
    prefix: str  # its environment folder (sys.prefix)
    installed_version: str
    installed: list[Installed]  # from the install record
    run: Runner  # runs an installer command
    new_version: Callable[[], str]  # version installed after an upgrade, read by a new process
    refresh: Callable[[Installed], list[Fact]]  # `wingbox install` of the new version for one record entry


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


def choose_method(origin: Origin, python: str, prefix: str, run: Runner) -> Union[Method, CannotUpdate]:
    if isinstance(origin, InDocker):
        return CannotUpdate("docker-image", "git -C wingbox pull && docker build -t wingbox wingbox")
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
    found = _LATEST_LINE.search(ran.output)
    if found is None:
        return make_fact("update-failed", reason=f"{' '.join(method.latest)} named no version of wingbox")
    return found.group(1)


def _refresh_target(entry: Installed) -> Fact:
    return make_fact("update-would-refresh", entry.root, scope=entry.scope, assistant=entry.assistants)


def update(options: Options, env: Environment) -> list[Fact]:
    method = choose_method(env.origin, env.python, env.prefix, env.run)
    facts = [make_fact("update-from", env.installed_version)]
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
            _refresh_target(entry) for entry in env.installed
        ]
    ran = env.run(method.upgrade)
    if ran.status != 0:
        return facts + [_failed(method.upgrade, ran)]
    facts.append(make_fact("update-to", env.new_version()))
    for entry in env.installed:
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
            timeout=_INSTALLER_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError:
        return Ran(127, f"{command[0]}: not found")
    except subprocess.TimeoutExpired:
        return Ran(124, f"{command[0]}: no answer after {_INSTALLER_TIMEOUT_SECONDS} s")
    except OSError as error:
        return Ran(126, f"{command[0]}: {error}")
    return Ran(completed.returncode, (completed.stdout or "") + (completed.stderr or ""))


def read_new_version(python: str) -> str:
    ran = run_installer((python, "-c", "import importlib.metadata as m; print(m.version('wingbox'))"))
    return ran.output.strip().splitlines()[-1] if ran.status == 0 and ran.output.strip() else "unknown"


def refresh_installed(python: str, package_parent: str, entry: Installed) -> list[Fact]:
    """Run `wingbox install` of the upgraded package for one record entry; report what it rewrote."""
    command = [python, "-m", "wingbox.cli", "install", f"--{entry.scope}", "--sexp"]
    for name in entry.assistants:
        command += ["--assistant", name]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (package_parent, environment.get("PYTHONPATH", "")) if part
    )
    cwd = entry.root if entry.scope == "project" else None
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, cwd=cwd, env=environment,
            timeout=_INSTALLER_TIMEOUT_SECONDS, check=False,
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
