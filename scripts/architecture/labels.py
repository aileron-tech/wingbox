"""The layer of a file in a repository plan, with the style and the rule that set it.

Order, first that applies:
1. test file (index.toml [ignored] tests and test_files)           -> test
2. declared part with a readable layer table                        -> the declared layer
3. part without a determined style (none, undetermined, declared but
   not read, not judged)                                            -> other
4. route-tree: inside a routing root, a route file is `route`, any
   other file `feature`; folder names below the root are URL segments
5. a Dart file: the Flutter folder conventions (features/*/presentation
   -> ui, domain, data -> infra, bloc/cubit/state -> service, lib/core
   -> shared, config); `lib/` is the package source root, so a Dart
   file no convention places stays other (its role decides later)
6. a file pattern of a layer (layered: *.tsx -> ui; mvc: views.py)
7. the nearest folder above the file that the part maps to a layer
   (hexagonal: a layer with `under` applies only below those folders)
8. otherwise                                                         -> other
"""
from __future__ import annotations

import fnmatch
import functools
from dataclasses import dataclass
from typing import Optional

from .detect import is_test_file
from .library import Library, Style
from .plan import DECLARED, LayerFolder, PartPlan, RepositoryPlan
from .words import normalise

TEST = "test"
_INNER_SHARED = frozenset({"shared", "common", "commons"})
OTHER = "other"


@dataclass(frozen=True)
class Label:
    layer: str
    style: str     # style id of the part, "declared", or "-" outside any part
    rule: str      # what set the layer, for people and agents
    match: str     # exact | spelling | config | file | declared | test | none
    root: bool = False   # a composition root (index.toml): it wires the layers and may import every one


@dataclass(frozen=True)
class Deny:
    source: str
    target: str
    reason: str
    severity: str = "violation"   # violation | candidate
    fix: str = ""


def _relative(path: str, part: PartPlan) -> str:
    return path if part.path == "." else path[len(part.path) + 1:]


def _folder_rule(found: LayerFolder) -> tuple[str, str]:
    rule = f"folder {found.folder}/ -> {found.layer}"
    if found.match == "spelling":
        rule += f" ({found.match} of {found.via})"
    elif found.match == "learned":
        rule += f" (learned from {found.via})"
    return rule, found.match


def is_composition_root(relative: str, library: Library) -> bool:
    """A file that wires the application (main.ts, Program.cs, app.module.ts; index.ts at the top)."""
    segments = relative.split("/")
    base = segments[-1].lower()
    for pattern in library.index.composition_roots:
        name, _, where = pattern.partition("@")
        if where == "root" and len(segments) > 2:
            continue
        if fnmatch.fnmatchcase(base, name.lower()):
            return True
    return False


def label_in_part(path: str, part: PartPlan, library: Library, imported: frozenset[str] = frozenset()) -> Label:
    """Layer of a repository-relative file that lies in `part`.

    `imported` holds the "module:name" imports of the file, when the build has
    parsed it: an import that starts the application (NestFactory, a
    nest-commander command) makes the file a composition root.
    """
    label = _label_in_part(path, part, library)
    if label.layer == TEST:
        return label
    starts = sorted(imported & library.index.composition_imports)
    if starts:
        return Label(label.layer, label.style, f"{label.rule}; composition root (imports {starts[0]})", label.match, root=True)
    if label.layer != OTHER and is_composition_root(_relative(path, part), library):
        return Label(label.layer, label.style, label.rule + "; composition root", label.match, root=True)
    return label


def _label_in_part(path: str, part: PartPlan, library: Library) -> Label:
    relative = _relative(path, part)
    if is_test_file(relative, library):
        return Label(TEST, part.style, "test file", "test")
    if part.style == DECLARED:
        rules = part.declared
        if rules is None or rules.layers is None:
            return Label(OTHER, DECLARED, "declared rules not read", "none")
        table = rules.layers
        inside = path if not table.base else path[len(table.base) + 1:] if path.startswith(table.base + "/") else ""
        layer = table.layer_of(inside) if inside else None
        if layer is None:
            return Label(OTHER, DECLARED, f"in no layer of {table.file}", "none")
        return Label(layer, DECLARED, f"{table.file}: layer {layer}", "declared")
    style = library.style(part.style)
    if style is None or part.state in ("undetermined", "none", "not-judged"):
        return Label(OTHER, part.style, f"style {part.style}: no layers", "none")
    segments = relative.split("/")
    folders, base = segments[:-1], segments[-1]
    # A folder path (lib/hooks, models/legacy) stated for the part decides for the files below it: the
    # longest such path wins over every name rule.
    paths = [f for f in part.layers if "/" in f.folder]
    if paths and folders:
        directory = "/".join(folders)
        variants = (directory, directory[4:] if directory.startswith("src/") else directory)
        hits = [f for f in paths if any(d == f.folder or d.startswith(f.folder + "/") for d in variants)]
        if hits:
            best = max(hits, key=lambda f: len(f.folder))
            rule, match = _folder_rule(best)
            return Label(best.layer, style.id, rule, match)
    if part.routing_root or style.routing_root:
        roots = {normalise(name) for name in (part.routing_root or style.routing_root)}
        for index, segment in enumerate(folders):
            if normalise(segment) in roots:
                root = "/".join(folders[: index + 1])
                below = folders[index + 1:]
                if below and below[0].startswith("_") and style.layer("ui") is not None:
                    return Label("ui", style.id, f"private folder {below[0]}/ at routing root {root}/ -> ui", "exact")
                return _routing_label(style, base, root)
    if base.endswith(".dart"):
        layer, folder = flutter_layer(relative)
        if layer != OTHER and style.layer(layer) is not None:
            return Label(layer, style.id, f"Flutter folder {folder} -> {layer}", "exact")
        return Label(OTHER, style.id, "Dart: no Flutter folder convention", "none")
    cqrs_files = () if "cqrs" in part.overlays else library.cqrs_words()[1]
    for layer in style.layers:
        pattern = next(
            (p for p in layer.files if fnmatch.fnmatchcase(base.lower(), p.lower())
             and not (layer.id == "domain" and any(fnmatch.fnmatchcase(p.lower(), c) for c in cqrs_files))),
            None,
        )
        if pattern:
            return Label(layer.id, style.id, f"file {pattern} -> {layer.id}", "file")
    mapping = _mapping(part)
    for index in range(len(folders) - 1, -1, -1):
        found = mapping.get(normalise(folders[index]))
        if found is None:
            continue
        layer = style.layer(found.layer)
        if layer is not None and layer.under:
            under = {normalise(name) for name in layer.under}
            if not any(normalise(above) in under for above in folders[:index]):
                continue
        if normalise(folders[index]) in _INNER_SHARED:
            # services/shared, api/common: shared within the enclosing layer, which decides.
            outer = next((mapping[normalise(f)] for f in reversed(folders[:index]) if normalise(f) in mapping), None)
            if outer is not None and outer.layer != found.layer:
                rule, match = _folder_rule(outer)
                return Label(outer.layer, style.id, f"{rule} (enclosing {folders[index]}/)", match)
        rule, match = _folder_rule(found)
        return Label(found.layer, style.id, rule, match)
    return Label(OTHER, style.id, f"no folder of style {style.id}", "none")


_FLUTTER_SERVICE_FOLDERS = ("/bloc/", "/cubit/", "/state/", "/application/")


def flutter_layer(relative: str) -> tuple[str, str]:
    """Layer of a Dart file by the Flutter folder conventions, and the folder that set it.

    Feature folders name the layer; `lib/core` is shared; config and env folders are
    config. Test folders are judged before (step 1).
    """
    path = "/" + relative.lower().lstrip("/")
    if "/lib/features/" in path:
        for folder, layer in (("/presentation/", "ui"), ("/domain/", "domain"), ("/data/", "infra")):
            if folder in path:
                return layer, folder.strip("/") + "/"
        found = next((folder for folder in _FLUTTER_SERVICE_FOLDERS if folder in path), None)
        if found:
            return "service", found.strip("/") + "/"
    for folder in ("/config/", "/env/"):
        if folder in path:
            return "config", folder.strip("/") + "/"
    if "/lib/core/" in path:
        return "shared", "lib/core/"
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if "/lib/features/" in path and stem.endswith(("_bloc", "_cubit", "_state", "_event")):
        return "service", f"features/ (*{stem[stem.rindex('_'):]})"
    return OTHER, ""


def _routing_label(style: Style, base: str, root: str) -> Label:
    for layer in style.layers:
        pattern = next((p for p in layer.files_in_routing_root if fnmatch.fnmatchcase(base, p)), None)
        if pattern:
            return Label(layer.id, style.id, f"route file {pattern} in {root}/", "file")
    rest = next((layer for layer in style.layers if layer.rest_of_routing_root), None)
    if rest is None:
        return Label(OTHER, style.id, f"inside routing root {root}/", "none")
    return Label(rest.id, style.id, f"inside routing root {root}/ -> {rest.id}", "exact")


def _mapping(part: PartPlan) -> dict[str, LayerFolder]:
    return _mapping_of(part.layers)


@functools.lru_cache(maxsize=256)
def _mapping_of(layers: tuple[LayerFolder, ...]) -> dict[str, LayerFolder]:
    mapping: dict[str, LayerFolder] = {}
    for found in layers:
        mapping.setdefault(normalise(found.folder), found)
    return mapping


def label_file(path: str, plan: RepositoryPlan, library: Library, imported: frozenset[str] = frozenset()) -> Label:
    part = plan.part_of(path)
    if part is None:
        return Label(OTHER, "-", "in no part", "none")
    return label_in_part(path, part, library, imported)


def part_denies(part: PartPlan, library: Library) -> tuple[Deny, ...]:
    """Boundary rules of a part: the style's deny rules, or the declared order (no import upward)."""
    if part.style == DECLARED:
        if part.declared is None or part.declared.layers is None:
            return ()
        names = [layer.name for layer in part.declared.layers.layers]
        file = part.declared.layers.file
        return tuple(
            Deny(lower, higher, f"{lower} imports the higher layer {higher} (layer order of {file})", "violation",
                 "move the imported code down, the importing code up, or invert the dependency ({file})".replace("{file}", file))
            for index, lower in enumerate(names) for higher in names[index + 1:]
        )
    if not part.has_rules or part.state in ("undetermined", "none", "not-judged"):
        return ()
    style = library.style(part.style)
    if style is None:
        return ()
    layers = {layer.id for layer in style.layers}
    return tuple(
        Deny(rule.source, rule.target, rule.reason, rule.severity, rule.fix)
        for rule in style.denies
        if rule.source in layers and rule.target in layers
    )
