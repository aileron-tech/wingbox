"""Matching a folder name to the words of a style: exact name, spelling variant.

Two steps, cheapest first; the first that matches decides and is recorded:

1. exact     the normalised name is a word of the dictionary (lowercase, "-" and
             "_" dropped, a dotted name read by its last word: index.toml).
2. spelling  Levenshtein distance 1 to a dictionary word when both have at least
             SPELLING_MIN_LENGTH letters (controlers, repositorys).

The results of both steps on the test half of benchmarks/architecture/labels.tsv
are in benchmarks/architecture/README.md.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional

SPELLING_MIN_LENGTH = 7

MatchStep = Literal["exact", "spelling"]


@dataclass(frozen=True)
class WordMatch:
    role: str          # the role (layer, group) the name maps to
    step: MatchStep    # which step produced the match
    via: str           # the dictionary word it matched
    similarity: float  # 1.0 for exact, 1 - distance / length for spelling


def normalise(segment: str) -> str:
    """index.toml [matching]: lowercase, drop - and _, read a dotted name by its last word."""
    lowered = segment.lower()
    word = lowered if lowered.startswith(".") else lowered.split(".")[-1]
    return word.replace("-", "").replace("_", "")


def levenshtein(a: str, b: str) -> int:
    """Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# Dictionaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dictionary:
    """Words -> role, for one style (or one group set)."""

    words: Mapping[str, str]                       # normalised word -> role


def make_dictionary(entries: Iterable[tuple[str, str]]) -> Dictionary:
    """Dictionary from (word, role) pairs; the first role given for a word wins."""
    words: dict[str, str] = {}
    for word, role in entries:
        key = normalise(word)
        if key and key not in words:
            words[key] = role
    return Dictionary(words=words)


@dataclass(frozen=True)
class MatchSettings:
    spelling: bool = True
    spelling_min_length: int = SPELLING_MIN_LENGTH


DEFAULT_SETTINGS = MatchSettings()


def match_name(segment: str, dictionary: Dictionary, settings: MatchSettings = DEFAULT_SETTINGS) -> Optional[WordMatch]:
    """The role of a folder name in a dictionary, or None."""
    return _match_cached(segment, dictionary, settings)


@functools.lru_cache(maxsize=262144)
def _match_cached(segment: str, dictionary: Dictionary, settings: MatchSettings) -> Optional[WordMatch]:
    key = normalise(segment)
    if not key:
        return None
    role = dictionary.words.get(key)
    if role is not None:
        return WordMatch(role=role, step="exact", via=key, similarity=1.0)
    if settings.spelling and len(key) >= settings.spelling_min_length:
        close = sorted(
            (word for word in dictionary.words if len(word) >= settings.spelling_min_length and abs(len(word) - len(key)) <= 1),
        )
        for word in close:
            if levenshtein(key, word) == 1:
                return WordMatch(role=dictionary.words[word], step="spelling", via=word, similarity=1 - 1 / len(word))
    return None


# Hashing a Dictionary for the cache: identity is enough, dictionaries are built once per style.
Dictionary.__hash__ = object.__hash__  # type: ignore[assignment]
Dictionary.__eq__ = object.__eq__      # type: ignore[assignment]
