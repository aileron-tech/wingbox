""".wingbox/config.yaml: the architecture of a repository as people state it.

`wingbox init` writes it from what it detects; `wingbox learn` settles the open
points through the coding agent (or questions in a terminal); `wingbox build`
reads it. Format:

    parts:
      <part folder, "." for the root>:
        level: container                  # system | container | component (C4), informative
        style: layered                    # a style id, declared, none or undetermined
        layers:                           # layer -> whole folder names inside the part
          api: [routes, controllers]
        routing-root: [app]               # route-tree only: folders whose subfolders are URLs
        capabilities:                     # over the style's rules: where SQL, HTTP, the framework... may be
          sql: {allowed_in: [infra]}
        overlays: [cqrs]                  # overlays found next to the style
        declared: [platform/backend/layers.mjs]   # declared only: the rule files
        order:                            # declared only: the layers of the part, lowest first
          - core: [core, platform]        #   <layer>: folders of the part ("." = the files directly in it)
          - game: [game, ui]              #   a file may depend on its own layer and on the layers below
        target:                           # written by init: the full layout of the style, present or proposed
          - {layer: domain, block: entities, folder: src/domain/entities, proposed: true}

A part listed in the file starts at its folder and ends where another listed
part starts. A file in no listed part gets no layer. A style without `layers`
takes the folders of the part that its dictionaries name.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional


from .capabilities import CAPABILITIES, CapabilityRule, rules_from_toml
from .declared import layers_from_order
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
    capabilities: tuple[CapabilityRule, ...] = ()   # `capabilities:` of the part, over the style's rules
    order: tuple[tuple[str, tuple[str, ...]], ...] = ()   # `order:` of a declared part: (layer, folders), lowest first


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
    note: str = ""   # one line for the reader, when the file was read in a way it does not say itself


NO_PARTS_NOTE = f"{CONFIG_FILE} declares no parts: the whole repository is one undetermined part"


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


def _order(value: Any, where: str) -> tuple[tuple[str, tuple[str, ...]], ...] | str:
    """`order:` of a declared part: a list of one-key mappings `<layer>: [<folder>, ...]`, lowest layer first."""
    if not isinstance(value, list) or not value:
        return f"{where}: `order` must be a list of `<layer>: [<folder>, ...]` entries, lowest layer first"
    order: list[tuple[str, tuple[str, ...]]] = []
    seen_layers: set[str] = set()
    seen_folders: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or len(item) != 1 or not isinstance(next(iter(item)), str):
            return f"{where}: every entry of `order` is one `<layer>: [<folder>, ...]`"
        layer, folders = next(iter(item.items()))
        names = _names(folders, f"{where}: order.{layer}")
        if isinstance(names, str):
            return names
        if not names:
            return f"{where}: `order` layer {layer} names no folder"
        if layer in seen_layers:
            return f"{where}: `order` names the layer {layer} twice"
        twice = [name for name in names if name.strip("/") in seen_folders]
        if twice or len({name.strip("/") for name in names}) != len(names):
            return f"{where}: `order` names the folder {(twice or list(names))[0]} twice"
        seen_layers.add(layer)
        seen_folders.update(name.strip("/") for name in names)
        order.append((layer, names))
    return tuple(order)


def parse_config(text: str, library: Library) -> ConfigResult:
    """Validate the YAML text of .wingbox/config.yaml into typed part configurations."""
    try:
        import yaml  # PyYAML is needed only when a config file exists: a Python-only build stays stdlib-only.
    except ImportError:
        return ConfigErr(f"{CONFIG_FILE} needs PyYAML: python3 -m pip install -r requirements.txt")
    try:
        data = yaml.safe_load(text)
        lines = _key_lines(yaml.compose(text))
    except yaml.YAMLError as error:
        return ConfigErr(f"{CONFIG_FILE} is not valid YAML: {error}")
    data = {} if data is None else data
    if not isinstance(data, dict):
        return ConfigErr(f"{CONFIG_FILE} line 1: expected a mapping with `parts`")
    if data.get("parts") in (None, {}):
        # A small repository: 0.6.0 to 0.8.0 wrote `parts:` with no entry. Nothing is declared, so nothing is checked.
        empty = PartConfig(path=".", style=UNDETERMINED, level="", layers=(), routing_root=(), declared=())
        return ConfigOk((empty,), note=NO_PARTS_NOTE)
    if not isinstance(data["parts"], dict):
        return ConfigErr(f"{CONFIG_FILE} line {lines.get(('parts',), 1)}: `parts` must map each part folder to its style")
    known = {style.id for style in library.styles} | {alias for style in library.styles for alias in style.aliases}
    parts: list[PartConfig] = []
    for path, entry in data["parts"].items():
        where = f"{CONFIG_FILE} line {lines.get(('parts', str(path)), lines.get(('parts',), 1))}: part {path}"
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
        capabilities = _capabilities(entry.get("capabilities"), resolved, where)
        if isinstance(capabilities, str):
            return ConfigErr(capabilities)
        routing = _names(entry.get("routing-root"), f"{where}: routing-root")
        declared = _names(entry.get("declared"), f"{where}: declared")
        if isinstance(routing, str):
            return ConfigErr(routing)
        if isinstance(declared, str):
            return ConfigErr(declared)
        order: tuple[tuple[str, tuple[str, ...]], ...] | str = ()
        if entry.get("order") is not None:
            if style != DECLARED:
                return ConfigErr(f"{where}: `order` states the layers of a part with `style: declared`")
            order = _order(entry["order"], where)
            if isinstance(order, str):
                return ConfigErr(order)
        parts.append(PartConfig(
            path=path.strip("/") or ".", style=style_id, level=str(entry.get("level", "")),
            layers=tuple(layers), routing_root=routing, declared=declared, overlays=overlays, capabilities=capabilities,
            target=tuple(
                TargetEntry(str(item.get("layer", "")), str(item.get("block", "")), str(item["folder"]),
                            bool(item.get("proposed", False)))
                for item in target
            ),
            order=order,
        ))
    return ConfigOk(tuple(parts))


def _key_lines(node: Any) -> dict[tuple[str, ...], int]:
    """Line (1-based) of the `parts` key and of each part key in the composed YAML document."""
    lines: dict[tuple[str, ...], int] = {}
    if node is None or not hasattr(node, "value") or not isinstance(node.value, list):
        return lines
    for key, value in node.value if node.tag.endswith(":map") else ():
        if getattr(key, "value", None) == "parts":
            lines[("parts",)] = key.start_mark.line + 1
            if value.tag.endswith(":map"):
                for part_key, _entry in value.value:
                    lines[("parts", str(part_key.value))] = part_key.start_mark.line + 1
    return lines


def _capabilities(value: Any, style: Any, where: str) -> tuple[CapabilityRule, ...] | str:
    """`capabilities:` of a part: capability -> {allowed_in: [layers], forbidden_in: [layers], fix: text}."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        return f"{where}: `capabilities` must map a capability to {{allowed_in, forbidden_in}}"
    for name, rule in value.items():
        if name not in CAPABILITIES:
            return f"{where}: unknown capability {name!r}; use one of {', '.join(CAPABILITIES)}"
        if not isinstance(rule, dict) or not set(rule) <= {"allowed_in", "forbidden_in", "fix"}:
            return f"{where}: capabilities.{name} takes allowed_in, forbidden_in and fix"
        for key in ("allowed_in", "forbidden_in"):
            layers = rule.get(key) or []
            if not isinstance(layers, list) or not all(isinstance(layer, str) for layer in layers):
                return f"{where}: capabilities.{name}.{key} must be a list of layers"
            unknown = [layer for layer in layers if style is not None and style.layer(layer) is None]
            if unknown:
                return f"{where}: capabilities.{name}.{key}: style {style.id} has no layer {unknown[0]!r}"
    return rules_from_toml(value)


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
            declared=None, overlays=entry.overlays, capabilities=entry.capabilities,
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
            if entry.order:   # the layers stated in this file stand for a layer table of the project
                rules = replace(rules, layers=layers_from_order(CONFIG_FILE, root, entry.order))
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
        "# - order (style declared only): the layers of the part, lowest first, each `- <layer>: [folders]`,",
        "#   the folders relative to the part (\".\": the files directly in it). A file may depend on its",
        "#   own layer and on the layers below it. `wingbox init --from-folders <part>` writes it from",
        "#   the graph; reorder the entries or move a folder to another entry by hand.",
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
        "# - capabilities: where the part allows what a file does - sql, db-client, http-client,",
        "#   framework, http-response, env, filesystem, process - as {allowed_in: [layers]} or",
        "#   {forbidden_in: [layers]}; an entry replaces the style's rule for that capability.",
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
    judged = [part for part in plan.parts if part.state != "not-judged"]
    # An empty mapping, not `parts:` alone (that is null): a small repository has no part to declare.
    lines = [*config_header(library, generated), "parts:" if judged else "parts: {}"]
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
            table = part.declared.layers
            if rules or table is None or table.file != CONFIG_FILE:
                lines.append(f"    declared: {_flow(rules)}")
            if table is not None and table.file == CONFIG_FILE:
                lines.append("    order:   # lowest first: a file may depend on its own layer and on the layers below it")
                for layer in table.layers:
                    lines.append(f"      - {_quote(layer.name)}: {_flow(layer.folders)}")
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
                notes = [f"{f.folder}: {f.match} of {f.via}" for f in folders if f.match == "spelling"]
                notes += [
                    f"{f.folder}: learned by wingbox align ({f.via[len('align: '):]})" if f.via.startswith("align: ")
                    else f"{f.folder}: learned by agent: {f.via[len('agent: '):]}" if f.via.startswith("agent: ")
                    else f"{f.folder}: answered by a person: {f.via[len('person: '):]}" if f.via.startswith("person: ")
                    else f"{f.folder}: learned from advice {f.via}"
                    for f in folders if f.match == "learned"
                ]
                note = f"   # {'; '.join(notes)}" if notes else ""
                lines.append(f"      {layer}: {_flow(tuple(f.folder for f in folders))}{note}")
        if part.capabilities:
            lines.append("    capabilities:   # where the part allows SQL, HTTP, the framework...; replaces the style's rule")
            for rule in part.capabilities:
                keys = [f"allowed_in: {_flow(rule.allowed_in)}"] if rule.allowed_in else []
                keys += [f"forbidden_in: {_flow(rule.forbidden_in)}"] if rule.forbidden_in else []
                keys += [f"fix: {_quote(rule.fix)}"] if rule.fix else []
                lines.append(f"      {rule.capability}: {{{', '.join(keys)}}}")
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
