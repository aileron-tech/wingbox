""".wingbox/config.yaml: the architecture of a repository as people state it.

`wingbox init` writes it from what it detects; people edit it; `wingbox build`
reads it. Format:

    parts:
      <part folder, "." for the root>:
        level: container                  # system | container | component (C4), informative
        style: layered                    # a style id, declared, none or undetermined
        layers:                           # layer -> whole folder names inside the part
          api: [routes, controllers]
        routing-root: [app]               # route-tree only: folders whose subfolders are URLs
        overlays: [cqrs]                  # overlays found next to the style
        declared: [platform/backend/layers.mjs]   # declared only: the rule files
        target:                           # written by init: the full layout of the style, present or proposed
          - {layer: domain, block: entities, folder: src/domain/entities, proposed: true}

A part listed in the file starts at its folder and ends where another listed
part starts. A file in no listed part gets no layer. A style without `layers`
takes the folders of the part that its dictionaries name.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional


from .detect import DeclaredFile, ReadText, judge, part_kind, split_parts, c4_level
from .target import target_layout
from .library import Library
from .plan import (
    DECLARED,
    NO_STYLE,
    UNDETERMINED,
    LayerFolder,
    PartPlan,
    RepositoryPlan,
    declared_rules,
    layer_folders,
)
from .words import DEFAULT_SETTINGS, MatchSettings

CONFIG_DIRECTORY = ".wingbox"
CONFIG_FILE = ".wingbox/config.yaml"


@dataclass(frozen=True)
class PartConfig:
    path: str
    style: str
    level: str
    layers: tuple[tuple[str, tuple[str, ...]], ...]   # (layer, folders), in file order
    routing_root: tuple[str, ...]
    declared: tuple[str, ...]
    overlays: tuple[str, ...] = ()
    target: tuple["TargetEntry", ...] = ()


@dataclass(frozen=True)
class TargetEntry:
    """One folder of the target layout as the config states it (for `wingbox place`)."""

    layer: str
    block: str
    folder: str
    proposed: bool


@dataclass(frozen=True)
class ConfigOk:
    parts: tuple[PartConfig, ...]


@dataclass(frozen=True)
class ConfigErr:
    error: str


ConfigResult = ConfigOk | ConfigErr


def _names(value: Any, where: str) -> tuple[str, ...] | str:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return f"{where} must be a folder name or a list of folder names"


def parse_config(text: str, library: Library) -> ConfigResult:
    """Validate the YAML text of .wingbox/config.yaml into typed part configurations."""
    try:
        import yaml  # PyYAML is needed only when a config file exists: a Python-only build stays stdlib-only.
    except ImportError:
        return ConfigErr(f"{CONFIG_FILE} needs PyYAML: python3 -m pip install -r requirements.txt")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return ConfigErr(f"{CONFIG_FILE} is not valid YAML: {error}")
    if not isinstance(data, dict) or not isinstance(data.get("parts"), dict):
        return ConfigErr(f"{CONFIG_FILE} must have a mapping `parts`")
    known = {style.id for style in library.styles} | {alias for style in library.styles for alias in style.aliases}
    parts: list[PartConfig] = []
    for path, entry in data["parts"].items():
        where = f"{CONFIG_FILE}: part {path}"
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            return ConfigErr(f"{where}: the part must be a folder relative to the repository root")
        if not isinstance(entry, dict):
            return ConfigErr(f"{where}: expected a mapping with `style`")
        style = entry.get("style")
        if style not in known | {DECLARED, NO_STYLE, UNDETERMINED}:
            return ConfigErr(
                f"{where}: unknown style {style!r}; use one of "
                + ", ".join(sorted(known | {DECLARED, NO_STYLE, UNDETERMINED}))
            )
        resolved = library.style(style)
        style_id = resolved.id if resolved else str(style)
        layers_value = entry.get("layers") or {}
        if not isinstance(layers_value, dict):
            return ConfigErr(f"{where}: `layers` must map a layer to folder names")
        layers: list[tuple[str, tuple[str, ...]]] = []
        for layer, folders in layers_value.items():
            if resolved is None or resolved.layer(str(layer)) is None:
                valid = ", ".join(layer.id for layer in resolved.layers) if resolved else "none"
                return ConfigErr(f"{where}: style {style_id} has no layer {layer!r} (layers: {valid})")
            names = _names(folders, f"{where}: layers.{layer}")
            if isinstance(names, str):
                return ConfigErr(names)
            layers.append((str(layer), names))
        target = entry.get("target", [])
        if not isinstance(target, list) or not all(isinstance(item, dict) and "folder" in item for item in target):
            return ConfigErr(f"{where}: `target` must be a list of {{layer, folder}} entries")
        overlays = _names(entry.get("overlays"), f"{where}: overlays")
        if isinstance(overlays, str):
            return ConfigErr(overlays)
        unknown = [name for name in overlays if name not in {o.id for o in library.overlays}]
        if unknown:
            return ConfigErr(f"{where}: unknown overlay {unknown[0]!r}; use " + ", ".join(o.id for o in library.overlays))
        routing = _names(entry.get("routing-root"), f"{where}: routing-root")
        declared = _names(entry.get("declared"), f"{where}: declared")
        if isinstance(routing, str):
            return ConfigErr(routing)
        if isinstance(declared, str):
            return ConfigErr(declared)
        parts.append(PartConfig(
            path=path.strip("/") or ".", style=style_id, level=str(entry.get("level", "")),
            layers=tuple(layers), routing_root=routing, declared=declared, overlays=overlays,
            target=tuple(
                TargetEntry(str(item.get("layer", "")), str(item.get("block", "")), str(item["folder"]),
                            bool(item.get("proposed", False)))
                for item in target
            ),
        ))
    return ConfigOk(tuple(parts))


def plan_from_config(
    config: ConfigOk, paths: tuple[str, ...], read_text: ReadText, library: Library,
    settings: MatchSettings = DEFAULT_SETTINGS,
) -> RepositoryPlan:
    """The plan the configuration states; parts are the configured folders."""
    roots = tuple(sorted({"" if part.path == "." else part.path for part in config.parts}, key=lambda r: (-len(r), r)))
    # Split the files by the configured folders: each folder is a part root.
    by_root = {("" if part.path == "." else part.path): part for part in config.parts}
    parts = {part.path: part for part in split_parts(paths, library, roots=roots)}
    plans: list[PartPlan] = []
    for root in sorted(by_root):
        entry = by_root[root]
        part = parts.get(entry.path)
        if part is None:
            continue
        level = entry.level or c4_level(part, paths, library, read_text)
        base = PartPlan(
            path=entry.path, kind=part_kind(part), level=level, files=len(part.judged), style=entry.style,
            state="configured", origin="config", score=None, judgement=None, layers=(), routing_root=(),
            declared=None, overlays=entry.overlays,
        )
        style = library.style(entry.style)
        if entry.style == DECLARED:
            files = tuple(DeclaredFile(path, "rules") for path in entry.declared)
            exceptions = tuple(
                DeclaredFile(path, "exceptions") for path in paths
                if path.rsplit("/", 1)[-1] in library.index.known_exceptions
                and (root == "" or path.startswith(root + "/"))
            )
            rules = declared_rules(files + exceptions, read_text)
            plans.append(replace(base, declared=rules, origin="declared",
                                 state="declared" if rules.layers is not None else "declared-unread"))
        elif style is None:
            plans.append(base)
        elif entry.layers or entry.routing_root:
            layers = tuple(
                LayerFolder(folder, layer, "config", CONFIG_FILE, 0)
                for layer, folders in entry.layers for folder in folders
            )
            plans.append(replace(base, layers=layers, routing_root=entry.routing_root))
        else:
            layers, routing = layer_folders(style, part, settings, library, cqrs="cqrs" in entry.overlays)
            plans.append(replace(base, layers=layers, routing_root=routing))
    return RepositoryPlan(parts=tuple(plans), roots=roots)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _quote(value: str) -> str:
    plain = value and all(c.isalnum() or c in "-_./@+" for c in value) and value not in ("yes", "no", "true", "false", "null")
    return value if plain else '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _flow(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_quote(value) for value in values) + "]"


HEADER_MARK = "# Wingbox architecture config."


def config_header(library: Library, generated: str) -> list[str]:
    """The instructions at the top of .wingbox/config.yaml, rewritten on every write.

    They say what each key means and how the layer of a file is decided, so that an agent
    asked to move a folder edits the file correctly. Everything below them is the user's.
    """
    styles = ", ".join(style.id for style in library.styles)
    return [
        f"{HEADER_MARK} Read this before you change the file (written {generated}).",
        "# Wingbox writes it (wingbox init, align --learn, learn); a coding agent settles what Wingbox",
        "# cannot decide, asking the operator when the code does not tell. Edit by these rules only.",
        "# - parts: one entry per part, keyed by its root folder (\".\" is the repository root). A part",
        "#   ends where another listed part starts.",
        "# - level: C4 level (system, container, component); informative.",
        f"# - style: {styles};",
        "#   declared (the project's own rule files, listed under `declared`, decide),",
        "#   none or undetermined (no layers and no rules for the part).",
        "# - layers: layer -> folders. A name (services) matches a folder of that name at any depth; a",
        "#   path (lib/hooks) matches that folder and everything below it. Only the style's layers.",
        "# - The layer of a file, first rule that applies: a test file is in no layer; the longest folder",
        "#   path listed under layers; below a routing-root (route-tree), route files are route, other",
        "#   files feature; a file pattern of the style (*.tsx -> ui); the nearest folder above the",
        "#   file whose name is listed; otherwise no layer (other).",
        "# - What a layer may import is the style's rule list (wingbox boundaries names each rule).",
        "# - To move a folder to another layer, move its name or path to that layer's list; to move a",
        "#   subfolder alone, list its path (lib/hooks) under the new layer. Write the reason in a",
        "#   comment on the line. A single file cannot be pinned: move it into a folder of its layer.",
        "# - overlays, routing-root, target: found by init; target is the style's full layout,",
        "#   informative.",
        "# - Known rule breaks accepted on purpose are in .wingbox/baseline.sexp (align --learn).",
        "# - After any change run `wingbox build` and `wingbox boundaries`: no new violation may appear.",
        "# - Unsure where something belongs? Run `wingbox align --learn` (or `wingbox learn`) and answer",
        "#   its questions instead of guessing.",
    ]


def with_header(text: str, library: Library, generated: str) -> str:
    """text with the current instruction header: the leading comment block is replaced, the rest kept."""
    lines = text.split("\n")
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    return "\n".join([*config_header(library, generated), *lines[start:]])


def render_config(
    plan: RepositoryPlan, library: Library, generated: str, paths: tuple[str, ...] = (),
    with_target: bool = False, chosen: str = "",
) -> str:
    """YAML text of .wingbox/config.yaml for a plan; comments say what each value means."""
    lines = [*config_header(library, generated), "parts:"]
    for part in plan.parts:
        if part.state == "not-judged":
            continue
        lines.append(f"  {_quote(part.path)}:")
        lines.append(f"    level: {part.level}")
        lines.append(f"    style: {part.style}" + (f"   # chosen: {chosen}" if chosen else ""))
        if part.style == UNDETERMINED and part.judgement:
            names = ", ".join(f"{c.style} {c.score:.2f}" for c in part.judgement.candidates)
            lines.append(f"    # candidates: {names}; choose one, or keep undetermined (no layers, no rules)")
        if part.declared is not None:
            rules = tuple(item.path for item in part.declared.files if item.kind != "exceptions")
            lines.append(f"    declared: {_flow(rules)}")
            for item in part.declared.not_read:
                lines.append(f"    # {item.file}: not read ({item.reason})")
        if part.overlays:
            lines.append(f"    overlays: {_flow(part.overlays)}")
        if part.routing_root:
            lines.append(f"    routing-root: {_flow(part.routing_root)}")
        if part.layers:
            lines.append("    layers:")
            by_layer: dict[str, list[LayerFolder]] = {}
            for folder in part.layers:
                by_layer.setdefault(folder.layer, []).append(folder)
            for layer, folders in by_layer.items():
                notes = [f"{f.folder}: {f.match} of {f.via}" for f in folders if f.match in ("spelling", "synonym")]
                notes += [
                    f"{f.folder}: learned by wingbox align ({f.via[len('align: '):]})" if f.via.startswith("align: ")
                    else f"{f.folder}: learned by agent: {f.via[len('agent: '):]}" if f.via.startswith("agent: ")
                    else f"{f.folder}: answered by a person: {f.via[len('person: '):]}" if f.via.startswith("person: ")
                    else f"{f.folder}: learned from advice {f.via}"
                    for f in folders if f.match == "learned"
                ]
                note = f"   # {'; '.join(notes)}" if notes else ""
                lines.append(f"      {layer}: {_flow(tuple(f.folder for f in folders))}{note}")
        targets = target_layout(part, paths, library) if paths or with_target else ()
        if targets:
            lines.append("    target:   # the full layout of the style: present with the real folders, or proposed")
            for item in targets:
                block = f", block: {item.block}" if item.block else ""
                where = f"present: {_flow(item.present)}" if item.present else "proposed: true"
                lines.append(f"      - {{layer: {item.layer}{block}, folder: {_quote(item.folder)}, {where}}}")
    small = [part.path for part in plan.parts if part.state == "not-judged"]
    if small:
        lines.append(f"# not judged (fewer than {library.index.min_source_files} source files): {', '.join(small)}")
    return "\n".join(lines) + "\n"


def read_config(read_text: ReadText, library: Library) -> Optional[ConfigResult]:
    """The configuration of the repository, or None when there is no config file."""
    text = read_text(CONFIG_FILE)
    return None if text is None else parse_config(text, library)
