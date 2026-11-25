"""PHP and Laravel source parsing through the tree-sitter PHP grammar.

The parser records syntax only.  Class references leave this module as
pseudo-import strings (``php-class:`` and ``php-symbol:``) that the bound
resolver maps to local files for one repository.  Nothing here touches the
file system except ``file_hash``.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_php as _tsphp
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName, SourceRelationPayload
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from ..treesitter import node_text, unique_strings, walk_named
from .validation import validation_evidence


_PHP_LANGUAGE = _TreeSitterLanguage(_tsphp.language_php())

_DECLARATION_KINDS: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}
_DECLARATION_NODES = frozenset(_DECLARATION_KINDS)
_NAME_NODES = frozenset({"name", "qualified_name", "relative_name"})
_BUILTIN_TYPES = frozenset({
    "array",
    "bool",
    "callable",
    "closure",
    "false",
    "float",
    "generator",
    "int",
    "iterable",
    "mixed",
    "never",
    "null",
    "object",
    "parent",
    "self",
    "static",
    "string",
    "true",
    "void",
})
_ASSOCIATION_NAMES = (
    "belongsTo",
    "hasOne",
    "hasMany",
    "hasOneThrough",
    "hasManyThrough",
    "belongsToMany",
    "morphTo",
    "morphMany",
    "morphOne",
    "morphToMany",
    "morphedByMany",
)
_ASSOCIATIONS = frozenset(name.casefold() for name in _ASSOCIATION_NAMES)


@dataclass(frozen=True)
class PhpDeclaration:
    """One class-like declaration in a PHP file."""

    name: str
    kind: str
    line: int
    bases: tuple[str, ...]
    methods: tuple[str, ...]


@dataclass(frozen=True)
class PhpFileMetadata:
    """The declaration and import index the resolver needs per file."""

    namespace: str
    declarations: tuple[PhpDeclaration, ...]
    uses: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _DeclarationInfo:
    declaration: PhpDeclaration
    decorators: tuple[str, ...]
    extends: tuple[str, ...]
    implements: tuple[str, ...]
    method_nodes: tuple[Node, ...]
    is_abstract: bool


def _parse_root(content: str) -> Node:
    return Parser(_PHP_LANGUAGE).parse(
        content.encode("utf-8", errors="replace")
    ).root_node


def _first_class_name(node: Node) -> str:
    for child in node.named_children:
        if child.type in _NAME_NODES or child.type == "namespace_name":
            text = node_text(child).strip()
            if text:
                return text
    return ""


def _namespace_name(node: Node) -> str:
    return node_text(node.child_by_field_name("name")).strip()


def _namespace_body(node: Node) -> Node | None:
    for child in node.named_children:
        if child.type == "compound_statement":
            return child
    return None


def _last_segment(value: str) -> str:
    return value.rsplit("\\", 1)[-1]


def _is_facade(receiver: str, name: str) -> bool:
    """True for ``Name``, ``\\Name`` and the Illuminate facade namespace."""
    normalized = receiver.strip().lstrip("\\").casefold()
    expected = name.casefold()
    return normalized == expected or (
        normalized.startswith("illuminate\\")
        and normalized.endswith(f"\\{expected}")
    )


def _normalize_symbol(value: str) -> str:
    raw = value.strip()
    name = raw.lstrip("\\")
    if not name or name.casefold() in _BUILTIN_TYPES:
        return ""
    return f"\\{name}" if raw.startswith("\\") else name


def _attribute_names(container: Node) -> tuple[str, ...]:
    names: list[str] = []
    for child in container.named_children:
        if child.type != "attribute_list":
            continue
        for group in child.named_children:
            if group.type != "attribute_group":
                continue
            for attribute in group.named_children:
                if attribute.type == "attribute":
                    name = _first_class_name(attribute)
                    if name:
                        names.append(name)
    return tuple(unique_strings(iter(names)))


def _base_names(
    node: Node,
    body: Node | None,
) -> tuple[list[str], list[str], list[str]]:
    extends: list[str] = []
    implements: list[str] = []
    traits: list[str] = []
    for child in node.named_children:
        if child.type == "base_clause":
            extends.extend(
                name
                for base in child.named_children
                if base.type in _NAME_NODES
                if (name := node_text(base).strip())
            )
        elif child.type == "class_interface_clause":
            implements.extend(
                name
                for base in child.named_children
                if base.type in _NAME_NODES
                if (name := node_text(base).strip())
            )
    if body is not None:
        for child in body.named_children:
            if child.type != "use_declaration":
                continue
            traits.extend(
                name
                for trait in child.named_children
                if trait.type in _NAME_NODES
                if (name := node_text(trait).strip())
            )
    return extends, implements, traits


def _declaration_info(node: Node, namespace: str) -> _DeclarationInfo | None:
    name_node = node.child_by_field_name("name")
    raw_name = node_text(name_node).strip()
    if name_node is None or not raw_name:
        return None
    kind = _DECLARATION_KINDS[node.type]
    name = f"{namespace}\\{raw_name}" if namespace else raw_name
    body = node.child_by_field_name("body")
    extends, implements, traits = _base_names(node, body)
    method_nodes: tuple[Node, ...] = ()
    if body is not None:
        method_nodes = tuple(sorted(
            (
                child
                for child in body.named_children
                if child.type == "method_declaration"
            ),
            key=lambda child: child.start_point,
        ))
    methods = tuple(unique_strings(
        node_text(method.child_by_field_name("name")).strip()
        for method in method_nodes
    ))
    return _DeclarationInfo(
        declaration=PhpDeclaration(
            name=name,
            kind=kind,
            line=name_node.start_point[0] + 1,
            bases=tuple(unique_strings(iter(extends + implements + traits))),
            methods=methods,
        ),
        decorators=_attribute_names(node),
        extends=tuple(unique_strings(iter(extends))),
        implements=tuple(unique_strings(iter(implements))),
        method_nodes=method_nodes,
        is_abstract=(
            kind == "class"
            and any(child.type == "abstract_modifier" for child in node.children)
        ),
    )


def _collect_declarations(root: Node) -> list[_DeclarationInfo]:
    declarations: list[_DeclarationInfo] = []

    # One frame per list of siblings: [the siblings, the index of the next one, the namespace in force].
    # A stack of frames in place of recursion: an expression of a thousand operands is a thousand levels deep.
    frames: list[list] = [[root.named_children, 0, ""]]
    while frames:
        frame = frames[-1]
        children, index, current = frame
        if index >= len(children):
            frames.pop()
            continue
        frame[1] = index + 1
        node = children[index]
        if node.type == "namespace_definition":
            name = _namespace_name(node)
            body = _namespace_body(node)
            if body is not None:
                frame[2] = ""
                frames.append([body.named_children, 0, name])
            else:
                frame[2] = name
            continue
        if node.type in _DECLARATION_NODES:
            info = _declaration_info(node, current)
            if info is not None:
                declarations.append(info)
            frames.append([node.named_children, 0, current])
            continue
        if node.type in ("anonymous_class", "namespace_use_declaration"):
            continue
        frames.append([node.named_children, 0, current])
    declarations.sort(key=lambda info: info.declaration.line)
    return declarations


def _first_namespace(root: Node) -> str:
    for node in root.named_children:
        if node.type == "namespace_definition":
            return _namespace_name(node)
    return ""


def _use_clause_entry(
    clause: Node,
    prefix: str,
) -> tuple[str, str] | None:
    if any(child.type in ("function", "const") for child in clause.children):
        return None
    name_node = next(
        (child for child in clause.named_children if child.type in _NAME_NODES),
        None,
    )
    if name_node is None:
        return None
    raw_name = node_text(name_node).strip()
    if not raw_name:
        return None
    fqcn = f"{prefix}\\{raw_name}" if prefix else raw_name
    fqcn = fqcn.strip().lstrip("\\")
    if not fqcn:
        return None
    alias_node = clause.child_by_field_name("alias")
    alias = node_text(alias_node).strip() if alias_node is not None else ""
    if not alias:
        alias = _last_segment(fqcn)
    if not alias:
        return None
    return alias, fqcn


def _use_entries(node: Node) -> list[tuple[str, str]]:
    if any(child.type in ("function", "const") for child in node.children):
        return []
    group = node.child_by_field_name("body")
    if group is not None and group.type == "namespace_use_group":
        prefix = _first_class_name(node).lstrip("\\")
        entries: list[tuple[str, str]] = []
        for clause in group.named_children:
            if clause.type != "namespace_use_clause":
                continue
            entry = _use_clause_entry(clause, prefix)
            if entry is not None:
                entries.append(entry)
        return entries
    entries = []
    for clause in node.named_children:
        if clause.type != "namespace_use_clause":
            continue
        entry = _use_clause_entry(clause, "")
        if entry is not None:
            entries.append(entry)
    return entries


def _collect_uses(root: Node) -> list[tuple[str, str]]:
    uses: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in walk_named(root):
        if node.type != "namespace_use_declaration":
            continue
        for entry in _use_entries(node):
            if entry not in seen:
                seen.add(entry)
                uses.append(entry)
    return uses


def _use_lines(root: Node) -> dict[str, list[int]]:
    """Class name of a `use` declaration -> the 1-based lines of the declarations that name it."""
    lines: dict[str, list[int]] = {}
    for node in walk_named(root):
        if node.type != "namespace_use_declaration":
            continue
        for _alias, fqcn in _use_entries(node):
            add_import_line(lines, fqcn, node.start_point[0] + 1)
    return lines


def _scoped_receiver(node: Node) -> str:
    named = node.named_children
    if not named or named[0].type not in _NAME_NODES:
        return ""
    return node_text(named[0]).strip()


def _method_name(node: Node) -> str:
    return node_text(node.child_by_field_name("name")).strip()


def _function_name(node: Node) -> str:
    callee = node.child_by_field_name("function")
    if callee is None:
        return ""
    return _last_segment(node_text(callee).strip()).casefold()


def _call_arguments(node: Node) -> tuple[Node, ...]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return ()
    return tuple(
        child for child in arguments.named_children
        if child.type == "argument"
    )


def _argument_expression(node: Node, index: int) -> Node | None:
    arguments = _call_arguments(node)
    if index >= len(arguments):
        return None
    argument = arguments[index]
    return argument.named_children[0] if argument.named_children else None


def _first_argument_expression(node: Node) -> Node | None:
    return _argument_expression(node, 0)


def _empty_argument_list(node: Node) -> bool:
    arguments = node.child_by_field_name("arguments")
    return arguments is not None and not _call_arguments(node)


def _literal_string(node: Node | None) -> str:
    if node is None or node.type not in ("string", "encapsed_string"):
        return ""
    raw = node_text(node)
    if len(raw) < 3:
        return ""
    quote = raw[0]
    if quote not in ("'", '"') or raw[-1] != quote:
        return ""
    value = raw[1:-1]
    if not value:
        return ""
    if "\\" in value or "\n" in value or "\r" in value:
        return ""
    if node.type == "encapsed_string" and "$" in value:
        return ""
    return value


def _class_constant_class(node: Node | None) -> Node | None:
    if node is None or node.type != "class_constant_access_expression":
        return None
    named = node.named_children
    if len(named) < 2:
        return None
    if node_text(named[-1]).strip().casefold() != "class":
        return None
    if named[0].type not in _NAME_NODES:
        return None
    return named[0]


def _new_class_node(node: Node | None) -> Node | None:
    if node is None or node.type != "object_creation_expression":
        return None
    for child in node.named_children:
        if child.type in _NAME_NODES:
            return child
        if child.type == "anonymous_class":
            return None
    return None


def _points_at_own(
    target: str,
    own_full: frozenset[str],
    own_short: frozenset[str],
) -> bool:
    if target.startswith("php-class:"):
        return target[len("php-class:"):].casefold() in own_full
    if target.startswith("php-symbol:"):
        raw_name = target[len("php-symbol:"):]
        absolute = raw_name.startswith("\\")
        name = raw_name.lstrip("\\")
        if absolute:
            return name.casefold() in own_full
        if "\\" in name:
            return name.casefold() in own_full
        return name.casefold() in own_short
    return False


def _walk_dependencies(
    root: Node,
    own_full: frozenset[str],
    own_short: frozenset[str],
) -> tuple[list[str], list[SourceRelationPayload]]:
    imports: list[str] = []
    seen_imports: set[str] = set()
    relations: list[SourceRelationPayload] = []
    seen_relations: set[tuple[str, str]] = set()

    def add_import(target: str) -> None:
        if not target or target in seen_imports:
            return
        if _points_at_own(target, own_full, own_short):
            return
        seen_imports.add(target)
        imports.append(target)

    def add_reference(value: str) -> None:
        name = _normalize_symbol(value)
        if not name:
            return
        absolute = name.startswith("\\")
        bare_name = name.lstrip("\\")
        key = bare_name.casefold()
        if absolute:
            if key in own_full:
                return
        elif key in own_short:
            return
        elif "\\" in bare_name and key in own_full:
            return
        add_import(f"php-symbol:{name}")

    def add_relation_target(target: str, relation: str) -> None:
        if not target:
            return
        key = (target, relation)
        if key not in seen_relations:
            seen_relations.add(key)
            relations.append({"target": target, "relation": relation})
        add_import(target)

    def add_class_relation(value: str, relation: str) -> None:
        name = _normalize_symbol(value)
        if name:
            add_relation_target(f"php-symbol:{name}", relation)

    def visit(node: Node, in_declaration: bool) -> bool | None:
        """Read one node; the answer is `in_declaration` for its children, None when they are not read."""
        node_type = node.type
        child_in_declaration = in_declaration
        if node_type in _DECLARATION_NODES:
            child_in_declaration = True
        elif node_type == "anonymous_class":
            child_in_declaration = False

        if node_type == "namespace_use_declaration":
            return None

        if node_type in ("base_clause", "class_interface_clause"):
            for child in node.named_children:
                if child.type in _NAME_NODES:
                    add_reference(node_text(child).strip())
        elif node_type == "use_declaration":
            for child in node.named_children:
                if child.type in _NAME_NODES:
                    add_reference(node_text(child).strip())
        elif node_type == "object_creation_expression":
            for child in node.named_children:
                if child.type in _NAME_NODES:
                    add_reference(node_text(child).strip())
                    break
                if child.type == "anonymous_class":
                    break
        elif node_type == "scoped_call_expression":
            receiver = _scoped_receiver(node)
            method = _method_name(node).casefold()
            if receiver:
                add_reference(receiver)
            if method in ("dispatch", "dispatchsync"):
                target = ""
                if _is_facade(receiver, "bus"):
                    class_node = _new_class_node(_first_argument_expression(node))
                    if class_node is not None:
                        target = node_text(class_node).strip()
                else:
                    target = receiver
                if target:
                    add_class_relation(target, "job")
            elif method == "make" and _is_facade(receiver, "view"):
                literal = _literal_string(_first_argument_expression(node))
                if literal:
                    add_relation_target(f"php-view:{literal}", "view")
            elif method == "view" and _is_facade(receiver, "route"):
                literal = _literal_string(_argument_expression(node, 1))
                if literal:
                    add_relation_target(f"php-view:{literal}", "view")
        elif node_type == "member_call_expression":
            method = _method_name(node).casefold()
            if method in _ASSOCIATIONS and in_declaration:
                class_node = _class_constant_class(
                    _first_argument_expression(node)
                )
                if class_node is not None:
                    add_class_relation(node_text(class_node).strip(), "association")
            elif method == "make":
                receiver = node.child_by_field_name("object")
                if (
                    receiver is not None
                    and receiver.type == "function_call_expression"
                    and _function_name(receiver) == "view"
                    and _empty_argument_list(receiver)
                ):
                    literal = _literal_string(_first_argument_expression(node))
                    if literal:
                        add_relation_target(f"php-view:{literal}", "view")
            elif method == "view":
                receiver = node.child_by_field_name("object")
                if receiver is not None and (
                    (
                        receiver.type == "function_call_expression"
                        and _function_name(receiver) == "response"
                    )
                    or (
                        receiver.type == "variable_name"
                        and node_text(receiver).strip().casefold()
                        == "$response"
                    )
                ):
                    literal = _literal_string(_first_argument_expression(node))
                    if literal:
                        add_relation_target(f"php-view:{literal}", "view")
            elif method in ("dispatch", "dispatchsync"):
                class_node = _new_class_node(_first_argument_expression(node))
                if class_node is not None:
                    add_class_relation(node_text(class_node).strip(), "job")
        elif node_type == "function_call_expression":
            function = _function_name(node)
            if function == "view":
                literal = _literal_string(_first_argument_expression(node))
                if literal:
                    add_relation_target(f"php-view:{literal}", "view")
            elif function == "event":
                class_node = _new_class_node(_first_argument_expression(node))
                if class_node is not None:
                    add_class_relation(node_text(class_node).strip(), "event")
            elif function in ("dispatch", "dispatchsync"):
                class_node = _new_class_node(_first_argument_expression(node))
                if class_node is not None:
                    add_class_relation(node_text(class_node).strip(), "job")
        elif node_type == "class_constant_access_expression":
            named = node.named_children
            if named and named[0].type in _NAME_NODES:
                add_reference(node_text(named[0]).strip())
        elif node_type == "scoped_property_access_expression":
            named = node.named_children
            if named and named[0].type in _NAME_NODES:
                add_reference(node_text(named[0]).strip())
        elif node_type == "named_type":
            add_reference(node_text(node).strip())
        elif node_type == "attribute":
            add_reference(_first_class_name(node))
        elif node_type == "binary_expression":
            operator = node.child_by_field_name("operator")
            if (
                operator is not None
                and node_text(operator).strip().casefold() == "instanceof"
            ):
                right = node.child_by_field_name("right")
                if right is not None and right.type in _NAME_NODES:
                    add_reference(node_text(right).strip())

        return child_in_declaration

    # In document order, with a stack in place of recursion (see _collect_declarations).
    pending: list[tuple[Node, bool]] = [(root, False)]
    while pending:
        node, in_declaration = pending.pop()
        child_in_declaration = visit(node, in_declaration)
        if child_in_declaration is not None:
            pending.extend((child, child_in_declaration) for child in reversed(node.named_children))
    return imports, relations


def _entities(declarations: list[_DeclarationInfo]) -> list[Entity]:
    entities: list[Entity] = []
    for info in declarations:
        declaration = info.declaration
        entities.append(Entity(
            name=declaration.name,
            kind=declaration.kind,
            line=declaration.line,
            methods=list(declaration.methods),
            decorators=list(info.decorators),
            bases=list(declaration.bases),
        ))
        for method in info.method_nodes:
            method_name_node = method.child_by_field_name("name")
            method_name = node_text(method_name_node).strip()
            if method_name_node is None or not method_name:
                continue
            entities.append(Entity(
                name=f"{declaration.name}::{method_name}",
                kind="function",
                line=method_name_node.start_point[0] + 1,
                methods=[],
                decorators=list(_attribute_names(method)),
                bases=[],
            ))
    return entities


def php_metadata(content: str) -> PhpFileMetadata:
    """Return the namespace, declarations and class imports of one file."""
    root = _parse_root(content)
    return PhpFileMetadata(
        namespace=_first_namespace(root),
        declarations=tuple(
            info.declaration for info in _collect_declarations(root)
        ),
        uses=tuple(_collect_uses(root)),
    )


def parse_php_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    """Parse one PHP source file into the shared result contract."""
    root = _parse_root(content)
    declarations = _collect_declarations(root)
    own_full = frozenset(
        info.declaration.name.casefold() for info in declarations
    )
    own_short = frozenset(
        _last_segment(info.declaration.name).casefold()
        for info in declarations
    )

    imports: list[str] = []
    imported_names: list[ImportedName] = []
    seen_imports: set[str] = set()

    def add_import(target: str) -> None:
        if not target or target in seen_imports:
            return
        if _points_at_own(target, own_full, own_short):
            return
        seen_imports.add(target)
        imports.append(target)

    uses = _collect_uses(root)
    validation = validation_evidence(root, content, uses)
    # Only a `use` declaration is an import statement with a line of its own: a class named in the code is a
    # use, a view or a route name a convention.
    use_lines = _use_lines(root)
    import_lines: dict[str, list[int]] = {}
    for alias, fqcn in uses:
        add_import(f"php-class:{fqcn}")
        stated = use_lines.get(fqcn, [])
        if f"php-class:{fqcn}" in seen_imports:
            for line in stated:
                add_import_line(import_lines, f"php-class:{fqcn}", line)
        imported_names.append(ImportedName(
            local_name=alias,
            original_name=_last_segment(fqcn),
            module=f"php-class:{fqcn}",
            line=stated[0] if stated else 0,
        ))

    references, relations = _walk_dependencies(root, own_full, own_short)
    for target in references:
        add_import(target)

    entities = _entities(declarations)
    entity_payloads = [entity.to_dict() for entity in entities]
    return FileResult(
        path=path,
        language="php",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=[
            info.declaration.name
            for info in declarations
            if info.declaration.kind in ("interface", "trait")
            or info.is_abstract
        ],
        implements=unique_strings(
            base for info in declarations for base in info.implements
        ),
        extends=unique_strings(
            base for info in declarations for base in info.extends
        ),
        purpose=infer_purpose(path, content, "php", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(validation),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=[info.declaration.name for info in declarations],
        relations=relations,
        import_lines=import_lines,
        validation_evidence=validation,
    )


__all__ = [
    "PhpDeclaration",
    "PhpFileMetadata",
    "parse_php_treesitter",
    "php_metadata",
]