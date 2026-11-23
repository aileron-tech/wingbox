"""The answer of `impact` and its three printers: the compact S-expression (default), JSON and text.

`impact` builds one ImpactFound (or ImpactMissing) per asked file; every printer reads that same
value, so the counts, the files and the reasons of the three forms cannot differ.

The compact form names each thing once:

    (impact src/core/util.rs :direct 2 :total 3
     (1 src/billing/invoice.rs (:use limit)
        src/billing/report.rs)
     (2
      (:through src/billing/invoice.rs
       src/api/handler.rs :more 1 (:use Invoice)))
     (read-first src/core/util.rs src/billing/invoice.rs src/billing/report.rs))

One form per asked file. Rows are grouped by depth and, below depth 1, by the file they are reached
through. A bare path is a plain import; a path is followed by `:via <relation>` when the relation is
not an import, `:more N` when it depends on N other files of the previous depth too, and the lists
that say which names it takes. A name that matches nothing gives `(error no-match <file> ...)` in its
place; a file of the repository that is not in the graph gives `(error not-in-graph <file> :reason ...)`.

Pure functions: values in, text out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union

if __package__:
    from .fact_json import atom, fact_json, route_json, split_fact
else:
    from fact_json import atom, fact_json, route_json, split_fact

DEFAULT_RELATION = "imports"
# Text for people: an answer names at most this many dependent files and read-first files unless --all.
ANSWER_FILE_LIMIT = 200
READ_FIRST_LIMIT = 10


@dataclass(frozen=True)
class GraphHeader:
    """What is known of the graph an answer was read from."""

    built: str             # when the graph file was written, UTC, 2026-01-31T09:15:00Z
    files: int             # files in the graph
    stale: Optional[int]   # changed files the graph is known to miss; None when nothing checked it
    # (language, files) of the files of the repository in languages Wingbox has no parser for
    not_parsed: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ImpactLine:
    """One dependent and why it is listed."""

    file: str
    via: str                  # relation of its edge to the file it is reached through
    through: str              # that file; "" at depth 1 (it is the asked file)
    more: int                 # other files of the previous depth it depends on as well
    reasons: tuple[str, ...]  # the name lists of the edge as the graph facts carry them: (:use A B)
    text: str                 # the same reason in words


@dataclass(frozen=True)
class Advice:
    fact: str   # the advice fact
    text: str   # its line for people


@dataclass(frozen=True)
class ImpactFound:
    file: str                                   # the asked file, relative to the graph root
    max_depth: int
    levels: tuple[tuple[ImpactLine, ...], ...]  # dependents by depth, depth 1 first
    read_first: tuple[str, ...]                 # the file and its direct dependents
    note_fact: str = ""                         # (impact-ambiguous ...) when several modules match
    note_text: str = ""
    advice: tuple[Advice, ...] = ()

    @property
    def direct(self) -> int:
        return len(self.levels[0]) if self.levels else 0

    @property
    def total(self) -> int:
        return sum(len(level) for level in self.levels)


@dataclass(frozen=True)
class ImpactMissing:
    asked: str                         # the file as it was asked
    suggested_files: tuple[str, ...]   # files of the graph with a similar name
    human: str                         # the line the text form prints for it
    # Set when the file exists in the repository and is not in the graph: not-parsed-language (Wingbox has
    # no parser for it), skipped-language (its parser is not installed), excluded (a skipped folder, a path
    # Git ignores), not-source (a document, a data file). language is the display name, "" for not-source.
    reason: str = ""
    language: str = ""


ImpactAnswer = Union[ImpactFound, ImpactMissing]


def missing_fact(answer: ImpactMissing) -> str:
    if answer.reason:
        language = f' :language "{answer.language}"' if answer.language else ""
        return f"(error not-in-graph {atom(answer.asked)} :reason {answer.reason}{language})"
    tail = f" :suggestions {' '.join(atom(path) for path in answer.suggested_files)}" if answer.suggested_files else ""
    return f"(error no-match {atom(answer.asked)}{tail})"


def header_sexp(header: GraphHeader) -> str:
    stale = "unknown" if header.stale is None else str(header.stale)
    not_parsed = ""
    if header.not_parsed:
        not_parsed = " :not-parsed (" + " ".join(f'"{language}" {files}' for language, files in header.not_parsed) + ")"
    return f"(graph :built {header.built} :files {header.files} :stale {stale}{not_parsed})"


# ---------------------------------------------------------------------------
# Compact S-expression
# ---------------------------------------------------------------------------


def _row(line: ImpactLine) -> str:
    parts = [atom(line.file)]
    if line.via != DEFAULT_RELATION:
        parts.append(f":via {line.via}")
    if line.more:
        parts.append(f":more {line.more}")
    parts.extend(line.reasons)
    return " ".join(parts)


def found_sexp(answer: ImpactFound) -> str:
    out = [f"(impact {atom(answer.file)} :direct {answer.direct} :total {answer.total}"]
    for depth, level in enumerate(answer.levels, start=1):
        if depth == 1:
            out.append(" (1 " + "\n    ".join(_row(line) for line in level) + ")")
            continue
        groups: dict[str, list[ImpactLine]] = {}
        for line in level:
            groups.setdefault(line.through, []).append(line)
        out.append(f" ({depth}")
        for through, members in groups.items():
            out.append(f"  (:through {atom(through)}\n   " + "\n   ".join(_row(line) for line in members) + ")")
        out[-1] += ")"
    out.append(" (read-first " + " ".join(atom(path) for path in answer.read_first) + "))")
    lines = ([answer.note_fact] if answer.note_fact else []) + out + [item.fact for item in answer.advice]
    return "\n".join(lines) + "\n"


def impact_sexp(answers: Sequence[ImpactAnswer], header: Optional[GraphHeader]) -> str:
    """The default form: one form per asked file in the order asked, after the graph fact when there is one."""
    parts = [header_sexp(header) + "\n"] if header is not None else []
    for answer in answers:
        if isinstance(answer, ImpactMissing):
            parts.append(missing_fact(answer) + "\n")
        else:
            parts.append(found_sexp(answer))
    return "".join(parts)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _row_json(line: ImpactLine, depth: int) -> dict[str, Any]:
    return {
        "file": line.file,
        "depth": depth,
        "via": line.via,
        "through": line.through or None,
        "more": line.more,
        "reasons": [route_json(items) for items in (split_fact(reason) or [] for reason in line.reasons) if items],
    }


def answer_json(answer: ImpactAnswer) -> dict[str, Any]:
    if isinstance(answer, ImpactMissing):
        if answer.reason:
            outside: dict[str, Any] = {"file": answer.asked, "error": "not-in-graph", "reason": answer.reason}
            if answer.language:
                outside["language"] = answer.language
            return outside
        return {"file": answer.asked, "error": "no-match", "suggestions": list(answer.suggested_files)}
    document: dict[str, Any] = {
        "file": answer.file,
        "direct": answer.direct,
        "total": answer.total,
        "rows": [_row_json(line, depth) for depth, level in enumerate(answer.levels, start=1) for line in level],
        "read_first": list(answer.read_first),
        "advice": [fact_json(item.fact) for item in answer.advice],
    }
    if answer.note_fact:
        document["note"] = fact_json(answer.note_fact)
    return document


def header_json(header: Optional[GraphHeader]) -> Optional[dict[str, Any]]:
    if header is None:
        return None
    document: dict[str, Any] = {"built": header.built, "files": header.files, "stale": header.stale}
    if header.not_parsed:
        document["not_parsed"] = [{"language": language, "files": files} for language, files in header.not_parsed]
    return document


def impact_json(answers: Sequence[ImpactAnswer], header: Optional[GraphHeader]) -> dict[str, Any]:
    """{"graph": {...} or null, "impact": [one object per asked file, in the order asked]}; every row has the
    same keys, and nothing is cut."""
    return {"graph": header_json(header), "impact": [answer_json(answer) for answer in answers]}


# ---------------------------------------------------------------------------
# Text for people
# ---------------------------------------------------------------------------


def _block(title: str, rows: Sequence[tuple[str, str]]) -> list[str]:
    width = max(len(name) for name, _detail in rows)
    return [title] + [f"  {name:<{width}}   {detail}" if detail else f"  {name}" for name, detail in rows]


def found_text(answer: ImpactFound, show_all: bool) -> str:
    """The answer as people read it. The counts are complete; the lists stop at the file limits unless show_all."""
    lines: list[str] = []
    if answer.note_text:
        lines += [answer.note_text, ""]
    hops = "hop" if answer.max_depth == 1 else "hops"
    lines.append(f"Impact of changing {answer.file}")
    lines.append(f"  {'direct dependents':<20}{answer.direct}")
    lines.append(f"  {f'within {answer.max_depth} {hops}':<20}{answer.total}")
    lines.append("")
    if not answer.levels:
        lines.append("No other module depends on it.")
        return "\n".join(lines) + "\n"
    # Nearest dependents first: the limit is spent on depth 1 before depth 2.
    budget = answer.total if show_all else ANSWER_FILE_LIMIT
    shown = 0
    for depth, level in enumerate(answer.levels, start=1):
        rows = level[:budget]
        budget -= len(rows)
        shown += len(rows)
        if rows:
            lines += _block(f"Depth {depth}", [(line.file, line.text) for line in rows]) + [""]
    hidden = answer.total - shown
    if hidden:
        lines += [f"  ... {hidden} more {'file' if hidden == 1 else 'files'}: rerun with --all", ""]
    count = len(answer.read_first)
    read_first = answer.read_first if show_all else answer.read_first[: READ_FIRST_LIMIT + 1]
    lines += _block(f"Read first ({count} {'file' if count == 1 else 'files'})", [(path, "") for path in read_first])
    if count > len(read_first):
        lines.append(f"  ... {count - len(read_first)} more: the other files of Depth 1")
    if answer.advice:
        lines += ["", "Advice"] + [item.text for item in answer.advice]
    return "\n".join(lines) + "\n"


def impact_text(answers: Sequence[ImpactAnswer], show_all: bool) -> str:
    """One block per asked file in the order asked, a blank line between two blocks."""
    blocks = [
        answer.human + "\n" if isinstance(answer, ImpactMissing) else found_text(answer, show_all)
        for answer in answers
    ]
    return "\n".join(blocks)
