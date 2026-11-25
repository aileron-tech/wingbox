r"""What differs between Windows, macOS and Linux, in one place. Everything else calls these helpers.

- file locks: `lock` / `unlock` on an open file (fcntl.flock, or msvcrt.locking on Windows); the system
  releases them when the process ends;
- a detached child process: `detached_process_options` (a new session, or on Windows a new process group
  with a console that has no window), and `hidden_process_options` for every other child Wingbox starts;
- atomic replacement: `replace` retries a moment when Windows refuses to replace a file another process
  holds open; `fsync_directory` is a no-op where a folder cannot be opened;
- the start method of worker processes: `start_method` (as before: fork for the resolution workers where
  it exists, the system default for parsing; WINGBOX_START_METHOD overrides it, which the tests use to run
  the Windows path, spawn, anywhere);
- text streams: `utf8_streams` makes stdout and stderr write UTF-8 and replace what they cannot encode;
- paths at the edge: `local_path` turns a path from a person or a hook into this system's form (on Windows
  backslashes, Git Bash drives /c/..., the \\?\ prefix), `relative_posix` gives it relative to a root with
  / (None outside), the form every .wingbox file, printout and comparison uses;
- writes below a project's .wingbox: `refuse_links`, `make_folder` and `open_unlinked` raise `LinkRefused`
  when `.wingbox` or anything below it on the way to the file is a symbolic link (a repository may commit
  such a link; a write through it would leave the project). Every writer of a file in .wingbox calls one
  of them. The check is an lstat of each part, so it works where O_NOFOLLOW does not exist (Windows);
  where it exists the file is opened with it as well.

Standard library only: the build scripts import it before anything else.
"""
from __future__ import annotations

import errno
import multiprocessing
import ntpath
import functools
import os
import posixpath
import re
import sys
import time
from typing import IO, Any, Optional

WINDOWS = os.name == "nt"
_REPLACE_ATTEMPTS = 20
_REPLACE_PAUSE = 0.05


def lock(handle: IO[Any], blocking: bool = True) -> bool:
    """Take the exclusive lock of an open file; False when another process holds it and blocking is off."""
    if WINDOWS:
        import msvcrt

        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(_REPLACE_PAUSE)
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except BlockingIOError:
        return False


def unlock(handle: IO[Any]) -> None:
    if WINDOWS:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def detached_process_options() -> dict[str, Any]:
    """Popen options for a child that outlives its parent and is not tied to its console or session.

    Windows: a new process group with a console of its own that has no window (CREATE_NO_WINDOW), not
    DETACHED_PROCESS: a detached process has no console at all, so each console program it starts (git, a
    spawn worker) gets a new, visible console window. A console without a window is inherited by those
    children instead. CREATE_NO_WINDOW is ignored together with DETACHED_PROCESS or CREATE_NEW_CONSOLE, so
    neither is used. The child keeps running when the hook that started it ends (Windows does not end a
    process with its parent); its own console also keeps it apart from the hook's console.
    """
    if WINDOWS:
        import subprocess

        return {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            "startupinfo": _hidden_startupinfo(),
            "close_fds": True,
        }
    return {"start_new_session": True, "close_fds": True}


def hidden_process_options() -> dict[str, Any]:
    """Popen options for a short child whose output is captured (git, an installer, a build): on Windows it
    runs without a console window even when this process has no console; nothing elsewhere."""
    if not WINDOWS:
        return {}
    import subprocess

    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": _hidden_startupinfo()}


def _hidden_startupinfo() -> Any:
    import subprocess

    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def replace(source: str, target: str) -> None:
    """os.replace, retried a moment while Windows reports the target in use by another process."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if not WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE)


def read_text(path: str) -> Optional[str]:
    """The text of a file, None when it does not exist; retried a moment while Windows reports the file
    in use by another process (one that is replacing it)."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return None
        except PermissionError:
            if not WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE)
    return None


STATE_FOLDER = ".wingbox"


class LinkRefused(OSError):
    """A write below a project's .wingbox would go through a symbolic link: it is not made."""


def _is_link(path: str) -> bool:
    """A symbolic link, or on Windows a junction (os.path.isjunction exists from Python 3.12)."""
    return os.path.islink(path) or bool(WINDOWS and getattr(os.path, "isjunction", lambda _path: False)(path))


def linked_part(path: str) -> Optional[str]:
    """The first symbolic link on the way from the `.wingbox` folder of path down to path itself (both
    included), None when there is none. Only the last `.wingbox` of the path and what lies below it are looked
    at: a project may itself sit below a linked folder, and a path outside any `.wingbox` is not judged."""
    parts: list[str] = []
    head = os.path.abspath(path)
    while True:
        parent, name = os.path.split(head)
        if not name:
            return None   # no .wingbox folder on the path
        parts.append(head)
        if os.path.normcase(name) == STATE_FOLDER:
            break
        head = parent
    return next((part for part in reversed(parts) if _is_link(part)), None)


def _refusal(linked: str) -> LinkRefused:
    return LinkRefused(
        f"{linked} is a symbolic link: Wingbox writes nothing through a link at {STATE_FOLDER} or below it. "
        "Remove the link and run the command again."
    )


def refuse_links(path: str) -> None:
    """Raise LinkRefused (an OSError) when a write to path would go through a symbolic link at `.wingbox` or
    below it. Call it with the file for a write into the file itself (truncate, append, a lock), and with its
    folder for a write that replaces the file (a temporary file renamed over it replaces a link, it does not
    follow it)."""
    linked = linked_part(path)
    if linked is not None:
        raise _refusal(linked)


def make_folder(path: str) -> None:
    """os.makedirs(path, exist_ok=True) for a project's `.wingbox` or a folder below it; LinkRefused instead
    when it, or `.wingbox` above it, is a symbolic link."""
    refuse_links(path)
    os.makedirs(path, exist_ok=True)


def open_unlinked(path: str, mode: str, **options: Any) -> IO[Any]:
    """open(path, mode, ...) for a file below a project's .wingbox that is written in place: LinkRefused when
    `.wingbox`, a folder below it or the file itself is a symbolic link. Where the system has O_NOFOLLOW the
    file is opened with it too, so a link put in place after the check is not followed either."""
    refuse_links(path)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        return open(path, mode, **options)
    try:
        return open(path, mode, opener=lambda name, flags: os.open(name, flags | no_follow, 0o666), **options)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):   # the file became a link after the check
            raise _refusal(path) from error
        raise


JOBS_VARIABLE = "WINGBOX_JOBS"


def worker_count(tasks: Optional[int] = None) -> int:
    """How many worker processes a build or a refresh may start: WINGBOX_JOBS when it is a whole number of at
    least 1, else every processor of the machine; never more than `tasks` when that is given."""
    available = os.cpu_count() or 1
    try:
        limit = int(os.environ.get(JOBS_VARIABLE, ""))
    except ValueError:
        limit = available
    count = limit if limit >= 1 else available
    return count if tasks is None else max(1, min(count, tasks))


def fsync_directory(directory: str) -> None:
    """Make a rename in directory durable where the system allows opening a folder (not on Windows)."""
    if WINDOWS:
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def start_method(prefer_fork: bool = False) -> str:
    """The start method of worker processes. WINGBOX_START_METHOD overrides it (the tests run the Windows
    path anywhere with spawn). Otherwise: fork where it exists when prefer_fork (the resolution workers
    share the parent's context that way), else the system's default method (spawn on Windows and macOS)."""
    available = multiprocessing.get_all_start_methods()
    wanted = os.environ.get("WINGBOX_START_METHOD", "")
    if wanted in available:
        return wanted
    if prefer_fork and "fork" in available:
        return "fork"
    return multiprocessing.get_start_method(allow_none=True) or available[0]


def ansi_colours(stream: Any) -> bool:
    """Whether ANSI colours can be written to stream: a terminal, NO_COLOR unset, and on Windows a console
    where virtual terminal processing could be turned on."""
    if "NO_COLOR" in os.environ or not getattr(stream, "isatty", lambda: False)():
        return False
    if not WINDOWS:
        return True
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError, ValueError):
        return False


def utf8_streams() -> None:
    """stdout and stderr write UTF-8, and replace what they cannot encode, whatever the console code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass   # a stream that was already written to binary or closed keeps its settings


_GIT_BASH_DRIVE = re.compile(r"^/([A-Za-z])(/|$)")


def local_path(text: str, windows: bool = WINDOWS) -> str:
    """A path from a person or a hook in this system's form. On Windows: the \\\\?\\ prefix goes, a Git Bash
    drive (/c/Users/me) becomes C:\\Users\\me, the drive letter is upper case and every separator a
    backslash. Elsewhere only the redundant parts go (a//b, ./a)."""
    if not windows:
        return posixpath.normpath(text) if text else text
    if text.startswith(("\\\\?\\", "//?/")):
        text = text[4:]
    text = _GIT_BASH_DRIVE.sub(lambda match: match.group(1).upper() + ":/", text)
    text = ntpath.normpath(text)
    if len(text) >= 2 and text[1] == ":":
        text = text[0].upper() + text[1:]
    return text


@functools.lru_cache(maxsize=16)
def _resolved_root(root: str) -> str:
    """The project folder with its links resolved: asked once per file of a graph, worked out once."""
    return os.path.realpath(local_path(root))


def forget_resolved_roots() -> None:
    """Drop the remembered folders (a test that moves a link, a server that runs for days)."""
    _resolved_root.cache_clear()


def relative_posix(path: str, root: str) -> Optional[str]:
    """path relative to root written with /, "" for root itself, None outside root (a parent, a sibling, another
    drive). Links are resolved first; on Windows the comparison ignores case, as the file system does."""
    try:
        relative = os.path.relpath(os.path.realpath(local_path(path)), _resolved_root(root))
    except ValueError:   # another drive on Windows
        return None
    if relative == os.pardir or relative.startswith(os.pardir + os.sep) or os.path.isabs(relative):
        return None
    return "" if relative == os.curdir else relative.replace(os.sep, "/")


class RawKeys:
    """Single keys from the terminal while it is used as a context manager: "up", "down", "space", "enter",
    "escape", a letter. `available` is False where the terminal gives no single keys (then ask for lines)."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdin
        self.available = False
        self._saved: Any = None

    def __enter__(self) -> "RawKeys":
        if WINDOWS:
            try:
                import msvcrt  # noqa: F401

                self.available = self.stream.isatty()
            except ImportError:
                self.available = False
            return self
        try:
            import termios
            import tty

            descriptor = self.stream.fileno()
            self._saved = termios.tcgetattr(descriptor)
            tty.setcbreak(descriptor)
            self.available = True
        except (ImportError, OSError, ValueError, AttributeError):
            self.available = False
        return self

    def __exit__(self, *exc: object) -> None:
        if self._saved is not None:
            import termios

            termios.tcsetattr(self.stream.fileno(), termios.TCSADRAIN, self._saved)

    def read(self) -> str:
        if WINDOWS:
            import msvcrt

            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
            return self._name(char)
        char = os.read(self.stream.fileno(), 1).decode("utf-8", "replace")
        if char == "\x1b":
            rest = os.read(self.stream.fileno(), 2).decode("utf-8", "replace")
            return {"[A": "up", "[B": "down"}.get(rest, "escape")
        return self._name(char)

    @staticmethod
    def _name(char: str) -> str:
        return {" ": "space", "\r": "enter", "\n": "enter", "\x1b": "escape", "k": "up", "j": "down"}.get(char, char.lower())

