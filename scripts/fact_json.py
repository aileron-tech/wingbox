"""The `--json` form of an answer made of fact lines: one JSON object per fact, with stable keys.

A fact line `(head a b :key value :flag (:tag x y))` becomes
`{"fact": "head", "args": ["a", "b"], "key": "value", "flag": true, "tag": ["x", "y"]}`:

- the atoms that belong to no keyword are `args`, in their order;
- a keyword takes the one value that follows it, or is `true` when none does; the keywords of
  LIST_KEYS take every atom up to the next keyword and are always a list;
- a list `(:tag ...)` is the list of its items under `tag`; the lists that say how names arrive
  (`(:use A B :of 9)`, `(:glob "super" A)`) are collected under `names`, one object each;
- an atom of digits written without quotes is a number;
- a comment line (`; ...`) is `{"note": "..."}` and any other line `{"line": "..."}`.

Pure functions: text in, values out.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# Keywords whose value is every atom up to the next keyword: (fact head or "" for any fact, keyword).
LIST_KEYS = frozenset({("", "suggestions"), ("", "others"), ("analyze-dependent", "via")})
# The ways a name arrives over an import edge, and those whose first item is the module it comes through.
ROUTE_WAYS = frozenset({"use", "reexport", "glob", "member", "macro"})
ROUTES_WITH_THROUGH = frozenset({"reexport", "glob", "member"})

_NUMBER = re.compile(r"-?\d+\Z")


class Quoted(str):
    """An atom that was written in quotes: never a keyword, never a number."""


def atom(value: str) -> str:
    """One atom of an answer: bare when it reads back as written, else a quoted string."""
    if value and not value.startswith(":") and not any(ch in value for ch in ' "();\\\n\t'):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def split_fact(line: str) -> Optional[list[str | list]]:
    """The items of one fact line: atoms (Quoted when quoted) and the lists that open with `(:`.

    None for a line that is no fact. A bare atom runs to the next space, so a path with brackets
    in it (`app/(docs)/page`) stays one atom.
    """
    text = line.strip()
    if len(text) < 2 or not text.startswith("(") or not text.endswith(")"):
        return None
    return _items(text[1:-1])


def _items(inner: str) -> list[str | list]:
    items: list[str | list] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == " ":
            i += 1
        elif ch == '"':
            end = _closing_quote(inner, i)
            items.append(Quoted(inner[i + 1 : end].replace('\\"', '"').replace("\\\\", "\\")))
            i = end + 1
        elif inner.startswith("(:", i):
            end = _closing_bracket(inner, i)
            items.append(_items(inner[i + 1 : end]))
            i = end + 1
        else:
            end = inner.find(" ", i)
            end = len(inner) if end < 0 else end
            items.append(inner[i:end])
            i = end
    return items


def _closing_quote(text: str, start: int) -> int:
    j = start + 1
    while j < len(text):
        if text[j] == "\\" and j + 1 < len(text):
            j += 2
        elif text[j] == '"':
            return j
        else:
            j += 1
    return len(text)


def _closing_bracket(text: str, start: int) -> int:
    depth, j = 0, start
    while j < len(text):
        if text[j] == '"':
            j = _closing_quote(text, j)
        elif text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return len(text)


def _is_keyword(item: object) -> bool:
    return isinstance(item, str) and not isinstance(item, Quoted) and len(item) > 1 and item.startswith(":")


def _scalar(item: str) -> Any:
    if not isinstance(item, Quoted) and _NUMBER.match(item):
        return int(item)
    return str(item)


def _value(item: str | list) -> Any:
    return [_value(member) for member in item] if isinstance(item, list) else _scalar(item)


def route_json(items: list) -> dict[str, Any]:
    """`(:use A B :of 9)` as {"way": "use", "through": "", "names": ["A", "B"], "of": 9}; `of` counts
    every name, also those the list does not spell out."""
    way = str(items[0])[1:]
    values = [str(item) for item in items[1:] if not isinstance(item, list)]
    through = values.pop(0) if way in ROUTES_WITH_THROUGH and values else ""
    count = len(values)
    if len(values) >= 2 and values[-2] == ":of" and values[-1].isdigit():
        count, values = int(values[-1]), values[:-2]
    return {"way": way, "through": through, "names": values, "of": count}


def fact_json(line: str) -> dict[str, Any]:
    """One line of an answer as a JSON object (see the module text for the rules)."""
    text = line.strip()
    if text.startswith(";"):
        return {"note": text[1:].strip()}
    items = split_fact(text)
    if not items or isinstance(items[0], list):
        return {"line": text}
    head = str(items[0])
    fact: dict[str, Any] = {"fact": head, "args": []}
    index = 1
    while index < len(items):
        item = items[index]
        index += 1
        if isinstance(item, list):
            if item and _is_keyword(item[0]):
                tag = str(item[0])[1:]
                if tag in ROUTE_WAYS:
                    fact.setdefault("names", []).append(route_json(item))
                else:
                    fact.setdefault(tag, []).extend(_value(member) for member in item[1:])
            else:
                fact["args"].append(_value(item))
        elif _is_keyword(item):
            key = item[1:]
            if ("", key) in LIST_KEYS or (head, key) in LIST_KEYS:
                values = []
                while index < len(items) and not isinstance(items[index], list) and not _is_keyword(items[index]):
                    values.append(_scalar(items[index]))
                    index += 1
                fact[key] = values
            elif index < len(items) and not _is_keyword(items[index]) and not _tagged(items[index]):
                fact[key] = _value(items[index])
                index += 1
            else:
                fact[key] = True
        else:
            fact["args"].append(_scalar(item))
    return fact


def _tagged(item: object) -> bool:
    return isinstance(item, list) and bool(item) and _is_keyword(item[0])


def _whole_facts(text: str) -> list[str]:
    """The lines of an answer, a fact whose quoted text holds line breaks (the message of a failed command)
    kept as one: a line continues while a quote is open."""
    found: list[str] = []
    held = ""
    for line in text.splitlines():
        held = f"{held}\n{line}" if held else line
        if _quotes_closed(held):
            found.append(held)
            held = ""
    if held:
        found.append(held)
    return found


def _quotes_closed(text: str) -> bool:
    inside, j = False, 0
    while j < len(text):
        if text[j] == "\\" and inside:
            j += 2
            continue
        if text[j] == '"':
            inside = not inside
        j += 1
    return not inside


def facts_json(text: str) -> list[dict[str, Any]]:
    """Every fact of an answer as a JSON object, in the order printed."""
    return [fact_json(line) for line in _whole_facts(text) if line.strip()]


def with_error(document: dict[str, Any], fallback: Optional[tuple[str, str]]) -> dict[str, Any]:
    """The document of a call, with `error` as its first key when the call failed as a whole: the first error
    fact of its facts, the error of a `usages` answer, or the fallback (code, message) when the answer is
    empty. A file of an `impact` call that is not in the graph carries its own error and is not one of these.
    Code checks `answer.error` and nothing else."""
    error: Optional[dict[str, str]] = None
    facts = document.get("facts")
    if isinstance(facts, list):
        first = next((fact for fact in facts if isinstance(fact, dict) and fact.get("fact") == "error"), None)
        if first is not None:
            args = [str(arg) for arg in first.get("args", [])]
            error = {"code": args[0] if args else "error", "message": " ".join(args[1:])}
        elif not facts and fallback is not None:
            error = {"code": fallback[0], "message": fallback[1]}
    usages = document.get("usages")
    if error is None and isinstance(usages, dict) and "error" in usages:
        error = {"code": str(usages["error"]), "message": str(usages.get("symbol", ""))}
    return document if error is None else {"error": error, **document}


def render(document: dict[str, Any]) -> str:
    """A JSON document as the commands print it: one line, keys in the order built, ASCII only."""
    return json.dumps(document, separators=(",", ":"))
