"""What a code graph was built from, written after its header:

  (built-from :tree <digest> [:commit <sha>] [:wingbox <version>])

`:tree` is a digest of the path and the text of every source file the graph
holds, as the parsers read it (decoded, without a byte order mark, every line
ending "\n"): the same files give the same digest on any machine and with any
line endings, and a changed character gives another. `:commit` is the commit checked out when Git reported no
change and no untracked file under the root at build time; with any change it
is left out, because the graph then describes files no commit holds. Git is
not started inside a submodule, so a change there is not looked at.
`:wingbox` is the version that built the graph, when it runs as an installed
package.

Pure functions, and one that asks Git (clean_commit).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from typing import Iterable

try:
    from . import git_safe
except ImportError:  # loaded as a top-level script module
    import git_safe

FACT = "built-from"
_GIT_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Snapshot:
    tree: str          # digest of the source files of the graph (path and text of each)
    commit: str = ""   # the commit, when the working tree had no change at build time
    version: str = ""  # the Wingbox version that built the graph


def tree_digest(files: Iterable[tuple[str, str]]) -> str:
    """Digest of (path relative to the root with "/", hash of the text) of every source file, in path order."""
    digest = hashlib.sha256()
    for path, content_hash in sorted(files):
        digest.update(f"{path}\0{content_hash}\n".encode("utf-8"))
    return digest.hexdigest()[:32]


def snapshot_fact(snapshot: Snapshot) -> str:
    commit = f" :commit {snapshot.commit}" if snapshot.commit else ""
    version = f" :wingbox {snapshot.version}" if snapshot.version else ""
    return f"({FACT} :tree {snapshot.tree}{commit}{version})"


def read_snapshot(tokens: list[str]) -> Snapshot:
    """The snapshot of a tokenised `(built-from ...)` fact; fields the fact does not state are ""."""
    values = {tokens[i][1:]: tokens[i + 1] for i in range(len(tokens) - 1) if tokens[i].startswith(":")}
    return Snapshot(values.get("tree", ""), values.get("commit", ""), values.get("wingbox", ""))


def clean_commit(root: str) -> str:
    """The commit checked out in the repository that holds root, when Git reports no change and no untracked
    file under root; "" with any change, outside a repository, before the first commit or without Git."""
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"   # a status that only reads: no index refresh behind the user's back
    options = dict(capture_output=True, check=False, environment=environment, text=True, encoding="utf-8",
                   errors="replace", timeout=_GIT_TIMEOUT_SECONDS)
    try:
        head = git_safe.run(root, "rev-parse", ("--verify", "--quiet", "HEAD"), **options)
        if head.returncode != 0 or not head.stdout.strip():
            return ""
        status = git_safe.run(root, "status", ("--porcelain", "--", "."), **options)
    except (OSError, subprocess.SubprocessError):   # no Git, no answer in time, or a call git_safe refuses
        return ""
    if status.returncode != 0 or status.stdout.strip():
        return ""
    return head.stdout.strip()
