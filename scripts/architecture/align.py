"""`wingbox align`: architecture gaps of each part, against its style or another one, and learning.

Pure functions over plain values (the command line builds them from the code
graph). For a part with rules:

- gaps: the share of its files that have a layer, the layers of its style
  present and missing, the share of its dependencies (between placed files)
  that follow the rules, the folders without a layer, and the rules broken
  with their count and an example;
- `--to <style>`: the same rules count, after mapping each current layer to
  the target style through its `[from.<style>]` table (a migration view);
- learning (learn.py): the closest style of the library places the folders
  it knows; a folder it does not place gets a layer from its file names, its
  folder name in another style, and last from its imports (layers_by_imports
  here); what still breaks a rule is the baseline.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .advice import ModuleFact, PartRules
from .words import normalise

MIN_LAYER_SHARE = 0.05
MIN_FOLDER_FILES = 2


@dataclass(frozen=True)
class Gap:
    kind: str        # unplaced-folder | missing-layer | rule
    target: str      # the folder, the layer, or "<from> -> <to>"
    count: int
    example: str


@dataclass(frozen=True)
class PartAlignment:
    part: str
    style: str
    placed: int
    files: int
    following: int           # dependencies between placed files that follow the rules
    dependencies: int
    layers_present: tuple[str, ...]
    layers_missing: tuple[str, ...]
    gaps: tuple[Gap, ...]


@dataclass(frozen=True)
class Learned:
    part: str
    folder: str
    layer: str
    why: str


@dataclass(frozen=True)
class BaselineEntry:
    part: str
    source: str
    target: str
    rule: str
    origin: str      # the project's own baseline file, or "align"


def _folder(module: ModuleFact, part: str) -> str:
    """The first folder below the source root of the part (src/ when the file is under it)."""
    relative = module.file if part == "." else module.file[len(part) + 1:]
    segments = relative.split("/")[:-1]
    if segments and segments[0] == "src":
        segments = segments[1:]
    return segments[0] if segments else ""


def _edges(part: PartRules, modules: Mapping[str, ModuleFact], dependencies: Mapping[str, tuple[str, ...]]):
    for source, targets in sorted(dependencies.items()):
        left = modules.get(source)
        if left is None or left.part != part.path or left.layer == "test":
            continue
        for target in targets:
            right = modules.get(target)
            if right is not None and right.part == part.path and right.layer != "test":
                yield left, right


def _broken(part: PartRules, source_layer: str, target_layer: str) -> bool:
    rule = part.denies.get((source_layer, target_layer))
    return source_layer != target_layer and rule is not None and rule[1] != "candidate"


def align_part(
    part: PartRules, modules: Mapping[str, ModuleFact], dependencies: Mapping[str, tuple[str, ...]],
    style_layers: tuple[str, ...], mapping: Mapping[str, str] | None = None,
    type_only: frozenset[tuple[str, str]] = frozenset(),
) -> PartAlignment:
    """Gaps of a part; with `mapping` (current layer -> target layer), against the target rules in `part`."""
    mapping = mapping or {}
    mine = [m for m in modules.values() if m.part == part.path and m.layer != "test"]
    layer_of = {m.id: mapping.get(m.layer, m.layer) for m in mine}
    placed = [m for m in mine if layer_of[m.id] not in ("other", "")]
    present = tuple(sorted({layer_of[m.id] for m in placed}))
    missing = tuple(layer for layer in style_layers if layer not in present)
    total = following = 0
    broken: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for left, right in _edges(part, modules, dependencies):
        a, b = layer_of.get(left.id, "other"), layer_of.get(right.id, "other")
        if a in ("other", "") or b in ("other", ""):
            continue
        total += 1
        # The same rule set as `boundaries`: both layers set by rules (a composition root is never a source),
        # a pair imported for types only is not a break, a candidate rule gives no break.
        if (_broken(part, a, b) and left.certain and right.certain and (left.id, right.id) not in type_only
                and right.id not in left.known_targets):
            key = f"{a} -> {b}"
            broken[key] += 1
            examples.setdefault(key, f"{left.file} -> {right.file}")
        else:
            following += 1
    unplaced: Counter[str] = Counter(_folder(m, part.path) for m in mine if layer_of[m.id] in ("other", ""))
    gaps = [Gap("unplaced-folder", folder or "(root files)", count, "") for folder, count in sorted(unplaced.items())]
    gaps += [Gap("missing-layer", layer, 0, "") for layer in missing]
    gaps += [Gap("rule", rule, count, examples[rule]) for rule, count in sorted(broken.items(), key=lambda kv: (-kv[1], kv[0]))]
    return PartAlignment(part.path, part.style, len(placed), len(mine), following, total, present, missing, tuple(gaps))


def layers_by_imports(
    part: PartRules, modules: Mapping[str, ModuleFact], dependencies: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, str]]:
    """folder -> (layer, evidence) for the folders whose files have no layer: the layer that gives their
    dependencies the fewest broken rules, among the layers a rule names and that hold at least
    MIN_LAYER_SHARE of the placed files; ties go to the place in the dependency graph (below every
    file that imports the folder, not below what it imports). The last step of `align --learn`."""
    mine = {m.id: m for m in modules.values() if m.part == part.path and m.layer != "test"}
    placed = [m for m in mine.values() if m.layer != "other"]
    named = {layer for pair in part.denies for layer in pair}
    shares = Counter(m.layer for m in placed)
    candidates = sorted(l for l in named if placed and shares[l] / len(placed) >= MIN_LAYER_SHARE)
    edges = list(_edges(part, modules, dependencies))
    by_folder: dict[str, list[ModuleFact]] = {}
    for module in mine.values():
        by_folder.setdefault(_folder(module, part.path), []).append(module)
    found: dict[str, tuple[str, str]] = {}
    for folder, members in sorted(by_folder.items()):
        if not folder or len(members) < MIN_FOLDER_FILES or any(m.layer != "other" for m in members):
            continue
        ids = {m.id for m in members}
        touching = [(l, r) for l, r in edges if (l.id in ids) != (r.id in ids)]
        if not any((r if l.id in ids else l).layer != "other" for l, r in touching):
            continue

        def broken_as(layer: str) -> int:
            return sum(
                1 for left, right in touching
                if "other" not in ((layer if left.id in ids else left.layer), (layer if right.id in ids else right.layer))
                and _broken(part, layer if left.id in ids else left.layer, layer if right.id in ids else right.layer)
            )

        users = [part.ranks.get(l.layer, 0) for l, r in touching if r.id in ids and l.layer != "other"]
        used = [part.ranks.get(r.layer, 0) for l, r in touching if l.id in ids and r.layer != "other"]

        def placed_well(layer: str) -> int:
            rank = part.ranks.get(layer, 0)
            return 0 if (not users or rank < min(users)) and (not used or rank >= max(used)) else 1

        scored = sorted((broken_as(layer), placed_well(layer), -part.ranks.get(layer, 0), layer) for layer in candidates)
        if scored:
            count, _, _, layer = scored[0]
            found[folder] = (layer, f"{len(touching)} dependencies, {count} against the rules as {layer}")
    return found


def baseline(
    part: PartRules, modules: Mapping[str, ModuleFact], dependencies: Mapping[str, tuple[str, ...]],
    learned: Mapping[str, str], project_baseline: Mapping[tuple[str, str], str],
    type_only: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[BaselineEntry, ...]:
    """What still breaks a rule after learning (`learned`: folder -> layer)."""
    entries: list[BaselineEntry] = []
    for left, right in _edges(part, modules, dependencies):
        a = learned.get(_folder(left, part.path), left.layer) if left.layer == "other" else left.layer
        b = learned.get(_folder(right, part.path), right.layer) if right.layer == "other" else right.layer
        if "other" in (a, b) or (left.id, right.id) in type_only:
            continue
        sure = (left.certain or left.layer == "other") and (right.certain or right.layer == "other")
        if _broken(part, a, b) and sure:
            origin = project_baseline.get((left.id, right.id), "align")
            entries.append(BaselineEntry(part.path, left.id, right.id, f"{a} -> {b}", origin))
    return tuple(entries)
