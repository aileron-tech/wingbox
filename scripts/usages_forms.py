"""The answer of `usages` and its three printers: the compact S-expression (default), JSON and text.

The compact form names the symbol and each file once; the uses of a file are grouped by kind, in
the order of their first use, each kind followed by its line:column positions:

    (usages Invoice cls :at app/models.py:4 :uses 4 :files 2
     (app/api.py import 1:24 call 9:12 14:8)
     (app/report.py type 7:20))

The head says the symbol, the kind of its definition and where it is defined (file:line). A kind
without positions (`(lib/a.pas reference)`) is a file of a language that has no use positions.
`:unresolved N` is said only when N method calls have a receiver whose type is not in sight. A name
with several definitions gives `(usages-ambiguous Name :definitions 2 (<file>::Name cls 4) ...)`:
each candidate is written as it can be asked.

Pure functions: values in, text out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence, Union

if __package__:
    from .fact_json import atom
else:
    from fact_json import atom

# Text for people: an answer lists at most this many uses unless --all.
USAGES_LIMIT = 200


@dataclass(frozen=True)
class Site:
    """One place that uses the symbol; line 0 when its language has no use positions."""

    file: str
    line: int
    column: int
    kind: str


@dataclass(frozen=True)
class UsagesFound:
    symbol: str
    kind: str            # kind of the definition as the graph writes it: fn, cls, ...
    what: str            # the same for people: function, method of Invoice, ...
    file: str            # file of the definition
    line: int
    sites: tuple[Site, ...]   # sorted by file and position
    unresolved: int      # how many of the sites are unresolved calls


@dataclass(frozen=True)
class Candidate:
    file: str
    kind: str
    word: str   # the kind for people
    line: int


@dataclass(frozen=True)
class UsagesAmbiguous:
    symbol: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class UsagesMissing:
    asked: str
    fact: str                      # the (error no-match ...) line
    suggestions: tuple[str, ...]


UsagesAnswer = Union[UsagesFound, UsagesAmbiguous, UsagesMissing]


def _counts(answer: UsagesFound) -> tuple[int, int]:
    """(resolved uses, files that hold one)."""
    return (
        len(answer.sites) - answer.unresolved,
        len({site.file for site in answer.sites if site.kind != "unresolved-call"}),
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def usages_sexp(answer: UsagesAnswer) -> str:
    """The default form, complete."""
    if isinstance(answer, UsagesMissing):
        return answer.fact + "\n"
    if isinstance(answer, UsagesAmbiguous):
        rows = [f" ({atom(f'{c.file}::{answer.symbol}')} {c.kind} {c.line})" for c in answer.candidates]
        return "\n".join([f"(usages-ambiguous {atom(answer.symbol)} :definitions {len(rows)}"] + rows) + ")\n"
    uses, files = _counts(answer)
    head = (f"(usages {atom(answer.symbol)} {answer.kind} :at {atom(f'{answer.file}:{answer.line}')}"
            f" :uses {uses} :files {files}" + (f" :unresolved {answer.unresolved}" if answer.unresolved else ""))
    by_file: dict[str, dict[str, list[str]]] = {}
    for site in answer.sites:
        kinds = by_file.setdefault(site.file, {})
        positions = kinds.setdefault(site.kind, [])
        if site.line:
            positions.append(f"{site.line}:{site.column}")
    rows = [
        f" ({atom(file)} " + " ".join(" ".join([kind] + positions) for kind, positions in kinds.items()) + ")"
        for file, kinds in by_file.items()
    ]
    return "\n".join([head] + rows) + ")\n"


def usages_json(answer: UsagesAnswer) -> dict[str, Any]:
    """{"usages": {...}}: the definition, the counts and one row per use; nothing is cut."""
    if isinstance(answer, UsagesMissing):
        return {"usages": {"symbol": answer.asked, "error": "no-match", "suggestions": list(answer.suggestions)}}
    if isinstance(answer, UsagesAmbiguous):
        return {"usages": {"symbol": answer.symbol, "error": "ambiguous", "candidates": [
            {"target": f"{c.file}::{answer.symbol}", "file": c.file, "kind": c.kind, "line": c.line} for c in answer.candidates
        ]}}
    uses, files = _counts(answer)
    return {"usages": {
        "symbol": answer.symbol, "kind": answer.kind, "file": answer.file, "line": answer.line,
        "uses": uses, "files": files, "unresolved": answer.unresolved,
        "rows": [{"file": site.file, "line": site.line or None, "column": site.column if site.line else None, "kind": site.kind}
                 for site in answer.sites],
    }}


def usages_text(answer: UsagesAnswer, show_all: bool) -> str:
    """The answer as people read it; the list stops at USAGES_LIMIT uses unless show_all."""
    if isinstance(answer, UsagesMissing):
        return answer.fact + "\n"
    if isinstance(answer, UsagesAmbiguous):
        lines = [f"{len(answer.candidates)} definitions of {answer.symbol}; ask for one of them:"]
        width = max(len(c.file) + len(answer.symbol) + 2 for c in answer.candidates)
        for c in answer.candidates:
            name = f"{c.file}::{answer.symbol}"
            lines.append(f"  {name:<{width}}   {c.word}, line {c.line}")
        return "\n".join(lines) + "\n"
    uses, files = _counts(answer)
    lines = [f"Usages of {answer.symbol} ({answer.what}, {answer.file}:{answer.line})"]
    summary = f"{_plural(uses, 'use')} in {_plural(files, 'file')}"
    if answer.unresolved:
        summary += f"; {_plural(answer.unresolved, 'unresolved call')} (receiver type not in sight)"
    lines.append(f"  {summary}")
    if not answer.sites:
        return "\n".join(lines) + "\n"
    lines.append("")
    shown: Sequence[Site] = answer.sites if show_all else answer.sites[:USAGES_LIMIT]
    current = ""
    for site in shown:
        if site.file != current:
            current = site.file
            lines.append(current)
        where = f"{site.line}:{site.column}" if site.line else "-"
        detail = site.kind if site.line else "reference (no use positions for this language)"
        lines.append(f"  {where}   {detail}")
    hidden = len(answer.sites) - len(shown)
    if hidden:
        lines.append(f"  ... {hidden} more {'use' if hidden == 1 else 'uses'}: rerun with --all")
    return "\n".join(lines) + "\n"
