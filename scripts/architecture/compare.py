"""`wingbox compare`: the architectures of the parts of a monorepo, side by side, per language.

For each language: every part with its style and layer set; the differences
between them - a layer some parts have and others lack, a folder name that
means another layer in another part, a style that differs from the majority
(its rules differ) - and an advice item for a part that deviates from the
majority style of its language. Pure.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .advice import Advice, advice_id
from .plan import PartPlan
from .words import normalise


@dataclass(frozen=True)
class ComparedPart:
    part: str
    language: str
    style: str
    layers: tuple[str, ...]


@dataclass(frozen=True)
class Difference:
    language: str
    kind: str      # missing-layer | folder-meaning | style
    detail: str


def compare(parts: tuple[PartPlan, ...], languages: Mapping[str, str]) -> tuple[tuple[ComparedPart, ...], tuple[Difference, ...], tuple[Advice, ...]]:
    """`languages`: part path -> language. Parts without a style or a language are left out."""
    compared = tuple(
        ComparedPart(part.path, languages[part.path], part.style, tuple(sorted({f.layer for f in part.layers})))
        for part in sorted(parts, key=lambda p: p.path)
        if languages.get(part.path) and part.style not in ("none", "undetermined") and part.state != "not-judged"
    )
    differences: list[Difference] = []
    advice: list[Advice] = []
    for language in sorted({c.language for c in compared}):
        group = [c for c in compared if c.language == language]
        if len(group) < 2:
            continue
        styles = Counter(c.style for c in group)
        majority, count = styles.most_common(1)[0]
        for item in group:
            if item.style != majority and count > len(group) / 2:
                differences.append(Difference(language, "style", f"{item.part} is {item.style}, {count} of {len(group)} {language} parts are {majority}"))
                rule = f"majority {majority}"
                advice.append(Advice(
                    advice_id(item.part, "deviates", item.part, rule), item.part, "deviates", item.part,
                    f"{item.part} follows {item.style} while most {language} parts follow {majority}; align it or state why",
                    item.style, rule, f"{count} of {len(group)} {language} parts are {majority}",
                ))
        same_style = [c for c in group if c.style == majority]
        everywhere = set.union(*(set(c.layers) for c in same_style)) if same_style else set()
        for item in same_style:
            missing = sorted(everywhere - set(item.layers))
            if missing:
                differences.append(Difference(language, "missing-layer", f"{item.part} has no {', '.join(missing)}"))
        meanings: dict[str, set[tuple[str, str]]] = {}
        by_path = {p.path: p for p in parts}
        for item in group:
            for folder in by_path[item.part].layers:
                meanings.setdefault(normalise(folder.folder), set()).add((item.part, f"{item.style}:{folder.layer}"))
        for name, uses in sorted(meanings.items()):
            if len({meaning for _, meaning in uses}) > 1:
                detail = "; ".join(f"{part} {meaning}" for part, meaning in sorted(uses))
                differences.append(Difference(language, "folder-meaning", f"{name}/ means different layers: {detail}"))
    return compared, tuple(differences), tuple(advice)
