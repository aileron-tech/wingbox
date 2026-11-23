"""Rules a project declares itself: read when they are plain data, named otherwise.

Read today: a dependency-cruiser layer list in a JavaScript module (layers.mjs
and the like) - an array literal of objects with a string `name`, a regex string
`path` and/or a string array `files`, in order lowest layer first, in a file that
states that a layer may not import a higher one ("no-higher-layer" or "lowest
first"). A file belongs to the layer that pins it in `files`, else to the first
layer whose `path` matches; paths are relative to the folder of the file. Any
other value in a layer entry (a spread, a call, a computed key) makes the file
"not read": its rules are named, no violation is stated from them.

A baseline of known exceptions (layers.known-upward-imports.json: a JSON list of
"src/a.ts -> src/b.ts (rule)") is read so the report can say which of its
findings the project already knows.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeclaredLayer:
    name: str
    path: Optional[str]           # regex relative to the folder of the declaring file
    files: tuple[str, ...]        # files pinned to this layer
    folders: tuple[str, ...] = () # the folders of an `order:` entry of .wingbox/config.yaml, as written


@dataclass(frozen=True)
class DeclaredLayers:
    file: str                     # repository-relative path of the declaring file
    base: str                     # folder the patterns are relative to ("" for the root)
    layers: tuple[DeclaredLayer, ...]

    def layer_of(self, relative: str) -> Optional[str]:
        """Layer of a path relative to `base` (dependency-cruiser layerOf without tests)."""
        for layer in self.layers:
            if relative in layer.files:
                return layer.name
        pinned = {f for layer in self.layers for f in layer.files}
        if relative in pinned:
            return None
        for layer in self.layers:
            if layer.path and _search(layer.path, relative):
                return layer.name
        return None

    def rank(self, name: str) -> int:
        return next(index for index, layer in enumerate(self.layers) if layer.name == name)


@dataclass(frozen=True)
class NotRead:
    file: str
    reason: str


_REGEX_CACHE: dict[str, Optional[re.Pattern[str]]] = {}


def _search(pattern: str, text: str) -> bool:
    if pattern not in _REGEX_CACHE:
        try:
            _REGEX_CACHE[pattern] = re.compile(pattern)
        except re.error:
            _REGEX_CACHE[pattern] = None
    compiled = _REGEX_CACHE[pattern]
    return bool(compiled and compiled.search(text))


# ---------------------------------------------------------------------------
# A small reader of JavaScript literals: strings, arrays, objects, comments.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s+|//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\.\.\.|[\[\]{}(),:]|[^\s]""",
    re.S,
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text) if not t.isspace() and not t.startswith(("//", "/*"))]


def _string(token: str) -> Optional[str]:
    if len(token) >= 2 and token[0] in "\"'" and token[-1] == token[0]:
        body = token[1:-1]
        try:
            return json.loads('"' + body.replace('"', '\\"').replace("\\'", "'") + '"')
        except ValueError:
            return None
    return None


class _Unreadable(Exception):
    pass


def _parse_value(tokens: list[str], i: int) -> tuple[object, int]:
    token = tokens[i]
    if token == "[":
        items: list[object] = []
        i += 1
        while tokens[i] != "]":
            value, i = _parse_value(tokens, i)
            items.append(value)
            if tokens[i] == ",":
                i += 1
        return items, i + 1
    if token == "{":
        entries: dict[str, object] = {}
        i += 1
        while tokens[i] != "}":
            key = tokens[i]
            if key == "...":
                raise _Unreadable("a spread in a layer entry")
            name = _string(key) if key[0] in "\"'" else key
            if name is None or tokens[i + 1] != ":":
                raise _Unreadable(f"an entry that is not plain data near {key!r}")
            value, i = _parse_value(tokens, i + 2)
            entries[name] = value
            if tokens[i] == ",":
                i += 1
        return entries, i + 1
    text = _string(token)
    if text is not None:
        return text, i + 1
    # An identifier or another expression: kept as an opaque marker, skipped to the next separator.
    depth = 0
    start = i
    while not (depth == 0 and tokens[i] in (",", "}", "]")):
        if tokens[i] in "([{":
            depth += 1
        elif tokens[i] in ")]}":
            depth -= 1
        i += 1
    return ("expr", " ".join(tokens[start:i])), i


def layers_from_order(file: str, base: str, order: tuple[tuple[str, tuple[str, ...]], ...]) -> DeclaredLayers:
    """The layer table of an `order:` list of .wingbox/config.yaml: (layer, folders) entries, lowest first,
    the folders relative to the part (`.`: the files directly in it)."""
    layers = []
    for name, folders in order:
        patterns = ["[^/]+$" if folder.strip("/") in (".", "") else re.escape(folder.strip("/")) + "/" for folder in folders]
        layers.append(DeclaredLayer(name=name, path="^(?:" + "|".join(patterns) + ")", files=(), folders=tuple(folders)))
    return DeclaredLayers(file=file, base=base, layers=tuple(layers))


def read_layer_table(path: str, text: str) -> DeclaredLayers | NotRead:
    """The layer list of a dependency-cruiser layer module, or why it was not read."""
    if "no-higher-layer" not in text and "lowest first" not in text.lower():
        return NotRead(path, "it states no layer order")
    tokens = _tokens(text)
    for i in range(len(tokens) - 2):
        if tokens[i + 1] == "=" and tokens[i + 2] == "[" and re.search(r"layer", tokens[i], re.I):
            try:
                value, _ = _parse_value(tokens, i + 2)
            except (_Unreadable, IndexError) as error:
                return NotRead(path, str(error) or "unexpected syntax")
            layers = _layers(value)
            if isinstance(layers, str):
                return NotRead(path, layers)
            base = path.rsplit("/", 1)[0] if "/" in path else ""
            return DeclaredLayers(file=path, base=base, layers=layers)
    return NotRead(path, "no array of layers found")


def _layers(value: object) -> tuple[DeclaredLayer, ...] | str:
    if not isinstance(value, list) or not value:
        return "the layer list is empty"
    layers: list[DeclaredLayer] = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            return "a layer without a string name"
        path = entry.get("path")
        files = entry.get("files", [])
        if path is not None and not isinstance(path, str):
            return f"layer {entry['name']}: path is not a plain string"
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            return f"layer {entry['name']}: files is not a list of strings"
        if path is None and not files:
            return f"layer {entry['name']}: neither path nor files"
        layers.append(DeclaredLayer(name=entry["name"], path=path, files=tuple(files)))
    return tuple(layers)


def read_baseline(text: str) -> frozenset[tuple[str, str]]:
    """Known exceptions: "a -> b (rule)" strings of a JSON list, as (a, b) pairs."""
    try:
        items = json.loads(text)
    except ValueError:
        return frozenset()
    pairs: set[tuple[str, str]] = set()
    for item in items if isinstance(items, list) else []:
        match = re.match(r"\s*(\S+)\s*->\s*(\S+)", str(item))
        if match:
            pairs.add((match.group(1), match.group(2)))
    return frozenset(pairs)
