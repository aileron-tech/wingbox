"""Facts: the result format of `wingbox install`, `uninstall` and `update`.

A command builds a list of facts. `--sexp` prints them as S-expressions, one
per line, for agents; the default output is a pretty print of the same facts
for people. Nothing else decides what a command reports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

Value = Union[str, tuple[str, ...]]


@dataclass(frozen=True)
class Fact:
    head: str
    args: tuple[str, ...] = ()
    keys: tuple[tuple[str, Value], ...] = ()

    def get(self, key: str) -> Value:
        for name, value in self.keys:
            if name == key:
                return value
        return ""


def make_fact(head: str, *args: str, **keys: Value) -> Fact:
    """A fact; keyword names use '-' for '_' and empty values are left out."""
    return Fact(
        head,
        tuple(args),
        tuple((name.replace("_", "-"), value) for name, value in keys.items() if value not in ("", ())),
    )


_BARE_ATOM = re.compile(r"^[A-Za-z0-9_./~@+=,*<>-][A-Za-z0-9_./~@+=,*<>:-]*$")


def atom(value: str) -> str:
    """One S-expression atom: bare when it is safe, otherwise a quoted string."""
    if _BARE_ATOM.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value_sexp(value: Value) -> str:
    if isinstance(value, tuple):
        return "(" + " ".join(atom(item) for item in value) + ")"
    return atom(value)


def fact_sexp(fact: Fact) -> str:
    parts = [fact.head] + [atom(arg) for arg in fact.args]
    for name, value in fact.keys:
        parts.extend((f":{name}", _value_sexp(value)))
    return "(" + " ".join(parts) + ")"


def render_sexp(facts: list[Fact]) -> str:
    return "".join(fact_sexp(fact) + "\n" for fact in facts)


def _value_text(value: Value) -> str:
    return " ".join(value) if isinstance(value, tuple) else value


def _shorten(value: str, home: str) -> str:
    if home and (value == home or value.startswith(home.rstrip("/") + "/")):
        return "~" + value[len(home.rstrip("/")):]
    return value


_TRAILING_KEYS = ("reason", "instead")  # printed as text after the arguments, not as columns


def _text_row(fact: Fact, columns: list[str], home: str) -> list[str]:
    verb = fact.head.split("-", 1)[1] if "-" in fact.head else fact.head
    values = [_shorten(_value_text(fact.get(name)), home) for name in columns]
    trailing = []
    reason, instead = _value_text(fact.get("reason")), _value_text(fact.get("instead"))
    if reason:
        trailing.append(f"({reason})")
    if instead:
        trailing.append(f"instead run: {instead}")
    return [verb.replace("-", " ")] + values + [" ".join(_shorten(arg, home) for arg in fact.args)] + (
        ["  ".join(trailing)] if trailing else []
    )


def render_text(facts: list[Fact], home: str = "") -> str:
    """Pretty print: the verb, one column per keyword, the arguments, the reason; home is shown as ~."""
    columns: list[str] = []
    for fact in facts:
        for name, _value in fact.keys:
            if name not in _TRAILING_KEYS and name not in columns:
                columns.append(name)
    rows = [_text_row(fact, columns, home) for fact in facts]
    widths = [max((len(row[index]) for row in rows if len(row) > index + 1), default=0) for index in range(len(columns) + 2)]
    lines = []
    for row in rows:
        cells = [cell.ljust(widths[index]) if index < len(row) - 1 else cell for index, cell in enumerate(row)]
        lines.append("  ".join(cell for cell in cells).rstrip())
    return "".join(line + "\n" for line in lines)


def tokenize(line: str) -> list[str]:
    """Atoms of one flat S-expression line; quoted strings are unquoted, lists stay one token."""
    tokens: list[str] = []
    index = 0
    text = line.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char == '"':
            value, index = _read_string(text, index + 1)
            tokens.append(value)
        elif char == "(":
            end = text.find(")", index)
            end = len(text) if end < 0 else end + 1
            tokens.append(text[index:end])
            index = end
        else:
            end = index
            while end < len(text) and not text[end].isspace() and text[end] not in "()":
                end += 1
            tokens.append(text[index:end])
            index = end
    return tokens


def _read_string(text: str, index: int) -> tuple[str, int]:
    chars: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            chars.append(text[index + 1])
            index += 2
            continue
        if char == '"':
            return "".join(chars), index + 1
        chars.append(char)
        index += 1
    return "".join(chars), index


def parse_fact(line: str) -> Fact:
    """Read back a line written by fact_sexp; list values become tuples."""
    tokens = tokenize(line)
    if not tokens:
        return Fact("")
    head, rest = tokens[0], tokens[1:]
    args: list[str] = []
    keys: list[tuple[str, Value]] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token.startswith(":") and index + 1 < len(rest):
            raw = rest[index + 1]
            value: Value = tuple(tokenize(raw)) if raw.startswith("(") else raw
            keys.append((token[1:], value))
            index += 2
            continue
        args.append(token)
        index += 1
    return Fact(head, tuple(args), tuple(keys))
