"""Recommendations: what to create, move or fix, by the rules of each part's style.

Pure functions over plain values; the code graph adapter (code_graph_search)
and `wingbox init` build the inputs. Three kinds, each naming the style, the
rule and the evidence:

- missing-folder  a folder of the target layout the part does not have yet
                  (domain building blocks included).
- move            a file whose dependents in the same part break a rule by
                  importing it: it sits in the wrong layer. The advice names
                  the nearest layer that every dependent may import and whose
                  own imports stay allowed; the counts are the evidence.
- fix-violation   a boundary violation with the fix the style prescribes.

A part whose style is not determined gets no advice; a part with declared
rules is advised by those rules (and its baseline: a violation the project
already lists is not repeated as advice).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .sexp import q

NO_ADVICE_STATES = ("undetermined", "none", "not-judged", "declared-unread")
MOVE_MIN_BREAKING = 2


@dataclass(frozen=True)
class Advice:
    id: str           # stable: hash of part, kind, target, rule
    part: str
    kind: str         # missing-folder | move | fix-violation
    target: str       # the folder to create, the file to move, or the file whose import to fix
    reason: str
    style: str
    rule: str
    evidence: str
    modules: tuple[str, ...] = ()   # modules the advice concerns (for analyze and impact)
    destination: str = ""           # move: the folder of the advised layer


@dataclass(frozen=True)
class PartRules:
    path: str
    style: str
    state: str
    ranks: Mapping[str, int]                      # layer -> rank (higher may import lower)
    denies: Mapping[tuple[str, str], tuple[str, str, str]]   # (from, to) -> (reason, severity, fix)


@dataclass(frozen=True)
class ModuleFact:
    id: str
    file: str
    part: str
    layer: str
    certain: bool
    known_targets: frozenset[str] = frozenset()   # targets of imports the project's baseline lists


@dataclass(frozen=True)
class TargetFact:
    part: str
    layer: str
    block: str
    folder: str
    proposed: bool
    source: str


@dataclass(frozen=True)
class Violation:
    source: str
    target: str
    source_layer: str
    target_layer: str
    part: str
    style: str
    reason: str
    known: bool


def advice_id(part: str, kind: str, target: str, rule: str) -> str:
    return hashlib.sha256(f"{part}\0{kind}\0{target}\0{rule}".encode("utf-8")).hexdigest()[:12]


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


_FIX_NAME = re.compile(r"\{(source|target|source_folder|target_folder)\}")


def fix_text(fix: str, source_file: str, target_file: str) -> str:
    """The fix of a rule with its four names filled in: {source}, {target}, {source_folder}, {target_folder}.

    Only these names are replaced, in one pass: any other brace of the text stays as it is (the text of a
    declared rule holds the path of its file, and a path may hold braces).
    """
    values = {"source": source_file, "target": target_file,
              "source_folder": _folder(source_file), "target_folder": _folder(target_file)}
    return _FIX_NAME.sub(lambda match: values[match.group(1)], fix)


def missing_folders(part: PartRules, targets: tuple[TargetFact, ...]) -> list[Advice]:
    found: list[Advice] = []
    for item in targets:
        if item.part != part.path or not item.proposed:
            continue
        what = f"domain building block {item.block}" if item.block else f"layer {item.layer}"
        rule = f"target {item.layer}" + (f"/{item.block}" if item.block else "")
        found.append(Advice(
            id=advice_id(part.path, "missing-folder", item.folder, rule), part=part.path, kind="missing-folder",
            target=item.folder, reason=f"the {part.style} layout has a folder for the {what}; create it when such code appears",
            style=part.style, rule=rule, evidence=item.source,
        ))
    return found


def fix_violations(part: PartRules, violations: tuple[Violation, ...], files: Mapping[str, str]) -> list[Advice]:
    found: list[Advice] = []
    for item in violations:
        if item.part != part.path or item.known:
            continue
        reason, _severity, fix = part.denies.get((item.source_layer, item.target_layer), (item.reason, "", ""))
        source_file, target_file = files.get(item.source, item.source), files.get(item.target, item.target)
        text = fix_text(fix or "", source_file, target_file) or reason
        rule = f"{item.source_layer} -> {item.target_layer}"
        found.append(Advice(
            id=advice_id(part.path, "fix-violation", f"{source_file} -> {target_file}", rule), part=part.path,
            kind="fix-violation", target=source_file, reason=text, style=part.style, rule=rule,
            evidence=f"{source_file} ({item.source_layer}) imports {target_file} ({item.target_layer}): {reason}",
            modules=(item.source, item.target),
        ))
    return found


def move_candidates(
    part: PartRules, modules: Mapping[str, ModuleFact], dependents: Mapping[str, tuple[str, ...]],
    dependencies: Mapping[str, tuple[str, ...]], layer_folders: Mapping[str, str],
) -> list[Advice]:
    """Files that the dependents of their own part import against the rules."""
    found: list[Advice] = []
    layers = [layer for layer, _ in sorted(part.ranks.items(), key=lambda item: (-item[1], item[0]))]
    for module in sorted((m for m in modules.values() if m.part == part.path and m.certain), key=lambda m: m.id):
        users = [modules[d] for d in dependents.get(module.id, ()) if d in modules and modules[d].part == part.path and modules[d].certain]
        breaking = [u for u in users if (u.layer, module.layer) in part.denies and u.layer != module.layer
                    and part.denies[(u.layer, module.layer)][1] != "candidate" and module.id not in u.known_targets]
        if len(breaking) < MOVE_MIN_BREAKING or len(breaking) * 2 < len(users):
            continue
        uses = [modules[t].layer for t in dependencies.get(module.id, ()) if t in modules and modules[t].part == part.path]

        def allowed(layer: str) -> bool:
            return all(u.layer == layer or (u.layer, layer) not in part.denies for u in users) and all(
                use == layer or (layer, use) not in part.denies for use in uses
            )

        # The highest layer that is not above any dependent: they then import it downwards or sideways.
        ceiling = min(part.ranks.get(u.layer, 0) for u in users)
        choice = next(
            (layer for layer in layers if layer != module.layer and part.ranks[layer] <= ceiling and allowed(layer)), None
        )
        if choice is None:
            continue
        by_layer = Counter(u.layer for u in breaking)
        evidence = (
            f"{len(breaking)} of {len(users)} dependents break a rule by importing it ("
            + ", ".join(f"{layer} {count}" for layer, count in sorted(by_layer.items())) + ")"
            + (f"; it imports {', '.join(f'{layer} {count}' for layer, count in sorted(Counter(uses).items()))}" if uses else "")
        )
        destination = layer_folders.get(choice, choice)
        rule = f"move {module.layer} -> {choice}"
        found.append(Advice(
            id=advice_id(part.path, "move", module.file, rule), part=part.path, kind="move", target=module.file,
            reason=f"move {module.file} from the {module.layer} layer to {choice} ({destination}/): its dependents may import it there",
            style=part.style, rule=rule, evidence=evidence, modules=(module.id,), destination=destination,
        ))
    return found


def advise(
    parts: tuple[PartRules, ...], modules: Mapping[str, ModuleFact], dependents: Mapping[str, tuple[str, ...]],
    dependencies: Mapping[str, tuple[str, ...]], violations: tuple[Violation, ...], targets: tuple[TargetFact, ...],
    layer_folders: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[Advice, ...], tuple[tuple[str, str], ...]]:
    """(advice, parts without advice and why), in a fixed order."""
    files = {module.id: module.file for module in modules.values()}
    advice: list[Advice] = []
    silent: list[tuple[str, str]] = []
    for part in sorted(parts, key=lambda p: p.path):
        if part.state in NO_ADVICE_STATES or part.style in ("none", "undetermined", "-"):
            silent.append((part.path, f"style {part.style} ({part.state}): no advice"))
            continue
        advice += move_candidates(part, modules, dependents, dependencies, layer_folders.get(part.path, {}))
        advice += fix_violations(part, violations, files)
        advice += missing_folders(part, targets)
    order = {"move": 0, "fix-violation": 1, "missing-folder": 2}
    unique = {item.id: item for item in advice}   # an import and a data-flow edge of one pair give one item
    return tuple(sorted(unique.values(), key=lambda a: (order[a.kind], a.part, a.target, a.rule))), tuple(silent)


def to_sexp(item: Advice) -> str:
    """`(advice <part> :id <id> :kind <kind> :target <path> :style <style> :rule <rule> :reason <text> :evidence <text>)`."""
    return (
        f"(advice {q(item.part)} :id {item.id} :kind {item.kind} :target {q(item.target)} :style {item.style} "
        f":rule {q(item.rule)} :reason {q(item.reason)} :evidence {q(item.evidence)})"
    )


def quiet_sexp(part: str, reason: str) -> str:
    return f"(advice-none {q(part)} :reason {q(reason)})"
