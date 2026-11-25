"""`wingbox install` and `wingbox uninstall`: put Wingbox in front of your coding assistants.

    wingbox install   [--assistant NAME ...] [--without ITEM,...] [--global-gitignore] [--yes] [--dry-run] [--sexp]
    wingbox uninstall [--global|--project] [--assistant NAME ...] [--without ITEM,...] [--sexp]

Install writes your assistant settings. Select Global Git ignore or pass --global-gitignore to
add .wingbox/ to Git's global excludes file. To set up a repository run `wingbox init` (it writes .wingbox/ and updates
the project's .gitignore). Install shows a list of items with a checkbox each, grouped by assistant:

- skill: the skill (SKILL.md and references/, stamped with the version) in the assistant's skill folder,
  so `/wingbox` exists there;
- block: an always-on block between `<!-- wingbox:begin -->` and `<!-- wingbox:end -->` in the
  instructions file the assistant reads in every session;
- Claude Code hooks in ~/.claude/settings.json: search-hook (PreToolUse on Grep, Glob and Read: context
  from the graph), edit-hook (PostToolUse on Edit and Write: marks the edited file), session-hook
  (SessionStart: refreshes a stale graph in the background), git-hook (PostToolUse on Bash: refreshes the
  graph after the agent ran git switch, checkout, pull, merge, rebase, reset or stash pop).

In a terminal the Global Git ignore checkbox starts unchecked unless --global-gitignore is passed.
The person toggles items and confirms; an agent, a pipe or `--yes` installs assistant settings
except the items named by `--without` (an item, or ASSISTANT:ITEM for one assistant; `--without none`
installs everything again). The choice is recorded in ~/.wingbox/installed.sexp, so `wingbox update`
refreshes only what was installed and a later install keeps it. `wingbox uninstall` removes the
stamped skill folders, the marked blocks and the hook entries, except the items named by
`--without`; `--project` removes what versions 0.2.6 to 0.8.x wrote into a repository.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Union

from wingbox.facts import Fact, fact_sexp, make_fact, parse_fact

ASSISTANTS = ("claude-code", "codex", "gemini", "cursor", "copilot", "agents")
SKILL_NAME = "wingbox"
STAMP_FILE = ".wingbox-version"
BEGIN_MARKER = "<!-- wingbox:begin -->"
END_MARKER = "<!-- wingbox:end -->"
SESSION_SUBCOMMAND = "hook session-start"
SESSION_TIMEOUT_SECONDS = 5
GIT_SUBCOMMAND = "hook git"
GIT_MATCHER = "Bash"
GIT_TIMEOUT_SECONDS = 5
HOOK_SUBCOMMAND = "hook pre-search"
HOOK_MATCHER = "Grep|Glob|Read"
# The search hook may wait for a refresh of the graph (WINGBOX_HOOK_WAIT, 3 s) before it reads it.
HOOK_TIMEOUT_SECONDS = 10
DIRTY_SUBCOMMAND = "hook dirty"
DIRTY_MATCHER = "Edit|Write|MultiEdit"
DIRTY_TIMEOUT_SECONDS = 5
_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart")
ITEMS = ("skill", "block", "search-hook", "edit-hook", "session-hook", "git-hook")
HOOK_ITEMS = ("search-hook", "edit-hook", "session-hook", "git-hook")
PROJECT_INSTALL_REMOVED = (
    "`wingbox install --project` was removed in 0.9.0. To set up this repository run "
    "`wingbox init` (writes .wingbox/ and updates .gitignore); to set up your "
    "assistants run `wingbox install` (writes your own settings). `wingbox uninstall --project` removes "
    "what earlier versions wrote into a repository."
)
BACKUP_SUFFIX = ".wingbox-bak"
RECORD_RELATIVE = os.path.join(".wingbox", "installed.sexp")


def _join(base: str, *relatives: str) -> str:
    """base joined with paths written with / (".claude/skills", "references/x.md"), in the system's form."""
    return os.path.join(base, *(part for relative in relatives for part in relative.split("/")))
# The program of a hook and its leading arguments, before `hook pre-search` / `hook dirty`:
# ("/abs/path/wingbox",) or (python, "-P", "-m", "wingbox.cli") for a global install, ("wingbox",) for a project.
HookProgram = tuple[str, ...]
PROJECT_HOOK_PROGRAM: HookProgram = ("wingbox",)

ALWAYS_ON_BLOCK = f"""{BEGIN_MARKER}
## Wingbox code graph

When the project has a Wingbox code graph (`.wingbox/index.sexp` in the project folder or a parent):

- Prefer Wingbox to `grep`, `rg`, `find` and opening files for research, search and reading code: ask the graph first (`wingbox quick "<keyword>"`, `wingbox analyze <module>`, `wingbox usages <symbol>`), then read only the files and lines it names. A text search is the fallback when the keyword is not a symbol (a string key, a message text, an error code) or the graph is older than the files in question.
- Before changing a file, run `wingbox impact <file>` to see what depends on it.
- Tests follow the impact: the dependents that `wingbox impact` names decide which tests to write and run, not the edited file alone. To validate tests, show for every changed function (`wingbox diff_functions <ref>`) a test that reaches it (`wingbox usages <function>` lists a test file). This proves that a test reaches the code, not that its assertions are strong: still read them.
- From code (Code Mode, a script), `wingbox impact <file> [<file> ...] 2 --json` answers for all the files in one call: each of `impact[i].rows` names a dependent in `file`, listed once, the list not cut, test files included. Each row carries the reason it is listed. Make the call in one block and let its value be the map from each file to its rows' files. More: the skill's `references/code-mode.md`.
- For an overview, use the wingbox skill (`/wingbox`): it builds the graph when it is missing or stale.
{END_MARKER}
"""

# Configuration folder (under the home folder) whose presence means the assistant is installed.
_CONFIG_FOLDERS = {
    "claude-code": ".claude",
    "codex": ".codex",
    "gemini": ".gemini",
    "cursor": ".cursor",
    "copilot": ".copilot",
    "agents": ".agents",
}

# Skill folders as each assistant documents them, relative to the home folder or the repository.
_GLOBAL_SKILLS = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "gemini": ".gemini/skills",
    "cursor": ".cursor/skills",
    "copilot": ".copilot/skills",
    "agents": ".agents/skills",
}
_PROJECT_SKILLS = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
    "gemini": ".gemini/skills",
    "cursor": ".cursor/skills",
    "copilot": ".github/skills",
    "agents": ".agents/skills",
}

# Instructions file read in every session; None where the assistant documents no such file.
_GLOBAL_BLOCKS: dict[str, Optional[str]] = {
    "claude-code": ".claude/CLAUDE.md",
    "codex": ".codex/AGENTS.md",
    "gemini": ".gemini/GEMINI.md",
    "cursor": None,
    "copilot": ".copilot/copilot-instructions.md",
    "agents": None,
}
_PROJECT_BLOCKS: dict[str, Optional[str]] = {
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "gemini": "GEMINI.md",
    "cursor": "AGENTS.md",
    "copilot": "AGENTS.md",
    "agents": "AGENTS.md",
}

ScopeKind = Literal["global", "project"]
What = Literal["skill", "block", "hook"]


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    root: str  # the home folder for global, the repository for project


@dataclass(frozen=True)
class Target:
    what: What
    path: str
    assistants: tuple[str, ...]


@dataclass(frozen=True)
class Options:
    command: Literal["install", "uninstall"]
    scope: ScopeKind
    assistants: tuple[str, ...]  # empty: detected (install) or all (uninstall)
    dry_run: bool
    sexp: bool
    without: Optional[tuple[str, ...]] = None  # items left out (ITEM or ASSISTANT:ITEM); None: the recorded choice
    yes: bool = False  # ask nothing, even in a terminal
    global_gitignore: bool = False


@dataclass(frozen=True)
class UsageError:
    message: str


@dataclass(frozen=True)
class Machine:
    """What the composition root reads once: folders, version, packaged skill, hook command."""

    home: str
    cwd: str
    version: str
    skill_files: Mapping[str, bytes]
    hook_program: HookProgram


@dataclass(frozen=True)
class Installed:
    scope: ScopeKind
    root: str
    assistants: tuple[str, ...]
    folders: tuple[str, ...] = ()  # folders install created, removed by uninstall when empty
    without: tuple[str, ...] = ()  # items the person left out (ITEM or ASSISTANT:ITEM)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def parse_arguments(command: Literal["install", "uninstall"], args: list[str]) -> Union[Options, UsageError]:
    scope: ScopeKind = "global"
    assistants: list[str] = []
    dry_run = False
    sexp = False
    yes = False
    global_gitignore = False
    without: Optional[list[str]] = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--global":
            scope = "global"
        elif argument == "--project":
            if command == "install":
                return UsageError(PROJECT_INSTALL_REMOVED)
            scope = "project"
        elif argument == "--dry-run" and command == "install":
            dry_run = True
        elif argument == "--yes" and command == "install":
            yes = True
        elif argument == "--global-gitignore" and command == "install":
            global_gitignore = True
        elif argument == "--sexp":
            sexp = True
        elif argument == "--without" and index + 1 < len(args):
            without = [] if without is None else without
            for item in (part.strip() for part in args[index + 1].split(",")):
                if item in ("", "none"):
                    continue
                problem = _item_problem(item)
                if problem:
                    return UsageError(problem)
                if item not in without:
                    without.append(item)
            index += 1
        elif argument == "--assistant" and index + 1 < len(args):
            name = args[index + 1]
            if name not in ASSISTANTS:
                return UsageError(f"unknown assistant {name}; known: {', '.join(ASSISTANTS)}")
            if name not in assistants:
                assistants.append(name)
            index += 1
        else:
            return UsageError(f"unknown argument {argument}")
        index += 1
    return Options(command, scope, tuple(assistants), dry_run, sexp, None if without is None else tuple(without),
                   yes, global_gitignore)


def _item_problem(item: str) -> Optional[str]:
    """Why an item of --without is not one, or None."""
    assistant, _, key = item.rpartition(":")
    if assistant and assistant not in ASSISTANTS:
        return f"unknown assistant {assistant} in --without {item}; known: {', '.join(ASSISTANTS)}"
    if key not in ITEMS:
        return f"unknown item {key} in --without; items: {', '.join(ITEMS)}"
    return None


def is_selected(assistant: str, key: str, without: tuple[str, ...]) -> bool:
    """Whether the item key of assistant is installed: neither KEY nor ASSISTANT:KEY is left out."""
    return key not in without and f"{assistant}:{key}" not in without


@dataclass(frozen=True)
class Item:
    """One line of the install list: an item of an assistant, the file it goes to, ticked or not."""

    assistant: str
    key: str
    path: str
    ticked: bool


def install_items(home: str, assistants: tuple[str, ...], without: tuple[str, ...]) -> list[Item]:
    """What install puts in place, per assistant, in the order of the list."""
    items: list[Item] = []
    for name in assistants:
        items.append(Item(name, "skill", _join(home, _GLOBAL_SKILLS[name], SKILL_NAME), is_selected(name, "skill", without)))
        block = _GLOBAL_BLOCKS[name]
        if block is not None:
            items.append(Item(name, "block", _join(home, block), is_selected(name, "block", without)))
        if name == "claude-code":
            settings = os.path.join(home, ".claude", "settings.json")
            items.extend(Item(name, key, settings, is_selected(name, key, without)) for key in HOOK_ITEMS)
    return items


def without_from_items(items: list[Item]) -> tuple[str, ...]:
    """The --without value that leaves out the unticked items: KEY when every assistant leaves it out."""
    unticked = [item for item in items if not item.ticked]
    keys = []
    for key in ITEMS:
        offering = [item for item in items if item.key == key]
        if offering and all(not item.ticked for item in offering):
            keys.append(key)
    left = [f"{item.assistant}:{item.key}" for item in unticked if item.key not in keys]
    return tuple(keys + [item for item in dict.fromkeys(left)])


# ---------------------------------------------------------------------------
# Where things go
# ---------------------------------------------------------------------------

def detect_assistants(home: str, created: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Assistants whose configuration folder exists and was not created by a Wingbox install."""
    found = []
    for name in ASSISTANTS:
        folder = _join(home, _CONFIG_FOLDERS[name])
        if os.path.isdir(folder) and folder not in created:
            found.append(name)
    return tuple(found)


def missing_folders(scope: Scope, targets: list[Target]) -> list[str]:
    """Folders under the scope root that writing the targets would create."""
    root = os.path.abspath(scope.root)
    missing: list[str] = []
    for target in targets:
        folder = target.path if target.what == "skill" else os.path.dirname(target.path)
        while folder.startswith(root + os.sep) and not os.path.exists(folder):
            if folder not in missing:
                missing.append(folder)
            folder = os.path.dirname(folder)
    return missing


def find_project_root(cwd: str) -> str:
    """The nearest folder at or above cwd that holds .git; cwd when there is none."""
    folder = os.path.abspath(cwd)
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return os.path.abspath(cwd)
        folder = parent


def plan_targets(scope: Scope, assistants: tuple[str, ...]) -> list[Target]:
    """Skill folders, instruction files and settings files for these assistants, each path once.

    Two names for one file (CLAUDE.md as a link to AGENTS.md) are one target.
    """
    skills = _GLOBAL_SKILLS if scope.kind == "global" else _PROJECT_SKILLS
    blocks = _GLOBAL_BLOCKS if scope.kind == "global" else _PROJECT_BLOCKS
    wanted: list[tuple[What, str, str]] = []
    for name in assistants:
        wanted.append(("skill", _join(scope.root, skills[name], SKILL_NAME), name))
    for name in assistants:
        block = blocks[name]
        if block is not None:
            wanted.append(("block", _join(scope.root, block), name))
    if "claude-code" in assistants:
        wanted.append(("hook", os.path.join(scope.root, ".claude", "settings.json"), "claude-code"))

    order: list[tuple[What, str]] = []
    paths: dict[tuple[What, str], str] = {}
    owners: dict[tuple[What, str], list[str]] = {}
    for what, path, name in wanted:
        key = (what, os.path.realpath(path))
        if key not in owners:
            order.append(key)
            paths[key] = path
            owners[key] = []
        if name not in owners[key]:
            owners[key].append(name)
    return [Target(what, paths[(what, real)], tuple(owners[(what, real)])) for what, real in order]


def record_path(home: str) -> str:
    return os.path.join(home, RECORD_RELATIVE)


# ---------------------------------------------------------------------------
# Pure edits
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Edited:
    text: str


@dataclass(frozen=True)
class Unbalanced:
    pass


EditResult = Union[Edited, Unbalanced]


def _block_span(text: str) -> Union[tuple[int, int], None, Unbalanced]:
    """Start and end (after the end marker's line) of the marked block; None when absent."""
    begin = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if begin < 0 and end < 0:
        return None
    if begin < 0 or end < begin or text.find(BEGIN_MARKER, begin + 1) >= 0:
        return Unbalanced()
    stop = end + len(END_MARKER)
    if text.startswith("\n", stop):
        stop += 1
    return begin, stop


def upsert_block(text: str, block: str = ALWAYS_ON_BLOCK) -> EditResult:
    """Replace the marked block, or append it after a blank line; text outside the markers is kept."""
    span = _block_span(text)
    if isinstance(span, Unbalanced):
        return span
    if span is not None:
        return Edited(text[: span[0]] + block + text[span[1]:])
    if not text:
        return Edited(block)
    separator = "\n" if text.endswith("\n") else "\n\n"
    return Edited(text + separator + block)


def remove_block(text: str) -> EditResult:
    """Remove the marked block and the blank line install put before it."""
    span = _block_span(text)
    if isinstance(span, Unbalanced):
        return span
    if span is None:
        return Edited(text)
    before, after = text[: span[0]], text[span[1]:]
    if before.endswith("\n\n"):
        before = before[:-1]
    return Edited(before + after)


_HOOK_SUBCOMMANDS = {
    "search-hook": HOOK_SUBCOMMAND, "edit-hook": DIRTY_SUBCOMMAND, "session-hook": SESSION_SUBCOMMAND, "git-hook": GIT_SUBCOMMAND,
}
# event, matcher (None: every source of the event), timeout of each hook item
_HOOK_PLACES = {
    "search-hook": ("PreToolUse", HOOK_MATCHER, HOOK_TIMEOUT_SECONDS),
    "edit-hook": ("PostToolUse", DIRTY_MATCHER, DIRTY_TIMEOUT_SECONDS),
    "session-hook": ("SessionStart", None, SESSION_TIMEOUT_SECONDS),
    "git-hook": ("PostToolUse", GIT_MATCHER, GIT_TIMEOUT_SECONDS),
}


def hook_kind(entry: object) -> Optional[str]:
    """The item of a hook entry Wingbox wrote (search-hook, edit-hook, session-hook, git-hook), else None.
    Exec form (`args` ending in `hook pre-search`, ...) and the shell form of versions before 0.7.1."""
    if not isinstance(entry, dict) or entry.get("type") != "command":
        return None
    command = entry.get("command")
    args = entry.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list):
        return None
    line = " ".join([command, *(str(arg) for arg in args)]).rstrip()
    if "wingbox" not in line:
        return None
    return next((key for key, subcommand in _HOOK_SUBCOMMANDS.items() if line.endswith(subcommand)), None)


def is_wingbox_hook(entry: object) -> bool:
    """A hook entry Wingbox wrote."""
    return hook_kind(entry) is not None


def _hook_entry(program: HookProgram, subcommand: str, timeout: int) -> dict:
    """A hook entry in exec form on every system: Claude Code starts the program found on PATH (or at its
    absolute path) with `args` as the argument vector and no shell, so it needs neither Git Bash nor
    PowerShell on Windows; `onFailure: continue` lets the tool call go ahead when the program is missing,
    fails or times out (code.claude.com/docs/en/hooks, "Exec form and shell form", "Block the action
    when a hook fails")."""
    return {
        "type": "command",
        "command": program[0],
        "args": [*program[1:], *subcommand.split()],
        "timeout": timeout,
        "onFailure": "continue",
    }


def item_group(program: HookProgram, key: str) -> dict:
    """The settings group of one hook item: its matcher (none for SessionStart) and its entry."""
    _event, matcher, timeout = _HOOK_PLACES[key]
    group: dict = {"matcher": matcher} if matcher is not None else {}
    group["hooks"] = [_hook_entry(program, _HOOK_SUBCOMMANDS[key], timeout)]
    return group


def hook_group(program: HookProgram) -> dict:
    return item_group(program, "search-hook")


def dirty_group(program: HookProgram) -> dict:
    return item_group(program, "edit-hook")


def _stripped_groups(groups: list, keys: frozenset[str]) -> list:
    kept: list = []
    for group in groups:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(entries, list) or not any(hook_kind(entry) in keys for entry in entries):
            kept.append(group)
            continue
        remaining = [entry for entry in entries if hook_kind(entry) not in keys]
        if remaining:
            kept.append({**group, "hooks": remaining})
    return kept


def strip_hook(settings: dict, keys: frozenset[str] = frozenset(HOOK_ITEMS)) -> dict:
    """Settings without Wingbox's hook entries of these items; other entries and groups stay as they are."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not any(isinstance(hooks.get(event), list) for event in _HOOK_EVENTS):
        return settings
    new_hooks = {key: value for key, value in hooks.items() if key not in _HOOK_EVENTS}
    for event in _HOOK_EVENTS:
        if isinstance(hooks.get(event), list):
            groups = _stripped_groups(hooks[event], keys)
            if groups:
                new_hooks[event] = groups
        elif event in hooks:
            new_hooks[event] = hooks[event]
    result = {key: value for key, value in settings.items() if key != "hooks"}
    if new_hooks:
        result["hooks"] = new_hooks
    return result


def _wingbox_entry_count(groups: list) -> int:
    return sum(
        1
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for entry in group["hooks"]
        if is_wingbox_hook(entry)
    )


def merge_hook(settings: dict, program: HookProgram, keys: tuple[str, ...] = HOOK_ITEMS) -> dict:
    """Settings with exactly one Wingbox group per hook item of keys, and none of the other items; other hooks and
    settings are kept."""
    wanted = [(_HOOK_PLACES[key][0], item_group(program, key)) for key in HOOK_ITEMS if key in keys]
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    present = sum(_wingbox_entry_count(hooks[event]) for event in _HOOK_EVENTS if isinstance(hooks.get(event), list))
    if present == len(wanted) and all(isinstance(hooks.get(event), list) and group in hooks[event] for event, group in wanted):
        return settings
    stripped = strip_hook(settings)
    stripped_hooks = stripped.get("hooks") if isinstance(stripped.get("hooks"), dict) else {}
    merged = dict(stripped_hooks)
    for event, group in wanted:
        merged[event] = list(merged.get(event, [])) + [group]
    if not merged:
        return {key: value for key, value in stripped.items() if key != "hooks"}
    return {**stripped, "hooks": merged}


def settings_text(settings: dict) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def _scope_kind(value: object) -> Optional[ScopeKind]:
    if value == "global":
        return "global"
    if value == "project":
        return "project"
    return None


def read_record(text: str) -> list[Installed]:
    entries: list[Installed] = []
    for line in text.splitlines():
        if not line.startswith("(installed"):
            continue
        fact = parse_fact(line)
        scope, root, assistants = _scope_kind(fact.get("scope")), fact.get("root"), fact.get("assistants")
        names = assistants if isinstance(assistants, tuple) else (assistants,)
        folders = fact.get("folders")
        folders = folders if isinstance(folders, tuple) else (folders,) if folders else ()
        without = fact.get("without")
        without = without if isinstance(without, tuple) else (without,) if without else ()
        if scope is not None and isinstance(root, str) and root:
            entries.append(Installed(scope, root, tuple(name for name in ASSISTANTS if name in names), folders,
                                     tuple(item for item in without if _item_problem(item) is None)))
    return entries


def render_record(entries: list[Installed]) -> str:
    lines = ["; Written by `wingbox install`; read by `wingbox update` and `wingbox uninstall`."]
    for entry in entries:
        lines.append(fact_sexp(make_fact(
            "installed", scope=entry.scope, root=entry.root, assistants=entry.assistants, folders=entry.folders,
            without=entry.without,
        )))
    return "\n".join(lines) + "\n"


def with_installed(
    entries: list[Installed], scope: Scope, assistants: tuple[str, ...], folders: tuple[str, ...],
    without: tuple[str, ...] = (),
) -> list[Installed]:
    result: list[Installed] = []
    found = False
    for entry in entries:
        if entry.scope == scope.kind and entry.root == scope.root:
            merged = entry.assistants + assistants
            merged_folders = entry.folders + tuple(folder for folder in folders if folder not in entry.folders)
            result.append(Installed(
                entry.scope, entry.root, tuple(name for name in ASSISTANTS if name in merged), merged_folders, without
            ))
            found = True
        else:
            result.append(entry)
    if not found:
        result.append(Installed(
            scope.kind, scope.root, tuple(name for name in ASSISTANTS if name in assistants), folders, without
        ))
    return result


def without_installed(
    entries: list[Installed], scope: Scope, assistants: tuple[str, ...], folders_left: tuple[str, ...]
) -> list[Installed]:
    result: list[Installed] = []
    for entry in entries:
        if entry.scope == scope.kind and entry.root == scope.root:
            remaining = tuple(name for name in entry.assistants if name not in assistants)
            if remaining:
                result.append(Installed(entry.scope, entry.root, remaining, folders_left, entry.without))
        else:
            result.append(entry)
    return result


def created_folders(entries: list[Installed]) -> frozenset[str]:
    return frozenset(folder for entry in entries for folder in entry.folders)


# ---------------------------------------------------------------------------
# Files (adapters)
# ---------------------------------------------------------------------------

def _read_text(path: str) -> Optional[str]:
    """The text of a file, None when there is none. A file that cannot be read (no permission, a folder in its
    place, not UTF-8) raises OSError: install and uninstall answer with a fact for that file (_reason)."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as error:
        raise OSError(f"not UTF-8 text (byte {error.start})") from error


def _reason(error: OSError) -> str:
    """Why a file could not be read or written, without its path: the fact names the file."""
    return error.strerror or str(error)


def _write_text(path: str, text: str) -> None:
    # Plain write, not a rename: a CLAUDE.md that links to AGENTS.md stays a link.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None


def _names_wingbox(line: bytes) -> bool:
    """Whether a line of an ignore file is a rule about the folder .wingbox: `.wingbox/`, `/.wingbox/`,
    `.wingbox/*`, `!.wingbox/config.yaml`, `**/.wingbox`. A comment and another name that only contains it are not."""
    rule = line.strip()
    return bool(rule) and not rule.startswith(b"#") and b".wingbox" in rule.lstrip(b"!").split(b"/")


def append_wingbox_ignore(path: str, *, project: bool, dry_run: bool = False) -> bool:
    """Append the directory rule if needed, preserving the existing file's bytes and line endings.

    A file that already has a rule about .wingbox is left as it is, whatever the rule says: a team that
    ignores `.wingbox/*` and excepts `config.yaml` to commit it wrote that on purpose, and a rule appended
    after theirs would override it."""
    pattern = b".wingbox/"
    rule = b"/" + pattern if project else pattern
    if project and os.path.islink(path):
        raise OSError("Git does not follow symbolic links in a project's .gitignore")
    current = _read_bytes(path) or b""
    if any(_names_wingbox(line) for line in current.splitlines()):
        return False
    if not dry_run:
        newline = b"\r\n" if b"\r\n" in current else b"\n"
        separator = newline if current and not current.endswith(b"\n") else b""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as handle:
            handle.write(separator + rule + newline)
    return True


def _global_ignore_path(home: str) -> str:
    """Ask Git for its user-level excludes path, falling back to Git's XDG default."""
    result = subprocess.run(
        ["git", "config", "--global", "--includes", "--null", "--type=path", "--get", "core.excludesFile"],
        cwd=home, capture_output=True, check=False, timeout=5,
    )
    if result.returncode not in (0, 1) or result.stderr:
        detail = os.fsdecode(result.stderr).strip()
        raise OSError(detail or f"git config exited with status {result.returncode}")
    if result.returncode == 0:
        path = os.fsdecode(result.stdout.removesuffix(b"\0"))
        if not os.path.isabs(path):
            raise OSError("global core.excludesFile must use an absolute path")
        return path
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    if not os.path.isabs(config_home):
        raise OSError("XDG_CONFIG_HOME must use an absolute path")
    return os.path.join(config_home, "git", "ignore")


def _install_global_ignore(home: str, dry_run: bool) -> Fact:
    path = ""
    try:
        path = _global_ignore_path(home)
        changed = append_wingbox_ignore(path, project=False, dry_run=dry_run)
    except (OSError, subprocess.TimeoutExpired) as error:
        location = (path,) if path else ()
        return make_fact("install-failed", *location, what="global-gitignore", reason=str(error))
    if changed:
        return make_fact(_verb(dry_run), path, what="global-gitignore")
    return make_fact("install-kept", path, what="global-gitignore", reason="unchanged")


def _read_settings(path: str) -> Union[dict, None, UsageError]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        settings = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as error:
        return UsageError(f"not valid JSON: {error.msg} at line {error.lineno}")
    if not isinstance(settings, dict):
        return UsageError("not a JSON object")
    return settings


def skill_payload(skill_files: Mapping[str, bytes], version: str) -> dict[str, bytes]:
    return {**skill_files, STAMP_FILE: (version + "\n").encode("utf-8")}


def _verb(dry_run: bool) -> str:
    return "install-would-write" if dry_run else "install-wrote"


def _install_skill(target: Target, payload: Mapping[str, bytes], version: str, dry_run: bool) -> Fact:
    stamp = os.path.join(target.path, STAMP_FILE)
    if os.path.isdir(target.path) and os.listdir(target.path) and not os.path.isfile(stamp):
        return make_fact("install-kept", target.path, what="skill", assistant=target.assistants,
                         reason="installed-by-another-tool")
    changed = [rel for rel, data in sorted(payload.items()) if _read_bytes(_join(target.path, rel)) != data]
    if not changed:
        return make_fact("install-kept", target.path, what="skill", assistant=target.assistants, reason="unchanged")
    if not dry_run:
        for rel in changed:
            destination = _join(target.path, rel)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as handle:
                handle.write(payload[rel])
    return make_fact(_verb(dry_run), target.path, what="skill", assistant=target.assistants, version=version)


def _install_block(target: Target, dry_run: bool) -> Fact:
    current = _read_text(target.path)
    edited = upsert_block(current or "")
    if isinstance(edited, Unbalanced):
        return make_fact("install-kept", target.path, what="block", assistant=target.assistants,
                         reason="unbalanced-markers")
    if current is not None and edited.text == current:
        return make_fact("install-kept", target.path, what="block", assistant=target.assistants, reason="unchanged")
    if not dry_run:
        _write_text(target.path, edited.text)
    return make_fact(_verb(dry_run), target.path, what="block", assistant=target.assistants)


def _install_hook(target: Target, command: HookProgram, dry_run: bool, keys: tuple[str, ...] = HOOK_ITEMS) -> Fact:
    settings = _read_settings(target.path)
    if isinstance(settings, UsageError):
        return make_fact("install-failed", target.path, what="hook", assistant=target.assistants,
                         reason=settings.message)
    if settings is None and not keys:
        return make_fact("install-kept", target.path, what="hook", assistant=target.assistants, reason="left-out")
    merged = merge_hook(settings or {}, command, keys)
    if settings is not None and merged == settings:
        return make_fact("install-kept", target.path, what="hook", assistant=target.assistants, reason="unchanged")
    if not dry_run:
        backup = target.path + BACKUP_SUFFIX
        backed_up = settings is not None and not os.path.exists(backup)
        if backed_up:
            shutil.copy2(target.path, backup)
        try:
            _write_text(target.path, settings_text(merged))
        except OSError:
            if backed_up:   # the settings stay as they were: no backup of them is left behind
                os.chmod(backup, 0o600)   # the copy of a read-only file is read-only: Windows removes none
                os.remove(backup)
            raise
    return make_fact(_verb(dry_run), target.path, what="hook", assistant=target.assistants)


def _write_record(path: str, entries: list[Installed], dry_run: bool, verb: str) -> list[Fact]:
    current = _read_text(path)
    if not entries:
        if current is None:
            return []
        if not dry_run:
            os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass  # the folder holds other files: keep it
        return [make_fact("uninstall-removed", path, what="record")]
    text = render_record(entries)
    if text == current:
        return [make_fact(f"{verb}-kept", path, what="record", reason="unchanged")]
    if not dry_run:
        _write_text(path, text)
    written = "install-would-write" if dry_run else "install-wrote"
    return [make_fact(written if verb == "install" else "uninstall-wrote", path, what="record")]


def _read_record_file(home: str) -> list[Installed]:
    return read_record(_read_text(record_path(home)) or "")


def record_failure(home: str, command: str = "install") -> Optional[Fact]:
    """The `<command>-failed` fact for a record ~/.wingbox/installed.sexp that exists and cannot be read; None
    otherwise. Install and uninstall stop on it before they write anything: the record says which folders an
    earlier install created and which items the person left out."""
    try:
        _read_record_file(home)
    except OSError as error:
        return make_fact(f"{command}-failed", record_path(home), what="record", reason=_reason(error))
    return None


def scope_of(options_scope: ScopeKind, machine: Machine) -> Scope:
    if options_scope == "global":
        return Scope("global", os.path.abspath(machine.home))
    return Scope("project", find_project_root(machine.cwd))


ASSISTANT_SETUP_FILE = ".wingbox/assistant-setup.md"


def assistant_setup_text() -> str:
    """What `wingbox init` prints and saves as .wingbox/assistant-setup.md: the lines a team may add by hand to the
    repository's own files so that every member's assistant uses Wingbox here. Wingbox itself writes none of them."""
    settings = {"hooks": {}}
    for key in HOOK_ITEMS:
        event = _HOOK_PLACES[key][0]
        settings["hooks"].setdefault(event, []).append(item_group(PROJECT_HOOK_PROGRAM, key))
    return (
        "# Wingbox for every assistant in this repository\n\n"
        "Project setup writes into `.wingbox/` and adds it to the project's `.gitignore`. Each\n"
        "person can run `wingbox install` once to set up their assistant. A team that wants every\n"
        "member's coding assistant to use Wingbox in this repository can instead add these lines by hand to the\n"
        "repository's own files and commit them; they need the `wingbox` command on each member's PATH\n"
        "(`pipx install wingbox` or `uv tool install wingbox`).\n\n"
        "## AGENTS.md and CLAUDE.md\n\n"
        + ALWAYS_ON_BLOCK + "\n"
        "## .claude/settings.json (Claude Code)\n\n"
        "```json\n" + json.dumps(settings, indent=2) + "\n```\n"
    )


def recorded_without(home: str) -> tuple[str, ...]:
    """The items the person left out at the last global install (none when there was none)."""
    root = os.path.abspath(home)
    return next((entry.without for entry in _read_record_file(home) if entry.scope == "global" and entry.root == root), ())


def chosen_assistants(options: Options, machine: Machine) -> tuple[str, ...]:
    return options.assistants or detect_assistants(machine.home, created_folders(_read_record_file(machine.home)))


def install(options: Options, machine: Machine) -> list[Fact]:
    """Put the ticked items in place in the user's own settings (global only), record the choice.

    A file that cannot be read or written (no permission, a folder in its place, not UTF-8) is an
    `install-failed` fact with the file and the reason; the other items are put in place and the record is
    written all the same, so that `wingbox uninstall` knows what to remove."""
    scope = Scope("global", os.path.abspath(machine.home))
    unreadable = record_failure(machine.home)
    if unreadable is not None:
        return [unreadable]
    recorded = _read_record_file(machine.home)
    assistants = chosen_assistants(options, machine)
    if not assistants:
        return [make_fact("install-failed", reason="no assistant configuration folder found; name one with --assistant")]
    without = options.without if options.without is not None else recorded_without(machine.home)
    payload = skill_payload(machine.skill_files, machine.version)
    facts: list[Fact] = []
    if options.global_gitignore:
        facts.append(_install_global_ignore(scope.root, options.dry_run))
    targets = [target for target in plan_targets(scope, assistants) if _target_selected(target, without)]
    missing = missing_folders(scope, targets)
    for target in targets:
        try:
            if target.what == "skill":
                facts.append(_install_skill(target, payload, machine.version, options.dry_run))
            elif target.what == "block":
                facts.append(_install_block(target, options.dry_run))
            else:
                keys = tuple(key for key in HOOK_ITEMS if is_selected("claude-code", key, without))
                facts.append(_install_hook(target, machine.hook_program, options.dry_run, keys))
        except OSError as error:
            facts.append(make_fact("install-failed", target.path, what=target.what, assistant=target.assistants,
                                   reason=_reason(error)))
    facts.extend(make_fact("install-left-out", item.path, what=item.key, assistant=(item.assistant,))
                 for item in install_items(scope.root, assistants, without) if not item.ticked)
    created = tuple(folder for folder in missing if options.dry_run or os.path.isdir(folder))
    entries = with_installed(recorded, scope, assistants, created, without)
    try:
        facts.extend(_write_record(record_path(machine.home), entries, options.dry_run, "install"))
    except OSError as error:
        facts.append(make_fact("install-failed", record_path(machine.home), what="record", reason=_reason(error)))
    return facts


def _target_selected(target: Target, without: tuple[str, ...]) -> bool:
    """A skill or block target is written when one of its assistants keeps it; the hook target always (its
    groups follow the hook items, and a left-out hook is removed from the settings)."""
    if target.what == "hook":
        return True
    return any(is_selected(name, target.what, without) for name in target.assistants)


def _remove_skill(target: Target, payload: Mapping[str, bytes]) -> list[Fact]:
    if not os.path.isdir(target.path):
        return []
    if not os.path.isfile(os.path.join(target.path, STAMP_FILE)):
        return [make_fact("uninstall-kept", target.path, what="skill", assistant=target.assistants,
                          reason="installed-by-another-tool")]
    for rel in sorted(payload, key=lambda name: name.count("/"), reverse=True):
        path = _join(target.path, rel)
        if os.path.isfile(path):
            os.remove(path)
    for folder in sorted({os.path.dirname(_join(target.path, rel)) for rel in payload}, key=len, reverse=True):
        try:
            os.rmdir(folder)
        except OSError:
            pass  # not empty: files that install did not write stay
    if os.path.isdir(target.path):
        return [make_fact("uninstall-kept", target.path, what="skill", assistant=target.assistants,
                          reason="holds-other-files")]
    return [make_fact("uninstall-removed", target.path, what="skill", assistant=target.assistants)]


def _remove_block(target: Target) -> list[Fact]:
    current = _read_text(target.path)
    if current is None:
        return []
    edited = remove_block(current)
    if isinstance(edited, Unbalanced):
        return [make_fact("uninstall-kept", target.path, what="block", assistant=target.assistants,
                          reason="unbalanced-markers")]
    if edited.text == current:
        return []
    if edited.text.strip():
        _write_text(target.path, edited.text)
    else:
        os.remove(target.path)
    return [make_fact("uninstall-removed", target.path, what="block", assistant=target.assistants)]


def _remove_hook(target: Target, keys: frozenset[str] = frozenset(HOOK_ITEMS)) -> list[Fact]:
    settings = _read_settings(target.path)
    if settings is None:
        return []
    if isinstance(settings, UsageError):
        return [make_fact("uninstall-kept", target.path, what="hook", assistant=target.assistants,
                          reason=settings.message)]
    stripped = strip_hook(settings, keys)
    if stripped == settings:
        return []
    backup = target.path + BACKUP_SUFFIX
    if not stripped and not os.path.exists(backup):
        os.remove(target.path)  # install created this file: no backup was needed
    elif _read_settings(backup) == stripped:
        shutil.copy2(backup, target.path)  # the settings are the backed-up ones: restore their exact text
    else:
        _write_text(target.path, settings_text(stripped))
    return [make_fact("uninstall-removed", target.path, what="hook", assistant=target.assistants)]


def uninstall(options: Options, machine: Machine) -> list[Fact]:
    """Remove what install put in place. A file that cannot be read or written is kept, with the reason
    (`uninstall-kept`), like a block with unbalanced markers and settings that are not JSON."""
    scope = scope_of(options.scope, machine)
    removing = options.assistants or ASSISTANTS
    keeping = options.without or ()
    unreadable = record_failure(machine.home, "uninstall")
    if unreadable is not None:
        return [unreadable]
    entries = _read_record_file(machine.home)
    remaining_entries = without_installed(entries, scope, removing, ())
    staying = next(
        (entry.assistants for entry in remaining_entries if entry.scope == scope.kind and entry.root == scope.root), ()
    )
    payload = skill_payload(machine.skill_files, machine.version)
    kept_paths = {os.path.realpath(target.path) for target in plan_targets(scope, staying)}
    facts: list[Fact] = []
    for target in plan_targets(scope, removing):
        if os.path.realpath(target.path) in kept_paths:
            if os.path.exists(target.path):
                facts.append(make_fact("uninstall-kept", target.path, what=target.what, assistant=staying,
                                       reason="shared"))
            continue
        if target.what != "hook" and not any(is_selected(name, target.what, keeping) for name in target.assistants):
            continue   # named by --without: it stays
        try:
            if target.what == "skill":
                facts.extend(_remove_skill(target, payload))
            elif target.what == "block":
                facts.extend(_remove_block(target))
            else:
                facts.extend(_remove_hook(target, frozenset(key for key in HOOK_ITEMS if is_selected("claude-code", key, keeping))))
        except OSError as error:
            facts.append(make_fact("uninstall-kept", target.path, what=target.what, assistant=target.assistants,
                                   reason=_reason(error)))
    recorded = next((entry.folders for entry in entries if entry.scope == scope.kind and entry.root == scope.root), ())
    removed, left = _remove_empty_folders(recorded)
    facts.extend(make_fact("uninstall-removed", folder, what="folder") for folder in removed)
    remaining_entries = without_installed(entries, scope, removing, left)
    try:
        facts.extend(_write_record(record_path(machine.home), remaining_entries, False, "uninstall"))
    except OSError as error:
        facts.append(make_fact("uninstall-failed", record_path(machine.home), what="record", reason=_reason(error)))
    return facts


def _remove_empty_folders(folders: tuple[str, ...]) -> tuple[list[str], tuple[str, ...]]:
    """Remove the empty ones of the folders install created, deepest first; the others stay recorded."""
    removed: list[str] = []
    for folder in sorted(folders, key=lambda path: path.count(os.sep), reverse=True):
        if not os.path.isdir(folder):
            continue
        try:
            os.rmdir(folder)
            removed.append(folder)
        except OSError:
            pass  # it holds files install did not write: it stays
    return removed, tuple(folder for folder in folders if os.path.isdir(folder))


def exit_status(facts: list[Fact]) -> int:
    return 1 if any(fact.head.endswith("-failed") for fact in facts) else 0
