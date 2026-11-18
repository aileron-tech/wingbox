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
  / (None outside), the form every .wingbox file, printout and comparison uses.

Standard library only: the build scripts import it before anything else.
"""
from __future__ import annotations

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

