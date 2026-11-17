#!/usr/bin/env python3
"""Run every hook entry Wingbox writes the way Claude Code runs it, and check the answers.

The entries come from the real files: `wingbox install` into a temporary home folder, the
settings lines `wingbox init` saves in .wingbox/assistant-setup.md for a team to commit, and the
Claude Code plugin manifest.
Each entry is in exec form (`command` + `args`): Claude Code resolves `command` on PATH and
spawns it with no shell (code.claude.com/docs/en/hooks, "Exec form and shell form"), so this
script does the same: shutil.which, then subprocess with shell=False. On Windows the program
must be a real executable (.exe), since Claude Code cannot spawn a .cmd or .bat shim without a
shell, and the run uses a PATH without Git for Windows, so no sh or bash can be reached. A shell
form entry, should one appear, runs as Claude Code runs it: `sh -c` on macOS and Linux, and on
Windows without Git Bash in PowerShell (powershell.exe, and pwsh when present).

The project folder name holds a space and non-ASCII letters. Each search hook gets a Grep
input for a function of the project and must exit 0 and name the module that defines it;
each edit hook gets an Edit input, each git hook a Bash input running `git switch`, each
session-start hook a SessionStart input; these must exit 0 and print nothing.

    python tools/check_hooks.py --source PATH   PATH replaces the pinned git source of the
                                                plugin's uvx commands (the checkout under test)

Needs the `wingbox` program on PATH (the checkout installed into a virtual environment) and uvx.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "plugins" / "wingbox" / ".claude-plugin" / "plugin.json"
PIN = re.compile(r"^git\+https://github\.com/aileron-tech/wingbox@[0-9a-f]{40}$")
WINDOWS = os.name == "nt"
FILES = {
    "billing/__init__.py": "",
    "billing/invoice.py": "def compute_invoice_total(lines):\n    return sum(lines)\n",
    "api/__init__.py": "",
    "api/routes.py": "from billing.invoice import compute_invoice_total\n\n\ndef total(lines):\n    return compute_invoice_total(lines)\n",
}


def environment(home: Path) -> dict[str, str]:
    """This environment with home folder home; on Windows without any Git for Windows folder on PATH."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="0")
    if WINDOWS:
        parts = [part for part in env["PATH"].split(os.pathsep) if "\\git\\" not in part.lower() + "\\"]
        env["PATH"] = os.pathsep.join(parts)
    return env


def resolve(command: str, env: dict[str, str]) -> str:
    """The program Claude Code would spawn for an exec-form command."""
    found = shutil.which(command, path=env["PATH"])
    if found is None:
        raise SystemExit(f"FAIL: {command} is not on PATH")
    if WINDOWS and Path(found).suffix.lower() != ".exe":
        raise SystemExit(f"FAIL: {command} resolves to {found}, not a real executable: exec form cannot spawn it")
    return found


def run_entry(entry: dict, stdin: dict, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    data = json.dumps(stdin).encode("utf-8")
    if "args" in entry:
        argv = [resolve(entry["command"], env), *entry["args"]]
        return subprocess.run(argv, input=data, capture_output=True, cwd=cwd, env=env, shell=False, timeout=120)
    shells = [["sh", "-c"]] if not WINDOWS else [
        [name, "-NoProfile", "-NonInteractive", "-Command"] for name in ("powershell.exe", "pwsh") if shutil.which(name)
    ]
    results = [subprocess.run([*shell, entry["command"]], input=data, capture_output=True, cwd=cwd, env=env, timeout=120)
               for shell in shells]
    return next((result for result in results if result.returncode != 0), results[0])


def hook_entries(settings: dict) -> list[tuple[str, dict]]:
    return [
        (event, entry)
        for event, groups in settings.get("hooks", {}).items()
        for group in groups
        for entry in group["hooks"]
        if "wingbox" in json.dumps(entry)
    ]


# What Claude Code sends to each kind of hook: the tool and its input (none for SessionStart).
INPUTS = {
    "pre-search": lambda project: ("Grep", {"pattern": "compute_invoice_total", "path": str(project)}),
    "dirty": lambda project: ("Edit", {"file_path": str(project / "api" / "routes.py")}),
    "git": lambda project: ("Bash", {"command": "git switch -c feature"}),
    "session-start": lambda project: (None, None),
}


def _kind(entry: dict) -> str:
    """The hook subcommand an entry runs: the last word of its args (exec form) or of its command."""
    words = entry.get("args") or entry.get("command", "").split()
    return str(words[-1]) if words else ""


def team_settings(project: Path) -> dict:
    """The settings JSON of .wingbox/assistant-setup.md (what a team commits by hand)."""
    text = (project / ".wingbox" / "assistant-setup.md").read_text(encoding="utf-8")
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def check(label: str, settings: dict, project: Path, env: dict[str, str]) -> list[str]:
    """Run every Wingbox entry of settings in project; return the failures."""
    failures = []
    entries = hook_entries(settings)
    kinds = sorted(_kind(entry) for _, entry in entries)
    if kinds != sorted(INPUTS):
        failures.append(f"{label}: expected one entry each for {sorted(INPUTS)}, found {kinds}")
    for event, entry in entries:
        if entry.get("onFailure") != "continue":
            failures.append(f"{label} {event}: no \"onFailure\": \"continue\"")
        if _kind(entry) not in INPUTS:
            failures.append(f"{label} {event}: unknown hook {entry}")
            continue
        tool, tool_input = INPUTS[_kind(entry)](project)
        stdin = {"session_id": "check", "hook_event_name": event, "cwd": str(project)}
        if tool:
            stdin.update(tool_name=tool, tool_input=tool_input)
        result = run_entry(entry, stdin, project, env)
        out, err = result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        shown = json.dumps({key: entry[key] for key in ("command", "args") if key in entry}, ensure_ascii=False)
        print(f"{label} {event} {shown} -> exit {result.returncode}, {len(out)} bytes")
        if result.returncode != 0:
            failures.append(f"{label} {event}: exit {result.returncode}: {err.strip()[:400]}")
        elif _kind(entry) != "pre-search" and out.strip():
            failures.append(f"{label} {event}: printed {out[:400]!r}, expected nothing")
        elif event == "PreToolUse":
            context = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
            if "billing/invoice" not in context:
                failures.append(f"{label} {event}: no context naming billing/invoice: {out[:400]!r} {err[:400]!r}")
    return failures


def wait_for_refresh(project: Path) -> list[str]:
    """The git and session-start hooks start a refresh that outlives them (the edit hook marked a file): wait until
    it has published, so that the temporary folders are not removed under it."""
    sys.path.insert(0, str(REPO / "src"))
    from wingbox import freshness

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if freshness.is_fresh(freshness.read_state(str(project))) and not freshness.refresh_running(str(project)):
            time.sleep(1)   # the refresh process exits after it releases its lock
            return []
        time.sleep(0.2)
    return ["the refresh started by the hooks did not finish within 300 s"]


def main(args: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
    source = args[1] if args[:1] == ["--source"] and len(args) == 2 else None
    if source is None:
        print(__doc__, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        (home / ".claude").mkdir(parents=True)
        project = Path(tmp) / "shop app - \u00e9t\u00e9 \u043f\u0440\u043e\u0435\u043a\u0442"
        for name, text in FILES.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text, encoding="utf-8", newline="\n")
        if shutil.which("git"):
            subprocess.run(["git", "init", "-q", str(project)], check=True)
        else:
            (project / ".git").mkdir()
        env = environment(home)
        wingbox = resolve("wingbox", env)
        for argv, cwd in (([wingbox, "build", str(project)], project), ([wingbox, "install", "--yes"], project),
                          ([wingbox, "init", str(project), "--yes"], project)):
            subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        pinned = [arg for _, entry in hook_entries(manifest) for arg in entry.get("args", []) if arg.startswith("git+")]
        failures = [] if pinned and all(PIN.match(arg) for arg in pinned) else [f"plugin: unpinned source {pinned}"]
        plugin = json.loads(json.dumps(manifest).replace(json.dumps(pinned[0])[1:-1], json.dumps(source)[1:-1]))
        server = plugin["mcpServers"]["wingbox"]
        # The MCP server starts first in a session and fetches Wingbox; the hooks then run uvx offline.
        warm = subprocess.run([resolve(server["command"], env), *server["args"][:-1], "--version"], cwd=project, env=env,
                              capture_output=True, timeout=900)
        if warm.returncode != 0:
            failures.append(f"plugin MCP server command: exit {warm.returncode}: {warm.stderr.decode('utf-8', 'replace')[:400]}")
        settings = {
            "global install": json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")),
            "assistant-setup.md": team_settings(project),
            "plugin": plugin,
        }
        for label, written in settings.items():
            failures += check(label, written, project, env)
        print(json.dumps({label: written["hooks"] for label, written in settings.items()}, indent=2, ensure_ascii=False))
        failures += wait_for_refresh(project)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
