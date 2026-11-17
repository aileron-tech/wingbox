"""Matching a folder name to the words of a style: exact name, spelling variant, synonym.

Three steps, cheapest first; the first that matches decides and is recorded:

1. exact     the normalised name is a word of the dictionary (lowercase, "-" and
             "_" dropped, a dotted name read by its last word: index.toml).
2. spelling  Levenshtein distance 1 to a dictionary word when both have at least
             SPELLING_MIN_LENGTH letters (controlers, repositorys).
3. synonym   (only with --synonyms) the nearest dictionary word by cosine similarity of word vectors
             (built-in GloVe table, vectors/), when the similarity reaches
             SYNONYM_THRESHOLD and leads the best word of another role by
             SYNONYM_MARGIN. A folder name of several words (domain-services,
             UserRepository, usecases) is the mean of its words' vectors.

Only folder names and query words are looked up; source code is never turned
into vectors. The thresholds were set on the tune half of
benchmarks/architecture/labels.tsv; the results on the test half are in
benchmarks/architecture/README.md.
"""
from __future__ import annotations

import array
import functools
import math
import operator
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional

VECTORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors")
DIMENSIONS = 100
SPELLING_MIN_LENGTH = 7
SYNONYM_THRESHOLD = 0.74
SYNONYM_MARGIN = 0.10

MatchStep = Literal["exact", "spelling", "synonym"]


@dataclass(frozen=True)
class WordMatch:
    role: str          # the role (layer, group) the name maps to
    step: MatchStep    # which step produced the match
    via: str           # the dictionary word it matched
    similarity: float  # 1.0 for exact, 1 - distance / length for spelling, cosine for synonym


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
# Word vectors (read-only table shipped with the package)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VectorTable:
    rows: Mapping[str, int]
    data: array.array   # signed bytes, DIMENSIONS per row

    def vector(self, word: str) -> Optional[tuple[float, ...]]:
        index = self.rows.get(word)
        if index is None:
            return None
        start = index * DIMENSIONS
        return _unit(tuple(float(value) for value in self.data[start:start + DIMENSIONS]))


def _unit(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / norm for value in vector)


@functools.lru_cache(maxsize=1)
def load_vectors(directory: str = VECTORS_DIR) -> Optional[VectorTable]:
    """The built-in table, or None when its files are missing (matching then stops at spelling)."""
    try:
        with open(os.path.join(directory, "words.txt"), "r", encoding="ascii") as handle:
            words = handle.read().split()
        data = array.array("b")
        with open(os.path.join(directory, "glove100.i8"), "rb") as handle:
            data.frombytes(handle.read())
    except OSError:
        return None
    if len(data) != len(words) * DIMENSIONS:
        return None
    return VectorTable(rows={word: index for index, word in enumerate(words)}, data=data)


def split_words(segment: str, table: VectorTable) -> tuple[str, ...]:
    """Words of a folder name: split at -, _, ., digits and camelCase; a joined word the
    table does not know is split once into two words it knows (usecases -> use cases)."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", segment)
    pieces = [piece for piece in re.split(r"[^A-Za-z]+", spaced.lower()) if piece]
    words: list[str] = []
    for piece in pieces:
        if piece in table.rows:
            words.append(piece)
            continue
        split = next(
            (
                (piece[:cut], piece[cut:])
                for cut in range(len(piece) - 3, 2, -1)
                if piece[:cut] in table.rows and piece[cut:] in table.rows
            ),
            None,
        )
        if split:
            words.extend(split)
    return tuple(words)


@functools.lru_cache(maxsize=65536)
def name_vector(segment: str) -> Optional[tuple[float, ...]]:
    """Mean unit vector of the words of a folder name; None when no word is known."""
    table = load_vectors()
    if table is None:
        return None
    vectors = [vector for vector in (table.vector(word) for word in split_words(segment, table)) if vector]
    if not vectors:
        return None
    return _unit(tuple(sum(column) for column in zip(*vectors)))


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(map(operator.mul, a, b))


# ---------------------------------------------------------------------------
# Dictionaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dictionary:
    """Words -> role, for one style (or one group set), with the vectors of the words."""

    words: Mapping[str, str]                       # normalised word -> role
    vectors: tuple[tuple[str, str, tuple[float, ...]], ...]   # (word, role, unit vector)


def make_dictionary(entries: Iterable[tuple[str, str]], use_vectors: bool = True) -> Dictionary:
    """Dictionary from (word, role) pairs; the first role given for a word wins."""
    words: dict[str, str] = {}
    raw: dict[str, str] = {}
    for word, role in entries:
        key = normalise(word)
        if key and key not in words:
            words[key] = role
            raw[key] = word
    vectors: list[tuple[str, str, tuple[float, ...]]] = []
    if use_vectors:
        for key, role in sorted(words.items()):
            vector = name_vector(raw[key])
            if vector is not None:
                vectors.append((key, role, vector))
    return Dictionary(words=words, vectors=tuple(vectors))


@dataclass(frozen=True)
class MatchSettings:
    spelling: bool = True
    synonyms: bool = True
    threshold: float = SYNONYM_THRESHOLD
    margin: float = SYNONYM_MARGIN
    spelling_min_length: int = SPELLING_MIN_LENGTH


# The synonym step is off by default (`--synonyms` turns it on for folder -> layer
# mapping): on the held-out half of benchmarks/architecture it matched more
# folders but lowered accuracy (75.4 % against 76.2 %). Style detection never
# uses it: it made detection worse (30 of 33 parts right instead of 31).
DEFAULT_SETTINGS = MatchSettings(synonyms=False)
SYNONYM_SETTINGS = MatchSettings(synonyms=True)
DETECTION_SETTINGS = MatchSettings(synonyms=False)


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
    if settings.synonyms and dictionary.vectors:
        vector = name_vector(segment)
        if vector is None:
            return None
        best: dict[str, tuple[float, str]] = {}
        for word, word_role, word_vector in dictionary.vectors:
            similarity = _cosine(vector, word_vector)
            if similarity > best.get(word_role, (-2.0, ""))[0]:
                best[word_role] = (similarity, word)
        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        top_role, (top_similarity, top_word) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else -1.0
        if top_similarity >= settings.threshold and top_similarity - runner_up >= settings.margin:
            return WordMatch(role=top_role, step="synonym", via=top_word, similarity=round(top_similarity, 3))
    return None


# Hashing a Dictionary for the cache: identity is enough, dictionaries are built once per style.
Dictionary.__hash__ = object.__hash__  # type: ignore[assignment]
Dictionary.__eq__ = object.__eq__      # type: ignore[assignment]
