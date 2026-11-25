"""Architecture facts written into the code graph (.wingbox/index.sexp).

  (arch-part <part> :style <style> :state <state> :origin <origin> :level <level> :kind <kind> :files <n> [:source <citation>] [:declared <file>])
  (arch-deny <part> <from layer> <to layer> <reason> [:severity candidate] [:fix <prescribed fix>])
  (arch-rank <part> <layer> <rank>)                                  ; higher may import lower
  (arch-target <part> <layer> <folder> [:block <id>] :present <folder> | :proposed :source <citation>)
  (arch-known <part> <source module> <target module> <file> [:owner <who>] [:reason <why>] [:expires <date>])
                                                                     ; listed in a baseline, with the terms it states
  (arch-declared-unread <part> <file> :reason <why>)               ; a rule file of the part that was not read
  (arch-not-parsed <part> <language> :files <n>)                    ; source files of the part ("-": of no part) in a
                                                                     ; language no parser reads: they are in no module
  (arch-known-rule <part> <source module> <target module> <rule>)  ; the rule an entry of .wingbox/baseline.sexp states
  (arch-rule-files :config <digest> :baseline <digest> :learned <digest>)
                                                                     ; what the build read of the rule files ("-": missing)
  (arch-capability <part> <capability> [:allowed (<layer> ...)] [:forbidden (<layer> ...)] [:fix <text>])
  (arch-capability-unread <part> <language> :files <n>)            ; files of a part with capability rules in a language
                                                                     ; the capability detector does not read
  (arch-pattern <part> <pattern>)                                    ; a pattern check of the style
  (capability <module> <capability> :line <n> :evidence <text> [:suspect])
  (capability-known <part> <module> <capability> <file> [:owner <who>] [:reason <why>] [:expires <date>])
                                                                     ; accepted in .wingbox/baseline.sexp
  (layer <module> <layer> :part <part> :style <style> :rule <rule> :match <step>)

`layer`, `capability` and `capability-known` facts are written by the code graph
renderer, which knows the short module ids; this module prepares everything else.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from .baseline import BASELINE_FILE, Acceptance, BaselineKey, rule_file_digests
from .capabilities import Finding, merge_rules, supported as capability_language
from .detect import _is_ignored, is_source, is_test_file
from .labels import TEST, Label, label_file, part_denies
from .library import Library
from .plan import DECLARED, RepositoryPlan
from .sexp import q
from .target import target_layout

try:
    from .. import source_kinds
except ImportError:  # loaded as a top-level package (scripts/ on the module path)
    import source_kinds

NO_PART = "-"


def read_baseline_sexp(text: str) -> tuple[tuple[str, str, str, str], ...]:
    """(part, source, target, rule) of each `(baseline <part> <source> <target> [:rule <rule>] ...)` line;
    the rule is "" for an entry that states none."""
    from .sexp import keyword, parse

    entries = []
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] == "baseline":
            entries.append((tokens[1], tokens[2], tokens[3], keyword(tokens[4:], "rule", "") or ""))
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
    terms: Mapping[BaselineKey, Acceptance] = None   # owner, reason and end date of the entries that state them


def not_parsed_files(
    plan: RepositoryPlan, library: Library, paths: tuple[str, ...], in_graph: frozenset[str],
) -> dict[str, Counter]:
    """Per part ("-": no part), the source files in a language no parser reads, counted by language.

    A source file is what a part counts as one (detect.is_source), outside test and ignored folders: the
    files its style was judged on. Such a file is in no module, so no rule ever reads it.
    """
    found: dict[str, Counter] = {}
    for path in paths:
        if path in in_graph or not is_source(path):
            continue
        language = source_kinds.not_parsed_language(path)
        if language is None:
            continue
        part = plan.part_of(path)
        relative = path if part is None or part.path == "." else path[len(part.path) + 1:]
        if _is_ignored(relative, library) or is_test_file(relative, library):
            continue
        found.setdefault(part.path if part else NO_PART, Counter())[language] += 1
    return found


def _count_lines(fact: str, part: str, counts: Mapping[str, int]) -> list[str]:
    """`(<fact> <part> <language> :files N)` per language, most files first."""
    return [f"({fact} {q(part)} {q(language)} :files {files})"
            for language, files in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def graph_architecture(
    plan: RepositoryPlan, library: Library, node_paths: Mapping[str, str],
    node_imports: Mapping[str, frozenset[str]] | None = None, paths: tuple[str, ...] = (),
    baseline: tuple[tuple[str, str, str, str], ...] = (),
    capabilities: Mapping[str, tuple[Finding, ...]] | None = None,
    capability_baseline: tuple[tuple[str, str, str], ...] = (),
    baseline_terms: Mapping[BaselineKey, Acceptance] | None = None,
    read_text: Optional[Callable[[str], Optional[str]]] = None,
) -> GraphArchitecture:
    """Facts of the plan and the label of every node (node id -> repository-relative file).

    `node_imports` holds the "module:name" imports of each node, from the parse. `read_text` reads a file
    of the repository: with it the graph records what the build read of the rule files.
    """
    node_imports = node_imports or {}
    labels: dict[str, tuple[str, Label]] = {}
    for node_id, path in node_paths.items():
        part = plan.part_of(path)
        labels[node_id] = (part.path if part else NO_PART, label_file(path, plan, library, node_imports.get(node_id, frozenset())))
    not_parsed = not_parsed_files(plan, library, paths, frozenset(node_paths.values()))
    # Files the capability detector does not read, per part and language; tests are not judged by its rules.
    unread: dict[str, Counter] = {}
    for node_id, path in node_paths.items():
        part_path, label = labels[node_id]
        if label.layer != TEST and not capability_language(path):
            unread.setdefault(part_path, Counter())[source_kinds.parsed_language(path) or "other"] += 1
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
        lines.extend(_count_lines("arch-not-parsed", part.path, not_parsed.get(part.path, {})))
        capability_rules = part_capability_rules(part, library)
        for rule in capability_rules:
            allowed = f" :allowed ({' '.join(rule.allowed_in)})" if rule.allowed_in else ""
            forbidden = f" :forbidden ({' '.join(rule.forbidden_in)})" if rule.forbidden_in else ""
            fix = f" :fix {q(rule.fix)}" if rule.fix else ""
            lines.append(f"(arch-capability {q(part.path)} {rule.capability}{allowed}{forbidden}{fix})")
        if capability_rules:
            lines.extend(_count_lines("arch-capability-unread", part.path, unread.get(part.path, {})))
        style_patterns = style.patterns if style is not None and part.has_rules and part.style != DECLARED else ()
        lines.extend(f"(arch-pattern {q(part.path)} {pattern})" for pattern in style_patterns)
        for layer, rank in _ranks(part, library):
            lines.append(f"(arch-rank {q(part.path)} {layer} {rank})")
        for item in target_layout(part, paths, library) if paths else ():
            block = f" :block {item.block}" if item.block else ""
            where = f":present {q(item.present[0])}" if item.present else ":proposed"
            lines.append(f"(arch-target {q(part.path)} {item.layer} {q(item.folder)}{block} {where} :source {q(item.source)})")
    lines.extend(_count_lines("arch-not-parsed", NO_PART, not_parsed.get(NO_PART, {})))
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
    # .wingbox/baseline.sexp, written by `wingbox align --learn`: (part, source module, target module, rule).
    stated_rules: dict[tuple[str, str, str], set[str]] = {}
    for part_path, source_id, target_id, rule in baseline:
        if source_id in node_paths and target_id in node_paths:
            known.append((part_path, source_id, target_id, BASELINE_FILE))
            stated_rules.setdefault((part_path, source_id, target_id), set()).add(rule)
    # An entry that states a rule accepts a break of that rule only. A pair with an entry that states none
    # gets no such fact: that entry accepts the pair whatever the rule, as every entry did before.
    lines.extend(f"(arch-known-rule {q(part_path)} {q(source_id)} {q(target_id)} {q(rule)})"
                 for (part_path, source_id, target_id), rules in sorted(stated_rules.items()) if "" not in rules
                 for rule in sorted(rules))
    if read_text is not None:
        lines.append("(arch-rule-files " + " ".join(f":{name} {digest}" for name, digest in rule_file_digests(read_text)) + ")")
    capability_known = tuple(sorted({
        (part_path, node_id, capability) for part_path, node_id, capability in capability_baseline if node_id in node_paths
    }))
    return GraphArchitecture(part_lines=tuple(lines), labels=labels, known=tuple(sorted(set(known))),
                             capabilities=capabilities or {}, capability_known=capability_known,
                             terms=baseline_terms or {})


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
