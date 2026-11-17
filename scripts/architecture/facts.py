"""Architecture facts written into the code graph (.wingbox/index.sexp).

  (arch-part <part> :style <style> :state <state> :origin <origin> :level <level> :kind <kind> :files <n> [:source <citation>] [:declared <file>])
  (arch-deny <part> <from layer> <to layer> <reason> [:severity candidate] [:fix <prescribed fix>])
  (arch-rank <part> <layer> <rank>)                                  ; higher may import lower
  (arch-target <part> <layer> <folder> [:block <id>] :present <folder> | :proposed :source <citation>)
  (arch-known <part> <source module> <target module> <file>)       ; listed in the project's own baseline
  (arch-declared-unread <part> <file> :reason <why>)               ; a rule file of the part that was not read
  (arch-capability <part> <capability> [:allowed (<layer> ...)] [:forbidden (<layer> ...)] [:fix <text>])
  (arch-pattern <part> <pattern>)                                    ; a pattern check of the style
  (capability <module> <capability> :line <n> :evidence <text> [:suspect])
  (capability-known <part> <module> <capability> <file>)            ; accepted in .wingbox/baseline.sexp
  (layer <module> <layer> :part <part> :style <style> :rule <rule> :match <step>)

`layer`, `capability` and `capability-known` facts are written by the code graph
renderer, which knows the short module ids; this module prepares everything else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .capabilities import Finding, merge_rules
from .labels import Label, label_file, part_denies
from .library import Library
from .plan import DECLARED, RepositoryPlan
from .sexp import q
from .target import target_layout

BASELINE_FILE = ".wingbox/baseline.sexp"


def read_baseline_sexp(text: str) -> tuple[tuple[str, str, str], ...]:
    """(part, source, target) of each `(baseline <part> <source> <target> ...)` line."""
    from .sexp import parse

    entries = []
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] == "baseline":
            entries.append((tokens[1], tokens[2], tokens[3]))
    return tuple(entries)


def read_capability_baseline(text: str) -> tuple[tuple[str, str, str], ...]:
    """(part, module, capability) of each `(baseline-capability <part> <module> <capability> ...)` line."""
    from .sexp import parse

    entries = []
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] == "baseline-capability":
            entries.append((tokens[1], tokens[2], tokens[3]))
    return tuple(entries)


def part_capability_rules(part, library: Library):
    """The capability rules of a part: its style's, restated by the config; none without a determined style."""
    style = library.style(part.style)
    if style is None or part.style == DECLARED or part.state in ("undetermined", "none", "not-judged"):
        return ()
    return merge_rules(style.capabilities, part.capabilities)


@dataclass(frozen=True)
class GraphArchitecture:
    part_lines: tuple[str, ...]
    labels: Mapping[str, tuple[str, Label]]              # node id -> (part, label)
    known: tuple[tuple[str, str, str, str], ...]         # (part, source node, target node, baseline file)
    capabilities: Mapping[str, tuple[Finding, ...]] = None   # node id -> what its file does
    capability_known: tuple[tuple[str, str, str], ...] = ()   # (part, node, capability) accepted in the baseline


def graph_architecture(
    plan: RepositoryPlan, library: Library, node_paths: Mapping[str, str],
    node_imports: Mapping[str, frozenset[str]] | None = None, paths: tuple[str, ...] = (),
    baseline: tuple[tuple[str, str, str], ...] = (),
    capabilities: Mapping[str, tuple[Finding, ...]] | None = None,
    capability_baseline: tuple[tuple[str, str, str], ...] = (),
) -> GraphArchitecture:
    """Facts of the plan and the label of every node (node id -> repository-relative file).

    `node_imports` holds the "module:name" imports of each node, from the parse.
    """
    node_imports = node_imports or {}
    lines: list[str] = []
    for part in plan.parts:
        style = library.style(part.style)
        source = f" :source {q(style.cite())}" if style is not None else ""
        declared = (
            f" :declared {q(part.declared.layers.file)}" if part.declared is not None and part.declared.layers else ""
        )
        overlays = f" :overlays ({' '.join(part.overlays)})" if part.overlays else ""
        lines.append(
            f"(arch-part {q(part.path)} :style {part.style} :state {part.state} :origin {part.origin} "
            f":level {part.level} :kind {q(part.kind)} :files {part.files}{source}{declared}{overlays})"
        )
        for deny in part_denies(part, library):
            severity = " :severity candidate" if deny.severity == "candidate" else ""
            fix = f" :fix {q(deny.fix)}" if deny.fix else ""
            lines.append(f"(arch-deny {q(part.path)} {deny.source} {deny.target} {q(deny.reason)}{severity}{fix})")
        for item in part.declared.not_read if part.declared is not None else ():
            lines.append(f"(arch-declared-unread {q(part.path)} {q(item.file)} :reason {q(item.reason)})")
        for rule in part_capability_rules(part, library):
            allowed = f" :allowed ({' '.join(rule.allowed_in)})" if rule.allowed_in else ""
            forbidden = f" :forbidden ({' '.join(rule.forbidden_in)})" if rule.forbidden_in else ""
            fix = f" :fix {q(rule.fix)}" if rule.fix else ""
            lines.append(f"(arch-capability {q(part.path)} {rule.capability}{allowed}{forbidden}{fix})")
        style_patterns = style.patterns if style is not None and part.has_rules and part.style != DECLARED else ()
        lines.extend(f"(arch-pattern {q(part.path)} {pattern})" for pattern in style_patterns)
        for layer, rank in _ranks(part, library):
            lines.append(f"(arch-rank {q(part.path)} {layer} {rank})")
        for item in target_layout(part, paths, library) if paths else ():
            block = f" :block {item.block}" if item.block else ""
            where = f":present {q(item.present[0])}" if item.present else ":proposed"
            lines.append(f"(arch-target {q(part.path)} {item.layer} {q(item.folder)}{block} {where} :source {q(item.source)})")
    labels: dict[str, tuple[str, Label]] = {}
    for node_id, path in node_paths.items():
        part = plan.part_of(path)
        labels[node_id] = (part.path if part else "-", label_file(path, plan, library, node_imports.get(node_id, frozenset())))
    by_path = {path: node_id for node_id, path in node_paths.items()}
    known: list[tuple[str, str, str, str]] = []
    for part in plan.parts:
        if part.style != DECLARED or part.declared is None or part.declared.layers is None:
            continue
        base = part.declared.layers.base
        for source, target in sorted(part.declared.baseline):
            source_id = by_path.get(f"{base}/{source}" if base else source)
            target_id = by_path.get(f"{base}/{target}" if base else target)
            if source_id and target_id:
                known.append((part.path, source_id, target_id, part.declared.baseline_file))
    # .wingbox/baseline.sexp, written by `wingbox align --learn`: (part, source module, target module).
    for part_path, source_id, target_id in baseline:
        if source_id in node_paths and target_id in node_paths:
            known.append((part_path, source_id, target_id, BASELINE_FILE))
    capability_known = tuple(sorted({
        (part_path, node_id, capability) for part_path, node_id, capability in capability_baseline if node_id in node_paths
    }))
    return GraphArchitecture(part_lines=tuple(lines), labels=labels, known=tuple(sorted(set(known))),
                             capabilities=capabilities or {}, capability_known=capability_known)


def _ranks(part, library: Library) -> list[tuple[str, int]]:
    """Layer ranks of a part: the declared order, or the ranks of its style."""
    if part.style == DECLARED:
        if part.declared is None or part.declared.layers is None:
            return []
        return [(layer.name, index) for index, layer in enumerate(part.declared.layers.layers)]
    style = library.style(part.style)
    if style is None or part.state in ("undetermined", "none", "not-judged"):
        return []
    return [(layer.id, layer.rank) for layer in style.layers]
