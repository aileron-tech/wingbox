"""`wingbox learn`: who runs it, the task for a coding agent, its answer, and what is kept in `.wingbox/`.

Wingbox calls no model. Run by a coding agent, `learn` prints a task: the
closest style of the part, its layers, and the folders it cannot place with
real files and imports; the agent reads the files and answers in a fixed
S-expression format that `learn --apply` validates and records. Run by a
person in a terminal, `learn` proposes the deterministic learning of
`align --learn` and asks before writing. Pure functions; the command line
reads and writes the files.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NamedTuple, Optional

from .library import Library
from .sexp import keyword, parse, positional, q

# Variables a coding agent sets for the commands it runs, checked in each assistant's own code:
# Claude Code sets CLAUDECODE=1 for its Bash tool; Gemini CLI sets GEMINI_CLI=1 in its shell
# execution service. Other assistants are recognised by their commands not having a terminal.
AGENT_VARIABLES = (("CLAUDECODE", "1", "Claude Code"), ("GEMINI_CLI", "1", "Gemini CLI"))
LEARNED_FILE = ".wingbox/learned.sexp"
MAX_FOLDERS = 25   # folders asked per part, most rule breaks first
# What the agent does when the code does not decide: it asks the person it works for, then answers.
OPERATOR_RULE = (
    "If you cannot decide from the code (for example whether a folder is a public API or an internal "
    "helper), ask the operator in your chat and pass the answer on, with :because \"operator: <their words>\"."
)
ANSWER_FORMAT = (
    "(style <part> <style-name>)",
    '(layer <part> <folder> <layer-name> :because "<one sentence citing the files read>")',
    '(unknown <part> <folder> :because "<what is missing to decide>")',
)


class Mode(NamedTuple):
    mode: str       # agent | human
    because: str    # --agent, CLAUDECODE=1, not a terminal, terminal...
    agent: str      # the assistant's name when an environment variable names it, else ""


def learn_mode(flags: tuple[str, ...], environ: Mapping[str, str], stdin_tty: bool, stdout_tty: bool) -> Mode:
    """Explicit flags first, then the variables an agent sets, then: no terminal means an agent."""
    if "--agent" in flags:
        return Mode("agent", "--agent", _agent_name(environ))
    if "--human" in flags:
        return Mode("human", "--human", "")
    for name, value, assistant in AGENT_VARIABLES:
        if environ.get(name) == value:
            return Mode("agent", f"{name}={value}", assistant)
    if not (stdin_tty and stdout_tty):
        return Mode("agent", "stdin or stdout is not a terminal", "")
    return Mode("human", "terminal", "")


def _agent_name(environ: Mapping[str, str]) -> str:
    return next((assistant for name, value, assistant in AGENT_VARIABLES if environ.get(name) == value), "")


@dataclass(frozen=True)
class FolderInfo:
    folder: str
    files: tuple[str, ...]          # 2-3 real files, repository-relative
    imports: tuple[tuple[str, int], ...]       # layer -> count of imports from the folder
    imported_by: tuple[tuple[str, int], ...]   # layer -> count of imports into the folder
    guess: str                      # layer from names and patterns, or ""
    weak: str                       # "" for an unplaced folder, else how the style matched it
    breaks: int = 0                 # boundary violations and candidates its files take part in


@dataclass(frozen=True)
class PartTask:
    part: str
    style: str
    score: float
    runner_up: str
    runner_up_score: float
    layers: tuple[tuple[str, str], ...]        # layer -> meaning
    placed: tuple[tuple[str, tuple[str, ...]], ...]   # layer -> folders the style places
    folders: tuple[FolderInfo, ...]
    known: tuple[tuple[str, str, str], ...]    # (folder, layer, because) learned before
    more: int = 0                              # folders left out by the cap


def _counts(pairs: tuple[tuple[str, int], ...]) -> str:
    return " ".join(f"{layer} {count}" for layer, count in pairs)


def task_facts(tasks: tuple[PartTask, ...], apply_command: str) -> list[str]:
    facts: list[str] = []
    for task in tasks:
        facts.append(f"(learn-part {q(task.part)} :style {task.style} :score {task.score:.2f} "
                     f":runner-up {task.runner_up} :runner-up-score {task.runner_up_score:.2f})")
        facts += [f"(learn-layer {q(task.part)} {layer} :means {q(meaning)})" for layer, meaning in task.layers]
        facts += [f"(learn-placed {q(task.part)} {layer} {q(' '.join(folders))})" for layer, folders in task.placed]
        facts += [f"(learn-known {q(task.part)} {q(folder)} {layer} :because {q(because)})" for folder, layer, because in task.known]
        if not task.folders:
            facts.append(f"(learn-nothing {q(task.part)} :reason {q('every folder of the part is placed by the style or learned before')})")
        for info in task.folders:
            weak = f" :weak {q(info.weak)}" if info.weak else ""
            breaks = f" :breaks {info.breaks}" if info.breaks else ""
            facts.append(
                f"(learn-folder {q(task.part)} {q(info.folder)} :files {q(' '.join(info.files))} "
                f":imports {q(_counts(info.imports))} :imported-by {q(_counts(info.imported_by))} "
                f":guess {info.guess or '-'}{weak}{breaks})"
            )
        if task.more:
            facts.append(f"(learn-more {q(task.part)} :folders {task.more})")
    question = "For each listed folder: which layer of the part's style does it belong to? Confirm or change the style."
    facts.append(f"(learn-question {q(question)})")
    for rule in ("Read the listed files before answering.", "Use only the layer names given for the part.",
                 OPERATOR_RULE, "Never guess; answer unknown only when the operator does not know either."):
        facts.append(f"(learn-rule {q(rule)})")
    facts += [f"(learn-answer-format {q(line)})" for line in ANSWER_FORMAT]
    facts.append(f"(learn-apply {q(apply_command)})")
    return facts


def task_text(facts: list[str]) -> str:
    """The task for people and agents that read text."""
    lines: list[str] = []
    for fact in facts:
        t = parse(fact)
        head = t[0]
        if head == "learn-part":
            lines += ["", f"Part {t[1]}: closest style {keyword(t, 'style')} {keyword(t, 'score')} "
                          f"(runner-up {keyword(t, 'runner-up')} {keyword(t, 'runner-up-score')}). Layers:"]
        elif head == "learn-layer":
            lines.append(f"  {t[2]}: {keyword(t, 'means')}")
        elif head == "learn-placed":
            lines.append(f"  already placed by the style: {t[2]} = {t[3]}")
        elif head == "learn-nothing":
            lines.append(f"  nothing to ask: {keyword(t, 'reason')}")
        elif head == "learn-known":
            lines.append(f"  known: {t[2]}/ -> {t[3]} ({keyword(t, 'because')}); not asked again")
        elif head == "learn-more":
            lines.append(f"  ... {keyword(t, 'folders')} more folders; answer these first, then run learn again")
        elif head == "learn-folder":
            weak = f"; placed weakly: {keyword(t, 'weak')}" if keyword(t, "weak") else ""
            weak += f"; takes part in {keyword(t, 'breaks')} rule breaks" if keyword(t, "breaks") else ""
            lines.append(f"  folder {t[2]}/ - files: {keyword(t, 'files')}; imports: {keyword(t, 'imports') or 'none'}; "
                         f"imported by: {keyword(t, 'imported-by') or 'none'}; guess from its name: {keyword(t, 'guess')}{weak}")
        elif head == "learn-question":
            lines += ["", "Question: " + t[1]]
        elif head == "learn-rule":
            lines.append("Rule: " + t[1])
        elif head == "learn-answer-format":
            if not any(line.startswith("Answer with") for line in lines):
                lines += ["", "Answer with these S-expressions only, one per line:"]
            lines.append("  " + t[1])
        elif head == "learn-apply":
            lines += ["", "Submit the answer: " + t[1]]
    return "\n".join(lines).lstrip("\n") + "\n"


@dataclass(frozen=True)
class Entry:
    kind: str      # style | layer | unknown
    part: str
    folder: str
    value: str     # the style name or the layer name ("" for unknown)
    because: str


@dataclass(frozen=True)
class AnswerOk:
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class AnswerErr:
    errors: tuple[str, ...]


def parse_answer(
    text: str, parts: Mapping[str, tuple[str, frozenset[str]]], library: Library,
) -> AnswerOk | AnswerErr:
    """Validate an answer strictly. `parts`: part -> (current style, folders of the part)."""
    entries: list[Entry] = []
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if not (line.startswith("(") and line.endswith(")")) or line.count('"') % 2:
            errors.append(f"line {number}: not one complete S-expression: {line[:80]}")
            continue
        tokens = parse(line)
        head = tokens[0] if tokens else ""
        values = positional(tokens)
        if head not in ("style", "layer", "unknown"):
            errors.append(f"line {number}: unknown form {head or '(empty)'}; use style, layer or unknown")
            continue
        wanted = {"style": 2, "layer": 3, "unknown": 2}[head]
        if len(values) != wanted:
            errors.append(f"line {number}: ({head} ...) needs {wanted} values before the keywords, got {len(values)}")
            continue
        because = keyword(tokens, "because", "") or ""
        if head != "style" and not because:
            errors.append(f"line {number}: :because is missing")
        part = values[0]
        if part not in parts:
            errors.append(f"line {number}: {part} is not a part with a style; parts: {', '.join(sorted(parts))}")
            continue
        if head == "style":
            entries.append(Entry("style", part, "", values[1], because))
            if library.style(values[1]) is None:
                errors.append(f"line {number}: {values[1]} is not a style of the library")
        else:
            entries.append(Entry(head, part, values[1], values[2] if head == "layer" else "", because))
    for part in {e.part for e in entries}:
        styles = [e for e in entries if e.part == part and e.kind == "style"]
        if len(styles) > 1:
            errors.append(f"part {part}: more than one (style ...)")
        style_id = styles[0].value if styles else parts[part][0]
        style = library.style(style_id) or library.style(parts[part][0])
        folders = [e for e in entries if e.part == part and e.kind != "style"]
        for entry in folders:
            if entry.folder not in parts[part][1]:
                errors.append(f"{entry.folder} is not a folder of part {part}")
            if entry.kind == "layer" and style is not None and style.layer(entry.value) is None:
                errors.append(f"{entry.value} is not a layer of {style.id}; layers: {', '.join(l.id for l in style.layers)}")
        seen: set[str] = set()
        for entry in folders:
            if entry.folder in seen:
                errors.append(f"part {part}: folder {entry.folder} twice")
            seen.add(entry.folder)
    return AnswerErr(tuple(dict.fromkeys(errors))) if errors else AnswerOk(tuple(entries))


def learned_lines(entries: tuple[Entry, ...], today: str, mode: Mode, styles: Mapping[str, tuple[str, float]],
                  files: Mapping[tuple[str, str], int]) -> list[str]:
    """The accepted answer for .wingbox/learned.sexp: a header per part, then its entries."""
    lines: list[str] = []
    for part in sorted({e.part for e in entries}):
        style, score = styles.get(part, ("-", 0.0))
        agent = f" :agent {q(mode.agent)}" if mode.agent else ""
        lines.append(f"(learned-answer {q(part)} :at {today} :mode {mode.mode}{agent} :style {style} :score {score:.2f})")
        for e in (e for e in entries if e.part == part):
            if e.kind == "style":
                lines.append(f"(learned-style {q(part)} {e.value} :because {q(e.because)})")
            else:
                lines.append(f"(learned-entry {q(part)} {q(e.folder)} {e.value or 'unknown'} :because {q(e.because)} "
                             f":files {files.get((part, e.folder), 0)})")
    return lines


def read_learned(text: str) -> dict[tuple[str, str], tuple[str, str, int]]:
    """(part, folder) -> (layer, because, files at the time), the last entry winning."""
    found: dict[tuple[str, str], tuple[str, str, int]] = {}
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] == "learned-entry":
            found[(tokens[1], tokens[2])] = (tokens[3], keyword(tokens, "because", "") or "", int(keyword(tokens, "files", "0") or 0))
    return found


def changed_a_lot(before: int, now: int) -> bool:
    """Most of a folder's files are new since it was learned: it has more than twice as many."""
    return before > 0 and now > 2 * before
