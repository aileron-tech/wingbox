"""People-facing overview of a code graph: the data behind REPORT.md and map.html.

The code graph file (.wingbox/index.sexp) is the format for agents. This module
turns a loaded graph into plain values and renders the Markdown report.
Everything here is pure: no file access, no clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

LAYER_ORDER: tuple[str, ...] = (
    "ui", "api", "service", "domain", "infra", "shared", "config", "test", "other",
)
BLAST_RADIUS_DEPTH = 2
_TOP_MODULES = 15
_TOP_VIOLATIONS = 30
_TOP_ENTRY_POINTS = 10


@dataclass(frozen=True)
class ModuleInfo:
    id: str
    file: str
    lang: str
    layer: str
    layer_basis: str       # "path": from its folders, "role": guessed from the file role, "none": no rule applies
    fan_in: int            # modules that depend on this one directly
    fan_out: int           # modules this one depends on directly
    within_two_hops: int   # modules reached by a change, up to BLAST_RADIUS_DEPTH hops
    style: str = "-"       # architecture style of the module's part that set the layer
    rule: str = ""         # the rule that set the layer ("folder routes/ -> api")


@dataclass(frozen=True)
class EdgeInfo:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class ViolationInfo:
    source: str
    target: str
    source_layer: str
    target_layer: str
    kind: str
    reason: str
    part: str = "."
    style: str = "-"
    rule: str = ""
    known: str = ""        # baseline file of the project that already lists the pair
    severity: str = "violation"   # candidate: the rule gives candidates only


@dataclass(frozen=True)
class PartInfo:
    path: str
    style: str
    state: str
    origin: str
    level: str
    files: int
    source: str


@dataclass(frozen=True)
class AdviceInfo:
    id: str
    part: str
    kind: str        # move | fix-violation | missing-folder
    target: str
    reason: str
    style: str
    rule: str
    evidence: str


@dataclass(frozen=True)
class CapabilityInfo:
    file: str
    kind: str        # the capability (sql, http-client, ...) or the pattern id
    layer: str
    severity: str    # violation | known | candidate | pattern
    line: int
    evidence: str
    rule: str        # allowed or forbidden layers, or the reason of the pattern


@dataclass(frozen=True)
class Overview:
    project: str
    modules: tuple[ModuleInfo, ...]
    edges: tuple[EdgeInfo, ...]
    violations: tuple[ViolationInfo, ...]
    layers: tuple[tuple[str, int], ...]
    languages: tuple[tuple[str, int], ...]
    entity_count: int
    boundary_rules: tuple[tuple[str, str, str, str, str], ...]   # (part, style, source layer, target layer, reason)
    violation_candidates: int   # rule breaks where a layer is only a guess from the file role
    placed_by_path: int         # modules whose layer comes from their folders
    placed_by_role: int         # modules whose layer is a guess from the file role
    unplaced: int               # modules no layer rule applies to
    type_only_crossings: tuple[ViolationInfo, ...] = ()   # imports of types only across a layer boundary
    parts: tuple[PartInfo, ...] = ()
    advice: tuple[AdviceInfo, ...] = ()
    known_violations: tuple[ViolationInfo, ...] = ()   # rule breaks a baseline already lists
    quiet_parts: tuple[tuple[str, str], ...] = ()   # parts without advice and why
    not_checked: tuple[tuple[str, str, int], ...] = ()   # (part, reason in words, files) of parts no rule checked
    capability_breaks: tuple[CapabilityInfo, ...] = ()   # what a file does that its layer may not
    pattern_breaks: tuple[CapabilityInfo, ...] = ()      # pattern checks of the style the code does not follow
    skipped_languages: tuple[tuple[str, str, int], ...] = ()   # (display name, language, files) the build left out


def _layer_rank(layer: str) -> int:
    return LAYER_ORDER.index(layer) if layer in LAYER_ORDER else len(LAYER_ORDER)


def _count(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _is_generated(module: ModuleInfo) -> bool:
    """True for a Dart codegen part (foo.g.dart, foo.freezed.dart).

    Such a file is read output: it may be depended on, but it never starts a
    reading list or a blast radius of its own.
    """
    return module.lang == "dart" and module.id.rsplit("/", 1)[-1].endswith((".g", ".freezed"))


def build_overview(graph: Any, search: Any, project: str, advice: Any = None) -> Overview:
    """Summarise a loaded CodeGraph.

    `search` is the code_graph_search module: it owns the layer rules, the
    boundary rules and the dependent walk, so the report, the map and the
    query commands always agree.
    """
    module_ids = sorted(graph.nodes)
    known = set(module_ids)

    # One pass over the graph; every count below comes from these two maps.
    dependencies: dict[str, dict[str, str]] = {
        module_id: {
            target: kind
            for target, kind in search.module_dependencies(graph, module_id).items()
            if target in known
        }
        for module_id in module_ids
    }
    dependents: dict[str, set[str]] = {module_id: set() for module_id in module_ids}
    for module_id, targets in dependencies.items():
        for target in targets:
            dependents[target].add(module_id)

    def within_hops(module_id: str) -> int:
        """Modules that depend on module_id directly or through one other module."""
        first = dependents[module_id]
        second: set[str] = set()
        for dependent in first:
            second |= dependents[dependent]
        second -= first
        second.discard(module_id)
        return len(first) + len(second)

    labels = {module_id: search.module_label(graph, module_id) for module_id in module_ids}
    modules = tuple(
        ModuleInfo(
            id=module_id,
            file=search.module_file(graph, module_id),
            lang=graph.module_lang(module_id),
            layer=labels[module_id].layer,
            layer_basis=search.module_layer_basis(graph, module_id),
            fan_in=len(dependents[module_id]),
            fan_out=len(dependencies[module_id]),
            within_two_hops=within_hops(module_id),
            style=labels[module_id].style,
            rule=labels[module_id].rule,
        )
        for module_id in module_ids
    )
    edges = tuple(
        EdgeInfo(source=module_id, target=target, kind=kind)
        for module_id in module_ids
        for target, kind in sorted(dependencies[module_id].items())
    )
    basis = {module.id: module.layer_basis for module in modules}
    rule_breaks = sorted(
        search.boundary_violations(graph, known), key=lambda item: (item.source, item.target, item.kind)
    )
    # A rule break counts only when both layers come from rules (folders, file type, declared layers). A layer
    # guessed from the role of a file is too weak a ground to call a dependency a violation.
    # An import of types only is erased at build time: listed apart, never a violation.
    infos = tuple(dict.fromkeys(
        ViolationInfo(
            source=item.source, target=item.target, source_layer=item.source_layer,
            target_layer=item.target_layer, kind=item.kind, reason=item.reason,
            part=item.part, style=item.style, rule=item.rule, known=item.known, severity=item.severity,
        )
        for item in rule_breaks
    ))
    type_only_pairs = {
        (item.source, item.target) for item in infos if search.is_type_only_import(graph, item.source, item.target)
    }
    type_only_crossings = tuple(
        item for item in infos if item.kind == "imports" and (item.source, item.target) in type_only_pairs
    )
    infos = tuple(item for item in infos if (item.source, item.target) not in type_only_pairs or item.kind == "imports")
    ruled = tuple(
        item
        for item in infos
        if item not in type_only_crossings and item.severity != "candidate"
        and basis.get(item.source) == "path" and basis.get(item.target) == "path"
    )
    # A pair listed in a baseline (the project's own, or .wingbox/baseline.sexp) is known, not counted.
    violations = tuple(item for item in ruled if not item.known)
    known_violations = tuple(item for item in ruled if item.known)
    parts = tuple(
        PartInfo(
            path=path, style=values.get("style", "-"), state=values.get("state", ""),
            origin=values.get("origin", ""), level=values.get("level", ""),
            files=int(values.get("files", "0")) if values.get("files", "0").isdigit() else 0,
            source=values.get("source", ""),
        )
        for path, values in sorted(graph.arch_parts.items())
    )
    advice_items, quiet_parts = search.advice_items(graph)
    if advice is not None:
        advice_items = advice   # what the decisions store still shows, best first
    layer_counts = _count([module.layer for module in modules])
    language_counts = _count([module.lang or "unknown" for module in modules])
    return Overview(
        project=project,
        modules=modules,
        edges=edges,
        violations=violations,
        layers=tuple(sorted(layer_counts.items(), key=lambda item: (_layer_rank(item[0]), item[0]))),
        languages=tuple(sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))),
        entity_count=sum(len(entities) for entities in graph.entities.values()),
        boundary_rules=tuple(search.boundary_rules(graph)),
        parts=parts,
        advice=tuple(
            AdviceInfo(item.id, item.part, item.kind, item.target, item.reason, item.style, item.rule, item.evidence)
            for item in advice_items
        ),
        quiet_parts=tuple(quiet_parts),
        not_checked=tuple(
            (item.part, search.not_checked_text(item, "`"), item.count)
            for item in search.not_checked_parts(graph, set(graph.nodes))
        ),
        violation_candidates=len(
            {(item.source, item.target) for item in infos if item not in type_only_crossings}
            - {(item.source, item.target) for item in ruled}
        ),
        known_violations=known_violations,
        type_only_crossings=type_only_crossings,
        capability_breaks=tuple(
            CapabilityInfo(
                search.module_file(graph, item.module), item.capability, item.layer,
                "known" if item.known and item.severity == "violation" else item.severity, item.line, item.evidence,
                f"allowed in {', '.join(item.allowed)}" if item.allowed else f"not in {', '.join(item.forbidden)}",
            )
            for item in search.capability_violations(graph, set(graph.nodes))
        ),
        pattern_breaks=tuple(
            CapabilityInfo(
                search.module_file(graph, item.source), item.pattern, search.module_layer(graph, item.source), "pattern",
                item.line, item.evidence or (f"imports {search.module_file(graph, item.target)}" if item.target else ""),
                item.reason,
            )
            for item in search.pattern_violations(graph, set(graph.nodes))
        ),
        placed_by_path=sum(1 for module in modules if module.layer_basis == "path"),
        placed_by_role=sum(1 for module in modules if module.layer_basis == "role"),
        unplaced=sum(1 for module in modules if module.layer_basis == "none"),
    )


def most_depended_on(view: Overview, limit: int) -> tuple[ModuleInfo, ...]:
    """Modules with the most direct dependents; a change there reaches the most code."""
    ranked = sorted(
        (
            module
            for module in view.modules
            if module.fan_in > 0 and module.layer != "test" and not _is_generated(module)
        ),
        key=lambda module: (-module.fan_in, -module.within_two_hops, module.id),
    )
    return tuple(ranked[:limit])


def widest_blast_radius(view: Overview) -> ModuleInfo | None:
    """The module whose change reaches the most modules; test and generated files are left out."""
    candidates = [
        module
        for module in view.modules
        if module.within_two_hops > 0 and module.layer != "test" and not _is_generated(module)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda module: (-module.within_two_hops, -module.fan_in, module.id))


def _share(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def entry_points(view: Overview, limit: int) -> tuple[ModuleInfo, ...]:
    """Modules nothing depends on that depend on others: where execution starts."""
    ranked = sorted(
        (
            module for module in view.modules
            if module.fan_in == 0
            and module.fan_out > 0
            and module.layer != "test"
            and not _is_generated(module)
        ),
        key=lambda module: (-module.fan_out, module.id),
    )
    return tuple(ranked[:limit])


def _number(value: int) -> str:
    return f"{value:,}"


def _plural(count: int, noun: str) -> str:
    return f"{_number(count)} {noun}" if count == 1 else f"{_number(count)} {noun}s"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(text: str) -> str:
    return "`" + _cell(text).replace("`", "'") + "`"


def _layer_cell(module: ModuleInfo) -> str:
    if module.layer in ("other", "test") or module.style in ("-", ""):
        return module.layer
    return f"{module.layer} ({_cell(module.style)}: {_cell(module.rule)})"


def _rule_text(item: ViolationInfo) -> str:
    known = f"; listed in {item.known}" if item.known else ""
    return f"{item.style}: {item.rule}, {item.reason}{known}"


def _parts_section(view: Overview) -> list[str]:
    """One row per part: its C4 level, style and where the style comes from."""
    if not view.parts:
        return ["No architecture facts in this graph."]
    lines = [
        "Each part of the repository (a folder with its own manifest) is judged by one style. The style comes "
        "from `.wingbox/config.yaml` (`config`), from rules the part declares itself (`declared`) or from "
        "the folder names (`detected`); a part whose style is not determined gets no layers and no "
        "boundary rules.",
        "",
        "| Part | Level | Style | State | From | Files |",
        "|---|---|---|---|---|---:|",
    ]
    for part in sorted(view.parts, key=lambda item: (-item.files, item.path)):
        if part.state == "not-judged":
            continue
        lines.append(
            f"| {_code(part.path)} | {part.level} | {part.style} | {part.state} | {part.origin} | {_number(part.files)} |"
        )
    small = [part.path for part in view.parts if part.state == "not-judged"]
    if small:
        lines += ["", f"Not judged (too few source files): {', '.join(_code(path) for path in sorted(small))}."]
    return lines


_TOP_ADVICE = 30


def _capabilities(view: Overview) -> list[str]:
    """Capability rules (SQL in repositories, HTTP in infrastructure, ...) and pattern checks, with the line."""
    counts = _count([item.severity for item in view.capability_breaks])
    lines = [
        f"{_plural(counts.get('violation', 0), 'file')} {'does' if counts.get('violation', 0) == 1 else 'do'} what "
        f"its layer may not (SQL outside the repositories, outbound HTTP outside infrastructure, the framework in the "
        f"domain, ...); {_number(counts.get('known', 0))} accepted in the baseline, "
        f"{_number(counts.get('candidate', 0))} suspected (a query kept in a variable, or a layer guessed from the role), "
        f"{_plural(len(view.pattern_breaks), 'pattern break')}. Found by syntax: SQL assembled at run time or behind a "
        "helper of another package is not seen. `wingbox boundaries all` lists them all.",
        "",
        "| File | Capability or pattern | Layer | Line | Evidence | Rule |",
        "|---|---|---|---|---|---|",
    ]
    shown = [item for item in view.capability_breaks if item.severity == "violation"] + list(view.pattern_breaks)
    lines += [
        f"| {_code(item.file)} | {item.kind} | {item.layer} | {item.line or '-'} | {_cell(item.evidence)} | {_cell(item.rule)} |"
        for item in shown[:_TOP_ADVICE]
    ]
    if len(shown) > _TOP_ADVICE:
        lines += ["", f"Showing {_TOP_ADVICE} of {_number(len(shown))}."]
    return lines


def _recommendations(view: Overview) -> list[str]:
    """What the style of each part says to create, move or fix."""
    lines: list[str] = []
    if view.advice:
        counts = _count([item.kind for item in view.advice])
        lines += [
            "What the style of each part (or the rules it declares) says to do: "
            + ", ".join(f"{_number(counts.get(kind, 0))} {label}" for kind, label in
                        (("move", "files to move"), ("fix-violation", "imports to fix"),
                         ("fix-capability", "files doing what their layer may not"), ("fix-pattern", "pattern breaks"),
                         ("missing-folder", "folders of the target layout to create when needed")))
            + ". `wingbox advice` lists them for agents.",
            "",
            "| Kind | Target | Advice | Style and rule | Evidence |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {item.kind} | {_code(item.target)} | {_cell(item.reason)} | {_cell(item.style)}: {_cell(item.rule)} "
            f"| {_cell(item.evidence)} |"
            for item in view.advice[:_TOP_ADVICE]
        ]
        if len(view.advice) > _TOP_ADVICE:
            lines += ["", f"Showing {_TOP_ADVICE} of {_number(len(view.advice))}."]
    else:
        lines.append("No recommendation.")
    if view.quiet_parts:
        lines += ["", "No advice for: " + ", ".join(f"{_code(part)} ({_cell(why)})" for part, why in view.quiet_parts) + "."]
    return lines


_SKIPPED_FACT = re.compile(r"^\(skipped-language (\S+) :files (\d+) ")


def skipped_languages(record: str, display: Mapping[str, str]) -> tuple[tuple[str, str, int], ...]:
    """(display name, language, files) from the lines of .wingbox/graph-skipped.sexp."""
    found = []
    for line in record.splitlines():
        match = _SKIPPED_FACT.match(line)
        if match:
            found.append((display.get(match.group(1), match.group(1)), match.group(1), int(match.group(2))))
    return tuple(found)


def render_report(view: Overview, generated: str, tool_version: str) -> str:
    """REPORT.md: what the code graph says about the project, for a person."""
    files = {module.id: module.file for module in view.modules}
    module_count = len(view.modules)
    top = most_depended_on(view, _TOP_MODULES)
    starts = entry_points(view, _TOP_ENTRY_POINTS)
    placed = view.placed_by_path + view.placed_by_role
    widest = widest_blast_radius(view)
    candidates_note = (
        f" {_plural(view.violation_candidates, 'more candidate')} "
        f"({'rests' if view.violation_candidates == 1 else 'rest'} on a guessed layer or on a rule that gives "
        f"candidates) {'is' if view.violation_candidates == 1 else 'are'} not counted."
        if view.violation_candidates else ""
    )
    lines: list[str] = [
        f"# Wingbox report: {view.project}",
        "",
        f"Generated on {generated} by wingbox {tool_version} from the code graph of this project.",
        "Open `map.html` in this folder for the interactive map.",
        "",
        "## Key findings",
        "",
    ]
    lines += [
        f"- Not in this graph: {_plural(files, f'{display} file')} (the {display} parser is not installed). "
        f"Run `wingbox add {name}`, then `wingbox build`."
        for display, name, files in view.skipped_languages
    ]
    lines += [
        f"- Not checked: part {_code(part)} - {reason}; {_plural(count, 'file')} without a boundary check."
        for part, reason, count in view.not_checked
    ]
    if widest:
        lines.append(
            f"- A change to {_code(widest.file)} reaches {_number(widest.within_two_hops)} of "
            f"{_number(module_count)} modules ({_share(widest.within_two_hops, module_count)}) within "
            f"{BLAST_RADIUS_DEPTH} hops; {_plural(widest.fan_in, 'module')} "
            f"{'depends' if widest.fan_in == 1 else 'depend'} on it directly."
        )
    else:
        lines.append("- No module depends on another one in this graph.")
    lines += [
        f"- {_plural(len(view.violations), 'boundary violation')} between layers that are set by folders."
        + candidates_note,
        f"- The layer rules place {_number(placed)} of {_number(module_count)} modules "
        f"({_share(placed, module_count)}): {_number(view.placed_by_path)} by folder, "
        f"{_number(view.placed_by_role)} by the role of the file. "
        "The boundary rules say nothing about the rest.",
        "",
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| Modules | {_number(module_count)} |",
        f"| Dependencies between modules | {_number(len(view.edges))} |",
        f"| Functions, constants, classes and types | {_number(view.entity_count)} |",
        "| Languages | " + ", ".join(f"{lang} {_number(count)}" for lang, count in view.languages) + " |",
        f"| Boundary violations | {_number(len(view.violations))} |",
        "",
        "## Architecture",
        "",
    ]
    lines += _parts_section(view)
    lines += [
        "",
        "## Architecture layers",
        "",
        "Each module takes a layer of the style of its part: from its folders (or the rules the part declares); "
        "when no rule applies in a layered part, from the role of the file, which is a guess. A part without a "
        "determined style gives no layers. `other` holds the modules no rule applies to.",
        "",
        "| Layer | Modules | Share |",
        "|---|---:|---:|",
    ]
    for layer, count in view.layers:
        share = round(100 * count / module_count) if module_count else 0
        lines.append(f"| {layer} | {_number(count)} | {share}% |")

    lines += ["", "## Most depended-on modules", ""]
    if top:
        lines += [
            "A change in one of these files reaches the most code (test files are left out). "
            f"\"Within {BLAST_RADIUS_DEPTH} hops\" counts the modules that depend on it "
            "directly or through one other module.",
            "",
            f"| File | Layer (style: rule) | Direct dependents | Within {BLAST_RADIUS_DEPTH} hops |",
            "|---|---|---:|---:|",
        ]
        lines += [
            f"| {_code(module.file)} | {_layer_cell(module)} | {_number(module.fan_in)} | {_number(module.within_two_hops)} |"
            for module in top
        ]
    else:
        lines.append("No module depends on another one in this graph.")

    lines += ["", "## Boundary violations", ""]
    if view.not_checked:
        lines += [
            f"Not checked: part {_code(part)} - {reason}; {_plural(count, 'file')}. "
            "The counts below cover only the parts that were checked."
            for part, reason, count in view.not_checked
        ] + [""]
    if view.violations:
        lines += [
            "Dependencies that cross a boundary in the wrong direction, by the rules of the style of their part "
            "(or the rules the part declares). Only pairs where both layers come from rules (folders, file "
            "patterns, declared layers) are listed; read each line before acting on it.",
            "",
            "| From | To | Style and rule |",
            "|---|---|---|",
        ]
        lines += [
            f"| {_code(files.get(item.source, item.source))} ({item.source_layer}) "
            f"| {_code(files.get(item.target, item.target))} ({item.target_layer}) "
            f"| {_cell(_rule_text(item))} |"
            for item in view.violations[:_TOP_VIOLATIONS]
        ]
        if len(view.violations) > _TOP_VIOLATIONS:
            lines += [
                "",
                f"Showing {_TOP_VIOLATIONS} of {_number(len(view.violations))}. "
                "Full list: `wingbox boundaries all`.",
            ]
    else:
        lines.append(
            f"None found by the layer rules, which place {_share(placed, module_count)} of the modules."
        )
    if view.known_violations:
        known = len({(item.source, item.target) for item in view.known_violations})
        baselines = sorted({item.known for item in view.known_violations})
        lines += [
            "",
            f"{_plural(known, 'more rule break')} {'is' if known == 1 else 'are'} listed in a baseline "
            f"({', '.join(_code(b) for b in baselines)}) and not counted: `wingbox boundaries all` prints them as "
            "`violation-known`.",
        ]
    if view.violation_candidates:
        lines += [
            "",
            f"{_plural(view.violation_candidates, 'more candidate')} where a layer is only guessed from "
            "the role of a file, or where the rule gives candidates (a utility that depends on a layer that "
            "uses it): `wingbox boundaries all` lists them.",
        ]

    if view.type_only_crossings:
        count = len(view.type_only_crossings)
        lines += [
            "",
            f"{_plural(count, 'import')} of types only "
            f"{'crosses' if count == 1 else 'cross'} a layer boundary. The compiler erases such an import, "
            "so it is not counted as a violation; a change of the type still reaches the importing file.",
            "",
            "| From | To |",
            "|---|---|",
        ]
        lines += [
            f"| {_code(files.get(item.source, item.source))} ({item.source_layer}) "
            f"| {_code(files.get(item.target, item.target))} ({item.target_layer}) |"
            for item in view.type_only_crossings[:_TOP_VIOLATIONS]
        ]
        if count > _TOP_VIOLATIONS:
            lines += ["", f"Showing {_TOP_VIOLATIONS} of {_number(count)}. Full list: `wingbox boundaries all`."]

    if view.capability_breaks or view.pattern_breaks:
        lines += ["", "## What layers do", ""]
        lines += _capabilities(view)
    lines += ["", "## Recommendations", ""]
    lines += _recommendations(view)
    lines += ["", "## Where to start reading", ""]
    if starts:
        lines.append("Files that nothing else depends on and that pull in the most modules:")
        lines.append("")
        lines += [
            f"- {_code(module.file)} ({module.layer}), depends on {_plural(module.fan_out, 'module')}"
            for module in starts
        ]
    else:
        lines.append("No entry point stands out: every module with dependencies also has dependents.")

    lines += ["", "## Next steps", ""]
    if top:
        lines += [
            "Check what a change will reach before you make it:",
            "",
            "```",
            f"wingbox impact {top[0].file}",
            "```",
            "",
        ]
    lines += [
        "Rebuild after the code changes with `wingbox build`. "
        "This report and the map are rewritten on every build.",
        "",
        "## Limits",
        "",
        "The graph comes from static parsing. Dependencies created at run time "
        "(dependency injection, reflection, generated code) are not visible here.",
        "",
    ]
    return "\n".join(lines)
