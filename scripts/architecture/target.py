"""The target layout of a part: every folder its style expects, present or proposed.

For the style of a part, [target] in the style file names one standard folder
per layer, further folders of a layer (use cases, ports) and where the domain
building blocks live (index.toml [domain_blocks]: entities, value objects,
aggregates, domain events, domain services, repository interfaces,
specifications; Evans 2003, Vernon 2013). Each item is present when the part
already has such a folder (its real paths are listed) and proposed otherwise,
at the path the style gives, below the source root of the part (src/ when the
part has one). `<feature>` and `<slice>` stand for a folder per feature. Pure:
nothing is created on disk.
"""
from __future__ import annotations

from dataclasses import dataclass

from .detect import is_test_file
from .library import Library, Style
from .plan import PartPlan
from .words import normalise

_DOMAIN_LAYERS = ("domain", "model", "entities")
_MAX_PRESENT = 3


@dataclass(frozen=True)
class TargetItem:
    layer: str                 # the layer the folder belongs to
    block: str                 # a domain building block id, or "" for a layer folder
    folder: str                # where the style puts it, relative to the part
    present: tuple[str, ...]   # folders of the part that already hold it (relative to the part)
    source: str                # the style's or the building block's source
    meaning: str = ""


def _source_root(files: tuple[str, ...]) -> str:
    if not files:
        return "src"   # a new project starts its source in src/
    top = [f.split("/", 1)[0] for f in files if "/" in f]
    return "src" if top and top.count("src") * 2 >= len(top) else ""


def _join(root: str, folder: str) -> str:
    return f"{root}/{folder}" if root else folder


def _folders(files: tuple[str, ...]) -> tuple[str, ...]:
    found: set[str] = set()
    for f in files:
        segments = f.split("/")[:-1]
        for index in range(1, len(segments) + 1):
            found.add("/".join(segments[:index]))
    return tuple(sorted(found, key=lambda path: (path.count("/"), path)))


def _ends_with(folder: str, tail: str) -> bool:
    parts, wanted = folder.split("/"), [segment for segment in tail.split("/") if not segment.startswith("<")]
    return bool(wanted) and [normalise(p) for p in parts[-len(wanted):]] == [normalise(w) for w in wanted]


def target_layout(part: PartPlan, paths: tuple[str, ...], library: Library) -> tuple[TargetItem, ...]:
    """The target layout of a part with a determined or configured style; () for any other part."""
    style = library.style(part.style)
    if style is None or part.state in ("undetermined", "none", "not-judged"):
        return ()
    prefix = "" if part.path == "." else part.path + "/"
    files = tuple(p[len(prefix):] for p in paths if p.startswith(prefix) and not is_test_file(p[len(prefix):], library))
    root = _source_root(files)
    folders = _folders(files)
    mapped: dict[str, set[str]] = {}
    for found in part.layers:
        mapped.setdefault(found.layer, set()).add(normalise(found.folder))
    cite = style.cite()
    items: list[TargetItem] = []
    style_words = {layer.id: {normalise(name) for name in layer.folders} for layer in style.layers}
    for layer, folder in style.target_layers:
        names = mapped.get(layer, set())
        # The style's own folder names first (controllers before a learned bin/), then by depth.
        own = style_words.get(layer, set()) | {normalise(folder.split("/")[-1])}
        present = tuple(sorted(
            (f for f in folders if normalise(f.split("/")[-1]) in names),
            key=lambda f: (normalise(f.split("/")[-1]) not in own, f.count("/"), f),
        ))[:_MAX_PRESENT]
        if not present and "<" not in folder:
            present = tuple(f for f in folders if _ends_with(f, folder))[:_MAX_PRESENT]
        items.append(TargetItem(layer, "", _join(root, folder), present, cite))
    for layer, folder in style.target_extra:
        present = tuple(f for f in folders if _ends_with(f, folder))[:_MAX_PRESENT]
        items.append(TargetItem(layer, "", _join(root, folder), present, cite))
    items.extend(_blocks(style, part, folders, root, mapped, library))
    return tuple(items)


def _blocks(
    style: Style, part: PartPlan, folders: tuple[str, ...], root: str, mapped: dict[str, set[str]], library: Library
) -> list[TargetItem]:
    domain_layer = next((layer.id for layer in style.layers if layer.id in _DOMAIN_LAYERS), "")
    # Building blocks only where the style has a domain layer and the part has domain code
    # (a new project, with no code yet, gets the whole layout).
    if not style.target_domain or not domain_layer or (not part.kind.startswith("new ") and domain_layer not in mapped):
        return []
    snake = part.kind in ("Python project", "Rust crate", "Go module", "Ruby project", "Elixir project") or any(
        part.kind == f"new {language} project" for language in ("python", "rust", "go", "ruby", "elixir"))
    tier_names = set(mapped.get(domain_layer, set()))
    last = [segment for segment in style.target_domain.split("/") if not segment.startswith("<")]
    if last:
        tier_names.add(normalise(last[-1]))
    tiers = [f for f in folders if normalise(f.split("/")[-1]) in tier_names]
    elsewhere = dict(style.target_elsewhere)
    items: list[TargetItem] = []
    for block in library.domain_blocks:
        if block.id in elsewhere:
            target = elsewhere[block.id]
            present = tuple(f for f in folders if _ends_with(f, target))[:_MAX_PRESENT]
            layer = next((layer for layer, folder in style.target_extra if folder == target), domain_layer)
            items.append(TargetItem(layer, block.id, _join(root, target), present, block.source, block.meaning))
            continue
        names = {normalise(name) for name in block.names}
        present = tuple(
            f for f in folders
            if normalise(f.split("/")[-1]) in names and any(f.startswith(tier + "/") for tier in tiers)
        )[:_MAX_PRESENT]
        folder = block.folder.replace("-", "_") if snake else block.folder
        items.append(TargetItem(
            domain_layer, block.id, _join(root, f"{style.target_domain}/{folder}"), present, block.source, block.meaning,
        ))
    return items
