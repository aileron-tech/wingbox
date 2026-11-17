"""Graph freshness: keep the code graph in step with the sources it describes.

Model: dirty marking plus a lazy refresh.

- `wingbox hook dirty` (Claude Code PostToolUse on Edit and Write) records the
  changed path in `.wingbox/graph-state.json` and exits; it never builds.
- `ensure_graph_fresh(root)` runs before the graph is read (the PreToolUse
  search hook, the MCP tools, the query commands). It looks for changes made
  outside Claude Code by comparing the file set with the snapshot the last build
  wrote (`.wingbox/graph-snapshot.json`: size and modification time of every
  analysed source and resolution config, modification time of every folder),
  then, when something changed, starts one refresh (`python -m wingbox.refresh`)
  and waits for it up to a time limit. A refresh that does not finish in time
  keeps running and publishes later; the caller is told the graph is stale.
- Only the changes a published graph includes are cleared; a failed refresh
  keeps them and is not retried until something changes again.

This module is imported by the hooks: it reads and writes small JSON files and
calls stat; it never imports the builder. Writes are atomic (temporary file and
rename) and serialised by a lock file (`portable.lock`, released by the system
when its process dies, so a crashed writer leaves no stale lock).

Settings (environment):
- WINGBOX_AUTO_REFRESH=0 turns the refresh off; a dirty graph is then reported
  as stale instead of being refreshed.
- WINGBOX_HOOK_WAIT (seconds, default 3): how long the search hook waits for a
  refresh before it answers without context.
- WINGBOX_REFRESH_WAIT (seconds, default 600): the same for MCP tools and the
  query commands.
- WINGBOX_CHECK_INTERVAL (seconds, default 0): the least time between two
  checks for changes made outside Claude Code; 0 checks before every use.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Mapping, Optional, Union

if TYPE_CHECKING:
    import subprocess

STATE_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
STATE_DIRECTORY = ".wingbox"
STATE_NAME = "graph-state.json"
SNAPSHOT_NAME = "graph-snapshot.json"
LOCK_NAME = "graph-state.lock"
REFRESH_LOCK_NAME = "refresh.lock"
REFRESH_LOG_NAME = "refresh.log"
CHECKED_NAME = "graph-checked"
EDIT_TOOLS = frozenset(("Edit", "Write", "MultiEdit"))
DEFAULT_HOOK_WAIT = 3.0
DEFAULT_REFRESH_WAIT = 600.0
DEFAULT_CHECK_INTERVAL = 0.0
_POLL_SECONDS = 0.05
_NOT_TRACKED = frozenset((STATE_DIRECTORY, ".git"))


def state_directory(root: str) -> str:
    """ROOT/.wingbox/: everything Wingbox writes into a project, and the mark of its root."""
    return os.path.join(root, STATE_DIRECTORY)


def graph_path(root: str) -> str:
    """The code graph that `wingbox build` publishes for root; the one place that names it."""
    return os.path.join(state_directory(root), "index.sexp")


def report_path(root: str) -> str:
    return os.path.join(state_directory(root), "REPORT.md")


def map_path(root: str) -> str:
    return os.path.join(state_directory(root), "map.html")


NO_PROJECT = "no .wingbox/ in this folder or its parents: run wingbox build"


def searched_graphs(start: str) -> list[str]:
    """The graph paths find_project looks at from start, nearest first."""
    folder = os.path.abspath(start)
    if not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    found = []
    while True:
        found.append(graph_path(folder))
        parent = os.path.dirname(folder)
        if parent == folder:
            return found
        folder = parent


def find_project(start: str) -> Optional[str]:
    """The project root: the nearest folder at or above start that holds .wingbox/index.sexp."""
    for candidate in searched_graphs(start):
        if os.path.isfile(candidate):
            return os.path.dirname(os.path.dirname(candidate))
    return None


def no_project_message(start: str) -> str:
    """What a reader says when find_project(start) finds nothing: the paths it looked at and the options."""
    searched = searched_graphs(start)
    shown = searched[:6] + ([f"... {len(searched) - 6} more"] if len(searched) > 6 else [])
    return (
        f"{NO_PROJECT}\n"
        f"  looked for: {', '.join(shown)}\n"
        "  or point at a graph: --map /path/to/project/.wingbox/index.sexp --root /path/to/project"
    )


def _state_file(root: str, name: str) -> str:
    return os.path.join(state_directory(root), name)


def snapshot_path(root: str) -> str:
    return _state_file(root, SNAPSHOT_NAME)


def refresh_lock_path(root: str) -> str:
    return _state_file(root, REFRESH_LOCK_NAME)


def refresh_log_path(root: str) -> str:
    return _state_file(root, REFRESH_LOG_NAME)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    generation: int
    reason: str


@dataclass(frozen=True)
class State:
    """What the published graph is known to miss.

    generation counts the marks; changed_files maps each path (relative to the
    root, with /) to the generation of its last mark. A refresh that started at
    generation g clears the paths marked at g or before.
    """

    dirty: bool = False
    generation: int = 0
    changed_files: Mapping[str, int] = field(default_factory=dict)
    failed: Optional[Failure] = None
    exclude_dirs: tuple[str, ...] = ()
    counters: Mapping[str, int] = field(default_factory=dict)
    last_refresh: Mapping[str, object] = field(default_factory=dict)
    readable: bool = True


def _int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def decode_state(raw: object) -> State:
    """The state from its JSON value; anything unreadable is a dirty state (freshness unknown)."""
    if not isinstance(raw, dict) or raw.get("schema_version") != STATE_SCHEMA_VERSION:
        return State(dirty=True, readable=False)
    changed_raw = raw.get("changed_files")
    changed = (
        {path: _int(gen) for path, gen in changed_raw.items() if isinstance(path, str)}
        if isinstance(changed_raw, dict)
        else {}
    )
    failed_raw = raw.get("failed")
    failed = (
        Failure(_int(failed_raw.get("generation")), str(failed_raw.get("reason", "")))
        if isinstance(failed_raw, dict)
        else None
    )
    excludes = raw.get("exclude_dirs")
    counters = raw.get("counters")
    last = raw.get("last_refresh")
    return State(
        dirty=bool(raw.get("dirty")),
        generation=_int(raw.get("generation")),
        changed_files=changed,
        failed=failed,
        exclude_dirs=tuple(item for item in excludes if isinstance(item, str)) if isinstance(excludes, list) else (),
        counters={key: _int(value) for key, value in counters.items()} if isinstance(counters, dict) else {},
        last_refresh=dict(last) if isinstance(last, dict) else {},
    )


def encode_state(state: State) -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "dirty": state.dirty,
        "generation": state.generation,
        "changed_files": dict(sorted(state.changed_files.items())),
        "failed": None if state.failed is None else {"generation": state.failed.generation, "reason": state.failed.reason},
        "exclude_dirs": list(state.exclude_dirs),
        "counters": dict(sorted(state.counters.items())),
        "last_refresh": dict(state.last_refresh),
    }


def counted(state: State, name: str, step: int = 1) -> dict[str, int]:
    return {**state.counters, name: state.counters.get(name, 0) + step}


def with_marks(state: State, paths: list[str], counter: str) -> State:
    """The state after marking paths changed: one new generation for the whole batch."""
    if not paths:
        return state
    generation = state.generation + 1
    changed = {**state.changed_files, **{path: generation for path in paths}}
    return State(
        dirty=True,
        generation=generation,
        changed_files=changed,
        failed=state.failed,
        exclude_dirs=state.exclude_dirs,
        counters=counted(state, counter, len(paths)),
        last_refresh=state.last_refresh,
    )


def after_publish(state: State, started_at: int, last_refresh: Mapping[str, object], counter: str) -> State:
    """The state once a graph that includes every mark up to started_at is published."""
    remaining = {path: gen for path, gen in state.changed_files.items() if gen > started_at}
    return State(
        dirty=bool(remaining),
        generation=state.generation,
        changed_files=remaining,
        failed=None,
        exclude_dirs=state.exclude_dirs,
        counters=counted(state, counter),
        last_refresh=dict(last_refresh),
    )


def after_failure(state: State, started_at: int, reason: str) -> State:
    """The state after a failed refresh: every mark stays; no retry until a new mark."""
    return State(
        dirty=True,
        generation=state.generation,
        changed_files=state.changed_files,
        failed=Failure(started_at, reason),
        exclude_dirs=state.exclude_dirs,
        counters=counted(state, "refresh_failures"),
        last_refresh=state.last_refresh,
    )


def after_build(state: State, started_at: int, exclude_dirs: tuple[str, ...]) -> State:
    """The state after `wingbox build` published a graph; its --exclude-dir names are reused by refreshes."""
    published = after_publish(state, started_at, {"kind": "wingbox build"}, "builds")
    return State(
        published.dirty, published.generation, published.changed_files, None, exclude_dirs,
        published.counters, published.last_refresh,
    )


def is_fresh(state: State) -> bool:
    return state.readable and not state.dirty and not state.changed_files


def failed_for_now(state: State) -> bool:
    """A refresh already failed with exactly these marks: building again would fail again."""
    return state.failed is not None and state.failed.generation >= state.generation


# ---------------------------------------------------------------------------
# Files (adapters)
# ---------------------------------------------------------------------------


def _read_json(path: str) -> object:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        print(f"[wingbox] cannot read {path}: {error}", file=sys.stderr)
        return {"unreadable": True}


def write_json_atomic(path: str, value: object) -> None:
    """Write through a temporary file in the same folder and rename: readers see the old or the new file."""
    import tempfile  # here: the fast path of the hooks never writes

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, separators=(",", ":"))
        portable().replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def read_state(root: str) -> State:
    raw = _read_json(_state_file(root, STATE_NAME))
    if raw is None:
        # No state yet: fresh only if the last build left a snapshot to compare with.
        return State() if os.path.isfile(snapshot_path(root)) else State(dirty=True, readable=False)
    return decode_state(raw)


def portable():
    """scripts/portable.py: the platform differences (locks, detached processes, atomic replace, paths)."""
    from wingbox.cli import load_script

    return load_script("portable")


def write_state(root: str, state: State) -> None:
    write_json_atomic(_state_file(root, STATE_NAME), encode_state(state))


@contextmanager
def state_lock(root: str) -> Iterator[None]:
    """Serialise read-modify-write of the state between processes."""
    os.makedirs(state_directory(root), exist_ok=True)
    with open(_state_file(root, LOCK_NAME), "a+", encoding="utf-8") as handle:
        helpers = portable()
        helpers.lock(handle)
        try:
            yield
        finally:
            helpers.unlock(handle)


def update_state(root: str, change) -> State:
    """Apply change (State -> State) to the current state under the lock and write the result."""
    with state_lock(root):
        current = read_state(root)
        if not current.readable:
            current = State(dirty=True, exclude_dirs=current.exclude_dirs)
        new = change(current)
        if new != current or not os.path.isfile(_state_file(root, STATE_NAME)):
            write_state(root, new)
        return new


def mark_changed(root: str, paths: list[str], counter: str = "marks") -> State:
    return update_state(root, lambda state: with_marks(state, paths, counter))


def record_build(root: str, started_at: int, exclude_dirs: tuple[str, ...]) -> State:
    return update_state(root, lambda state: after_build(state, started_at, exclude_dirs))


# ---------------------------------------------------------------------------
# Snapshot of the file set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """The analysed sources and resolution configs (size, mtime) and the folders (mtime) of the last build."""

    files: Mapping[str, tuple[int, int]]
    directories: Mapping[str, int]
    extensions: frozenset[str]
    config_names: frozenset[str]
    config_patterns: tuple[tuple[str, str], ...]  # (prefix, suffix) of names such as tsconfig.*.json
    skip_dirs: frozenset[str]
    root: str = ""  # the folder the graph was built from
    data_folders: frozenset[str] = frozenset()  # folders of module loaders: their JSON, YAML, TOML files count


def decode_snapshot(raw: object) -> Optional[Snapshot]:
    if not isinstance(raw, dict) or raw.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return None
    files = raw.get("files")
    directories = raw.get("directories")
    if not isinstance(files, dict) or not isinstance(directories, dict):
        return None
    try:
        return Snapshot(
            files={path: (int(value[0]), int(value[1])) for path, value in files.items()},
            directories={path: int(value) for path, value in directories.items()},
            extensions=frozenset(raw.get("extensions", ())),
            config_names=frozenset(raw.get("config_names", ())),
            config_patterns=tuple((str(pair[0]), str(pair[1])) for pair in raw.get("config_patterns", ())),
            skip_dirs=frozenset(raw.get("skip_dirs", ())),
            root=str(raw.get("root", "")),
            data_folders=frozenset(str(folder) for folder in raw.get("data_folders", ())),
        )
    except (TypeError, ValueError, IndexError):
        return None


def encode_snapshot(snapshot: Snapshot) -> dict:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "extensions": sorted(snapshot.extensions),
        "config_names": sorted(snapshot.config_names),
        "config_patterns": [list(pair) for pair in snapshot.config_patterns],
        "skip_dirs": sorted(snapshot.skip_dirs),
        "root": snapshot.root,
        "files": {path: list(value) for path, value in sorted(snapshot.files.items())},
        "directories": dict(sorted(snapshot.directories.items())),
        "data_folders": sorted(snapshot.data_folders),
    }


def read_snapshot(root: str) -> Optional[Snapshot]:
    return decode_snapshot(_read_json(snapshot_path(root)))


def write_snapshot(root: str, snapshot: Snapshot) -> None:
    write_json_atomic(snapshot_path(root), encode_snapshot(snapshot))


def is_config_name(snapshot: Snapshot, name: str) -> bool:
    return name in snapshot.config_names or any(
        name.startswith(prefix) and name.endswith(suffix) for prefix, suffix in snapshot.config_patterns
    )


DATA_SUFFIXES = (".json", ".yaml", ".yml", ".toml")


def is_tracked_path(snapshot: Snapshot, relative: str) -> bool:
    """A file the graph depends on: a tracked name, a file of the last build, or a configuration
    file beside a module loader (the names its registry loads)."""
    folder, _, name = relative.rpartition("/")
    return (
        relative in snapshot.files
        or is_tracked_name(snapshot, name)
        or (folder in snapshot.data_folders and name.endswith(DATA_SUFFIXES))
    )


def is_tracked_name(snapshot: Snapshot, name: str) -> bool:
    """A file name the graph depends on: a source extension or a resolution config."""
    return os.path.splitext(name)[1] in snapshot.extensions or is_config_name(snapshot, name)


def _signature(path: str) -> Optional[tuple[int, int]]:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def _directory_time(path: str) -> Optional[int]:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _join(root: str, relative: str) -> str:
    if not relative:
        return root
    return root + "/" + relative if os.sep == "/" else os.path.join(root, *relative.split("/"))


def _child(relative: str, name: str) -> str:
    return name if not relative else relative + "/" + name


@dataclass(frozen=True)
class OutsideChanges:
    paths: tuple[str, ...]  # changed, new and deleted tracked files, relative
    files: Mapping[str, Optional[tuple[int, int]]]  # their signatures now (None: deleted)
    directories: Mapping[str, Optional[int]]  # changed folders and their times now (None: deleted)


def _new_entries(
    root: str, relative: str, snapshot: Snapshot, files: dict, directories: dict
) -> None:
    """Tracked files and folders under a folder whose time changed, that the snapshot does not hold."""
    try:
        entries = list(os.scandir(_join(root, relative)))
    except OSError:
        return
    for entry in entries:
        child = _child(relative, entry.name)
        try:
            if entry.is_dir(follow_symlinks=False):
                if entry.name in snapshot.skip_dirs or entry.name in _NOT_TRACKED or child in snapshot.directories:
                    continue
                if child not in directories:
                    directories[child] = _directory_time(entry.path)
                    _new_entries(root, child, snapshot, files, directories)
            elif entry.is_file(follow_symlinks=False) and child not in snapshot.files and is_tracked_path(snapshot, child):
                files[child] = _signature(entry.path)
        except OSError:
            continue


def outside_changes(root: str, snapshot: Snapshot) -> OutsideChanges:
    """Tracked files whose size or time differ from the snapshot, new ones in changed folders, deleted ones.

    One stat per tracked file and folder; a folder is listed only when its time
    changed (an entry was added, removed or renamed in it).
    """
    files: dict[str, Optional[tuple[int, int]]] = {}
    directories: dict[str, Optional[int]] = {}
    for relative, signature in snapshot.files.items():
        now = _signature(_join(root, relative))
        if now != signature:
            files[relative] = now
    for relative, seen in snapshot.directories.items():
        now = _directory_time(_join(root, relative))
        if now != seen:
            directories[relative] = now
            if now is not None:
                _new_entries(root, relative, snapshot, files, directories)
    return OutsideChanges(tuple(sorted(files)), files, directories)


def with_outside_changes(snapshot: Snapshot, changes: OutsideChanges) -> Snapshot:
    """The snapshot that has seen these changes, so that they are reported once."""
    files = dict(snapshot.files)
    for path, signature in changes.files.items():
        if signature is None:
            files.pop(path, None)
        else:
            files[path] = signature
    directories = dict(snapshot.directories)
    for path, seen in changes.directories.items():
        if seen is None:
            directories.pop(path, None)
        else:
            directories[path] = seen
    return Snapshot(
        files, directories, snapshot.extensions, snapshot.config_names, snapshot.config_patterns, snapshot.skip_dirs,
        snapshot.root, snapshot.data_folders,
    )


# ---------------------------------------------------------------------------
# The PostToolUse hook
# ---------------------------------------------------------------------------


def relative_path(root: str, path: str) -> Optional[str]:
    """path relative to root with /, or None outside root."""
    relative = portable().relative_posix(path, root)
    return relative or None


def _edited_path(payload: object) -> Optional[str]:
    if not isinstance(payload, dict) or payload.get("tool_name") not in EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    base = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    local_path = portable().local_path
    return os.path.join(local_path(base), local_path(file_path))


def _reported_failure(payload: dict) -> bool:
    response = payload.get("tool_response")
    return isinstance(response, dict) and (response.get("success") is False or bool(response.get("error")))


def _changed_on_disk(root: str, relative: str, snapshot: Optional[Snapshot]) -> bool:
    if snapshot is None:
        return True
    return _signature(_join(root, relative)) != snapshot.files.get(relative)


def run_post_edit(stdin_text: str) -> None:
    """Mark the file of a successful Edit or Write changed, when it belongs to a project with a graph."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else None
    except json.JSONDecodeError:
        return
    path = _edited_path(payload)
    if path is None:
        return
    root = find_project(os.path.dirname(path))
    if root is None:
        return
    relative = relative_path(root, path)
    if relative is None or relative.split("/", 1)[0] in _NOT_TRACKED:
        return
    snapshot = read_snapshot(root)
    if snapshot is not None and not is_tracked_path(snapshot, relative):
        return
    assert isinstance(payload, dict)
    if _reported_failure(payload) and not _changed_on_disk(root, relative, snapshot):
        return
    mark_changed(root, [relative])


# ---------------------------------------------------------------------------
# ensure_graph_fresh
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fresh:
    refreshed: bool


@dataclass(frozen=True)
class Stale:
    reason: str  # refresh-timeout, refresh-failed, auto-refresh-off
    changed: int


@dataclass(frozen=True)
class Unmanaged:
    """No graph published at graph_path(root): the caller keeps its own policy (no first build here)."""


@dataclass(frozen=True)
class Unconfirmed:
    """The graph is read as it is and never refreshed: it was not built from this folder (moved or
    copied .wingbox/, or built without a snapshot), so a refresh could only replace it by a wrong one."""

    reason: str  # moved, no-snapshot


Freshness = Union[Fresh, Stale, Unmanaged, Unconfirmed]


def built_here(root: str, snapshot: Optional[Snapshot]) -> Optional[Unconfirmed]:
    """None when the snapshot says the graph was built from root; else why it cannot be refreshed."""
    if snapshot is None:
        return Unconfirmed("no-snapshot")
    if os.path.realpath(snapshot.root) != os.path.realpath(root):
        return Unconfirmed("moved")
    return None


def _seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value >= 0 else default


def hook_wait() -> float:
    return _seconds("WINGBOX_HOOK_WAIT", DEFAULT_HOOK_WAIT)


def refresh_wait() -> float:
    return _seconds("WINGBOX_REFRESH_WAIT", DEFAULT_REFRESH_WAIT)


def auto_refresh_enabled() -> bool:
    return os.environ.get("WINGBOX_AUTO_REFRESH", "1").strip().lower() not in ("0", "false", "no", "off")


def _check_due(root: str) -> bool:
    """Whether the outside-change check runs now (WINGBOX_CHECK_INTERVAL since the last one)."""
    interval = _seconds("WINGBOX_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL)
    if interval <= 0:
        return True
    marker = _state_file(root, CHECKED_NAME)
    try:
        if time.time() - os.stat(marker).st_mtime < interval:
            return False
    except OSError:
        pass
    try:
        with open(marker, "a", encoding="utf-8", newline="\n"):
            pass
        os.utime(marker)
    except OSError as error:
        print(f"[wingbox] cannot write {marker}: {error}", file=sys.stderr)
    return True


def note_outside_changes(root: str) -> Optional[State]:
    """Mark changes made outside Claude Code; None when there is no snapshot to compare with."""
    snapshot = read_snapshot(root)
    if snapshot is None:
        return None
    changes = outside_changes(root, snapshot)
    if not changes.paths and not changes.directories:
        return read_state(root)
    with state_lock(root):
        current = read_snapshot(root) or snapshot
        recheck = outside_changes(root, current)  # another process may have recorded them meanwhile
        write_snapshot(root, with_outside_changes(current, recheck))
    return mark_changed(root, list(recheck.paths), "outside_marks")


def refresh_running(root: str) -> bool:
    try:
        handle = open(refresh_lock_path(root), "a+", encoding="utf-8")
    except OSError:
        return False
    with handle:
        helpers = portable()
        if not helpers.lock(handle, blocking=False):
            return True
        helpers.unlock(handle)
    return False


def _package_parent() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_refresh(root: str) -> "subprocess.Popen":
    """Start `python -m wingbox.refresh root` detached: its own session, output to .wingbox/refresh.log.

    The hook's stdout and stderr are not inherited, so Claude Code does not wait
    for the refresh when the hook returns before it ends.
    """
    import subprocess  # here: the fast path of the hooks starts nothing
    os.makedirs(state_directory(root), exist_ok=True)
    environment = dict(os.environ)
    parent = _package_parent()
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (parent, environment.get("PYTHONPATH", "")) if item
    )
    with open(refresh_log_path(root), "ab") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "wingbox.refresh", root],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            cwd=root,
            env=environment,
            **portable().detached_process_options(),
        )


def _wait(root: str, process: Optional["subprocess.Popen"], deadline: float) -> bool:
    """Wait for the refresh to end; False at the deadline (it keeps running)."""
    while time.monotonic() < deadline:
        if process is not None:
            if process.poll() is not None:
                return True
        elif not refresh_running(root):
            return True
        time.sleep(_POLL_SECONDS)
    return False


def _stale(root: str, reason: str, state: State) -> Stale:
    try:
        update_state(root, lambda current: _with_counters(current, counted(current, "stale_" + reason.replace("-", "_"))))
    except OSError as error:
        print(f"[wingbox] cannot write the graph state: {error}", file=sys.stderr)
    return Stale(reason, len(state.changed_files))


def _with_counters(state: State, counters: dict[str, int]) -> State:
    return State(
        state.dirty, state.generation, state.changed_files, state.failed, state.exclude_dirs, counters,
        state.last_refresh, state.readable,
    )


def ensure_graph_fresh(root: str, wait: Optional[float] = None) -> Freshness:
    """Make the published graph of root include every known change, or say why it may not.

    Fast path (nothing changed): one small JSON read, the snapshot read and one
    stat per tracked file and folder. Otherwise one refresh runs (never two for
    one project) and the call waits for it up to `wait` seconds.
    """
    if not os.path.isfile(graph_path(root)):
        return Unmanaged()
    elsewhere = built_here(root, read_snapshot(root))
    if elsewhere is not None:
        return elsewhere
    state = (note_outside_changes(root) if _check_due(root) else None) or read_state(root)
    if is_fresh(state):
        return Fresh(False)
    if not auto_refresh_enabled():
        return _stale(root, "auto-refresh-off", state)
    if failed_for_now(state) and not refresh_running(root):
        return _stale(root, "refresh-failed", state)
    limit = refresh_wait() if wait is None else wait
    deadline = time.monotonic() + limit
    process: Optional["subprocess.Popen"] = None
    if not refresh_running(root):
        process = start_refresh(root)
    if not _wait(root, process, deadline):
        return _stale(root, "refresh-timeout", state)
    after = read_state(root)
    if is_fresh(after):
        return Fresh(True)
    if failed_for_now(after):
        return _stale(root, "refresh-failed", after)
    if time.monotonic() < deadline:
        # Marks made while that refresh ran: one more refresh, in the time left.
        return ensure_graph_fresh(root, deadline - time.monotonic())
    return _stale(root, "refresh-timeout", after)


def refresh_in_background(root: str) -> bool:
    """Record the changes made since the snapshot and start a refresh when the graph misses some; return at once.

    For the SessionStart hook and the hook after a git command the agent ran: nothing waits for the refresh,
    nothing is printed. True when a refresh was started."""
    if not os.path.isfile(graph_path(root)) or built_here(root, read_snapshot(root)) is not None:
        return False
    state = note_outside_changes(root) or read_state(root)
    if is_fresh(state) or not auto_refresh_enabled() or failed_for_now(state) or refresh_running(root):
        return False
    start_refresh(root)
    return True


def stale_line(result: Freshness, root: str = ".") -> str:
    """The S-expression that tells an agent the graph may miss recent changes; empty when fresh."""
    if isinstance(result, Unconfirmed):
        hint = f"rebuild: wingbox build {root}"
        return f'(wingbox-graph-unconfirmed :reason {result.reason} :hint "{hint}")'
    if not isinstance(result, Stale):
        return ""
    return f"(wingbox-graph-stale :reason {result.reason} :changed-files {result.changed})"


def withholds_context(result: Freshness) -> bool:
    """A graph known to miss changes gives no context; an unconfirmed one is read as it is, with its note."""
    return isinstance(result, Stale)
