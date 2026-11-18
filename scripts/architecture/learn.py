"""`align --learn`: match the part against the style library, keep what the closest style places, and
derive the rest from names and patterns.

1. Every style of the library is scored on the part (detect.judge); the closest one is the base, stated
   with its score and the runner-up.
2. The folders that style places keep its layers; its target folders stay as the style defines them.
3. A top folder whose files get no layer from it is derived, first rule that applies:
   suffix   most of its files are named like files of one layer (*.controller.ts, *.repository.ts,
            *.dto.ts, *.entity.ts, *.worker.ts), in any style of the library, for a layer the base has;
   name     its name is a folder word of a layer in another style, a layer the base has;
   imports  the layer that gives its dependencies the fewest broken rules (align.layers_by_imports).
Each derived folder keeps its evidence. Declared rules are never learned over. Pure.
"""
from __future__ import annotations

import fnmatch
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Optional

from .detect import Judgement, is_test_file, layer_dictionary
from .labels import label_in_part
from .library import Library, Style
from .plan import PartPlan
from .words import DEFAULT_SETTINGS, match_name

MIN_FOLDER_FILES = 2


@dataclass(frozen=True)
class Closest:
    style: str
    score: float
    coverage: float
    runner_up: str
    runner_up_score: float


@dataclass(frozen=True)
class Derived:
    folder: str
    layer: str
    by: str      # suffix | name | imports
    why: str


def closest_style(judgement: Judgement) -> Optional[Closest]:
    """The style closest to the part: the determined one, else the best score of all."""
    scores = list(judgement.candidates) + [s for s in judgement.near if s not in judgement.candidates]
    if judgement.best is not None:
        scores = [judgement.best] + [s for s in scores if s.style != judgement.best.style]
    scores = sorted(scores, key=lambda s: (s is not judgement.best, -s.score, -s.coverage, s.style))
    if not scores:
        return None
    first = scores[0]
    second = scores[1] if len(scores) > 1 else None
    return Closest(first.style, first.score, first.coverage, second.style if second else "-", second.score if second else 0.0)


def _top(relative: str) -> str:
    segments = relative.split("/")[:-1]
    if segments and segments[0] == "src":
        segments = segments[1:]
    return segments[0] if segments else ""


def unplaced_folders(base: PartPlan, files: tuple[str, ...], library: Library) -> dict[str, tuple[str, ...]]:
    """Top folders (below src/) none of whose source files gets a layer from the base style."""
    prefix = "" if base.path == "." else base.path + "/"
    by_folder: dict[str, list[str]] = {}
    for relative in files:
        if is_test_file(relative, library):
            continue
        by_folder.setdefault(_top(relative), []).append(relative)
    style = library.style(base.style)
    routing = {name.lower() for name in (style.routing_root if style else ())}
    return {
        folder: tuple(members) for folder, members in sorted(by_folder.items())
        if folder and folder.lower() not in routing and len(members) >= MIN_FOLDER_FILES
        and all(label_in_part(prefix + f, base, library).layer == "other" for f in members)
    }


def derive(
    folder: str, members: tuple[str, ...], base: Style, library: Library, by_imports: Mapping[str, tuple[str, str]],
) -> Optional[Derived]:
    layers = {layer.id for layer in base.layers}
    suffix: Counter[tuple[str, str]] = Counter()
    for style in library.styles:
        for layer in style.layers:
            if layer.id not in layers:
                continue
            for pattern in layer.files:
                if "*" in pattern and pattern.startswith("*."):
                    hits = sum(1 for f in members if fnmatch.fnmatchcase(f.rsplit("/", 1)[-1].lower(), pattern.lower()))
                    if hits:
                        suffix[(layer.id, pattern)] = max(suffix[(layer.id, pattern)], hits)
    if suffix:
        (layer, pattern), hits = max(suffix.items(), key=lambda item: (item[1], item[0]))
        if hits * 2 >= len(members):
            return Derived(folder, layer, "suffix", f"{hits} of {len(members)} files are named {pattern}")
    for style in library.styles:
        if style.id == base.id:
            continue
        match = match_name(folder, layer_dictionary(style), DEFAULT_SETTINGS)
        if match is not None and match.role == "domain" and match.via in library.cqrs_words()[0]:
            continue   # commands/, queries/ are CQRS words: not domain without the CQRS overlay
        if match is not None and match.role in layers:
            return Derived(folder, match.role, "name", f"{folder}/ is a {match.role} folder in {style.id} ({match.step} of {match.via})")
    if folder in by_imports:
        layer, why = by_imports[folder]
        return Derived(folder, layer, "imports", why)
    return None
