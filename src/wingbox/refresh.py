"""`python -m wingbox.refresh ROOT`: bring the published graph of ROOT up to date, once.

Started by freshness.ensure_graph_fresh, detached, with its output in
.wingbox/refresh.log. Under the refresh lock and the builder's lock (one build
of a project at a time, whoever starts it):

1. records the changes made outside Claude Code since the snapshot;
2. reads the marks (their generation g); with none, and a readable state, it ends at once: another refresh
   published them while this one waited for the lock;
3. patches the graph for the marked paths (scripts/graph_update.py), or, when a
   patch does not apply, runs the incremental build;
4. on success clears the marks up to g; on failure keeps them all and records
   the failure, so the same marks are not built again.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time
from typing import Optional

from wingbox import cli, freshness


def fragments_path(root: str) -> str:
    builder = cli.load_script("build_code_graph")
    return os.path.join(builder.wingbox_directory(root), builder.FRAGMENTS_FILE)


def build_arguments(root: str, exclude_dirs: tuple[str, ...]) -> list[str]:
    arguments = ["--root", root]
    for name in exclude_dirs:
        arguments += ["--exclude-dir", name]
    return arguments


def run_build(root: str, exclude_dirs: tuple[str, ...]) -> int:
    """The incremental build, with the fragment store and the snapshot; the caller holds the builder's lock."""
    builder = cli.load_script("build_code_graph")
    parsed = builder.parse_builder_options(build_arguments(root, exclude_dirs))
    if isinstance(parsed, builder.Err):
        print(f"[wingbox] refresh: {parsed.error}", file=sys.stderr)
        return 2
    store, snapshot = builder.freshness_files(parsed.value)
    return int(builder._run_build(
        dataclasses.replace(parsed.value, fragments_path=store, snapshot_path=snapshot, keep_published=True)
    ))


def refresh(root: str) -> int:
    """One refresh of root; the exit status is 0 when the published graph includes every mark it started with."""
    started = time.monotonic()
    freshness.note_outside_changes(root)
    state = freshness.read_state(root)
    if freshness.is_fresh(state):
        # Another refresh published these changes while this one waited for the lock (two hooks at once): the
        # state is read under the lock, so nothing is left to build.
        print("[wingbox] refresh: nothing changed", file=sys.stderr)
        return 0
    generation = state.generation
    changed = sorted(state.changed_files)
    kind = "partial"
    status = 0
    reason = ""
    updated: Optional[object] = None
    if state.readable and os.path.isfile(freshness.snapshot_path(root)) and changed:
        update = cli.load_script("graph_update")
        try:
            updated = update.update_graph(root, changed, fragments_path(root), jobs=cli.load_script("portable").worker_count())
        except Exception as error:  # a failing patch falls back to the build, which reports for itself
            print(f"[wingbox] refresh: partial update failed: {type(error).__name__}: {error}", file=sys.stderr)
            updated = update.UpdateFailed(str(error))
        if not isinstance(updated, update.Updated):
            print(f"[wingbox] refresh: partial update not applied: {updated}", file=sys.stderr)
            updated = None
    if updated is None:
        kind = "build"
        try:
            status = run_build(root, state.exclude_dirs)
        except Exception as error:
            print(f"[wingbox] refresh: build failed: {type(error).__name__}: {error}", file=sys.stderr)
            status = 1
        reason = f"build-exit-{status}"
    seconds = round(time.monotonic() - started, 3)
    if status == 0:
        # Publishing changed the root folder's time: record it now, not in the next search hook.
        freshness.note_outside_changes(root)
        summary = {"kind": kind, "seconds": seconds, "files": len(changed), "generation": generation}
        if updated is not None:
            summary["redone"] = getattr(updated, "redone", 0)
        freshness.update_state(root, lambda current: freshness.after_publish(current, generation, summary, f"refreshes_{kind}"))
        print(f"[wingbox] refresh: {kind} in {seconds}s for {len(changed)} changed files", file=sys.stderr)
        return 0
    freshness.update_state(root, lambda current: freshness.after_failure(current, generation, reason))
    print(f"[wingbox] refresh: failed ({reason}); the changes stay marked", file=sys.stderr)
    return 1


def main(args: list[str]) -> int:
    if len(args) != 1 or not os.path.isdir(args[0]):
        print("usage: python -m wingbox.refresh ROOT", file=sys.stderr)
        return 2
    root = os.path.realpath(args[0])
    builder = cli.load_script("build_code_graph")
    portable = cli.load_script("portable")
    try:
        portable.make_folder(freshness.state_directory(root))
        with portable.open_unlinked(freshness.refresh_lock_path(root), "a+", encoding="utf-8") as refresh_lock, portable.open_unlinked(
            builder._lock_path(root), "a+", encoding="utf-8"
        ) as build_lock:
            portable.lock(refresh_lock)
            portable.lock(build_lock)  # waits for a `wingbox build` in progress
            return refresh(root)
    except portable.LinkRefused as error:
        # Nothing is written through a symbolic link at .wingbox or below it. The failure is recorded, so
        # that the same marks do not start this refresh again and again.
        print(f"[wingbox] refresh: {error}", file=sys.stderr)
        try:
            freshness.update_state(root, lambda current: freshness.after_failure(current, current.generation, "linked-path"))
        except OSError as unrecorded:
            print(f"[wingbox] refresh: the failure is not recorded: {unrecorded}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # The detached process writes to refresh.log, including the project path.
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main(sys.argv[1:]))
