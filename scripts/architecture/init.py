"""`wingbox init`: the architecture of each part as S-expressions, and the text printer over them.

init_facts() builds the facts (what agents read with --sexp); render_text()
prints the same facts for people. There is no other path to the text.

Forms (see references/sexp-notation.md):

  (arch-part <part> :level <c4 level> :kind <kind> :files <n> :style <style> :state <state>
             :origin detected|config|declared [:score <s> :coverage <c>] [:name <style name>] [:source <citation>])
  (arch-evidence <part> <style> <folder or file pattern> :files <n> :group <group> :match exact|spelling|file [:via <word>])
  (arch-candidate <part> <style> :score <s> :coverage <c> :status candidate|below :source <citation>)
  (arch-overlay <part> <overlay> :score <s> :coverage <c>)
  (arch-layer <part> <layer> <folder> :files <n> :match <step> [:via <word>])
  (arch-declared <part> <file> :read yes :layers <n>) | (arch-declared <part> <file> :read no :reason <why>)
  (arch-baseline <part> <file> :entries <n>)
  (arch-target <part> <layer> <folder> [:block <id>] :present <first real path> | :proposed :source <citation>)
  (advice <part> :id <id> :kind move|fix-violation|missing-folder :target <path> :style <style> :rule <rule> :reason <text> :evidence <text>)
  (advice-none <part> :reason <why>)
  (arch-target-summary <part> :present <n> :proposed <n> :first <folders>)   ; without --details
  (advice-summary <part> :missing-folder <n>)                                 ; without --details
  (arch-not-judged <part> :files <n>)
  (arch-config-written <path>) | (arch-config-kept <path> :reason <why>)
"""
from __future__ import annotations

from typing import Callable, Optional

from .library import Library, Style
from .plan import DECLARED, NO_STYLE, UNDETERMINED, PartPlan, RepositoryPlan
from .sexp import keyword, parse, positional, q
from .target import target_layout
from . import advice as _advice

_EVIDENCE_SHOWN = 4


# What comes after init. People do not edit the config by hand: `wingbox learn` settles the open points.
NEXT_STEP = (
    "next: wingbox learn settles the open points (run by a coding agent it is the agent's task, asking you "
    "when the code does not tell; in a terminal it asks you), then wingbox build"
)

def _name(part: str) -> str:
    return q(part)


def _style_label(style: Style) -> str:
    author = style.sources[0].author if style.sources else ""
    return f"{style.id} ({author})" if author else style.id


def part_facts(part: PartPlan, library: Library, paths: tuple[str, ...] = (), details: bool = True) -> list[str]:
    facts: list[str] = []
    head = (
        f"(arch-part {_name(part.path)} :level {part.level} :kind {q(part.kind)} :files {part.files} "
        f":style {part.style} :state {part.state} :origin {part.origin}"
    )
    style = library.style(part.style)
    best = part.judgement.best if part.judgement else None
    if best is not None and style is not None and best.style == style.id:
        head += f" :score {best.score:.2f} :coverage {best.coverage:.2f}"
    if style is not None:
        head += f" :name {q(_style_label(style))} :source {q(style.cite())}"
    facts.append(head + ")")
    if part.state == "not-judged":
        return facts
    if part.declared is not None:
        if part.declared.layers is not None:
            facts.append(
                f"(arch-declared {_name(part.path)} {q(part.declared.layers.file)} :read yes "
                f":layers {len(part.declared.layers.layers)})"
            )
        for item in part.declared.not_read:
            facts.append(f"(arch-declared {_name(part.path)} {q(item.file)} :read no :reason {q(item.reason)})")
        if part.declared.baseline_file:
            facts.append(
                f"(arch-baseline {_name(part.path)} {q(part.declared.baseline_file)} "
                f":entries {len(part.declared.baseline)})"
            )
    judgement = part.judgement
    if judgement is not None:
        shown = [best] if best is not None else list(judgement.candidates)
        for score in shown:
            if score is None:
                continue
            for item in score.evidence:
                via = f" :via {q(item.via)}" if item.match == "spelling" else ""
                facts.append(
                    f"(arch-evidence {_name(part.path)} {score.style} {q(item.folder)} :files {item.files} "
                    f":group {item.group} :match {item.match}{via})"
                )
        others = [(c, "candidate") for c in judgement.candidates if best is None or c.style != best.style]
        others += [(c, "below") for c in judgement.near]
        for score, status in others:
            candidate = library.style(score.style)
            source = candidate.cite() if candidate else ""
            facts.append(
                f"(arch-candidate {_name(part.path)} {score.style} :score {score.score:.2f} "
                f":coverage {score.coverage:.2f} :status {status} :source {q(source)})"
            )
        for overlay in judgement.overlays:
            facts.append(
                f"(arch-overlay {_name(part.path)} {overlay.style} :score {overlay.score:.2f} :coverage {overlay.coverage:.2f})"
            )
    for root in part.routing_root:
        facts.append(f"(arch-layer {_name(part.path)} routing-root {q(root)} :files 0 :match exact)")
    for folder in part.layers:
        via = f" :via {q(folder.via)}" if folder.match == "spelling" else ""
        facts.append(
            f"(arch-layer {_name(part.path)} {folder.layer} {q(folder.folder)} :files {folder.files} :match {folder.match}{via})"
        )
    targets = target_layout(part, paths, library) if paths else ()
    if not details and targets:
        proposed = [item.folder for item in targets if not item.present]
        facts.append(f"(arch-target-summary {_name(part.path)} :present {len(targets) - len(proposed)} :proposed {len(proposed)} "
                     f":first {q(' '.join(proposed[:_EVIDENCE_SHOWN]))})")
        return facts
    for item in targets:
        block = f" :block {item.block}" if item.block else ""
        where = f":present {q(item.present[0])}" if item.present else ":proposed"
        facts.append(f"(arch-target {_name(part.path)} {item.layer} {q(item.folder)}{block} {where} :source {q(item.source)})")
    return facts


def part_advice(
    part: PartPlan, library: Library, paths: tuple[str, ...], from_graph: tuple = (),
    shown: Optional[Callable[[list], list]] = None, details: bool = True,
) -> list[str]:
    """Advice facts of a part: missing folders of its target layout, and the move and fix advice
    of the last build (`from_graph`, when .wingbox/index.sexp exists)."""
    if part.state in _advice.NO_ADVICE_STATES or part.style in (NO_STYLE, UNDETERMINED):
        return [_advice.quiet_sexp(part.path, f"style {part.style} ({part.state}): no advice")]
    rules = _advice.PartRules(part.path, part.style, part.state, {}, {})
    targets = tuple(
        _advice.TargetFact(
            part.path, item.layer, item.block, item.folder if part.path == "." else f"{part.path}/{item.folder}",
            not item.present, item.source,
        )
        for item in (target_layout(part, paths, library) if paths else ())
    )
    items = [item for item in from_graph if item.part == part.path and item.kind != "missing-folder"]
    items += _advice.missing_folders(rules, targets)
    if shown is not None:
        items = shown(items)   # what the decisions store still shows, best first
    if not details:
        missing = [item for item in items if item.kind == "missing-folder"]
        items = [item for item in items if item.kind != "missing-folder"]
        return [_advice.to_sexp(item) for item in items] + (
            [f"(advice-summary {_name(part.path)} :missing-folder {len(missing)})"] if missing else [])
    return [_advice.to_sexp(item) for item in items]


def init_facts(
    plan: RepositoryPlan, library: Library, config_line: str, paths: tuple[str, ...] = (), from_graph: tuple = (),
    shown: Optional[Callable[[list], list]] = None, details: bool = True,
) -> list[str]:
    """Every fact `wingbox init` prints, judged parts first by size, then the config line."""
    facts: list[str] = []
    judged = sorted((p for p in plan.parts if p.state != "not-judged"), key=lambda p: (-p.files, p.path))
    for part in judged:
        facts.extend(part_facts(part, library, paths, details))
        facts.extend(part_advice(part, library, paths, from_graph, shown, details))
    for part in sorted((p for p in plan.parts if p.state == "not-judged"), key=lambda p: p.path):
        facts.append(f"(arch-not-judged {_name(part.path)} :files {part.files} :kind {q(part.kind)})")
    facts.append(config_line)
    return facts


# ---------------------------------------------------------------------------
# Text printer over the facts
# ---------------------------------------------------------------------------


def _number(value: str) -> str:
    return f"{int(value):,}" if value.isdigit() else value


def _advice_lines(mine: list[list[str]]) -> list[str]:
    """`advice: 2 to move, 1 to fix, 7 folders to create`, then the move and fix items (at most 3)."""
    items = [t for t in mine if t[0] == "advice"]
    summary = next((t for t in mine if t[0] == "advice-summary"), None)
    if not items and summary is None:
        return []
    counts = {kind: sum(1 for t in items if keyword(t, "kind") == kind) for kind in ("move", "fix-violation", "missing-folder")}
    if summary is not None:
        counts["missing-folder"] += int(keyword(summary, "missing-folder", "0") or 0)
    summary = ", ".join(
        f"{counts[kind]} {label}" for kind, label in
        (("move", "to move"), ("fix-violation", "to fix"), ("missing-folder", "folders to create when needed"))
        if counts[kind]
    )
    lines = [f"  advice: {summary}"]
    actions = [t for t in items if keyword(t, "kind") in ("move", "fix-violation")]
    for t in actions[:3]:
        lines.append(f"  - {keyword(t, 'reason')} [{keyword(t, 'style')}: {keyword(t, 'rule')}]")
    if len(actions) > 3:
        lines.append(f"  - ... {len(actions) - 3} more: wingbox advice")
    return lines


def render_text(facts: list[str], min_source_files: int) -> str:
    """The facts of init_facts() for people (the agreed layout)."""
    parsed = [parse(fact) for fact in facts]
    parts = [tokens for tokens in parsed if tokens and tokens[0] == "arch-part"]
    width = max((len(positional(tokens)[0]) for tokens in parts), default=10) + 2
    lines: list[str] = []
    for tokens in parts:
        path = positional(tokens)[0]
        style = keyword(tokens, "style", "") or ""
        state = keyword(tokens, "state", "") or ""
        score = keyword(tokens, "score")
        level = keyword(tokens, "level", "") or ""
        mine = [t for t in parsed if t and t[0] != "arch-part" and len(t) > 1 and t[1] == path]
        if style == DECLARED:
            read = [t for t in mine if t[0] == "arch-declared" and keyword(t, "read") == "yes"]
            if read:
                title = f"declared by {read[0][2]} ({keyword(read[0], 'layers')} layers, read)"
            else:
                title = "declared by its own rules, not read: no layers, no violations stated"
            lines.append(f"{path:<{width}}{title}   {level}")
            for t in mine:
                if t[0] == "arch-declared" and keyword(t, "read") == "no":
                    lines.append(f"  also declared: {t[2]} (not read: {keyword(t, 'reason')})")
                elif t[0] == "arch-baseline":
                    lines.append(f"  known exceptions: {t[2]} ({_number(keyword(t, 'entries') or '0')} entries)")
            continue
        if style in (NO_STYLE, UNDETERMINED) or state in ("undetermined", "none"):
            kind = keyword(tokens, "kind", "") or ""
            title = (
                f"{kind}, no layer style" if style == NO_STYLE
                else "not determined: no layers, no violations stated"
            )
            lines.append(f"{path:<{width}}{title}   -   {level}")
            candidates = [t for t in mine if t[0] == "arch-candidate" and keyword(t, "status") == "candidate"]
            below = [t for t in mine if t[0] == "arch-candidate" and keyword(t, "status") == "below"]
            if candidates:
                lines.append("  candidates: " + ", ".join(f"{t[2]} {keyword(t, 'score')}" for t in candidates)
                             + (" (no style leads the next by 0.15)" if len(candidates) > 1 else ""))
            if below:
                lines.append("  below the thresholds: " + ", ".join(
                    f"{t[2]} {keyword(t, 'score')} (coverage {keyword(t, 'coverage')})" for t in below))
            continue
        name = keyword(tokens, "name", style) or style
        lines.append(f"{path:<{width}}{name}   {score or '-'}   {level}")
        evidence = [t for t in mine if t[0] == "arch-evidence" and t[2] == style]
        evidence.sort(key=lambda t: -int(keyword(t, "files", "0") or 0))
        shown = []
        for t in evidence[:_EVIDENCE_SHOWN]:
            note = f" ({keyword(t, 'match')} of {keyword(t, 'via')})" if keyword(t, "match") == "spelling" else ""
            shown.append(f"{t[3]} {_number(keyword(t, 'files', '0') or '0')}{note}")
        if shown:
            lines.append("  " + "  ".join(shown))
        others = [t for t in mine if t[0] == "arch-candidate"]
        if others:
            lines.append("  also: " + ", ".join(f"{t[2]} {keyword(t, 'score')}" for t in others))
        overlays = [t for t in mine if t[0] == "arch-overlay"]
        if overlays:
            lines.append("  with: " + ", ".join(f"{t[2]} {keyword(t, 'score')}" for t in overlays))
        targets = [t for t in mine if t[0] == "arch-target"]
        proposed = [t[3] + "/" for t in targets if ":proposed" in t]
        summary = next((t for t in mine if t[0] == "arch-target-summary"), None)
        if summary is not None:
            count = int(keyword(summary, "proposed", "0") or 0)
            first = [name + "/" for name in (keyword(summary, "first", "") or "").split()]
            more = f" (+{count - len(first)})" if count > len(first) else ""
            lines.append(f"  target: {keyword(summary, 'present')} of {int(keyword(summary, 'present', '0') or 0) + count} folders present"
                         + (f"; missing: {', '.join(first)}{more}" if count else ""))
        if targets:
            shown = ", ".join(proposed[:_EVIDENCE_SHOWN]) + (f" (+{len(proposed) - _EVIDENCE_SHOWN})" if len(proposed) > _EVIDENCE_SHOWN else "")
            lines.append(
                f"  target: {len(targets) - len(proposed)} of {len(targets)} folders present"
                + (f"; missing: {shown}" if proposed else "")
            )
        lines.extend(_advice_lines(mine))
    small = [positional(t)[0] for t in parsed if t and t[0] == "arch-not-judged"]
    if small:
        lines.append(f"not judged (fewer than {min_source_files} source files): {', '.join(small)}")
    lines.append("")
    for tokens in parsed:
        if tokens and tokens[0] == "arch-config-written":
            lines += [f"wrote {tokens[1]}", NEXT_STEP]
        elif tokens and tokens[0] == "arch-config-kept":
            lines.append(f"kept {tokens[1]}: {keyword(tokens, 'reason')}")
    return "\n".join(lines) + "\n"
