from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import tree_sitter_c_sharp as _tsc
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from ..treesitter import node_text, unique_strings, walk_named
from .validation import validation_evidence


_CSHARP_LANGUAGE = _TreeSitterLanguage(_tsc.language())
_TYPE_NODES = frozenset({
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "record_declaration",
    "record_struct_declaration",
    "enum_declaration",
    "delegate_declaration",
})
_KIND_BY_NODE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "struct_declaration": "class",
    "record_declaration": "class",
    "record_struct_declaration": "class",
    "enum_declaration": "enum",
    "delegate_declaration": "type",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class CSharpUsing:
    target: str
    alias: str | None
    is_static: bool
    line: int = 0   # the 1-based line of the using directive
    is_global: bool = False   # `global using`: it holds for every file of the project
    inner: bool = False   # written in a namespace: in its block, or after a file-scoped namespace declaration


@dataclass(frozen=True)
class CSharpFileScope:
    """What decides which type a simple name written in a file binds to.

    declared: (full type name, declared `partial`) of every type of the file. levels: the namespaces the
    compiler searches, nearest first (the own namespace, the usings written in it, each enclosing namespace,
    the global namespace, the usings of the file); a name binds in the first level that has it. ordered is
    false for a file that declares types in several namespaces: its names are searched in every namespace of
    `levels` alike. global_usings: the namespaces of its `global using` directives. explicit: the targets of
    alias and static usings, which name a type in full. referenced: the capitalized identifiers of the file.
    """

    declared: tuple[tuple[str, bool], ...]
    levels: tuple[tuple[str, ...], ...]
    ordered: bool
    global_usings: tuple[str, ...]
    explicit: frozenset[str]
    referenced: frozenset[str]


def _simple_type_name(node: Node) -> str:
    text = re.sub(r"<.*>", "", node_text(node))
    names = _IDENTIFIER_RE.findall(text)
    return names[-1] if names else ""


def _namespace_name(node: Node) -> str:
    return node_text(node.child_by_field_name("name"))


def _join_namespace(parent: str, child: str) -> str:
    if not parent or child.startswith(parent + "."):
        return child
    return f"{parent}.{child}" if child else parent


def _declarations(
    node: Node,
    inherited_namespace: str = "",
) -> Iterator[tuple[str, Node]]:
    file_namespace = inherited_namespace
    for child in node.named_children:
        if child.type == "file_scoped_namespace_declaration":
            file_namespace = _join_namespace(
                inherited_namespace,
                _namespace_name(child),
            )

    for child in node.named_children:
        if child.type == "namespace_declaration":
            namespace = _join_namespace(
                inherited_namespace,
                _namespace_name(child),
            )
            body = child.child_by_field_name("body")
            if body is not None:
                yield from _declarations(body, namespace)
        elif child.type in _TYPE_NODES:
            yield file_namespace, child


def declared_csharp_types(content: str) -> tuple[str, ...]:
    root = Parser(_CSHARP_LANGUAGE).parse(content.encode("utf-8")).root_node
    return tuple(
        f"{namespace}.{name}" if namespace else name
        for namespace, node in _declarations(root)
        if (name := node_text(node.child_by_field_name("name")))
    )


def _attributes(node: Node) -> list[str]:
    return unique_strings(
        node_text(attribute.child_by_field_name("name"))
        for child in node.named_children
        if child.type == "attribute_list"
        for attribute in child.named_children
        if attribute.type == "attribute"
    )


def _base_names(node: Node) -> list[str]:
    base_list = next(
        (child for child in node.named_children if child.type == "base_list"),
        None,
    )
    if base_list is None:
        return []
    return unique_strings(
        _simple_type_name(child) for child in base_list.named_children
    )


def _method_names(node: Node) -> list[str]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    method_types = {
        "method_declaration",
        "operator_declaration",
        "conversion_operator_declaration",
    }
    return unique_strings(
        node_text(child.child_by_field_name("name"))
        for child in body.named_children
        if child.type in method_types
    )


def _enum_members(node: Node) -> list[str]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    return unique_strings(
        node_text(child.child_by_field_name("name"))
        for child in body.named_children
        if child.type == "enum_member_declaration"
    )


def _entity(node: Node) -> Entity | None:
    name = node_text(node.child_by_field_name("name"))
    kind = _KIND_BY_NODE.get(node.type)
    if not name or kind is None:
        return None
    methods = _enum_members(node) if kind == "enum" else _method_names(node)
    return Entity(
        name=name,
        kind=kind,
        line=node.start_point[0] + 1,
        methods=methods,
        decorators=_attributes(node),
        bases=_base_names(node),
    )


def _parse_using(node: Node, inner: bool = False) -> CSharpUsing | None:
    named_children = list(node.named_children)
    if not named_children:
        return None
    target = node_text(named_children[-1])
    alias_node = node.child_by_field_name("name")
    alias = node_text(alias_node) or None
    text = node_text(node)
    return CSharpUsing(
        target=target,
        alias=alias,
        is_static=bool(re.search(r"\busing\s+static\b", text)),
        line=node.start_point[0] + 1,
        is_global=any(child.type == "global" for child in node.children),
        inner=inner,
    )


def _usings(root: Node) -> list[CSharpUsing]:
    """Every using directive of the file: those of the file itself and those written in a namespace."""
    usings: list[CSharpUsing] = []
    pending: list[tuple[Node, bool]] = [(root, False)]
    while pending:
        node, inner = pending.pop(0)
        for child in node.named_children:
            if child.type == "file_scoped_namespace_declaration":
                inner = True
            elif child.type == "using_directive":
                using = _parse_using(child, inner)
                if using is not None:
                    usings.append(using)
            elif child.type == "namespace_declaration":
                body = child.child_by_field_name("body")
                if body is not None:
                    pending.append((body, True))
    return usings


def _is_partial(node: Node) -> bool:
    return any(
        child.type == "modifier" and node_text(child) == "partial"
        for child in node.named_children
    )


def _namespace_chain(namespace: str) -> list[str]:
    """The namespace, each namespace that encloses it and the global namespace (""), nearest first."""
    chain = [namespace] if namespace else []
    while chain and "." in chain[-1]:
        chain.append(chain[-1].rpartition(".")[0])
    return [*chain, ""]


def csharp_file_scope(content: str) -> CSharpFileScope:
    root = Parser(_CSHARP_LANGUAGE).parse(content.encode("utf-8")).root_node
    declarations = [
        (namespace, name, _is_partial(node))
        for namespace, node in _declarations(root)
        if (name := node_text(node.child_by_field_name("name")))
    ]
    namespaces = unique_strings(namespace or "." for namespace, _name, _partial in declarations)
    usings = _usings(root)
    plain = [using for using in usings if using.alias is None and not using.is_static]
    inner = tuple(unique_strings(using.target for using in plain if using.inner))
    outer = tuple(unique_strings(using.target for using in plain if not using.inner))
    chains = [_namespace_chain("" if namespace == "." else namespace) for namespace in namespaces or ["."]]
    ordered = len(chains) == 1
    if ordered:
        own, *enclosing = chains[0]
        levels = [(own,), inner, *((namespace,) for namespace in enclosing), outer]
    else:
        levels = [tuple(dict.fromkeys(name for chain in chains for name in chain)), inner, outer]
    return CSharpFileScope(
        declared=tuple(
            (f"{namespace}.{name}" if namespace else name, partial)
            for namespace, name, partial in declarations
        ),
        levels=tuple(level for level in levels if level),
        ordered=ordered,
        global_usings=tuple(unique_strings(using.target for using in plain if using.is_global)),
        explicit=frozenset(using.target for using in usings if using.alias is not None or using.is_static),
        referenced=frozenset(_referenced_type_names(root)) - {name for _namespace, name, _partial in declarations},
    )


def _referenced_type_names(root: Node) -> list[str]:
    return unique_strings(
        value
        for node in walk_named(root)
        if node.type == "identifier"
        if (value := node_text(node))
        if value[0].isupper()
    )


def parse_csharp_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    root = Parser(_CSHARP_LANGUAGE).parse(content.encode("utf-8")).root_node
    declarations = list(_declarations(root))
    entity_nodes = [
        (entity, node)
        for _, node in declarations
        if (entity := _entity(node)) is not None
    ]
    entities = [entity for entity, _ in entity_nodes]
    declared_names = {entity.name for entity in entities}
    namespace = declarations[0][0] if declarations else ""
    usings = _usings(root)
    referenced_types = [
        name
        for name in _referenced_type_names(root)
        if name not in declared_names
    ]

    imports: list[str] = []
    imported_names: list[ImportedName] = []
    # A using directive has a line, and so has a type it brings in; a type of the file's own namespace is
    # not stated by any statement.
    import_lines: dict[str, list[int]] = {}
    for using in usings:
        imports.append(using.target)
        add_import_line(import_lines, using.target, using.line)
        if using.alias is not None or using.is_static:
            original = using.target.rsplit(".", 1)[-1]
            imported_names.append(ImportedName(
                local_name=using.alias or original,
                original_name=original,
                module=using.target,
                line=using.line,
            ))
            continue
        for type_name in referenced_types:
            candidate = f"{using.target}.{type_name}"
            imports.append(candidate)
            add_import_line(import_lines, candidate, using.line)
            imported_names.append(ImportedName(
                local_name=type_name,
                original_name=type_name,
                module=candidate,
                line=using.line,
            ))

    # The own namespace, the namespaces that enclose it and the global one: which of them a name binds to is
    # decided where the declarations of every file are known (BoundCSharpLanguage).
    for enclosing in _namespace_chain(namespace):
        for type_name in referenced_types:
            candidate = f"{enclosing}.{type_name}" if enclosing else type_name
            imports.append(candidate)
            imported_names.append(ImportedName(
                local_name=type_name,
                original_name=type_name,
                module=candidate,
            ))

    entity_payloads = [entity.to_dict() for entity in entities]
    exports_abstract = [
        entity.name
        for entity, node in entity_nodes
        if entity.kind == "interface"
        or any(
            child.type == "modifier" and node_text(child) == "abstract"
            for child in node.named_children
        )
    ]
    bases = unique_strings(
        base for entity in entities for base in entity.bases
    )
    validation = validation_evidence(
        root,
        content,
        frozenset(using.target for using in usings if using.alias is None and not using.is_static),
        {using.alias: using.target for using in usings if using.alias is not None},
    )
    return FileResult(
        path=path,
        language="csharp",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=unique_strings(iter(imports)),
        exports_abstract=exports_abstract,
        implements=bases,
        extends=bases,
        purpose=infer_purpose(path, content, "csharp", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(validation),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=[entity.name for entity in entities],
        import_lines=import_lines,
        validation_evidence=validation,
    )
