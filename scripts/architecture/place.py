"""`wingbox place`: where new code goes, by the [[placement]] rules and the layers of a style.

A request ("a client for the payment database") is split into words
(lowercase, plural folded: entities -> entity, clients -> client; small
words dropped). Each layer of the part's style scores the words it shares:
2 for a word of a placement text of that layer, 1 for a word of the layer
itself (its name, folder words, file-name patterns such as *.repository.ts);
a request word may also be the start of a layer word or the reverse (utility
and utils). The best layer wins; a tie goes to the layer whose own words match, then
to the one with more placement words, then to the order of the style file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .library import Style

_SMALL = frozenset("a an the for of to with and or new that this in on by from into my our is are it its".split())


@dataclass(frozen=True)
class Choice:
    layer: str
    what: str                            # the placement text, or "" when only layer words matched
    where: str                           # the placement's where, as the style states it ("" when none)
    may_import: Optional[tuple[str, ...]]   # None: the style does not state it
    shared_words: tuple[str, ...]


def _fold(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_fold(word) for word in re.findall(r"[a-z]+", text.lower()) if word not in _SMALL and len(word) >= 2}


def _hits(wanted: set[str], words: set[str]) -> set[str]:
    found = set()
    for word in wanted:
        if word in words or any(len(word) >= 4 and len(other) >= 4 and (word.startswith(other) or other.startswith(word))
                                for other in words):
            found.add(word)
    return found


def choose_placement(style: Style, request: str) -> Optional[Choice]:
    """The layer of `style` for the request, or None when no word is shared."""
    wanted = _words(request)
    best: Optional[tuple[tuple[int, int, int], Choice]] = None
    order = [layer.id for layer in style.layers] + sorted({p.layer for p in style.placements} - {l.id for l in style.layers})
    for index, layer_id in enumerate(order):
        layer = style.layer(layer_id)
        placements = [p for p in style.placements if p.layer == layer_id]
        what_hits = _hits(wanted, set().union(*(_words(p.what) for p in placements)) if placements else set())
        own = {layer_id} | set(layer.folders if layer else ()) | {
            pattern.lstrip("*.").split(".")[0] for pattern in (layer.files if layer else ()) if pattern.startswith("*.")
        }
        own_hits = _hits(wanted, _words(" ".join(own).replace("-", " ")))
        score = 2 * len(what_hits) + len(own_hits - what_hits)
        if score == 0:
            continue
        placement = max(placements, key=lambda p: len(_hits(wanted, _words(p.what))), default=None)
        choice = Choice(
            layer=layer_id, what=placement.what if placement else "", where=placement.where if placement else "",
            may_import=placement.may_import if placement else None,
            shared_words=tuple(sorted(what_hits | own_hits)),
        )
        key = (score, len(own_hits), len(what_hits), -index)   # ties: the layer whose own words match
        if best is None or key > best[0]:
            best = (key, choice)
    return best[1] if best else None


def place_declared(part, files: list[str], request: str) -> Optional[tuple[str, str]]:
    """(folder, declared layer) for a part with declared rules: the folder whose path words share the most
    words with the request, and the project's own layer of the files in it."""
    table = part.declared.layers if part.declared is not None else None
    if table is None:
        return None
    wanted = _words(request)
    folders: dict[str, list[str]] = {}
    for path in files:
        folders.setdefault(path.rsplit("/", 1)[0], []).append(path)
    scored = []
    for folder, members in folders.items():
        hits = _hits(wanted, _words(folder.replace("/", " ").replace("-", " ").replace("_", " ")))
        if not hits:
            continue
        base = table.base
        layer = next((table.layer_of(m[len(base) + 1:] if base else m) for m in sorted(members)), None)
        if layer:
            scored.append((-len(hits), folder.count("/"), folder, layer))
    if not scored:
        return None
    _, _, folder, layer = min(scored)
    return folder, layer
