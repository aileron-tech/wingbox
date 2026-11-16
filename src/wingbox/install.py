"""`wingbox install` and `wingbox uninstall`: put Wingbox in front of coding assistants.

    wingbox install   [--global|--project] [--assistant NAME ...] [--dry-run] [--sexp]
    wingbox uninstall [--global|--project] [--assistant NAME ...] [--sexp]

Install puts three things in place for each assistant:

- the skill (SKILL.md and references/, stamped with the version) in the
  assistant's skill folder, so `/wingbox` exists there;
- an always-on block between `<!-- wingbox:begin -->` and `<!-- wingbox:end -->`
  in the instructions file the assistant reads in every session;
- for Claude Code, a PreToolUse hook on Grep, Glob and Read that runs
  `wingbox hook pre-search`.

`--global` (default) writes into the home folder for the assistants whose
configuration folder exists; `--project` writes into the current repository.
A record in ~/.wingbox/installed.sexp says what was installed, for
`wingbox update` and `wingbox uninstall`. Uninstall removes exactly what
install added: stamped skill folders, the marked blocks, the hook entries.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Union

from wingbox.facts import Fact, fact_sexp, make_fact, parse_fact

ASSISTANTS = ("claude-code", "codex", "gemini", "cursor", "copilot", "agents")
SKILL_NAME = "wingbox"
STAMP_FILE = ".wingbox-version"
BEGIN_MARKER = "<!-- wingbox:begin -->"
END_MARKER = "<!-- wingbox:end -->"
HOOK_SUBCOMMAND = "hook pre-search"
HOOK_MATCHER = "Grep|Glob|Read"
HOOK_TIMEOUT_SECONDS = 5
BACKUP_SUFFIX = ".wingbox-bak"
RECORD_RELATIVE = os.path.join(".wingbox", "installed.sexp")
PROJECT_HOOK_COMMAND = f"wingbox {HOOK_SUBCOMMAND}"

ALWAYS_ON_BLOCK = f"""{BEGIN_MARKER}
## Wingbox code graph

When the project has a Wingbox code graph (`.wingbox/graph.sexp` or `.code_graph.sexp`):

- Before reading code for a task, run `wingbox quick "<keyword>"` or `wingbox analyze <module>` and read the files it names first.
- Before changing a file, run `wingbox impact <file>` to see what depends on it.
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
    hook_command: str


@dataclass(frozen=True)
class Installed:
    scope: ScopeKind
    root: str
    assistants: tuple[str, ...]
    folders: tuple[str, ...] = ()  # folders install created, removed by uninstall when empty


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def parse_arguments(command: Literal["install", "uninstall"], args: list[str]) -> Union[Options, UsageError]:
    scope: ScopeKind = "global"
    assistants: list[str] = []
    dry_run = False
    sexp = False
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--global":
            scope = "global"
        elif argument == "--project":
            scope = "project"
        elif argument == "--dry-run" and command == "install":
            dry_run = True
        elif argument == "--sexp":
            sexp = True
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
    return Options(command, scope, tuple(assistants), dry_run, sexp)


# ---------------------------------------------------------------------------
# Where things go
# ---------------------------------------------------------------------------

def detect_assistants(home: str, created: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Assistants whose configuration folder exists and was not created by a Wingbox install."""
    found = []
    for name in ASSISTANTS:
        folder = os.path.join(home, _CONFIG_FOLDERS[name])
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
        wanted.append(("skill", os.path.join(scope.root, skills[name], SKILL_NAME), name))
    for name in assistants:
        block = blocks[name]
        if block is not None:
            wanted.append(("block", os.path.join(scope.root, block), name))
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


def is_wingbox_hook(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get("type") != "command":
        return False
    command = entry.get("command")
    return isinstance(command, str) and "wingbox" in command and command.rstrip().endswith(HOOK_SUBCOMMAND)


def hook_group(command: str) -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}],
    }


def strip_hook(settings: dict) -> dict:
    """Settings without Wingbox's hook entries; other entries and groups stay as they are."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse"), list):
        return settings
    groups: list = []
    for group in hooks["PreToolUse"]:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(entries, list) or not any(is_wingbox_hook(entry) for entry in entries):
            groups.append(group)
            continue
        remaining = [entry for entry in entries if not is_wingbox_hook(entry)]
        if remaining:
            groups.append({**group, "hooks": remaining})
    new_hooks = {key: value for key, value in hooks.items() if key != "PreToolUse"}
    if groups:
        new_hooks["PreToolUse"] = groups
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


def merge_hook(settings: dict, command: str) -> dict:
    """Settings with exactly one Wingbox hook group; other hooks and settings are kept."""
    group = hook_group(command)
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    groups = hooks.get("PreToolUse") if isinstance(hooks.get("PreToolUse"), list) else []
    if group in groups and _wingbox_entry_count(groups) == 1:
        return settings
    stripped = strip_hook(settings)
    stripped_hooks = stripped.get("hooks") if isinstance(stripped.get("hooks"), dict) else {}
    pre_tool_use = list(stripped_hooks.get("PreToolUse", [])) + [group]
    return {**stripped, "hooks": {**stripped_hooks, "PreToolUse": pre_tool_use}}


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
        if scope is not None and isinstance(root, str) and root:
            entries.append(Installed(scope, root, tuple(name for name in ASSISTANTS if name in names), folders))
    return entries


def render_record(entries: list[Installed]) -> str:
    lines = ["; Written by `wingbox install`; read by `wingbox update` and `wingbox uninstall`."]
    for entry in entries:
        lines.append(fact_sexp(make_fact(
            "installed", scope=entry.scope, root=entry.root, assistants=entry.assistants, folders=entry.folders
        )))
    return "\n".join(lines) + "\n"


def with_installed(
    entries: list[Installed], scope: Scope, assistants: tuple[str, ...], folders: tuple[str, ...]
) -> list[Installed]:
    result: list[Installed] = []
    found = False
    for entry in entries:
        if entry.scope == scope.kind and entry.root == scope.root:
            merged = entry.assistants + assistants
            merged_folders = entry.folders + tuple(folder for folder in folders if folder not in entry.folders)
            result.append(Installed(
                entry.scope, entry.root, tuple(name for name in ASSISTANTS if name in merged), merged_folders
            ))
            found = True
        else:
            result.append(entry)
    if not found:
        result.append(Installed(scope.kind, scope.root, tuple(name for name in ASSISTANTS if name in assistants), folders))
    return result


def without_installed(
    entries: list[Installed], scope: Scope, assistants: tuple[str, ...], folders_left: tuple[str, ...]
) -> list[Installed]:
    result: list[Installed] = []
    for entry in entries:
        if entry.scope == scope.kind and entry.root == scope.root:
            remaining = tuple(name for name in entry.assistants if name not in assistants)
            if remaining:
                result.append(Installed(entry.scope, entry.root, remaining, folders_left))
        else:
            result.append(entry)
    return result


def created_folders(entries: list[Installed]) -> frozenset[str]:
    return frozenset(folder for entry in entries for folder in entry.folders)


# ---------------------------------------------------------------------------
# Files (adapters)
# ---------------------------------------------------------------------------

def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


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
    changed = [rel for rel, data in sorted(payload.items()) if _read_bytes(os.path.join(target.path, rel)) != data]
    if not changed:
        return make_fact("install-kept", target.path, what="skill", assistant=target.assistants, reason="unchanged")
    if not dry_run:
        for rel in changed:
            destination = os.path.join(target.path, rel)
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


def _install_hook(target: Target, command: str, dry_run: bool) -> Fact:
    settings = _read_settings(target.path)
    if isinstance(settings, UsageError):
        return make_fact("install-failed", target.path, what="hook", assistant=target.assistants,
                         reason=settings.message)
    merged = merge_hook(settings or {}, command)
    if settings is not None and merged == settings:
        return make_fact("install-kept", target.path, what="hook", assistant=target.assistants, reason="unchanged")
    if not dry_run:
        backup = target.path + BACKUP_SUFFIX
        if settings is not None and not os.path.exists(backup):
            shutil.copy2(target.path, backup)
        _write_text(target.path, settings_text(merged))
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


def scope_of(options_scope: ScopeKind, machine: Machine) -> Scope:
    if options_scope == "global":
        return Scope("global", os.path.abspath(machine.home))
    return Scope("project", find_project_root(machine.cwd))


def install(options: Options, machine: Machine) -> list[Fact]:
    scope = scope_of(options.scope, machine)
    recorded = _read_record_file(machine.home)
    assistants = options.assistants or detect_assistants(machine.home, created_folders(recorded))
    if not assistants:
        return [make_fact("install-failed", reason="no assistant configuration folder found; name one with --assistant")]
    payload = skill_payload(machine.skill_files, machine.version)
    command = machine.hook_command if scope.kind == "global" else PROJECT_HOOK_COMMAND
    facts: list[Fact] = []
    targets = plan_targets(scope, assistants)
    missing = missing_folders(scope, targets)
    for target in targets:
        if target.what == "skill":
            facts.append(_install_skill(target, payload, machine.version, options.dry_run))
        elif target.what == "block":
            facts.append(_install_block(target, options.dry_run))
        else:
            facts.append(_install_hook(target, command, options.dry_run))
    created = tuple(folder for folder in missing if options.dry_run or os.path.isdir(folder))
    entries = with_installed(recorded, scope, assistants, created)
    facts.extend(_write_record(record_path(machine.home), entries, options.dry_run, "install"))
    return facts


def _remove_skill(target: Target, payload: Mapping[str, bytes]) -> list[Fact]:
    if not os.path.isdir(target.path):
        return []
    if not os.path.isfile(os.path.join(target.path, STAMP_FILE)):
        return [make_fact("uninstall-kept", target.path, what="skill", assistant=target.assistants,
                          reason="installed-by-another-tool")]
    for rel in sorted(payload, key=lambda name: name.count("/"), reverse=True):
        path = os.path.join(target.path, rel)
        if os.path.isfile(path):
            os.remove(path)
    for folder in sorted({os.path.dirname(os.path.join(target.path, rel)) for rel in payload}, key=len, reverse=True):
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


def _remove_hook(target: Target) -> list[Fact]:
    settings = _read_settings(target.path)
    if settings is None:
        return []
    if isinstance(settings, UsageError):
        return [make_fact("uninstall-kept", target.path, what="hook", assistant=target.assistants,
                          reason=settings.message)]
    stripped = strip_hook(settings)
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
    scope = scope_of(options.scope, machine)
    removing = options.assistants or ASSISTANTS
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
        if target.what == "skill":
            facts.extend(_remove_skill(target, payload))
        elif target.what == "block":
            facts.extend(_remove_block(target))
        else:
            facts.extend(_remove_hook(target))
    recorded = next((entry.folders for entry in entries if entry.scope == scope.kind and entry.root == scope.root), ())
    removed, left = _remove_empty_folders(recorded)
    facts.extend(make_fact("uninstall-removed", folder, what="folder") for folder in removed)
    remaining_entries = without_installed(entries, scope, removing, left)
    facts.extend(_write_record(record_path(machine.home), remaining_entries, False, "uninstall"))
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
