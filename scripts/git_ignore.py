from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

try:
    from . import portable
    from .lang.discovery import is_excluded_directory
    from .lang.interface import DiscoveryExclusions
except ImportError:  # loaded as a top-level script module
    import portable
    from lang.discovery import is_excluded_directory
    from lang.interface import DiscoveryExclusions


@dataclass(frozen=True)
class GitIgnoreLoaded:
    """Repository-owned ignore decisions resolved by Git."""

    paths: frozenset[str]


@dataclass(frozen=True)
class GitIgnoreUnavailable:
    """Git could not provide repository ignore decisions."""

    reason: str


GitIgnoreLoadResult = GitIgnoreLoaded | GitIgnoreUnavailable


@dataclass(frozen=True)
class _RepositoryRootFound:
    path: str


@dataclass(frozen=True)
class _RepositoryRootUnavailable:
    reason: str


_RepositoryRootResult = _RepositoryRootFound | _RepositoryRootUnavailable


def _git_environment() -> dict[str, str]:
    """Remove machine-global ignore rules from repository-owned discovery."""
    environment = os.environ.copy()
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def _repository_root(root: str) -> _RepositoryRootResult:
    try:
        result = subprocess.run(
            ("git", "-C", root, "rev-parse", "--show-toplevel"),
            capture_output=True,
            check=False,
            env=_git_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            **portable.hidden_process_options(),
        )
    except OSError as error:
        return _RepositoryRootUnavailable(f"could not run Git: {error}")
    if result.returncode != 0:
        reason = result.stderr.strip() or f"git rev-parse exited {result.returncode}"
        return _RepositoryRootUnavailable(reason)
    repository_root = result.stdout.strip()
    if not repository_root:
        return _RepositoryRootUnavailable(
            "git rev-parse returned an empty repository root"
        )
    return _RepositoryRootFound(os.path.realpath(repository_root))


def _discovery_candidates(
    root: str,
    skipped_directory_names: frozenset[str],
) -> GitIgnoreLoadResult:
    candidates: set[str] = set()
    walk_errors: list[OSError] = []

    def _record_walk_error(error: OSError) -> None:
        walk_errors.append(error)

    for directory_path, directory_names, file_names in os.walk(
        root,
        onerror=_record_walk_error,
    ):
        # The same folder policy as discovery (a first-party `vendor` folder is walked).
        exclusions = DiscoveryExclusions(directory_names=skipped_directory_names, paths=frozenset())
        directory_names[:] = [
            name for name in directory_names if not is_excluded_directory(exclusions, directory_path, name)
        ]
        for directory_name in directory_names:
            candidates.add(os.path.join(directory_path, directory_name) + os.sep)
        for file_name in file_names:
            if file_name != ".git":
                candidates.add(os.path.join(directory_path, file_name))
    if walk_errors:
        return GitIgnoreUnavailable(
            f"could not inspect ignore candidates: {walk_errors[0]}"
        )
    return GitIgnoreLoaded(frozenset(candidates))


def _repository_relative_path(
    repository_root: str,
    candidate: str,
) -> str:
    has_trailing_separator = candidate.endswith(os.sep)
    normalized_candidate = os.path.realpath(candidate.rstrip(os.sep))
    relative_path = os.path.relpath(
        normalized_candidate,
        repository_root,
    ).replace(os.sep, "/")
    if has_trailing_separator and not relative_path.endswith("/"):
        return relative_path + "/"
    return relative_path


def _absolute_ignored_path(repository_root: str, relative_path: str) -> str:
    path_segments = relative_path.rstrip("/").split("/")
    return os.path.normcase(
        os.path.realpath(os.path.join(repository_root, *path_segments))
    )


def load_git_ignored_paths(
    root: str,
    skipped_directory_names: frozenset[str],
) -> GitIgnoreLoadResult:
    """Resolve target paths excluded by repository-owned Git ignore rules; tracked paths are never excluded."""
    repository_result = _repository_root(root)
    if isinstance(repository_result, _RepositoryRootUnavailable):
        return GitIgnoreUnavailable(repository_result.reason)
    repository_root = repository_result.path

    candidates_result = _discovery_candidates(root, skipped_directory_names)
    if isinstance(candidates_result, GitIgnoreUnavailable):
        return candidates_result
    return _ignored_candidates(repository_root, candidates_result.paths)


def git_ignored_among(root: str, candidates: frozenset[str]) -> GitIgnoreLoadResult:
    """The candidates (absolute paths, folders ending with os.sep) that load_git_ignored_paths would exclude."""
    repository_result = _repository_root(root)
    if isinstance(repository_result, _RepositoryRootUnavailable):
        return GitIgnoreUnavailable(repository_result.reason)
    return _ignored_candidates(repository_result.path, candidates)


def _ignored_candidates(repository_root: str, candidates: frozenset[str]) -> GitIgnoreLoadResult:
    if not candidates:
        return GitIgnoreLoaded(frozenset())

    relative_candidates = tuple(
        sorted(
            _repository_relative_path(repository_root, candidate)
            for candidate in candidates
        )
    )
    standard_input = (
        b"\0".join(os.fsencode(candidate) for candidate in relative_candidates) + b"\0"
    )
    try:
        result = subprocess.run(
            (
                "git",
                "-c",
                f"core.excludesFile={os.devnull}",
                "-C",
                repository_root,
                "check-ignore",
                "--no-index",
                "--stdin",
                "-z",
            ),
            capture_output=True,
            check=False,
            env=_git_environment(),
            input=standard_input,
            **portable.hidden_process_options(),
        )
    except OSError as error:
        return GitIgnoreUnavailable(f"could not run git check-ignore: {error}")

    if result.returncode == 1:
        return GitIgnoreLoaded(frozenset())
    if result.returncode != 0:
        reason = os.fsdecode(result.stderr).strip()
        if not reason:
            reason = f"git check-ignore exited {result.returncode}"
        return GitIgnoreUnavailable(reason)

    ignored_paths = frozenset(
        _absolute_ignored_path(repository_root, os.fsdecode(path))
        for path in result.stdout.split(b"\0")
        if path
    )
    tracked_result = _tracked_paths(repository_root)
    if isinstance(tracked_result, GitIgnoreUnavailable):
        return tracked_result
    # Like git: a tracked file is part of the repository whatever the ignore rules say,
    # and so is every folder on the way to it.
    kept = tracked_result.paths | _parent_folders(tracked_result.paths, repository_root)
    return GitIgnoreLoaded(ignored_paths - kept)


def _tracked_paths(repository_root: str) -> GitIgnoreLoadResult:
    """Absolute paths of the files git tracks in the repository."""
    try:
        result = subprocess.run(
            ("git", "-C", repository_root, "ls-files", "-z"),
            capture_output=True,
            check=False,
            env=_git_environment(),
            **portable.hidden_process_options(),
        )
    except OSError as error:
        return GitIgnoreUnavailable(f"could not run git ls-files: {error}")
    if result.returncode != 0:
        reason = os.fsdecode(result.stderr).strip() or f"git ls-files exited {result.returncode}"
        return GitIgnoreUnavailable(reason)
    return GitIgnoreLoaded(
        frozenset(
            _absolute_ignored_path(repository_root, os.fsdecode(path))
            for path in result.stdout.split(b"\0")
            if path
        )
    )


def _parent_folders(paths: frozenset[str], repository_root: str) -> frozenset[str]:
    root = os.path.normcase(os.path.realpath(repository_root))
    folders: set[str] = set()
    for path in paths:
        folder = os.path.dirname(path)
        while folder.startswith(root) and folder != root and folder not in folders:
            folders.add(folder)
            folder = os.path.dirname(folder)
    return frozenset(folders)
