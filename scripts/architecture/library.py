"""The built-in style library: styles/*.toml read into typed values.

index.toml holds what all styles share (matching, parts, ignored folders,
decision thresholds, declared rule files); one file per style holds its
sources, detection groups, layers and rules; c4.toml names the levels of a
system. The files are data; this module only validates and types them.
"""
from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

from .capabilities import CapabilityRule, rules_from_toml

STYLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles")


@dataclass(frozen=True)
class Source:
    title: str
    author: str
    year: str
    url: str

    def cite(self) -> str:
        year = f", {self.year}" if self.year else ""
        return f'{self.author}, "{self.title}"{year}'


@dataclass(frozen=True)
class Group:
    id: str
    weight: float
    required: bool
    names: tuple[str, ...]
    files: tuple[str, ...]
    min_files: int
    max_depth: int
    siblings: bool


@dataclass(frozen=True)
class Against:
    weight: float
    all_of: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Layer:
    id: str
    rank: int
    folders: tuple[str, ...]
    files: tuple[str, ...]
    under: tuple[str, ...]                       # hexagonal: only below these folders
    files_in_routing_root: tuple[str, ...]       # route-tree: entry files of a URL
    rest_of_routing_root: bool                   # route-tree: everything else in the routing root
    outside_routing_root: bool
    meaning: str = ""                            # the layer's note or ring in the style file


@dataclass(frozen=True)
class Deny:
    source: str
    target: str
    reason: str
    severity: str = "violation"    # violation | candidate
    fix: str = ""                  # the fix the style prescribes; {source} {target} {source_folder} {target_folder}


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    kind: str                 # style | overlay
    summary: str
    aliases: tuple[str, ...]
    wins_over: tuple[str, ...]
    sources: tuple[Source, ...]
    groups: tuple[Group, ...]
    against: tuple[Against, ...]
    layers: tuple[Layer, ...]
    denies: tuple[Deny, ...]
    routing_root: tuple[str, ...]
    internal_layers_judged: bool
    decisive_files: tuple[str, ...] = ()     # a file at the root of a part that settles this style
    role_folders: frozenset[str] = frozenset()   # overlay: normalised folder names of its command and query roles
    role_files: tuple[str, ...] = ()             # overlay: file patterns of its command and query roles
    target_layers: tuple[tuple[str, str], ...] = ()   # [target] layer -> standard folder
    target_domain: str = ""                           # [target] where the domain building blocks live
    target_extra: tuple[tuple[str, str], ...] = ()    # [target] further folders of a layer
    target_elsewhere: tuple[tuple[str, str], ...] = ()   # [target] block -> folder outside the domain
    target_note: str = ""
    placements: tuple["Placement", ...] = ()   # [[placement]]: what goes where
    languages: tuple[str, ...] = ("any",)      # languages a new project may choose this style for
    from_maps: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()   # [from.<style>] layer -> layer of this style

    def mapping_from(self, style_id: str) -> dict[str, str] | None:
        """How the layers of another style map onto this one, for `align --to`."""
        for source, pairs in self.from_maps:
            if source == style_id:
                return dict(pairs)
        return None
    compatible_with: tuple[str, ...] = ()    # styles that may hold for the same part
    capabilities: tuple["CapabilityRule", ...] = ()   # [capabilities]: where SQL, HTTP, the framework... may be
    patterns: tuple[str, ...] = ()           # pattern checks of the style (controller-skips-service, ...)

    def cite(self) -> str:
        return "; ".join(source.cite() for source in self.sources)

    def layer(self, layer_id: str) -> Layer | None:
        return next((layer for layer in self.layers if layer.id == layer_id), None)


@dataclass(frozen=True)
class Placement:
    what: str
    layer: str
    where: str
    may_import: Optional[tuple[str, ...]]   # None: the style does not state it


@dataclass(frozen=True)
class Index:
    styles: tuple[str, ...]
    overlays: tuple[str, ...]
    manifests: tuple[str, ...]
    not_a_boundary: tuple[str, ...]
    min_source_files: int
    ignored_folders: tuple[str, ...]
    test_folders: tuple[str, ...]
    test_files: tuple[str, ...]
    candidate_score: float
    min_coverage: float
    lead: float
    declared_files: tuple[str, ...]
    declared_fields: tuple[str, ...]
    known_exceptions: tuple[str, ...]
    composition_roots: tuple[str, ...]
    composition_imports: frozenset[str] = frozenset()   # "module:name" imports that start an application


@dataclass(frozen=True)
class C4:
    workspace_files: tuple[str, ...]
    workspace_folders: tuple[str, ...]
    container_folders: tuple[str, ...]
    container_files: tuple[str, ...]
    component_folders: tuple[str, ...]


@dataclass(frozen=True)
class DomainBlock:
    """A building block of the domain tier (index.toml [domain_blocks])."""

    id: str
    folder: str
    names: tuple[str, ...]
    meaning: str
    source: str


@dataclass(frozen=True)
class Library:
    index: Index
    styles: tuple[Style, ...]
    overlays: tuple[Style, ...]
    c4: C4
    domain_blocks: tuple[DomainBlock, ...] = ()

    def cqrs_words(self) -> tuple[frozenset[str], tuple[str, ...]]:
        """Folder names and file patterns of CQRS commands and queries (cqrs.toml roles)."""
        overlay = self.style("cqrs")
        if overlay is None:
            return frozenset(), ()
        return overlay.role_folders, overlay.role_files

    def style(self, style_id: str) -> Style | None:
        for style in self.styles + self.overlays:
            if style.id == style_id or style_id in style.aliases:
                return style
        return None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _read_file(path: str) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _style(data: dict[str, Any]) -> Style:
    detect = data.get("detect", {})
    rules = data.get("rules", {})
    denies: list[Deny] = []
    for rule in rules.get("deny", []):
        if "level" in rule or "except" in rule:
            continue   # C4 levels and rules with exceptions are not plain boundary rules of a part
        for source in _strings(rule.get("from")):
            for target in _strings(rule.get("to")):
                denies.append(Deny(
                    source=source, target=target, reason=str(rule.get("reason", "")),
                    severity=str(rule.get("severity", "violation")), fix=str(rule.get("fix", "")),
                ))
    return Style(
        id=str(data["id"]),
        name=str(data.get("name", data["id"])),
        kind=str(data.get("kind", "style")),
        summary=str(data.get("summary", "")),
        aliases=_strings(data.get("aliases")),
        wins_over=_strings(data.get("wins_over")),
        sources=tuple(
            Source(
                title=str(source.get("title", "")),
                author=str(source.get("author", "")),
                year=str(source.get("year", "")),
                url=str(source.get("url", "")),
            )
            for source in data.get("sources", [])
        ),
        groups=tuple(
            Group(
                id=str(group["id"]),
                weight=float(group.get("weight", 1)),
                required=bool(group.get("required", False)),
                names=_strings(group.get("names")),
                files=_strings(group.get("files")),
                min_files=int(group.get("min", 1)),
                max_depth=int(group.get("max_depth", 99)),
                siblings=bool(group.get("siblings", False)),
            )
            for group in detect.get("groups", [])
        ),
        against=tuple(
            Against(weight=float(item.get("weight", 1)), all_of=tuple(_strings(names) for names in item.get("all_of", [])))
            for item in detect.get("against", [])
        ),
        layers=tuple(
            Layer(
                id=str(layer["id"]),
                rank=int(layer.get("rank", 0)),
                folders=_strings(layer.get("folders")),
                files=_strings(layer.get("files")),
                under=_strings(layer.get("under")),
                files_in_routing_root=_strings(layer.get("files_in_routing_root")),
                rest_of_routing_root=bool(layer.get("rest_of_routing_root", False)),
                outside_routing_root=bool(layer.get("outside_routing_root", False)),
                meaning=str(layer.get("note") or layer.get("ring") or ""),
            )
            for layer in data.get("layers", [])
        ),
        denies=tuple(denies),
        routing_root=_strings(data.get("routing_root", {}).get("folders")),
        internal_layers_judged=rules.get("internal_layers") != "not judged",
        decisive_files=_strings(detect.get("decisive_files")),
        target_layers=tuple((str(k), str(v)) for k, v in data.get("target", {}).get("layers", {}).items()),
        target_domain=str(data.get("target", {}).get("domain", "")),
        target_extra=tuple(
            (str(layer), str(folder))
            for layer, folders in data.get("target", {}).get("extra", {}).items() for folder in _strings(folders)
        ),
        target_elsewhere=tuple((str(k), str(v)) for k, v in data.get("target", {}).get("blocks_elsewhere", {}).items()),
        target_note=str(data.get("target", {}).get("note", "")),
        languages=_strings(data.get("languages")) or ("any",),
        placements=tuple(
            Placement(str(item.get("what", "")), str(item.get("layer", item.get("role", ""))), str(item.get("where", "")),
                      _strings(item.get("may_import")) if "may_import" in item else None)
            for item in data.get("placement", [])
        ),
        from_maps=tuple(
            (str(source), tuple((str(k), str(v)) for k, v in pairs.items()))
            for source, pairs in data.get("from", {}).items() if isinstance(pairs, dict)
        ),
        role_folders=frozenset(
            name.lower().replace("-", "").replace("_", "")
            for role in data.get("roles", []) if role.get("id") in ("command", "query")
            for name in _strings(role.get("folders"))
        ),
        role_files=tuple(
            pattern.lower() for role in data.get("roles", []) if role.get("id") in ("command", "query")
            for pattern in _strings(role.get("files"))
        ),
        compatible_with=_strings(data.get("compatible_with")),
        capabilities=rules_from_toml(data.get("capabilities")),
        patterns=_strings(data.get("patterns")),
    )


def user_styles_dir() -> str:
    """$HOME/.wingbox/styles: style files of the user that replace built-in ones of the same name."""
    return os.path.join(str(Path.home()), ".wingbox", "styles")


def load_library(directory: str = STYLES_DIR, override: str | None = None) -> Library:
    """The style library; a file of the same name in `override` (default: $HOME/.wingbox/styles) wins."""
    return _load_library(directory, user_styles_dir() if override is None else override)


@functools.lru_cache(maxsize=8)
def _load_library(directory: str, override: str) -> Library:
    """Read index.toml, every style and overlay it names, and c4.toml."""

    def _read(base: str, name: str) -> dict[str, Any]:
        mine = os.path.join(override, name + ".toml") if override else ""
        return _read_file(mine if mine and os.path.isfile(mine) else os.path.join(base, name + ".toml"))

    index = _read(directory, "index")
    decision = index.get("decision", {})
    ignored = index.get("ignored", {})
    declared = index.get("declared_rules", {})
    c4 = _read(directory, "c4")
    c4_detect = c4.get("detect", {})
    return Library(
        index=Index(
            styles=_strings(index.get("styles")),
            overlays=_strings(index.get("overlays")),
            manifests=_strings(index.get("parts", {}).get("manifests")),
            not_a_boundary=_strings(index.get("parts", {}).get("not_a_boundary")),
            min_source_files=int(index.get("parts", {}).get("min_source_files", 12)),
            ignored_folders=_strings(ignored.get("folders")),
            test_folders=_strings(ignored.get("tests")),
            test_files=_strings(ignored.get("test_files")),
            candidate_score=float(decision.get("candidate_score", 0.6)),
            min_coverage=float(decision.get("min_coverage", 0.25)),
            lead=float(decision.get("lead", 0.15)),
            declared_files=_strings(declared.get("files")),
            declared_fields=_strings(declared.get("manifest_fields")),
            known_exceptions=_strings(declared.get("known_exceptions")),
            composition_roots=_strings(index.get("composition_roots", {}).get("files")),
            composition_imports=frozenset(_strings(index.get("composition_roots", {}).get("imports"))),
        ),
        styles=tuple(_style(_read(directory, name)) for name in _strings(index.get("styles"))),
        overlays=tuple(_style(_read(directory, name)) for name in _strings(index.get("overlays"))),
        domain_blocks=tuple(
            DomainBlock(
                id=str(block["id"]), folder=str(block.get("folder", block["id"])), names=_strings(block.get("names")),
                meaning=str(block.get("is", "")), source=str(block.get("source", "")),
            )
            for block in index.get("domain_blocks", {}).get("blocks", [])
        ),
        c4=C4(
            workspace_files=_strings(c4_detect.get("workspace", {}).get("files")),
            workspace_folders=_strings(c4_detect.get("workspace", {}).get("folders")),
            container_folders=_strings(c4_detect.get("container", {}).get("folders")),
            container_files=_strings(c4_detect.get("container", {}).get("files")),
            component_folders=_strings(c4_detect.get("component", {}).get("folders")),
        ),
    )
