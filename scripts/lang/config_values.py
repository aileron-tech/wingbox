"""Names listed in the configuration files of one folder (JSON, YAML, TOML).

A module loader that builds a module name from a variable takes the variable
from somewhere; the configuration files beside it are where a static reader can
see the values. Only strings shaped like a Python name or dotted path are kept,
in the order of the file, so that an ordered list (a pipeline) keeps its order.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from typing import Iterator

CONFIG_SUFFIXES = (".json", ".yaml", ".yml", ".toml")
MAX_CONFIG_BYTES = 1_000_000
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_YAML_SCALAR_RE = re.compile(r"^\s*(?:-\s+)?(?:[\w.-]+\s*:(?:\s+|$))?(.*)$")


@dataclass(frozen=True)
class ConfigValues:
    """The distinct name-shaped strings of one configuration file, first appearance first."""

    path: str
    values: tuple[str, ...]


def read_config_values(directory: str) -> tuple[ConfigValues, ...]:
    """Every configuration file directly inside directory, sorted by file name."""
    try:
        names = sorted(os.listdir(directory))
    except OSError as error:
        print(f"[code-graph] WARNING: cannot list {directory}: {error}", file=sys.stderr)
        return ()
    found: list[ConfigValues] = []
    for name in names:
        path = os.path.join(directory, name)
        if not name.endswith(CONFIG_SUFFIXES) or not os.path.isfile(path):
            continue
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            continue
        try:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read()
            values = config_names(name, text)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            print(f"[code-graph] WARNING: configuration file {path} not read: {error}", file=sys.stderr)
            continue
        if values:
            found.append(ConfigValues(path=path, values=values))
    return tuple(found)


def config_names(file_name: str, text: str) -> tuple[str, ...]:
    """Name-shaped string values of one configuration text; raises ValueError on a malformed file."""
    if file_name.endswith(".json"):
        strings = _strings(json.loads(text))
    elif file_name.endswith(".toml"):
        strings = _strings(tomllib.loads(text))
    else:
        strings = _yaml_scalars(text)
    return tuple(dict.fromkeys(value for value in strings if _NAME_RE.match(value)))


def _strings(value: object) -> Iterator[str]:
    """String values of a parsed document in document order; keys are not values."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _yaml_scalars(text: str) -> Iterator[str]:
    """Plain and quoted scalars of block mappings, sequences and flow lists, without a YAML parser."""
    for line in text.splitlines():
        content = re.sub(r"(^|\s)#.*$", "", line)
        match = _YAML_SCALAR_RE.match(content)
        if match is None:
            continue
        scalar = match.group(1).strip()
        items = scalar[1:-1].split(",") if scalar.startswith("[") and scalar.endswith("]") else [scalar]
        for item in items:
            yield item.strip().strip("'\"")
