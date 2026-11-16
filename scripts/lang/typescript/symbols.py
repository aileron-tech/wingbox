"""TypeScript and JavaScript symbol table of one file: import and export bindings, use sites.

A use site is a name (`X`, `<Comp />`, `{ x }`), a member chain whose first name is
a module-level binding (`ns.S`, `Enum.A`), a qualified type (`ns.Type`), or a
name in an import or export statement. Parameters, variables, catch parameters,
type parameters and declarations inside a function shadow a module-level name
of the same spelling and are not use sites.
"""
from __future__ import annotations

from typing import Iterator

from tree_sitter import Node

from ..interface import BindingPayload, UsePayload

Segment = tuple[str, int, int]

_FUNCTION_NODES = frozenset({
    "function_declaration", "function_expression", "function", "arrow_function", "method_definition",
    "generator_function_declaration", "generator_function", "function_signature",
})
_DECLARATION_NAMES = frozenset({
    "function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration",
    "interface_declaration", "type_alias_declaration", "enum_declaration", "type_parameter",
    "function_expression", "function", "generator_function", "class", "function_signature",
    "internal_module", "module", "method_definition", "method_signature", "abstract_method_signature",
    "public_field_definition", "property_signature", "enum_assignment",
})
_SKIPPED = frozenset({"comment", "string", "template_string", "regex", "import_statement"})


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _segment(node: Node) -> Segment:
    return (_text(node), node.start_point[0] + 1, node.start_point[1] + 1)


def _specifier(node: Node) -> str:
    source = node.child_by_field_name("source")
    return _text(source).strip("'\"`") if source is not None else ""


def pattern_names(node: Node | None) -> Iterator[str]:
    """Names a binding pattern binds (identifier, object and array patterns, defaults, rest)."""
    if node is None:
        return
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        yield _text(node)
    elif node.type == "pair_pattern":
        yield from pattern_names(node.child_by_field_name("value"))
    elif node.type in ("assignment_pattern", "object_assignment_pattern"):
        yield from pattern_names(node.child_by_field_name("left"))
    elif node.type in ("object_pattern", "array_pattern", "rest_pattern"):
        for child in node.named_children:
            yield from pattern_names(child)


def _parameter_names(node: Node) -> set[str]:
    names: set[str] = set()
    parameters = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
    if parameters is None:
        return names
    if parameters.type == "identifier":
        return {_text(parameters)}
    for parameter in parameters.named_children:
        names.update(pattern_names(parameter.child_by_field_name("pattern") or parameter))
    return names


def _type_parameter_names(node: Node) -> set[str]:
    parameters = node.child_by_field_name("type_parameters")
    if parameters is None:
        return set()
    return {_text(parameter.child_by_field_name("name")) for parameter in parameters.named_children
            if parameter.child_by_field_name("name") is not None}


def _body_names(node: Node) -> set[str]:
    """Names declared anywhere in a function body, nested functions excluded."""
    names: set[str] = set()

    def visit(current: Node) -> None:
        kind = current.type
        if kind == "variable_declarator":
            names.update(pattern_names(current.child_by_field_name("name")))
        elif kind in ("function_declaration", "generator_function_declaration", "class_declaration",
                      "abstract_class_declaration", "enum_declaration", "interface_declaration",
                      "type_alias_declaration"):
            name = current.child_by_field_name("name")
            if name is not None:
                names.add(_text(name))
        elif kind == "catch_clause":
            names.update(pattern_names(current.child_by_field_name("parameter")))
        elif kind in ("for_in_statement",):
            names.update(pattern_names(current.child_by_field_name("left")))
        elif kind == "import_statement":
            return
        if kind in _FUNCTION_NODES or kind in ("class_declaration", "class", "abstract_class_declaration"):
            return
        for child in current.named_children:
            visit(child)

    body = node.child_by_field_name("body")
    if body is not None:
        visit(body)
    return names


def module_bindings(root: Node) -> list[BindingPayload]:
    """Import bindings (in the file's scope) and export bindings (seen by importers)."""
    bindings: list[BindingPayload] = []
    for node in root.named_children:
        if node.type == "import_statement":
            module = _specifier(node)
            require = next((child for child in node.named_children if child.type == "import_require_clause"), None)
            if require is not None:
                local = next((child for child in require.named_children if child.type == "identifier"), None)
                source = require.child_by_field_name("source")
                if local is not None and source is not None:
                    bindings.append({"local": _text(local), "module": _text(source).strip("'\"`"), "name": "",
                                     "export": False, "scope": True})
                continue
            clause = next((child for child in node.named_children if child.type == "import_clause"), None)
            if clause is None or not module:
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    bindings.append({"local": _text(part), "module": module, "name": "default",
                                     "export": False, "scope": True})
                elif part.type == "namespace_import":
                    local = next((child for child in part.named_children if child.type == "identifier"), None)
                    if local is not None:
                        bindings.append({"local": _text(local), "module": module, "name": "",
                                         "export": False, "scope": True})
                elif part.type == "named_imports":
                    for specifier in part.named_children:
                        if specifier.type != "import_specifier":
                            continue
                        name = _text(specifier.child_by_field_name("name"))
                        alias = _text(specifier.child_by_field_name("alias"))
                        if name:
                            bindings.append({"local": alias or name, "module": module, "name": name,
                                             "export": False, "scope": True})
        elif node.type == "export_statement":
            bindings.extend(_export_bindings(node))
    return bindings


def _export_bindings(node: Node) -> list[BindingPayload]:
    module = _specifier(node)
    found: list[BindingPayload] = []
    clause = next((child for child in node.named_children if child.type == "export_clause"), None)
    if clause is not None:
        for specifier in clause.named_children:
            if specifier.type != "export_specifier":
                continue
            name = _text(specifier.child_by_field_name("name"))
            alias = _text(specifier.child_by_field_name("alias"))
            if name and (alias or module):
                # module "" is this file: export { k as K } renames a name of the file.
                found.append({"local": alias or name, "module": module, "name": name, "export": True, "scope": False})
        return found
    namespace = next((child for child in node.named_children if child.type == "namespace_export"), None)
    if namespace is not None and module:
        local = next((child for child in namespace.named_children if child.type in ("identifier", "string")), None)
        if local is not None:
            found.append({"local": _text(local).strip("'\""), "module": module, "name": "",
                          "export": True, "scope": False})
        return found
    if module and any(child.type == "*" for child in node.children):
        return [{"local": "*", "module": module, "name": "*", "export": True, "scope": False}]
    if any(child.type == "default" for child in node.children):
        value = node.child_by_field_name("value")
        declaration = node.child_by_field_name("declaration")
        named = value if value is not None and value.type == "identifier" else (
            declaration.child_by_field_name("name") if declaration is not None else None)
        if named is not None and _text(named):
            found.append({"local": "default", "module": "", "name": _text(named), "export": True, "scope": False})
    return found


def module_names(root: Node) -> frozenset[str]:
    """Names declared at the top of the file (exported or not)."""
    names: set[str] = set()
    for node in root.named_children:
        target = node.child_by_field_name("declaration") if node.type == "export_statement" else node
        if target is None:
            continue
        if target.type in ("lexical_declaration", "variable_declaration"):
            for declarator in target.named_children:
                if declarator.type == "variable_declarator":
                    names.update(pattern_names(declarator.child_by_field_name("name")))
        else:
            name = target.child_by_field_name("name")
            if name is not None and target.type in _DECLARATION_NAMES | {"ambient_declaration"}:
                names.add(_text(name))
    return frozenset(names)


def _type_path(node: Node | None) -> tuple[str, ...] | None:
    """A type or constructor written as a name or a qualified name; None for anything else."""
    if node is None:
        return None
    if node.type == "type_annotation":
        inner = node.named_children[0] if node.named_children else None
        return _type_path(inner)
    if node.type in ("type_identifier", "identifier"):
        return (_text(node),)
    if node.type in ("nested_type_identifier", "member_expression", "nested_identifier"):
        owner = node.child_by_field_name("module") or node.child_by_field_name("object") or (
            node.named_children[0] if node.named_children else None)
        name = node.child_by_field_name("name") or node.child_by_field_name("property") or (
            node.named_children[-1] if node.named_children else None)
        head = _type_path(owner)
        return (*head, _text(name)) if head is not None and name is not None else None
    return None


def _local_types(node: Node) -> dict[str, tuple[str, ...] | None]:
    """The class of each parameter and variable of a function when it is in sight:
    an annotation or `new C()`. A name bound in two ways has none."""
    found: dict[str, set[tuple[str, ...] | None]] = {}
    parameters = node.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters is not None else []:
        pattern = parameter.child_by_field_name("pattern")
        if pattern is not None and pattern.type == "identifier":
            found.setdefault(_text(pattern), set()).add(_type_path(parameter.child_by_field_name("type")))

    def visit(current: Node) -> None:
        if current.type == "variable_declarator":
            name = current.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                written = _type_path(current.child_by_field_name("type"))
                value = current.child_by_field_name("value")
                if written is None and value is not None and value.type == "new_expression":
                    written = _type_path(value.child_by_field_name("constructor"))
                found.setdefault(_text(name), set()).add(written)
        if current.type in _FUNCTION_NODES:
            return
        for child in current.named_children:
            visit(child)

    body = node.child_by_field_name("body")
    if body is not None:
        visit(body)
    return {name: next(iter(types)) if len(types) == 1 else None for name, types in found.items()}


_TYPE_NODES = frozenset({"type_identifier", "nested_type_identifier"})


def _use_kind(node: Node) -> str:
    """How a name or member chain is used: call (also `new C()`), extends (extends and
    implements clauses), type; "" otherwise."""
    parent = node.parent
    if parent is None:
        return ""
    if parent.type == "call_expression" and parent.child_by_field_name("function") == node:
        return "call"
    if parent.type == "new_expression" and parent.child_by_field_name("constructor") == node:
        return "call"
    ancestor: Node | None = parent
    for _ in range(3):
        if ancestor is None:
            break
        if ancestor.type in ("extends_clause", "implements_clause", "extends_type_clause"):
            return "extends"
        if ancestor.type in ("class_body", "statement_block", "object_type"):
            break
        ancestor = ancestor.parent
    if node.type in _TYPE_NODES or parent.type in ("type_query", "generic_type", "type_arguments"):
        return "type"
    return ""


def use_sites(root: Node, wanted: frozenset[str]) -> list[UsePayload]:
    """Use sites of names the file binds at module level (declarations and imports)."""
    uses: list[UsePayload] = []
    types_stack: list[dict[str, tuple[str, ...] | None]] = []
    class_stack: list[str] = []

    def local_type(name: str, scopes: tuple[frozenset[str], ...]) -> tuple[str, ...] | None:
        for types in reversed(types_stack):
            if name in types:
                return types[name]
        return None

    def method_call(node: Node, scopes: tuple[frozenset[str], ...]) -> None:
        """recv.m(...): a use of Class.m when the receiver's class is in sight (this, an
        annotation, `new C()`); module-level names and namespaces are paths, not calls."""
        function = node.child_by_field_name("function")
        if function is None or function.type != "member_expression":
            return
        receiver = function.child_by_field_name("object")
        member = function.child_by_field_name("property")
        if receiver is None or member is None or member.type != "property_identifier":
            return
        if receiver.type in ("string", "template_string", "array", "object", "number", "regex"):
            return
        head = receiver
        while head.type == "member_expression" and head.child_by_field_name("object") is not None:
            head = head.child_by_field_name("object")
        shadowed = any(_text(head) in scope for scope in scopes)
        if head.type == "identifier" and not shadowed and _text(head) in wanted:
            return
        written: tuple[str, ...] | None = None
        if receiver.type == "this" and class_stack and class_stack[-1]:
            written = (class_stack[-1],)
        elif receiver.type == "identifier" and shadowed:
            written = local_type(_text(receiver), scopes)
        start = [receiver.start_point[0] + 1, receiver.start_point[1] + 1]
        path = list(written) if written is not None else []
        uses.append({"path": path, "at": [start for _ in path] + [[member.start_point[0] + 1, member.start_point[1] + 1]],
                     "member": _text(member), "kind": "call"})

    def add(
        segments: list[Segment], scopes: tuple[frozenset[str], ...], module: str = "", kind: str = "",
    ) -> None:
        if not module and (segments[0][0] not in wanted or any(segments[0][0] in scope for scope in scopes)):
            return
        use: UsePayload = {"path": [name for name, _line, _col in segments],
                           "at": [[line, col] for _name, line, col in segments]}
        if module:
            use["module"] = module
        if kind:
            use["kind"] = kind
        uses.append(use)

    def member_chain(node: Node) -> list[Segment] | None:
        if node.type == "identifier":
            return [_segment(node)]
        if node.type in ("member_expression", "nested_identifier", "nested_type_identifier"):
            owner = node.child_by_field_name("object") or node.child_by_field_name("module") or (
                node.named_children[0] if node.named_children else None)
            name = node.child_by_field_name("property") or node.child_by_field_name("name") or (
                node.named_children[-1] if node.named_children else None)
            head = member_chain(owner) if owner is not None else None
            if head is None or name is None or name.type not in (
                    "property_identifier", "type_identifier", "identifier"):
                return None
            return [*head, _segment(name)]
        return None

    def visit_import(node: Node) -> None:
        module = _specifier(node)
        clause = next((child for child in node.named_children if child.type == "import_clause"), None)
        if clause is None or not module:
            return
        for part in clause.named_children:
            if part.type == "identifier":
                add([("default", *_segment(part)[1:])], (), module, "import")
            elif part.type == "named_imports":
                for specifier in part.named_children:
                    name = specifier.child_by_field_name("name") if specifier.type == "import_specifier" else None
                    if name is not None:
                        add([_segment(name)], (), module, "import")

    def visit_pattern(node: Node, scopes: tuple[frozenset[str], ...]) -> None:
        """Default values and computed keys of a pattern hold uses; its names do not."""
        if node.type in ("identifier", "shorthand_property_identifier_pattern"):
            return
        if node.type in ("assignment_pattern", "object_assignment_pattern"):
            visit_pattern(node.child_by_field_name("left"), scopes)
            right = node.child_by_field_name("right")
            if right is not None:
                visit(right, scopes)
            return
        if node.type == "pair_pattern":
            key = node.child_by_field_name("key")
            if key is not None and key.type == "computed_property_name":
                visit(key, scopes)
            value = node.child_by_field_name("value")
            if value is not None:
                visit_pattern(value, scopes)
            return
        for child in node.named_children:
            visit_pattern(child, scopes)

    def visit(node: Node | None, scopes: tuple[frozenset[str], ...]) -> None:
        if node is None:
            return
        kind = node.type
        if kind in _SKIPPED:
            if kind == "import_statement":
                visit_import(node)
            elif kind == "template_string":
                for child in node.named_children:
                    if child.type == "template_substitution":
                        visit(child, scopes)
            return
        if kind == "export_statement":
            module = _specifier(node)
            clause = next((child for child in node.named_children if child.type == "export_clause"), None)
            if clause is not None:
                for specifier in clause.named_children:
                    name = specifier.child_by_field_name("name") if specifier.type == "export_specifier" else None
                    if name is not None:
                        add([_segment(name)], scopes, module, "re-export")
                return
            if module:
                return
        if kind in ("member_expression", "nested_type_identifier", "nested_identifier"):
            chain = member_chain(node)
            if chain is not None:
                add(chain, scopes, kind=_use_kind(node))
                return
        if kind in ("identifier", "type_identifier", "shorthand_property_identifier"):
            parent = node.parent
            if parent is not None and parent.type in _DECLARATION_NAMES and parent.child_by_field_name("name") == node:
                return
            if parent is not None and parent.type in ("labeled_statement", "break_statement", "continue_statement"):
                return
            add([_segment(node)], scopes, kind=_use_kind(node))
            return
        if kind == "variable_declarator":
            visit_pattern(node.child_by_field_name("name"), scopes)
            visit(node.child_by_field_name("type"), scopes)
            visit(node.child_by_field_name("value"), scopes)
            return
        if kind in ("required_parameter", "optional_parameter"):
            pattern = node.child_by_field_name("pattern")
            for child in node.named_children:
                if child == pattern:
                    visit_pattern(child, scopes)
                else:
                    visit(child, scopes)
            return
        if kind == "catch_clause":
            visit(node.child_by_field_name("type"), scopes)
            visit(node.child_by_field_name("body"), scopes)
            return
        if kind == "call_expression":
            method_call(node, scopes)
        inner = scopes
        pushed_types = pushed_class = False
        if kind in _FUNCTION_NODES:
            types_stack.append(_local_types(node))
            pushed_types = True
            parameters = node.child_by_field_name("parameter")
            if parameters is not None and parameters.type == "identifier":
                inner = (*scopes, frozenset({_text(parameters)} | _body_names(node)))
            else:
                inner = (*scopes, frozenset(_parameter_names(node) | _type_parameter_names(node) | _body_names(node)))
            if parameters is not None and parameters.type == "identifier":
                visit(node.child_by_field_name("body"), inner)
                types_stack.pop()
                return
        elif kind in ("class_declaration", "class", "abstract_class_declaration", "interface_declaration",
                      "type_alias_declaration"):
            names = _type_parameter_names(node)
            if names:
                inner = (*scopes, frozenset(names))
            if kind != "interface_declaration" and kind != "type_alias_declaration":
                # `this` names a class declared at module level; other classes are not in the table.
                name = node.child_by_field_name("name")
                class_stack.append(_text(name) if name is not None and not scopes and kind != "class" else "")
                pushed_class = True
        elif kind in ("statement_block", "for_statement", "for_in_statement") and scopes:
            pass
        for child in node.named_children:
            visit(child, inner)
        if pushed_types:
            types_stack.pop()
        if pushed_class:
            class_stack.pop()

    for child in root.named_children:
        visit(child, ())
    return uses


__all__ = ["module_bindings", "module_names", "pattern_names", "use_sites"]
