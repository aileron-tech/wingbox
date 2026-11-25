from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_go as _tsg
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from ..treesitter import node_text, unique_strings, walk_named
from .validation import validation_evidence


_GO_LANGUAGE = _TreeSitterLanguage(_tsg.language())


def _field_children(node: Node, field_name: str) -> list[Node]:
    return [
        child
        for index, child in enumerate(node.children)
        if node.field_name_for_child(index) == field_name
    ]


def _import_specs(root: Node) -> list[tuple[str, str, int]]:
    """(import path, alias, 1-based line of the import spec) of every import of the file."""
    imports: list[tuple[str, str, int]] = []
    for node in walk_named(root):
        if node.type != "import_spec":
            continue
        import_path = node_text(node.child_by_field_name("path")).strip('"`')
        alias = node_text(node.child_by_field_name("name"))
        if import_path:
            imports.append((import_path, alias, node.start_point[0] + 1))
    return imports


def _type_entities(root: Node) -> list[Entity]:
    entities: list[Entity] = []
    for declaration in root.named_children:
        if declaration.type != "type_declaration":
            continue
        for spec in declaration.named_children:
            if spec.type not in ("type_spec", "type_alias"):
                continue
            name = node_text(spec.child_by_field_name("name"))
            type_node = spec.child_by_field_name("type")
            if not name or type_node is None:
                continue
            if type_node.type == "struct_type":
                kind = "class"
                methods: list[str] = []
            elif type_node.type == "interface_type":
                kind = "interface"
                methods = unique_strings(
                    node_text(method.child_by_field_name("name"))
                    for method in walk_named(type_node)
                    if method.type == "method_elem"
                )
            else:
                kind = "type"
                methods = []
            entities.append(Entity(
                name=name,
                kind=kind,
                line=spec.start_point[0] + 1,
                methods=methods,
                decorators=[],
                bases=[],
            ))
    return entities


def _value_entities(root: Node) -> list[Entity]:
    entities: list[Entity] = []
    declaration_types = {"const_declaration", "var_declaration"}
    spec_types = {"const_spec", "var_spec"}
    for declaration in root.named_children:
        if declaration.type not in declaration_types:
            continue
        for spec in walk_named(declaration):
            if spec.type not in spec_types:
                continue
            for name_node in _field_children(spec, "name"):
                name = node_text(name_node)
                if name:
                    entities.append(Entity(
                        name=name,
                        kind="function",
                        line=spec.start_point[0] + 1,
                        methods=[],
                        decorators=[],
                        bases=[],
                    ))
    return entities


def _function_entities(root: Node) -> list[Entity]:
    return [
        Entity(
            name=name,
            kind="function",
            line=node.start_point[0] + 1,
            methods=[],
            decorators=[],
            bases=[],
        )
        for node in root.named_children
        if node.type == "function_declaration"
        if (name := node_text(node.child_by_field_name("name")))
    ]


def _receiver_type(node: Node) -> str:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return ""
    return next(
        (
            node_text(descendant)
            for descendant in walk_named(receiver)
            if descendant.type == "type_identifier"
        ),
        "",
    )


def _attach_methods(root: Node, entities: list[Entity]) -> None:
    entities_by_name = {entity.name: entity for entity in entities}
    for node in root.named_children:
        if node.type != "method_declaration":
            continue
        receiver = _receiver_type(node)
        method = node_text(node.child_by_field_name("name"))
        entity = entities_by_name.get(receiver)
        if entity is not None and method and method not in entity.methods:
            entity.methods.append(method)


@dataclass(frozen=True)
class GoFileNames:
    """What links a file to the other files of its package: the package it states, the names it declares at
    package level and the names it uses that nothing in the file binds."""

    package: str
    declared: frozenset[str]
    used: frozenset[str]


_FUNCTION_NODES = frozenset({"function_declaration", "method_declaration", "func_literal"})
# A node that opens a block of its own; a name declared in it ends with it.
_SCOPE_NODES = _FUNCTION_NODES | frozenset({
    "block", "if_statement", "for_statement", "expression_switch_statement", "type_switch_statement",
    "select_statement", "expression_case", "type_case", "default_case", "communication_case",
    "type_spec", "type_alias",
})
_PARAMETER_NODES = frozenset({
    "parameter_declaration", "variadic_parameter_declaration", "type_parameter_declaration",
})
# The literal types whose keys are expressions; the key of any other literal may be the name of a struct field.
_KEYED_BY_EXPRESSION = frozenset({"map_type", "slice_type", "array_type", "implicit_length_array_type"})
# Not read for names: the package clause, the imports and a type of another package (`other.Type`).
_NAMELESS_NODES = frozenset({"package_clause", "import_declaration", "qualified_type", "comment"})
_LEAVE_SCOPE = object()


def _identifiers(node: Node | None) -> frozenset[str]:
    """The names an expression list on the left of `:=` binds."""
    if node is None:
        return frozenset()
    nodes = [node] if node.type == "identifier" else node.named_children
    return frozenset(node_text(item) for item in nodes if item.type == "identifier")


def _parameter_names(node: Node) -> frozenset[str]:
    """The receiver, the parameters, the named results and the type parameters of a function or a type."""
    names: set[str] = set()
    for field_name in ("type_parameters", "receiver", "parameters", "result"):
        parameters = node.child_by_field_name(field_name)
        if parameters is None:
            continue
        for declaration in parameters.named_children:
            if declaration.type in _PARAMETER_NODES:
                names.update(node_text(name) for name in _field_children(declaration, "name"))
    return frozenset(names)


def _binds_with_define(node: Node) -> bool:
    return any(child.type == ":=" for child in node.children)


def _package_level_names(root: Node) -> set[str]:
    names: set[str] = set()
    for declaration in root.named_children:
        if declaration.type == "function_declaration":
            names.add(node_text(declaration.child_by_field_name("name")))
        elif declaration.type in ("type_declaration", "const_declaration", "var_declaration"):
            specs = [
                spec
                for child in declaration.named_children
                for spec in (child.named_children if child.type.endswith("_spec_list") else [child])
            ]
            for spec in specs:
                if spec.type in ("type_spec", "type_alias", "const_spec", "var_spec"):
                    names.update(node_text(name) for name in _field_children(spec, "name"))
    names.discard("")
    names.discard("_")
    return names


def _free_names(root: Node) -> set[str]:
    """The identifiers of the file that no parameter, local declaration or `:=` of an enclosing block binds
    at the place of use. Walks with a stack of its own, so a deep tree cannot exhaust the interpreter's."""
    used: set[str] = set()
    scopes: list[set[str]] = []
    stack: list[object] = list(reversed(root.named_children))
    while stack:
        item = stack.pop()
        if item is _LEAVE_SCOPE:
            scopes.pop()
            continue
        if isinstance(item, frozenset):   # names bound from here to the end of the block
            if scopes:
                scopes[-1].update(item)
            continue
        node: Node = item  # type: ignore[assignment]
        kind = node.type
        if kind in ("identifier", "type_identifier"):
            name = node_text(node)
            if name != "_" and not any(name in scope for scope in scopes):
                used.add(name)
            continue
        if kind in _NAMELESS_NODES:
            continue
        named = [
            (child, node.field_name_for_child(index))
            for index, child in enumerate(node.children)
            if child.is_named
        ]
        follow: list[object]
        if kind in _FUNCTION_NODES:
            follow = [_parameter_names(node), *(child for child, field in named if field != "name")]
        elif kind in ("type_spec", "type_alias"):
            if scopes:   # a type declared in a function: its name is bound in the enclosing block
                scopes[-1].add(node_text(node.child_by_field_name("name")))
            follow = [_parameter_names(node), *(child for child, field in named if field != "name")]
        elif kind in _PARAMETER_NODES:
            follow = [child for child, field in named if field != "name"]
        elif kind in ("var_spec", "const_spec"):
            follow = [child for child, field in named if field != "name"]
            follow.append(frozenset(node_text(child) for child, field in named if field == "name"))
        elif kind == "short_var_declaration" or (
            kind in ("range_clause", "receive_statement") and _binds_with_define(node)
        ):
            follow = [child for child, field in named if field != "left"]
            follow.append(_identifiers(node.child_by_field_name("left")))
        elif kind == "type_switch_statement":
            follow = []
            for child, field in named:
                if field != "alias":
                    follow.append(child)
                if field == "value":
                    follow.append(_identifiers(node.child_by_field_name("alias")))
        elif kind == "keyed_element":
            owner = node.parent.parent if node.parent is not None else None
            literal_type = (
                owner.child_by_field_name("type")
                if owner is not None and owner.type == "composite_literal"
                else None
            )
            by_expression = literal_type is not None and literal_type.type in _KEYED_BY_EXPRESSION
            follow = [
                child
                for child, field in named
                if by_expression
                or field != "key"
                or [item.type for item in child.named_children] != ["identifier"]
            ]
        else:
            follow = [child for child, _field in named]
        if kind in _SCOPE_NODES:
            scopes.append(set())
            stack.append(_LEAVE_SCOPE)
        stack.extend(reversed(follow))
    return used


def go_file_names(content: str) -> GoFileNames:
    root = Parser(_GO_LANGUAGE).parse(content.encode("utf-8")).root_node
    package = next(
        (
            node_text(name)
            for clause in root.named_children
            if clause.type == "package_clause"
            for name in clause.named_children
            if name.type == "package_identifier"
        ),
        "",
    )
    declared = _package_level_names(root)
    imported = {alias or import_path.rsplit("/", 1)[-1] for import_path, alias, _line in _import_specs(root)}
    return GoFileNames(package, frozenset(declared), frozenset(_free_names(root) - declared - imported))


def parse_go_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    root = Parser(_GO_LANGUAGE).parse(content.encode("utf-8")).root_node
    located_specs = _import_specs(root)
    import_specs = [(import_path, alias) for import_path, alias, _line in located_specs]
    import_lines: dict[str, list[int]] = {}
    for import_path, _alias, line in located_specs:
        add_import_line(import_lines, import_path, line)
    entities = [
        *_type_entities(root),
        *_value_entities(root),
        *_function_entities(root),
    ]
    _attach_methods(root, entities)
    imports = unique_strings(import_path for import_path, _ in import_specs)
    imported_names = [
        ImportedName(
            local_name=alias or import_path.rsplit("/", 1)[-1],
            original_name=import_path.rsplit("/", 1)[-1],
            module=import_path,
            line=line,
        )
        for import_path, alias, line in located_specs
        if alias != "_"
    ]
    entity_payloads = [entity.to_dict() for entity in entities]
    validation = validation_evidence(root, content, import_specs)
    return FileResult(
        path=path,
        language="go",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=[
            entity.name for entity in entities if entity.kind == "interface"
        ],
        implements=[],
        extends=[],
        purpose=infer_purpose(path, content, "go", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(validation),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=[entity.name for entity in entities],
        import_lines=import_lines,
        validation_evidence=validation,
    )
