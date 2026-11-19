"""The layer that the role of a file suggests, and the file that stores those guesses.

A module the rules of its part leave in `other` takes, in a layered part, a guess from its purpose
("repository" -> infra, "request handlar" -> service). The guess is a pure function of the purpose text:

- `classify(purpose)`: exact keyword, then keyword inside the purpose, then the closest keyword word by
  edit distance (the closest keyword word of a purpose word is remembered: purposes share their words);
- `guess_key()`: names that function, a hash of the keyword table and of ALGORITHM_VERSION;
- `guesses`, `serialise`, `parse`: the content of .wingbox/cache.layer-guess.json, which a build writes
  with the guess of every distinct purpose of the graph it publishes and a query reads instead of
  matching words again. A file under another key holds the guesses of another table or another matching
  and is not read.

Pure: no file is opened here. The builder writes the file (build_code_graph.publish_layer_guesses) and
the search reads it (code_graph_search.CodeGraph.layer_guess); neither imports the other for it.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Optional

try:
    from .architecture.words import levenshtein
except ImportError:  # loaded as a top-level script module
    from architecture.words import levenshtein

FILE_NAME = "cache.layer-guess.json"   # in .wingbox/, ignored with the other caches
# Raise it with every change of classify or within that can change a guess: files written before are then not read.
ALGORITHM_VERSION = 1

PURPOSE_TO_LAYER: dict[str, str] = {
    # Domain layer
    "domain logic": "domain",
    "domain entity": "domain",
    "data model": "domain",
    "data transfer object": "domain",
    "schema": "domain",
    "enum definitions": "domain",
    "policy": "domain",
    "value object": "domain",
    "aggregate root": "domain",
    "domain event": "domain",
    "Specification pattern": "domain",
    # Infrastructure layer
    "infrastructure adapter": "infra",
    "database migration": "infra",
    "database seed": "infra",
    "repository": "infra",
    "adapter": "infra",
    "gateway": "infra",
    "client": "infra",
    "connector": "infra",
    "scheduler": "infra",
    "worker": "infra",
    "Unit of Work": "infra",
    "Identity Map": "infra",
    "Proxy pattern": "infra",
    "SDK": "infra",
    "API client": "infra",
    # API layer
    "API layer": "api",
    "API controller": "api",
    "router": "api",
    "access guard": "api",
    "middleware": "api",
    "interceptor": "api",
    "validation pipe": "api",
    "resolver": "api",
    # Service layer
    "service": "service",
    "application service": "service",
    "mailer": "service",
    "event handler": "service",
    "handler": "service",
    "event listener": "service",
    "processor": "service",
    "provider": "service",
    "registry": "service",
    "agent": "service",
    "manager": "service",
    "subscriber": "service",
    "dispatcher": "service",
    "emitter": "service",
    "Facade pattern": "service",
    "Mediator pattern": "service",
    "Observer pattern": "service",
    "Command pattern": "service",
    "Chain of Responsibility": "service",
    "Memento pattern": "service",
    # Config layer
    "NestJS wiring module": "config",
    "NestJS injectable": "config",
    "configuration": "config",
    "application entry point": "config",
    "application bootstrap": "config",
    "module entry point": "config",
    # UI layer
    "UI component": "ui",
    "page/route": "ui",
    "React hook": "ui",
    "state store": "other",
    # Test layer
    "test": "test",
    "test fixture": "test",
    "test mock": "test",
    # Shared layer
    "shared utilities": "shared",
    "utility helpers": "shared",
    "constants": "shared",
    "type definitions": "shared",
    "interface definitions": "shared",
    "decorator": "shared",
    "data mapper": "shared",
    "barrel re-exports": "shared",
    "plugin": "shared",
    "Factory pattern": "shared",
    "Builder pattern": "shared",
    "Strategy pattern": "shared",
    "Singleton pattern": "shared",
    "Composite pattern": "shared",
    "Visitor pattern": "shared",
    "Iterator pattern": "shared",
    "Decorator pattern": "shared",
}

NO_GUESS = "other"


def within(word: str, keyword: str, threshold: int) -> int:
    """The edit distance of two words, or threshold + 1 when their lengths alone put it above the threshold."""
    if abs(len(word) - len(keyword)) > threshold:
        return threshold + 1
    return levenshtein(word, keyword)


def _fuzzy_words(table: Mapping[str, str]) -> tuple[tuple[str, int, str], ...]:
    """(keyword word, allowed distance, layer) for the fuzzy step, in the order of the table. Words under
    four letters are left out (api, ui, dto: too ambiguous), a word of five or more may differ by two
    letters, a shorter one by one. A word that an earlier keyword already carries is left out: at the same
    distance the earlier keyword decides."""
    words: dict[str, tuple[str, int, str]] = {}
    for keyword, layer in table.items():
        for word in keyword.lower().split():
            if len(word) >= 4 and word not in words:
                words[word] = (word, 2 if len(word) >= 5 else 1, layer)
    return tuple(words.values())


_FUZZY_WORDS = _fuzzy_words(PURPOSE_TO_LAYER)


@functools.lru_cache(maxsize=65536)
def _closest(word: str, distance: Callable[[str, str, int], int]) -> Optional[tuple[int, int]]:
    """(distance, position in _FUZZY_WORDS) of the keyword word closest to a word of a purpose, the first
    in the table among equally close ones; None when no keyword word is within its allowed distance.
    Remembered per word: the same words come back in purpose after purpose."""
    best: Optional[tuple[int, int]] = None
    for position, (keyword, threshold, _layer) in enumerate(_FUZZY_WORDS):
        found = distance(word, keyword, threshold)
        if found <= threshold and (best is None or found < best[0]):
            best = (found, position)
    return best


def classify(purpose: str, distance: Callable[[str, str, int], int] = within) -> str:
    """Classify layer from the module's purpose string using fuzzy matching.

    Tries exact match, then substring, then Levenshtein distance on
    individual words from the purpose against the keyword map: the closest
    keyword word decides, the first in the table among equally close ones.
    """
    if not purpose or purpose == "general module":
        return NO_GUESS
    pl = purpose.lower()
    # 1. Exact match
    for key, layer in PURPOSE_TO_LAYER.items():
        if key.lower() == pl:
            return layer
    # 2. Substring match
    for key, layer in PURPOSE_TO_LAYER.items():
        if key.lower() in pl:
            return layer
    # 3. Fuzzy word match: each word of the purpose (four letters or more) against the keyword words.
    best: Optional[tuple[int, int]] = None
    for word in dict.fromkeys(re.split(r"[\s;,/]+", pl)):
        if len(word) < 4:
            continue
        found = _closest(word, distance)
        if found is not None and (best is None or found < best):
            best = found
    return _FUZZY_WORDS[best[1]][2] if best is not None else NO_GUESS


def guess_key(table: Optional[Mapping[str, str]] = None, version: Optional[int] = None) -> str:
    """What names the guess function: the keyword table in its order (the first keyword that matches
    decides) and the version of the matching; by default those of this module."""
    digest = hashlib.sha256(f"layer-guess {ALGORITHM_VERSION if version is None else version}\n".encode("utf-8"))
    for keyword, layer in (PURPOSE_TO_LAYER if table is None else table).items():
        digest.update(f"{keyword}\0{layer}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def guesses(purposes: Iterable[str], known: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The guess of every distinct purpose, sorted by purpose; one that `known` (the guesses of a file
    with the current key) already holds is taken from it instead of being matched again."""
    held = known or {}
    return {purpose: held[purpose] if purpose in held else classify(purpose) for purpose in sorted(set(purposes)) if purpose}


def serialise(layers: Mapping[str, str]) -> str:
    """The text of the file: the current key and the guesses, the same bytes for the same guesses."""
    return json.dumps({"key": guess_key(), "guesses": dict(layers)}, sort_keys=True, separators=(",", ":")) + "\n"


def parse(text: str) -> dict[str, str]:
    """The guesses of a file text; {} when it is not such a file or was written under another key.
    An entry whose layer is not one the table can give is left out, so that purpose is matched."""
    try:
        stored = json.loads(text)
    except (ValueError, RecursionError):   # not JSON, cut short, or nested beyond reading
        return {}
    if not isinstance(stored, dict) or stored.get("key") != guess_key():
        return {}
    layers = stored.get("guesses")
    if not isinstance(layers, dict):
        return {}
    possible = {NO_GUESS, *PURPOSE_TO_LAYER.values()}
    return {purpose: layer for purpose, layer in layers.items() if isinstance(layer, str) and layer in possible}
