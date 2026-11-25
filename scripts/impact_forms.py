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
not an import, `:more N` when it depends on N other files of the previous depth too, `:basis declared`
or `:basis inferred` when the source text does not state the edge, and the lists that say which names
it takes. A name that matches nothing gives `(error no-match <file> ...)` in its
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
    # What the edge rests on: "" when the source text states it (an import, an include found by the rules
    # of the language), `declared` (the config file), `inferred` (a marker, a build file, a launch call, an
    # include found without the folders the compiler was given).
    basis: str = ""
    # Filled for `--summary` only: generated (made from the file or started by it), test, entry-point (a file
    # nothing else depends on) or other.
    kind: str = ""


@dataclass(frozen=True)
class Advice:
    fact: str   # the advice fact
    text: str   # its line for people


@dataclass(frozen=True)
class Affected:
    """The build targets and the tests of the build files that the file and its listed dependents reach."""

    targets: tuple[str, ...]
    tests: tuple[str, ...]
    # Targets not among `targets` whose source list was not all read (a variable, a generator expression):
    # the file may be one of their sources.
    unread: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImpactFound:
    file: str                                   # the asked file, relative to the graph root
    max_depth: int
    levels: tuple[tuple[ImpactLine, ...], ...]  # dependents by depth, depth 1 first
    read_first: tuple[str, ...]                 # the file and its direct dependents
    note_fact: str = ""                         # (impact-ambiguous ...), (impact-paired-header ...), (impact-no-includers ...)
    note_text: str = ""
    advice: tuple[Advice, ...] = ()
    only: str = ""                              # "tests": the rows are the test files of the whole list
    affected: Optional[Affected] = None         # with only "tests", in a graph that has build targets or tests

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
    if line.basis:
        parts.append(f":basis {line.basis}")
    parts.extend(line.reasons)
    return " ".join(parts)


def found_sexp(answer: ImpactFound) -> str:
    only = f" :only {answer.only}" if answer.only else ""
    out = [f"(impact {atom(answer.file)} :direct {answer.direct} :total {answer.total}{only}"]
    for depth, level in enumerate(answer.levels, start=1):
        if not level:
            continue   # `--only` kept no row of this depth
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
    lines = ([answer.note_fact] if answer.note_fact else []) + out
    if answer.affected is not None:
        for fact, names in (("affected-targets", answer.affected.targets), ("affected-tests", answer.affected.tests)):
            lines.append("(" + " ".join([fact, atom(answer.file), *(atom(name) for name in names)]) + ")")
        if answer.affected.unread:
            lines.append("(" + " ".join(["unread-targets", atom(answer.file), *(atom(name) for name in answer.affected.unread)]) + ")")
    lines += [item.fact for item in answer.advice]
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
    row: dict[str, Any] = {
        "file": line.file,
        "depth": depth,
        "via": line.via,
        "through": line.through or None,
        "more": line.more,
        "reasons": [route_json(items) for items in (split_fact(reason) or [] for reason in line.reasons) if items],
    }
    if line.basis:
        # Only for an edge the source text does not state: the rows of every other edge keep their six keys.
        row["basis"] = line.basis
    return row


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
    if answer.only:
        document["only"] = answer.only
    if answer.affected is not None:
        document["targets"] = list(answer.affected.targets)
        document["tests"] = list(answer.affected.tests)
        if answer.affected.unread:
            document["unread_targets"] = list(answer.affected.unread)
    return document


def header_json(header: Optional[GraphHeader]) -> Optional[dict[str, Any]]:
    if header is None:
        return None
    document: dict[str, Any] = {"built": header.built, "files": header.files, "stale": header.stale}
    if header.not_parsed:
        document["not_parsed"] = [{"language": language, "files": files} for language, files in header.not_parsed]
    return document


@dataclass(frozen=True)
class Changed:
    """A function a diff changes, and the other files that use it."""

    symbol: str                 # <file>::<name>
    line: int
    used_by: tuple[str, ...]
    affected: Optional[Affected] = None   # with only "tests": the targets and tests of its file and its users


def changed_fact(item: Changed) -> str:
    built = ""
    if item.affected is not None:
        built = (f" :targets ({' '.join(atom(name) for name in item.affected.targets)})"
                 f" :tests ({' '.join(atom(name) for name in item.affected.tests)})")
    return f"(changed {atom(item.symbol)} :line {item.line} :used-by ({' '.join(atom(path) for path in item.used_by)}){built})"


def changed_json(item: Changed) -> dict[str, Any]:
    document: dict[str, Any] = {"symbol": item.symbol, "line": item.line, "used_by": list(item.used_by)}
    if item.affected is not None:
        document["targets"] = list(item.affected.targets)
        document["tests"] = list(item.affected.tests)
    return document


def impact_json(
    answers: Sequence[ImpactAnswer], header: Optional[GraphHeader], changed: Optional[Sequence[Changed]] = None,
) -> dict[str, Any]:
    """{"graph": {...} or null, "impact": [one object per asked file, in the order asked]}; every row has the
    same keys, and nothing is cut. With changed (`impact --diff`), "changed" lists the changed functions."""
    document: dict[str, Any] = {"graph": header_json(header), "impact": [answer_json(answer) for answer in answers]}
    if changed is not None:
        document["changed"] = [changed_json(item) for item in changed]
    return document


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
    lines.append(f"Impact of changing {answer.file}" + (f" ({answer.only} only)" if answer.only else ""))
    lines.append(f"  {'direct dependents':<20}{answer.direct}")
    lines.append(f"  {f'within {answer.max_depth} {hops}':<20}{answer.total}")
    lines.append("")
    built: list[str] = []
    if answer.affected is not None:
        for title, names in (("Build targets", answer.affected.targets), ("Tests to run", answer.affected.tests)):
            shown_names = names if show_all else names[:ANSWER_FILE_LIMIT]
            rest = f" ... {len(names) - len(shown_names)} more: rerun with --all" if len(names) > len(shown_names) else ""
            built.append(f"{title}: {', '.join(shown_names) if names else 'none'}{rest}")
        if answer.affected.unread:
            built.append(f"Build targets whose sources were not all read: {', '.join(answer.affected.unread)}")
    if not answer.levels:
        # A note already says what the empty list means (a source file nothing includes).
        if not answer.note_text:
            lines.append(f"No {'test file' if answer.only else 'other module'} depends on it.")
        return "\n".join(lines + ([""] + built if built else [])) + "\n"
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
    if built:
        lines += [""] + built
    if answer.advice:
        lines += ["", "Advice"] + [item.text for item in answer.advice]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The short form (`--summary`)
# ---------------------------------------------------------------------------

SUMMARY_FILES = 20     # files named by the short form
SUMMARY_FOLDERS = 8
SUMMARY_TEXT_FILES = 8
_KINDS = ("generated", "test", "entry-point", "other")
_KIND_KEYS = {"generated": "generated", "test": "tests", "entry-point": "entry-points", "other": "other"}


@dataclass(frozen=True)
class Summary:
    """The counts of an impact answer and the few files to look at first."""

    file: str
    direct: int
    total: int
    depth: int
    only: str
    depths: tuple[int, ...]
    kinds: tuple[tuple[str, int], ...]         # (generated, tests, entry-points, other) with their counts
    folders: tuple[tuple[str, int], ...]       # the folders with the most dependents
    elsewhere: int                             # dependents in the folders not named
    first: tuple[str, ...]
    more: int                                  # dependents not named in first
    full: str                                  # the command that prints the complete list
    note_fact: str = ""
    note_text: str = ""
    affected: Optional[Affected] = None


def summary_of(answer: ImpactFound) -> Summary:
    rows = [(depth, line) for depth, level in enumerate(answer.levels, start=1) for line in level]
    counts = {kind: sum(1 for _depth, line in rows if (line.kind or "other") == kind) for kind in _KINDS}
    by_folder: dict[str, int] = {}
    for _depth, line in rows:
        folder = line.file.rsplit("/", 1)[0] if "/" in line.file else "."
        by_folder[folder] = by_folder.get(folder, 0) + 1
    folders = sorted(by_folder.items(), key=lambda item: (-item[1], item[0]))
    named = folders[:SUMMARY_FOLDERS]
    # Nearest depth first; within a depth the generated files, the entry points, the tests, then the others.
    order = {"generated": 0, "entry-point": 1, "test": 2, "other": 3}
    ordered = sorted(rows, key=lambda item: (item[0], order[item[1].kind or "other"], item[1].file))
    first = tuple(line.file for _depth, line in ordered[:SUMMARY_FILES])
    full = f"wingbox impact {answer.file} {answer.max_depth}" + (f" --only {answer.only}" if answer.only else "")
    return Summary(
        answer.file, answer.direct, answer.total, answer.max_depth, answer.only,
        tuple(len(level) for level in answer.levels), tuple((_KIND_KEYS[kind], counts[kind]) for kind in _KINDS),
        tuple(named), sum(count for _folder, count in folders[SUMMARY_FOLDERS:]), first, len(rows) - len(first), full,
        answer.note_fact, answer.note_text, answer.affected,
    )


def _summary_sexp(item: Summary) -> str:
    only = f" :only {item.only}" if item.only else ""
    elsewhere = f" :more {item.elsewhere}" if item.elsewhere else ""
    more = f" :more {item.more}" if item.more else ""
    out = ([item.note_fact] if item.note_fact else []) + [
        f"(impact-summary {atom(item.file)} :direct {item.direct} :total {item.total} :depth {item.depth}{only}",
        f" (depths{''.join(f' {count}' for count in item.depths)})",
        " (kinds " + " ".join(f":{name} {count}" for name, count in item.kinds) + ")",
        " (folders" + "".join(f" ({atom(folder)} {count})" for folder, count in item.folders) + elsewhere + ")",
        " (first" + "".join(f" {atom(path)}" for path in item.first) + more + ")",
    ]
    if item.affected is not None:
        out.append(f" (build :targets {len(item.affected.targets)} :tests {len(item.affected.tests)})")
    out.append(f' (full "{item.full}"))')
    return "\n".join(out) + "\n"


def summary_sexp(answers: Sequence[ImpactAnswer], header: Optional[GraphHeader]) -> str:
    parts = [header_sexp(header) + "\n"] if header is not None else []
    for answer in answers:
        parts.append(missing_fact(answer) + "\n" if isinstance(answer, ImpactMissing) else _summary_sexp(summary_of(answer)))
    return "".join(parts)


def _summary_json(answer: ImpactAnswer) -> dict[str, Any]:
    if isinstance(answer, ImpactMissing):
        return answer_json(answer)
    item = summary_of(answer)
    document: dict[str, Any] = {
        "file": item.file, "summary": True, "direct": item.direct, "total": item.total, "depth": item.depth,
        "depths": list(item.depths), "kinds": {name.replace("-", "_"): count for name, count in item.kinds},
        "folders": [{"folder": folder, "files": count} for folder, count in item.folders],
        "more_in_other_folders": item.elsewhere, "first": list(item.first), "more": item.more, "full": item.full,
    }
    if item.only:
        document["only"] = item.only
    if item.note_fact:
        document["note"] = fact_json(item.note_fact)
    if item.affected is not None:
        document["build"] = {"targets": len(item.affected.targets), "tests": len(item.affected.tests)}
    return document


def changed_summary_fact(item: Changed) -> str:
    built = f" :targets {len(item.affected.targets)} :tests {len(item.affected.tests)}" if item.affected is not None else ""
    return f"(changed-summary {atom(item.symbol)} :line {item.line} :used-by {len(item.used_by)}{built})"


def summary_json(
    answers: Sequence[ImpactAnswer], header: Optional[GraphHeader], changed: Optional[Sequence[Changed]] = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {"graph": header_json(header), "impact": [_summary_json(answer) for answer in answers]}
    if changed is not None:
        document["changed"] = [
            {"symbol": item.symbol, "line": item.line, "used_by": len(item.used_by),
             **({"targets": len(item.affected.targets), "tests": len(item.affected.tests)} if item.affected is not None else {})}
            for item in changed
        ]
    return document


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def summary_text(answers: Sequence[ImpactAnswer]) -> str:
    blocks: list[str] = []
    for answer in answers:
        if isinstance(answer, ImpactMissing):
            blocks.append(answer.human + "\n")
            continue
        item = summary_of(answer)
        kinds = dict(item.kinds)
        what = "test files depend" if item.only else "files depend"
        if item.total == 1:
            what = "test file depends" if item.only else "file depends"
        hops = "hop" if item.depth == 1 else "hops"
        lines = ([item.note_text, ""] if item.note_text else []) + [
            f"{item.total} {what} on {item.file} within {item.depth} {hops} ({item.direct} directly).",
        ]
        if item.total:
            lines.append(
                f"  {_plural(kinds['generated'], 'file', 'files')} made from it or started by it, "
                f"{_plural(kinds['tests'], 'test file', 'test files')}, "
                f"{_plural(kinds['entry-points'], 'entry point', 'entry points')}, "
                f"{_plural(kinds['other'], 'other', 'others')}"
            )
            folders = ", ".join(f"{folder} {count}" for folder, count in item.folders)
            lines.append(f"  By folder: {folders}" + (f", elsewhere {item.elsewhere}" if item.elsewhere else ""))
            shown = item.first[:SUMMARY_TEXT_FILES]
            lines.append("  Look at first:")
            lines += [f"    {path}" for path in shown]
            if item.total > len(shown):
                lines.append(f"    ... {item.total - len(shown)} more")
        if item.affected is not None:
            lines.append(f"  Build targets: {len(item.affected.targets)}; tests to run: {len(item.affected.tests)}")
        lines.append(f"  Complete list: {item.full}")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


def impact_text(answers: Sequence[ImpactAnswer], show_all: bool) -> str:
    """One block per asked file in the order asked, a blank line between two blocks."""
    blocks = [
        answer.human + "\n" if isinstance(answer, ImpactMissing) else found_text(answer, show_all)
        for answer in answers
    ]
    return "\n".join(blocks)
