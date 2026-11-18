from __future__ import annotations

import re
from dataclasses import dataclass

import tree_sitter_rust as _tsr
from tree_sitter import Language as _RustTreeSitterLanguage
from tree_sitter import Node, Parser as _RustParser

from ..interface import Entity, FileResult, ImportedName, TypeMembersPayload, UsePayload
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash
from .symbols import use_bindings_and_sites


_RUST_LANGUAGE = _RustTreeSitterLanguage(_tsr.language())


@dataclass(frozen=True)
class RustUse:
    path: str
    alias: str | None = None
    glob: bool = False


def _node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _attribute_names(attributes: tuple[Node, ...]) -> list[str]:
    names: list[str] = []
    for attribute in attributes:
        match = re.match(r"#!?\[\s*([A-Za-z_][A-Za-z0-9_]*)", _node_text(attribute))
        if match is not None and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _use_leaves(node: Node, prefix: tuple[str, ...] = ()) -> list[RustUse]:
    if node.type in ("identifier", "type_identifier", "crate", "self", "super"):
        leaf = _node_text(node)
        return [RustUse("::".join((*prefix, leaf)))] if leaf else []

    if node.type == "scoped_identifier":
        path = _node_text(node)
        if prefix:
            path = "::".join((*prefix, path))
        return [RustUse(path)] if path else []

    if node.type == "use_as_clause":
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return []
        alias_node = next(
            (child for child in reversed(node.named_children) if child != path_node),
            None,
        )
        alias = _node_text(alias_node) or None
        return [RustUse(leaf.path, alias) for leaf in _use_leaves(path_node, prefix)]

    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        path = _node_text(path_node)
        nested_prefix = (*prefix, *tuple(part for part in path.split("::") if part))
        use_list = next(
            (child for child in node.named_children if child.type == "use_list"),
            None,
        )
        return _use_leaves(use_list, nested_prefix) if use_list is not None else []

    if node.type == "use_list":
        leaves: list[RustUse] = []
        for child in node.named_children:
            if child.type == "self":
                path = "::".join(prefix)
                if path:
                    leaves.append(RustUse(path))
                continue
            leaves.extend(_use_leaves(child, prefix))
        return leaves

    if node.type == "use_wildcard":
        path = _node_text(node).removesuffix("::*")
        if prefix:
            path = "::".join((*prefix, path))
        return [RustUse(path, glob=True)] if path else []

    argument = node.child_by_field_name("argument")
    return _use_leaves(argument, prefix) if argument is not None else []


def _path_seen_from_file(path: str, inline_depth: int) -> str | None:
    """Rewrite a `use` path written inside inline modules as the file's own module sees it.

    Inside `mod tests { ... }` the first `super` is the enclosing file, not its parent.
    Returns None when the path stays inside the file (no dependency on another module).
    """
    if inline_depth == 0:
        return path
    segments = [segment for segment in path.split("::") if segment]
    if not segments or segments[0] not in ("self", "super"):
        return path
    if segments[0] == "self":
        return None
    parents = 0
    while parents < len(segments) and segments[parents] == "super":
        parents += 1
    if parents < inline_depth:
        return None
    rest = ["super"] * (parents - inline_depth) + segments[parents:]
    if not rest:
        return None
    return "::".join(rest if rest[0] == "super" else ["self", *rest])


def _collect_uses(root: Node) -> list[RustUse]:
    uses: list[RustUse] = []

    def _visit(node: Node, inline_depth: int) -> None:
        if node.type == "use_declaration":
            for leaf in _use_leaves(node):
                path = _path_seen_from_file(leaf.path, inline_depth)
                if path is not None:
                    uses.append(RustUse(path, leaf.alias, leaf.glob))
            return
        is_inline_module = node.type == "mod_item" and node.child_by_field_name("body") is not None
        for child in node.named_children:
            _visit(child, inline_depth + 1 if is_inline_module else inline_depth)

    _visit(root, 0)
    unique: list[RustUse] = []
    seen: set[tuple[str, str | None, bool]] = set()
    for rust_use in uses:
        key = (rust_use.path, rust_use.alias, rust_use.glob)
        if rust_use.path and key not in seen:
            seen.add(key)
            unique.append(rust_use)
    return unique


_PATH_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MODULE_SEGMENT = re.compile(r"[a-z_][a-z0-9_]*\Z")
_STANDARD_ROOTS = frozenset(("std", "core", "alloc"))
_LOCAL_ROOTS = ("crate", "super", "self")
# Macro arguments are raw tokens, not parsed paths: matches!(x, crate::a::B::C).
_PATH_IN_TOKENS = re.compile(r"(?<![A-Za-z0-9_:])(?:crate|super|self)(?:\s*::\s*[A-Za-z_][A-Za-z0-9_]*)+")


def _collect_inline_paths(root: Node, uses: list[RustUse]) -> list[str]:
    """Qualified paths written in code without a `use`: crate::a::b::f(), super::x::Y.

    Kept: paths rooted at crate, super or self; paths that start with a module this
    file imported from its own crate (x86::linker::link() after use crate::backend::x86);
    and paths that start with any other lower-case name, which is the crate's own name
    in a binary (app::run()) or a child module. Names imported from other crates are
    dropped. Generic arguments end the path (crate::a::Foo::<T>::new -> crate::a::Foo).
    """
    local_modules: dict[str, str] = {}
    foreign_names: set[str] = set()
    for rust_use in uses:
        segments = [segment for segment in rust_use.path.split("::") if segment]
        if not segments:
            continue
        name = rust_use.alias or segments[-1]
        if segments[0] in _LOCAL_ROOTS:
            local_modules.setdefault(name, rust_use.path)
        elif segments[0] in _STANDARD_ROOTS:
            foreign_names.add(name)
        else:
            # A name taken from another crate: bridge::start::run() after use other::bridge is
            # other::bridge::start::run(); the resolver finds it when other is a crate of the
            # workspace and finds nothing for a crate from a registry.
            local_modules.setdefault(name, rust_use.path)

    paths: list[str] = []
    seen: set[str] = set()

    def _keep(path: str | None) -> None:
        if path is not None and path not in seen:
            seen.add(path)
            paths.append(path)

    def _visit(node: Node, inline_depth: int) -> None:
        if node.type == "use_declaration":
            return
        if node.type == "token_tree":
            for match in _PATH_IN_TOKENS.finditer(_node_text(node)):
                segments = [segment.strip() for segment in match.group(0).split("::")]
                if len(segments) >= 2:
                    _keep(_path_seen_from_file("::".join(segments), inline_depth))
            return
        if node.type in ("scoped_identifier", "scoped_type_identifier"):
            segments: list[str] = []
            written = _node_text(node).strip()
            # `::name::item` starts at a crate (edition 2015: at the crate root); the resolver reads the marker.
            absolute = written.startswith("::")
            for segment in written.removeprefix("::").split("::"):
                segment = segment.strip()
                if not _PATH_SEGMENT.match(segment):
                    break
                segments.append(segment)
            path: str | None = None
            if absolute:
                if len(segments) >= 2 and segments[0] not in _STANDARD_ROOTS:
                    path = "::" + "::".join(segments)
            elif len(segments) >= 2 and segments[0] in _LOCAL_ROOTS:
                path = _path_seen_from_file("::".join(segments), inline_depth)
            elif len(segments) >= 2 and segments[0] in local_modules:
                path = "::".join((local_modules[segments[0]], *segments[1:]))
            elif (
                len(segments) >= 2
                and _MODULE_SEGMENT.match(segments[0])
                and segments[0] not in _STANDARD_ROOTS
                and segments[0] not in foreign_names
            ):
                path = "::".join(segments)
            _keep(path)
            return
        is_inline_module = node.type == "mod_item" and node.child_by_field_name("body") is not None
        for child in node.named_children:
            _visit(child, inline_depth + 1 if is_inline_module else inline_depth)

    _visit(root, 0)
    return paths


def _written_path(node: Node | None) -> list[str]:
    """A type or a trait written as a path, without its generic arguments; [] for any other type."""
    if node is None:
        return []
    if node.type == "generic_type":
        return _written_path(node.child_by_field_name("type"))
    if node.type not in ("type_identifier", "scoped_type_identifier", "identifier", "scoped_identifier"):
        return []
    segments = [segment.strip() for segment in _node_text(node).split("::")]
    return segments if all(_PATH_SEGMENT.match(segment) for segment in segments) else []


_MEMBER_NODES = ("function_item", "function_signature_item", "const_item", "type_item")


def _type_members(container: Node) -> list[TypeMembersPayload]:
    """The impl blocks and type aliases of a file, those of its inline modules included."""
    found: list[TypeMembersPayload] = []
    for node in container.named_children:
        if node.type == "impl_item":
            written = _written_path(node.child_by_field_name("type"))
            body = node.child_by_field_name("body")
            members = [_node_text(child.child_by_field_name("name")) for child in (body.named_children if body is not None else ())
                       if child.type in _MEMBER_NODES]
            if written and members:
                entry: TypeMembersPayload = {"type": written, "members": sorted(set(filter(None, members)))}
                trait = _written_path(node.child_by_field_name("trait"))
                if node.child_by_field_name("trait") is not None:
                    entry["trait"] = trait
                found.append(entry)
        elif node.type == "type_item":
            written = _written_path(node.child_by_field_name("type"))
            name = _node_text(node.child_by_field_name("name"))
            if written and name:
                found.append({"type": written, "alias": name})
        elif node.type == "mod_item" and node.child_by_field_name("body") is not None:
            found.extend(_type_members(node.child_by_field_name("body")))
    return found


def _declared_modules(container: Node, prefix: tuple[str, ...]) -> list[str]:
    """`mod name;` declarations as paths from the file's module: self::name, and self::outer::name for a
    declaration inside the inline module outer (its file lies in the folder outer/)."""
    declared: list[str] = []
    for node in container.named_children:
        if node.type != "mod_item":
            continue
        name = _node_text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        if not name:
            continue
        if body is None:
            declared.append("::".join((*prefix, name)))
        else:
            declared.extend(_declared_modules(body, (*prefix, name)))
    return declared


def _imported_name(rust_use: RustUse) -> ImportedName | None:
    if rust_use.glob:
        # Every public name of the module, as Python's `from m import *`.
        return ImportedName(local_name="*", original_name="*", module=rust_use.path)
    parts = tuple(part for part in rust_use.path.split("::") if part)
    if len(parts) < 2:
        return None
    original_name = parts[-1]
    return ImportedName(
        local_name=rust_use.alias or original_name,
        original_name=original_name,
        module="::".join(parts[:-1]),
    )


def _method_names(body: Node | None) -> list[str]:
    if body is None:
        return []
    methods: list[str] = []
    for child in body.named_children:
        if child.type not in ("function_item", "function_signature_item"):
            continue
        name = _node_text(child.child_by_field_name("name"))
        if name and name not in methods:
            methods.append(name)
    return methods


def _walk_named(node: Node) -> list[Node]:
    descendants = [node]
    for child in node.named_children:
        descendants.extend(_walk_named(child))
    return descendants


def _type_name(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type in ("identifier", "type_identifier", "primitive_type"):
        return _node_text(node)
    name = node.child_by_field_name("name")
    if name is not None:
        return _type_name(name)
    if node.type == "generic_type":
        # `From<eyre::Report>` is From: the arguments are not the type.
        return _type_name(node.child_by_field_name("type"))
    type_identifiers = [
        descendant
        for descendant in _walk_named(node)
        if descendant.type == "type_identifier"
    ]
    return _node_text(type_identifiers[-1]) if type_identifiers else ""


def _entity_from_item(node: Node, decorators: list[str]) -> Entity | None:
    name = _node_text(node.child_by_field_name("name"))
    if not name:
        return None

    kind_by_node = {
        "struct_item": "class",
        "union_item": "class",
        "trait_item": "interface",
        "enum_item": "enum",
        "type_item": "type",
        "function_item": "function",
        "const_item": "function",
        "static_item": "function",
    }
    kind = kind_by_node.get(node.type)
    if kind is None:
        return None

    methods: list[str] = []
    if node.type == "trait_item":
        methods = _method_names(node.child_by_field_name("body"))
    elif node.type == "enum_item":
        body = node.child_by_field_name("body")
        if body is not None:
            methods = [
                _node_text(child.child_by_field_name("name"))
                for child in body.named_children
                if child.type == "enum_variant"
                and _node_text(child.child_by_field_name("name"))
            ]

    visibility = "".join(_node_text(next((child for child in node.children if child.type == "visibility_modifier"), None)).split())
    return Entity(
        name=name,
        kind=kind,
        line=node.start_point[0] + 1,
        methods=methods,
        decorators=decorators,
        bases=[],
        private=visibility in ("", "pub(self)"),
        visible_in="super" if visibility == "pub(super)" else visibility[len("pub(in"):-1] if visibility.startswith("pub(in") else "",
    )


def rust_use_positions(path: str, content: str) -> list[UsePayload]:
    """Every use site of the file (names, paths, method calls), for `usages` and `entity --positions`.

    A build keeps only the uses that can name another file; this is the full list, computed
    when a query first needs positions.
    """
    root = _RustParser(_RUST_LANGUAGE).parse(content.encode("utf-8")).root_node
    return use_bindings_and_sites(root, _path_seen_from_file, full=True)[1]


def parse_rust_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    """Parse Rust source into the shared language-neutral graph contract."""
    tree = _RustParser(_RUST_LANGUAGE).parse(content.encode("utf-8"))
    root = tree.root_node
    rust_uses = _collect_uses(root)
    imports = [rust_use.path for rust_use in rust_uses]
    imports.extend(path for path in _collect_inline_paths(root, rust_uses) if path not in imports)
    imported_names = [
        imported_name
        for rust_use in rust_uses
        if (imported_name := _imported_name(rust_use)) is not None
    ]

    entities: list[Entity] = []
    entities_by_name: dict[str, Entity] = {}
    pending_attributes: list[Node] = []
    for node in root.named_children:
        if node.type in ("attribute_item", "inner_attribute_item"):
            pending_attributes.append(node)
            continue

        decorators = _attribute_names(tuple(pending_attributes))
        pending_attributes.clear()
        entity = _entity_from_item(node, decorators)
        if entity is not None:
            entities.append(entity)
            entities_by_name[entity.name] = entity

    exports_abstract = [
        entity.name for entity in entities if entity.kind == "interface"
    ]
    implements: list[str] = []
    extends: list[str] = []

    for node in root.named_children:
        if node.type != "impl_item":
            continue
        implemented_type = _type_name(node.child_by_field_name("type"))
        implemented_trait = _type_name(node.child_by_field_name("trait"))
        methods = _method_names(node.child_by_field_name("body"))
        entity = entities_by_name.get(implemented_type)
        if entity is not None:
            entity.methods = sorted(set((*entity.methods, *methods)))
            if implemented_trait and implemented_trait not in entity.bases:
                entity.bases.append(implemented_trait)
        if implemented_trait and implemented_trait not in implements:
            implements.append(implemented_trait)

    for module_import in _declared_modules(root, ("self",)):
        if module_import not in imports:
            imports.append(module_import)

    entity_payloads = [entity.to_dict() for entity in entities]
    bindings, use_sites = use_bindings_and_sites(root, _path_seen_from_file, full=False)
    return FileResult(
        path=path,
        language="rust",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=exports_abstract,
        implements=implements,
        extends=extends,
        purpose=infer_purpose(path, content, "rust", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=False,
        entities=entity_payloads,
        imported_names=[imported_name.to_dict() for imported_name in imported_names],
        exported_names=[entity.name for entity in entities],
        bindings=bindings,
        uses=use_sites,
        type_members=_type_members(root),
    )
