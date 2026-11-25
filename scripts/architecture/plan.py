"""The architecture plan of a repository: per part, the style, its folder -> layer
mapping and where the decision came from (detected, .wingbox/config.yaml,
declared by the project). Pure: file access goes through `read_text`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional

from .declared import DeclaredLayers, NotRead, read_baseline, read_layer_table
from .detect import (
    DeclaredFile,
    Judgement,
    Part,
    ReadText,
    c4_level,
    declared_files,
    is_test_file,
    judge,
    layer_dictionary,
    part_kind,
    part_roots,
    split_parts,
)
from .library import Library, Style, load_library
from .words import DEFAULT_SETTINGS, MatchSettings, match_name, normalise

NO_STYLE = "none"
UNDETERMINED = "undetermined"
DECLARED = "declared"


@dataclass(frozen=True)
class LayerFolder:
    folder: str      # folder name as found in the part
    layer: str
    match: str       # exact | spelling | config
    via: str         # dictionary word (or the config file)
    files: int


@dataclass(frozen=True)
class DeclaredRules:
    files: tuple[DeclaredFile, ...]
    layers: Optional[DeclaredLayers]                 # the layer table that was read, if any
    not_read: tuple[NotRead, ...]
    baseline: frozenset[tuple[str, str]]             # known exceptions, relative to layers.base
    baseline_file: str


@dataclass(frozen=True)
class PartPlan:
    path: str                    # "." for the repository root
    kind: str                    # npm package, Rust crate, ...
    level: str                   # system | container | component (C4)
    files: int                   # source files judged
    style: str                   # a style id, "none", "undetermined" or "declared"
    state: str                   # determined | undetermined | none | not-judged | declared | declared-unread | configured
    origin: str                  # detected | config | declared
    score: Optional[float]
    judgement: Optional[Judgement]
    layers: tuple[LayerFolder, ...]
    routing_root: tuple[str, ...]
    declared: Optional[DeclaredRules]
    overlays: tuple[str, ...] = ()   # overlays found next to the style (cqrs)
    capabilities: tuple = ()         # CapabilityRule restated by .wingbox/config.yaml for this part

    @property
    def has_rules(self) -> bool:
        """True when boundary rules are stated for this part."""
        if self.style == DECLARED:
            return self.declared is not None and self.declared.layers is not None
        return self.style not in (NO_STYLE, UNDETERMINED)


@dataclass(frozen=True)
class RepositoryPlan:
    parts: tuple[PartPlan, ...]
    roots: tuple[str, ...]       # part folders, deepest first ("" = root)
    empty: tuple[str, ...] = ()  # parts .wingbox/config.yaml lists that hold no source file

    def part_of(self, path: str) -> Optional[PartPlan]:
        """The part a repository-relative file belongs to."""
        by_root = {("" if part.path == "." else part.path): part for part in self.parts}
        for root in self.roots:
            if (root == "" or path.startswith(root + "/")) and root in by_root:
                return by_root[root]
        return None


def layer_folders(
    style: Style, part: Part, settings: MatchSettings, library: Optional[Library] = None, cqrs: bool = False,
) -> tuple[tuple[LayerFolder, ...], tuple[str, ...]]:
    """Folders of the part that name a layer of the style, and its routing roots (route-tree)."""
    library = library or load_library()
    dictionary = layer_dictionary(style)
    # Without its own CQRS evidence a part's commands/ and queries/ are not CQRS commands of the domain
    # (a CLI command, a data-fetching query); other layers keep them.
    skipped = frozenset() if cqrs else library.cqrs_words()[0]
    routing = {normalise(name) for name in style.routing_root}
    counts: dict[str, int] = {}
    roots: dict[str, int] = {}
    # Every source file but tests: a folder left out of style detection (migrations, generated) still has a layer.
    for f in (f for f in part.files if not is_test_file(f, library)):
        segments = f.split("/")[:-1]
        inside = False
        for segment in segments:
            if routing and not inside and normalise(segment) in routing:
                roots[segment] = roots.get(segment, 0) + 1
                inside = True
                continue
            if not inside:
                counts[segment] = counts.get(segment, 0) + 1
    found: list[LayerFolder] = []
    for segment in sorted(counts):
        match = match_name(segment, dictionary, settings)
        if match is not None and not (match.role == "domain" and normalise(segment) in skipped):
            found.append(LayerFolder(segment, match.role, match.step, match.via, counts[segment]))
    order = {layer.id: index for index, layer in enumerate(style.layers)}
    found.sort(key=lambda item: (order.get(item.layer, 99), -item.files, item.folder))
    return tuple(found), tuple(sorted(roots, key=lambda name: (-roots[name], name)))


def declared_rules(files: tuple[DeclaredFile, ...], read_text: ReadText) -> DeclaredRules:
    layers: Optional[DeclaredLayers] = None
    not_read: list[NotRead] = []
    baseline: frozenset[tuple[str, str]] = frozenset()
    baseline_file = ""
    for item in files:
        found = read_text(item.path)
        text = found or ""
        if item.kind == "exceptions":
            baseline, baseline_file = read_baseline(text), item.path
            continue
        if found is None:   # the reader said why, when the file is there
            not_read.append(NotRead(item.path, "the file is missing or cannot be read"))
            continue
        if item.kind == "rules" and item.path.endswith((".mjs", ".js", ".cjs")) and layers is None:
            result = read_layer_table(item.path, text)
            if isinstance(result, DeclaredLayers):
                layers = result
                continue
            not_read.append(result)
            continue
        not_read.append(NotRead(item.path, "format not read by this version"))
    return DeclaredRules(files=files, layers=layers, not_read=tuple(not_read), baseline=baseline, baseline_file=baseline_file)


def detect_plan(
    paths: tuple[str, ...], read_text: ReadText, library: Library, settings: MatchSettings = DEFAULT_SETTINGS,
) -> RepositoryPlan:
    """Parts, levels and styles of a repository from its file list alone (no configuration)."""
    roots = part_roots(paths, library)
    plans: list[PartPlan] = []
    for part in split_parts(paths, library):
        level = c4_level(part, paths, library, read_text)
        declared = declared_files(part, paths, roots, library, read_text)
        base = PartPlan(
            path=part.path, kind=part_kind(part), level=level, files=len(part.judged), style=NO_STYLE,
            state="not-judged", origin="detected", score=None, judgement=None, layers=(), routing_root=(),
            declared=None,
        )
        if any(item.kind in ("rules", "manifest-field") for item in declared):
            rules = declared_rules(declared, read_text)
            plans.append(replace(
                base, style=DECLARED, origin="declared", declared=rules,
                state="declared" if rules.layers is not None else "declared-unread",
                judgement=judge(part, library, settings),
            ))
            continue
        judgement = judge(part, library, settings)
        if judgement.state == "not-judged":
            plans.append(replace(base, judgement=judgement))
        elif judgement.state == "undetermined" or judgement.best is None:
            plans.append(replace(base, style=UNDETERMINED if judgement.candidates else NO_STYLE,
                                 state="undetermined" if judgement.candidates else "none", judgement=judgement))
        else:
            style = library.style(judgement.best.style)
            assert style is not None
            overlays = tuple(overlay.style for overlay in judgement.overlays)
            layers, routing = layer_folders(style, part, settings, library, cqrs="cqrs" in overlays)
            plans.append(replace(base, style=style.id, state="determined", score=judgement.best.score,
                                 judgement=judgement, layers=layers, routing_root=routing, overlays=overlays))
    return RepositoryPlan(parts=tuple(plans), roots=roots)


def unknown_learned(plan: RepositoryPlan, learned: Mapping[str, list], library: Optional[Library] = None) -> tuple:
    """The learned folders whose layer is no layer of the style of their part: `apply_learned` leaves
    them out, and the caller says so."""
    library = library or load_library()
    found = []
    for part in plan.parts:
        style = library.style(part.style)
        if part.has_rules and part.style != DECLARED:
            found += [item for item in learned.get(part.path, []) if style is None or style.layer(item.layer) is None]
    return tuple(found)


def apply_learned(
    plan: RepositoryPlan, learned: Mapping[str, list], from_config: bool = False, library: Optional[Library] = None,
) -> RepositoryPlan:
    """Folders the decisions store learned (`learned-layer`) take precedence over the style's words.

    With a .wingbox/config.yaml (`from_config`) the config states the layers: nothing is applied, so that
    removing a learned folder from the config undoes it. A layer that the style of the part does not have
    (`unknown_learned`) is not applied: a line of the store never invents a layer.
    """
    if not learned or from_config:
        return plan
    unknown = unknown_learned(plan, learned, library)
    parts = []
    for part in plan.parts:
        items = [item for item in learned.get(part.path, []) if item not in unknown]
        if items and part.has_rules and part.style != DECLARED:
            taught = tuple(LayerFolder(item.folder, item.layer, "learned", item.source, 0) for item in items)
            part = replace(part, layers=taught + part.layers)
        parts.append(part)
    return replace(plan, parts=tuple(parts))
