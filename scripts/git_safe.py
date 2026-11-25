"""Every Git call of Wingbox on a repository it reads goes through `run`.

A repository can arrive with its `.git` folder from somebody else (an archive, a shared volume). Its
`.git/config`, its hooks and its `.gitattributes` can name programs, and a plain `git status` or `git diff`
starts them. Wingbox reads a repository and never runs it, so every call switches off:

- `core.fsmonitor`: a program that `status`, `diff` and `ls-files` ask for the changed files (set to the
  empty value, which switches it off in every Git; Git before 2.36 reads any other value as a program);
- hooks (`.git/hooks`, `core.hooksPath`): `status` and `diff` may write the index and run
  `post-index-change`;
- on `diff`, an external diff program and the text conversion programs (`diff.external`,
  `diff.<driver>.command`, `diff.<driver>.textconv`);
- on `status` and `diff`, Git started inside a submodule under the configuration of that submodule: a
  change inside a submodule is therefore not seen;
- on `status` and `diff`, the filter drivers that the repository's own configuration defines
  (`filter.<name>.clean`, `.smudge`, `.process`), which a `.gitattributes` line makes Git run on a file it
  reads. Each gets an empty program and `required = false` for the call, so such a file is compared as it
  is on disk. The drivers of the user's own (global and system) configuration keep running. A driver whose
  name cannot be written on Git's command line stops the call: GitRefused;
- the program that checks the signature of every commit `log` shows (`log.showSignature` with
  `gpg.program`);
- the fetch of missing objects from a promisor remote, whose transport the configuration can set to a
  program: no lazy fetch (Git 2.45 and later) and no transport allowed (every Git).

The configuration of the user is read as the caller's environment gives it. `ref_problem` says why a word
cannot be given to Git as a ref. Standard library and `portable` only.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Mapping, Optional, Sequence

try:
    from . import portable
except ImportError:  # loaded as a top-level script module
    import portable

_GIT = ("git", "-c", "core.fsmonitor=", "-c", f"core.hooksPath={os.devnull}", "-c", "log.showSignature=false")
_SWITCHES = {
    "status": ("--ignore-submodules=all",),
    "diff": ("--no-ext-diff", "--no-textconv", "--ignore-submodules=all"),
}
# The subcommands that read files of the work tree through the filter drivers.
_READS_WORK_TREE = frozenset({"status", "diff"})
_FILTER_PROGRAMS = ("clean", "smudge", "process")
# GIT_ALLOW_PROTOCOL lists the transports Git may use and overrides every `protocol.*` setting: this
# name is no transport, so none is allowed.
_NO_TRANSPORT = "none"


class GitRefused(OSError):
    """Git was not started: the call could not be made harmless."""


def ref_problem(ref: str) -> Optional[str]:
    """Why ref cannot be given to Git as a ref, or None. Git reads a word that starts with "-" as an
    option: `--output=<file>` makes `git diff` write that file."""
    if ref.startswith("-"):
        return f"not a git ref: {ref} starts with '-', which Git reads as an option"
    return None


def run(
    root: Optional[str], subcommand: str, arguments: Sequence[str] = (), *, config: Sequence[str] = (),
    environment: Optional[Mapping[str, str]] = None, **options: Any,
) -> subprocess.CompletedProcess:
    """Run `git <subcommand> <arguments>` in root (the current folder when None) with the programs of the
    repository's configuration switched off (see the module text). config holds further `key=value`
    settings of the call, environment the environment to start from (the caller's own when None); options
    go to subprocess.run. Raises what subprocess.run raises, and GitRefused."""
    started_in = _environment(environment)
    command = [*_GIT]
    if subcommand in _READS_WORK_TREE:
        command += _filter_overrides(root, started_in, options.get("timeout"))
    for setting in config:
        command += ("-c", setting)
    command += [*_folder(root), subcommand, *_SWITCHES.get(subcommand, ()), *arguments]
    return subprocess.run(command, env=started_in, **{**portable.hidden_process_options(), **options})


def _environment(environment: Optional[Mapping[str, str]]) -> dict[str, str]:
    started_in = dict(os.environ if environment is None else environment)
    started_in["GIT_NO_LAZY_FETCH"] = "1"
    started_in["GIT_ALLOW_PROTOCOL"] = _NO_TRANSPORT
    return started_in


def _folder(root: Optional[str]) -> tuple[str, ...]:
    return () if root is None else ("-C", root)


def _filter_overrides(root: Optional[str], environment: Mapping[str, str], timeout: Optional[float]) -> list[str]:
    """The settings that give every filter driver of the repository's own configuration an empty program."""
    own = dict(environment, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    listed = subprocess.run(
        (*_GIT, *_folder(root), "config", "--name-only", "-z", "--get-regexp", r"^filter\."),
        capture_output=True, check=False, env=own, timeout=timeout, **portable.hidden_process_options(),
    )
    if listed.returncode == 1:   # no such setting
        return []
    if listed.returncode != 0:
        reason = os.fsdecode(listed.stderr).strip() or f"git config exited {listed.returncode}"
        raise GitRefused(f"cannot read the filter drivers of the repository's configuration: {reason}")
    overrides: list[str] = []
    for name in sorted(_driver_names(os.fsdecode(listed.stdout).split("\0"))):
        if "=" in name or "\n" in name:
            raise GitRefused(f"the repository's configuration defines the filter driver {name!r}, which cannot be "
                             "switched off for one call: Git is not started on its files")
        for program in _FILTER_PROGRAMS:
            overrides += ("-c", f"filter.{name}.{program}=")
        overrides += ("-c", f"filter.{name}.required=false")
    return overrides


def _driver_names(keys: Sequence[str]) -> set[str]:
    """The <name> of every `filter.<name>.<variable>` key."""
    names = {key[len("filter."):].rpartition(".")[0] for key in keys if key.startswith("filter.")}
    names.discard("")   # `filter.<variable>`: a setting of no driver
    return names
