"""The install list in a terminal: every item with a checkbox, grouped by assistant, untick and confirm.

Two ways to answer, the same list: arrow keys and space where the terminal gives single keys (POSIX termios,
Windows msvcrt, through scripts/portable.py), and a numbered list that needs no raw mode (type the numbers
to toggle, Enter to install, q to quit). The functions here are pure apart from the reader and the writer
they are given.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from wingbox.install import Item

_TITLES = {
    "skill": "skill (/wingbox)",
    "block": "always-on instructions block",
    "search-hook": "search hook: graph context for Grep, Glob, Read",
    "edit-hook": "edit hook: marks the files the agent edits",
    "session-hook": "session-start hook: refreshes a stale graph",
    "git-hook": "git hook: refreshes the graph after git switch, pull, merge ...",
}


def lines(items: list[Item], cursor: Optional[int] = None, home: str = "") -> list[str]:
    """The list as text: one heading per assistant, one numbered line per item."""
    out: list[str] = []
    assistant = ""
    for number, item in enumerate(items, 1):
        if item.assistant != assistant:
            assistant = item.assistant
            out.append(f"{assistant}:")
        mark = "x" if item.ticked else " "
        pointer = ">" if cursor == number - 1 else " "
        path = item.path.replace(home, "~", 1) if home and item.path.startswith(home) else item.path
        out.append(f"{pointer} {number:>2}. [{mark}] {_TITLES[item.key]}  ({path})")
    return out


def toggled(items: list[Item], index: int) -> list[Item]:
    return [replace(item, ticked=not item.ticked) if position == index else item for position, item in enumerate(items)]


def numbered(items: list[Item], read_line: Callable[[str], str], write: Callable[[str], None],
             home: str = "") -> Optional[list[Item]]:
    """The fallback without raw keys: numbers toggle items, Enter installs, q quits (None)."""
    while True:
        write("\n".join(lines(items, home=home)) + "\n")
        answer = read_line("Numbers to untick or tick again (e.g. 3 5), Enter to install, q to quit: ").strip().lower()
        if answer in ("q", "quit"):
            return None
        if not answer:
            return items
        for word in answer.replace(",", " ").split():
            if word.isdigit() and 1 <= int(word) <= len(items):
                items = toggled(items, int(word) - 1)
            else:
                write(f"not a number of the list: {word}\n")


def with_keys(items: list[Item], read_key: Callable[[], str], write: Callable[[str], None],
              home: str = "") -> Optional[list[Item]]:
    """Arrow keys move, space toggles, Enter installs, q quits (None)."""
    cursor = 0
    while True:
        write("\033[2J\033[H" + "Wingbox will put these in place (space: untick, Enter: install, q: quit)\r\n"
              + "\r\n".join(lines(items, cursor, home)) + "\r\n")
        key = read_key()
        if key == "up":
            cursor = max(0, cursor - 1)
        elif key == "down":
            cursor = min(len(items) - 1, cursor + 1)
        elif key == "space":
            items = toggled(items, cursor)
        elif key == "enter":
            return items
        elif key in ("q", "escape"):
            return None


def asks(stdin_tty: bool, stdout_tty: bool, environ: dict, yes: bool, dry_run: bool, sexp: bool) -> bool:
    """Whether install asks: a person at a terminal, not CI, not an agent, not --yes, --dry-run or --sexp."""
    if yes or dry_run or sexp or not (stdin_tty and stdout_tty):
        return False
    agent = any(environ.get(name) for name in ("CLAUDECODE", "CODEX_SANDBOX", "CODEX_HOME_AGENT", "CURSOR_AGENT"))
    return not environ.get("CI") and not agent

