"""People-facing overview of a code graph: the data behind REPORT.md and map.html.

The code graph file (.wingbox/index.sexp) is the format for agents. This module
turns a loaded graph into plain values and renders the Markdown report.
Everything here is pure: no file access, no clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class Overview:
    project: str
    modules: tuple[ModuleInfo, ...]
    edges: tuple[EdgeInfo, ...]
    violations: tuple[ViolationInfo, ...]
    layers: tuple[tuple[str, int], ...]
    languages: tuple[tuple[str, int], ...]
    entity_count: int
    boundary_rules: tuple[tuple[str, str, str], ...]   # (source layer, target layer, reason)
    violation_candidates: int   # rule breaks where a layer is only a guess from the file role
    placed_by_path: int         # modules whose layer comes from their folders
    placed_by_role: int         # modules whose layer is a guess from the file role
    unplaced: int               # modules no layer rule applies to
    type_only_crossings: tuple[ViolationInfo, ...] = ()   # imports of types only across a layer boundary


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


def build_overview(graph: Any, search: Any, project: str) -> Overview:
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

    modules = tuple(
        ModuleInfo(
            id=module_id,
            file=search.module_file(graph, module_id),
            lang=graph.module_lang(module_id),
            layer=search.module_layer(graph, module_id),
            layer_basis=search.module_layer_basis(graph, module_id),
            fan_in=len(dependents[module_id]),
            fan_out=len(dependencies[module_id]),
            within_two_hops=within_hops(module_id),
        )
        for module_id in module_ids
    )
    edges = tuple(
        EdgeInfo(source=module_id, target=target, kind=kind)
        for module_id in module_ids
        for target, kind in sorted(dependencies[module_id].items())
    )
    basis = {module.id: module.layer_basis for module in modules}
    rule_breaks = sorted(set(search.boundary_violations(graph, known)))
    # A rule break counts only when both layers come from folders or the file type. A layer guessed from the
    # role of a file is too weak a ground to call a dependency a violation.
    # An import of types only is erased at build time: listed apart, never a violation.
    type_only_pairs = {
        (source, target)
        for source, target, _, _, kind, _ in rule_breaks
        if kind == "imports" and search.is_type_only_import(graph, source, target)
    }
    infos = tuple(
        ViolationInfo(
            source=source, target=target, source_layer=source_layer,
            target_layer=target_layer, kind=kind, reason=reason,
        )
        for source, target, source_layer, target_layer, kind, reason in rule_breaks
    )
    type_only_crossings = tuple(
        item for item in infos if item.kind == "imports" and (item.source, item.target) in type_only_pairs
    )
    violations = tuple(
        item
        for item in infos
        if item not in type_only_crossings and basis.get(item.source) == "path" and basis.get(item.target) == "path"
    )
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
        boundary_rules=tuple(search.BOUNDARY_DENY_RULES),
        violation_candidates=len(
            {(item.source, item.target) for item in infos if item not in type_only_crossings}
            - {(item.source, item.target) for item in violations}
        ),
        type_only_crossings=type_only_crossings,
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
        f"{'rests' if view.violation_candidates == 1 else 'rest'} on a guessed layer and "
        f"{'is' if view.violation_candidates == 1 else 'are'} not counted."
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
        "## Architecture layers",
        "",
        "Each module is assigned to a layer from its folders; when no folder rule applies, from the role "
        "of the file, which is a guess. `other` holds the modules no rule applies to.",
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
            f"| File | Layer | Direct dependents | Within {BLAST_RADIUS_DEPTH} hops |",
            "|---|---|---:|---:|",
        ]
        lines += [
            f"| {_code(module.file)} | {module.layer} | {_number(module.fan_in)} | {_number(module.within_two_hops)} |"
            for module in top
        ]
    else:
        lines.append("No module depends on another one in this graph.")

    lines += ["", "## Boundary violations", ""]
    if view.violations:
        lines += [
            "Dependencies that cross an architecture boundary in the wrong direction, by the default "
            "layer rules. Only pairs where both layers come from folders or from the file type (a `.tsx` component is UI) are listed; layers are inferred "
            "from paths and file roles, so read each line before acting on it.",
            "",
            "| From | To | Rule broken |",
            "|---|---|---|",
        ]
        lines += [
            f"| {_code(files.get(item.source, item.source))} ({item.source_layer}) "
            f"| {_code(files.get(item.target, item.target))} ({item.target_layer}) "
            f"| {_cell(item.reason)} |"
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
    if view.violation_candidates:
        lines += [
            "",
            f"{_plural(view.violation_candidates, 'more candidate')} where a layer is only guessed from "
            "the role of a file: `wingbox boundaries all` lists them.",
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
