"""TypeScript and JavaScript symbol table of one file: import and export bindings, use sites.

A use site is a name (`X`, `<Comp />`, `{ x }`), a member chain whose first name is
a module-level binding (`ns.S`, `Enum.A`), a qualified type (`ns.Type`), or a
name in an import or export statement. Parameters, variables, catch parameters,
type parameters and declarations inside a function shadow a module-level name
of the same spelling and are not use sites.
"""
from __future__ import annotations

from typing import Iterator

from tree_sitter import Language, Node, Query, QueryCursor

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
    todo = [node]
    while todo:
        current = todo.pop()
        if current is None:
            continue
        if current.type in ("identifier", "shorthand_property_identifier_pattern"):
            yield _text(current)
        elif current.type == "pair_pattern":
            todo.append(current.child_by_field_name("value"))
        elif current.type in ("assignment_pattern", "object_assignment_pattern"):
            todo.append(current.child_by_field_name("left"))
        elif current.type in ("object_pattern", "array_pattern", "rest_pattern"):
            todo.extend(reversed(current.named_children))


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
    while node is not None and node.type == "type_annotation":
        node = node.named_children[0] if node.named_children else None
    names: list[str] = []  # the last name first
    while node is not None and node.type in ("nested_type_identifier", "member_expression", "nested_identifier"):
        name = node.child_by_field_name("name") or node.child_by_field_name("property") or (
            node.named_children[-1] if node.named_children else None)
        if name is None:
            return None
        names.append(_text(name))
        node = node.child_by_field_name("module") or node.child_by_field_name("object") or (
            node.named_children[0] if node.named_children else None)
    if node is None or node.type not in ("type_identifier", "identifier"):
        return None
    return (_text(node), *reversed(names))


def declared_type(declarator: Node) -> tuple[str, ...] | None:
    """The class a declared variable holds when it is in sight: its annotation, or `new C()`."""
    written = _type_path(declarator.child_by_field_name("type"))
    value = declarator.child_by_field_name("value")
    if written is None and value is not None and value.type == "new_expression":
        written = _type_path(value.child_by_field_name("constructor"))
    return written


def _local_types(node: Node) -> dict[str, tuple[str, ...] | None]:
    """The class of each parameter and variable of a function when it is in sight:
    an annotation or `new C()`. A name bound in two ways has none."""
    found: dict[str, set[tuple[str, ...] | None]] = {}
    parameters = node.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters is not None else []:
        pattern = parameter.child_by_field_name("pattern")
        if pattern is not None and pattern.type == "identifier":
            found.setdefault(_text(pattern), set()).add(_type_path(parameter.child_by_field_name("type")))

    body = node.child_by_field_name("body")
    todo = [body] if body is not None else []
    while todo:
        current = todo.pop()
        if current.type == "variable_declarator":
            name = current.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                found.setdefault(_text(name), set()).add(declared_type(current))
        if current.type not in _FUNCTION_NODES:
            todo.extend(current.named_children)
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


_BLOCK_DECLARATIONS = frozenset({
    "function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration",
    "enum_declaration", "interface_declaration", "type_alias_declaration",
})
_MEMBER_CHAINS = frozenset({"member_expression", "nested_type_identifier", "nested_identifier"})


def _function_names(node: Node) -> set[str]:
    """Names a function binds for its whole body: parameters, type parameters, `var` declarations."""
    parameters = node.child_by_field_name("parameter")
    names = {_text(parameters)} if parameters is not None and parameters.type == "identifier" else \
        _parameter_names(node) | _type_parameter_names(node)

    body = node.child_by_field_name("body")
    todo = [body] if body is not None else []
    while todo:
        current = todo.pop()
        if current.type == "variable_declaration":
            for declarator in current.named_children:
                if declarator.type == "variable_declarator":
                    names.update(pattern_names(declarator.child_by_field_name("name")))
        if current.type not in _FUNCTION_NODES:
            todo.extend(current.named_children)
    return names


def _block_names(node: Node) -> set[str]:
    """Names a block binds for itself only: let, const, classes, functions and types declared in it;
    the let or const of a for loop; the parameter of a catch clause."""
    names: set[str] = set()
    if node.type == "catch_clause":
        names.update(pattern_names(node.child_by_field_name("parameter")))
        return names
    if node.type in ("for_statement", "for_in_statement"):
        for field in ("initializer", "left"):
            declaration = node.child_by_field_name(field)
            if declaration is not None and declaration.type == "lexical_declaration":
                for declarator in declaration.named_children:
                    if declarator.type == "variable_declarator":
                        names.update(pattern_names(declarator.child_by_field_name("name")))
            elif declaration is not None and field == "left" and node.child_by_field_name("kind") is not None:
                names.update(pattern_names(declaration))
        return names
    for child in node.named_children:
        target = child.child_by_field_name("declaration") if child.type == "export_statement" else child
        if target is None:
            continue
        if target.type == "lexical_declaration":
            for declarator in target.named_children:
                if declarator.type == "variable_declarator":
                    names.update(pattern_names(declarator.child_by_field_name("name")))
        elif target.type in _BLOCK_DECLARATIONS:
            name = target.child_by_field_name("name")
            if name is not None:
                names.add(_text(name))
    return names


_SCOPE_KINDS = {
    **{kind: "function" for kind in _FUNCTION_NODES},
    "statement_block": "block", "for_statement": "block", "for_in_statement": "block",
    "catch_clause": "block", "class_body": "block", "switch_body": "block",
}
_TYPE_PARAMETER_OWNERS = frozenset({
    "class_declaration", "class", "abstract_class_declaration", "interface_declaration", "type_alias_declaration",
})
_PATTERN_PARTS = frozenset({
    "object_pattern", "array_pattern", "pair_pattern", "assignment_pattern", "object_assignment_pattern",
    "rest_pattern",
})
_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_LITERAL_RECEIVERS = frozenset({"string", "template_string", "array", "object", "number", "regex"})
_NAME_QUERY = "[(identifier) (type_identifier) (shorthand_property_identifier)] @name"
_CALL_QUERY = (
    "(call_expression function: (member_expression object: (_) @receiver property: (property_identifier) @method))"
)
_QUERIES: dict[int, tuple[Query, Query]] = {}


def _queries(language: Language) -> tuple[Query, Query]:
    key = id(language)
    if key not in _QUERIES:
        _QUERIES[key] = (Query(language, _NAME_QUERY), Query(language, _CALL_QUERY))
    return _QUERIES[key]


class _ScopeNames:
    """Names bound by scopes (functions, blocks), read only for scopes around a module-level name."""

    def __init__(self) -> None:
        self._names: dict[int, frozenset[str]] = {}
        self._types: dict[int, dict[str, tuple[str, ...] | None]] = {}
        # Per node already climbed through: the scopes at and above it, and whether what lies below it is
        # outside the import and export statements. The names of one long expression share their way up.
        self._around: dict[int, tuple[tuple[Node, str], ...]] = {}
        self._outside: dict[int, bool] = {}

    def names(self, node: Node, kind: str) -> frozenset[str]:
        if node.id not in self._names:
            if kind == "function":
                found = _function_names(node) if node.type not in _TYPE_PARAMETER_OWNERS else _type_parameter_names(node)
            else:
                found = _block_names(node)
            self._names[node.id] = frozenset(found)
        return self._names[node.id]

    def scopes_of(self, node: Node) -> tuple[tuple[Node, str], ...]:
        """The scopes around node, innermost first."""
        around = self._around
        climbed: list[Node] = []
        current = node.parent
        while current is not None and current.id not in around:
            climbed.append(current)
            current = current.parent
        found = around[current.id] if current is not None else ()
        for current in reversed(climbed):
            kind = _SCOPE_KINDS.get(current.type)
            if kind is None and current.type in _TYPE_PARAMETER_OWNERS and \
                    current.child_by_field_name("type_parameters") is not None:
                kind = "function"
            if kind is not None:
                found = ((current, kind), *found)
            around[current.id] = found
        return found

    def outside_statements(self, node: Node) -> bool:
        """False for a name inside an import statement or an export clause (read on their own)."""
        outside = self._outside
        climbed: list[int] = []
        current = node.parent
        answer = True
        while current is not None:
            known = outside.get(current.id)
            if known is not None:
                answer = known
                break
            if current.type in ("import_statement", "export_clause"):
                answer = False
                break
            if current.type in ("statement_block", "program"):
                break
            climbed.append(current.id)
            current = current.parent
        for seen in climbed:
            outside[seen] = answer
        return answer

    def is_bound(self, node: Node, name: str) -> bool:
        return any(name in self.names(scope, kind) for scope, kind in self.scopes_of(node))

    def local_type(self, node: Node, name: str) -> tuple[str, ...] | None:
        """The class of a local name at node when it is in sight (an annotation, `new C()`)."""
        for scope, kind in self.scopes_of(node):
            if kind != "function" or scope.type not in _FUNCTION_NODES:
                continue
            if scope.id not in self._types:
                self._types[scope.id] = _local_types(scope)
            if name in self._types[scope.id]:
                return self._types[scope.id][name]
        return None


def _is_binding(node: Node) -> bool:
    """True when node is a name a declaration binds: a variable, a parameter, a catch parameter."""
    current = node
    parent = current.parent
    while parent is not None and parent.type in _PATTERN_PARTS:
        if parent.type in ("assignment_pattern", "object_assignment_pattern") and \
                parent.child_by_field_name("left") != current:
            return False  # a default value is an expression
        if parent.type == "pair_pattern" and parent.child_by_field_name("value") != current:
            return False
        current, parent = parent, parent.parent
    if parent is None:
        return False
    field = {
        "variable_declarator": "name", "required_parameter": "pattern", "optional_parameter": "pattern",
        "catch_clause": "parameter", "for_in_statement": "left", "arrow_function": "parameter",
    }.get(parent.type)
    return field is not None and parent.child_by_field_name(field) == current


def _chain_from_head(node: Node) -> tuple[Node, list[Segment]] | None:
    """For a name heading a member chain (ns.S, a.b.C, ns.Type), the outermost chain node and its
    segments; (node, [name]) for a plain name; None when node is not the head of its chain."""
    parent = node.parent
    if parent is not None and parent.type in _MEMBER_CHAINS:
        owner = parent.child_by_field_name("object") or parent.child_by_field_name("module") or (
            parent.named_children[0] if parent.named_children else None)
        if owner != node:
            return None
    segments = [_segment(node)]
    top = node
    while top.parent is not None and top.parent.type in _MEMBER_CHAINS:
        chain = top.parent
        owner = chain.child_by_field_name("object") or chain.child_by_field_name("module") or (
            chain.named_children[0] if chain.named_children else None)
        if owner != top:
            break
        name = chain.child_by_field_name("property") or chain.child_by_field_name("name") or (
            chain.named_children[-1] if chain.named_children else None)
        if name is None or name.type not in ("property_identifier", "type_identifier", "identifier"):
            break
        segments.append(_segment(name))
        top = chain
    return top, segments


def _enclosing_class(node: Node) -> str:
    """The class `this` names at node: a class declared at module level; "" otherwise."""
    current = node.parent
    while current is not None:
        if current.type in _CLASS_NODES:
            holder = current.parent
            if current.type != "class" and holder is not None and (
                    holder.type == "program" or (holder.type == "export_statement" and holder.parent is not None
                                                 and holder.parent.type == "program")):
                return _text(current.child_by_field_name("name"))
            return ""
        current = current.parent
    return ""


def use_sites(
    root: Node,
    wanted: frozenset[str],
    language: Language | None = None,
    full: bool = True,
    namespaces: frozenset[str] = frozenset(),
) -> list[UsePayload]:
    """Use sites of names the file binds at module level (declarations and imports).

    A query finds the names and the method calls in C; only names the module binds get more work:
    their scopes (a parameter, variable, catch parameter or type parameter of the same name, or a
    let/const/class of an enclosing block, shadows them), their member chain, their kind.
    full=False keeps the uses that can name another file (a build needs no more): import and
    export statements and chains headed by a namespace import (namespaces).
    """
    uses: list[UsePayload] = []
    wanted_bytes = frozenset(name.encode("utf-8") for name in wanted)
    scopes = _ScopeNames()

    def add(segments: list[Segment], module: str = "", kind: str = "") -> None:
        use: UsePayload = {"path": [name for name, _line, _col in segments],
                           "at": [[line, col] for _name, line, col in segments]}
        if module:
            use["module"] = module
        if kind:
            use["kind"] = kind
        uses.append(use)

    for node in root.named_children:
        if node.type == "import_statement":
            module = _specifier(node)
            clause = next((child for child in node.named_children if child.type == "import_clause"), None)
            if clause is None or not module:
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    add([("default", *_segment(part)[1:])], module, "import")
                elif part.type == "named_imports":
                    for specifier in part.named_children:
                        name = specifier.child_by_field_name("name") if specifier.type == "import_specifier" else None
                        if name is not None:
                            add([_segment(name)], module, "import")
        elif node.type == "export_statement":
            module = _specifier(node)
            clause = next((child for child in node.named_children if child.type == "export_clause"), None)
            for specifier in clause.named_children if clause is not None else []:
                name = specifier.child_by_field_name("name") if specifier.type == "export_specifier" else None
                if name is not None and (module or (name.text in wanted_bytes and not scopes.is_bound(name, _text(name)))):
                    add([_segment(name)], module, "re-export")
    if not full:
        wanted_bytes = frozenset(name.encode("utf-8") for name in namespaces)
    if language is None or not wanted_bytes:
        return uses
    name_query, call_query = _queries(language)
    for node in QueryCursor(name_query).captures(root).get("name", []):
        if node.text not in wanted_bytes:
            continue
        parent = node.parent
        if parent is not None and parent.type in _DECLARATION_NAMES and parent.child_by_field_name("name") == node:
            continue
        if parent is not None and parent.type in ("labeled_statement", "break_statement", "continue_statement"):
            continue
        if _is_binding(node) or not scopes.outside_statements(node):
            continue
        name = _text(node)
        if scopes.is_bound(node, name):
            continue
        chain = _chain_from_head(node)
        if chain is not None and (full or len(chain[1]) > 1):
            top, segments = chain
            add(segments, kind=_use_kind(top))
    if not full:
        return uses
    for _pattern, match in QueryCursor(call_query).matches(root):
        receiver, member = match["receiver"][0], match["method"][0]
        if receiver.type in _LITERAL_RECEIVERS:
            continue
        head = receiver
        while head.type in _MEMBER_CHAINS:
            owner = head.child_by_field_name("object") or head.child_by_field_name("module")
            if owner is None:
                break
            head = owner
        if head.type == "identifier" and head.text in wanted_bytes and not scopes.is_bound(head, _text(head)):
            continue  # a module-level name or namespace: a path, not a method call
        written: tuple[str, ...] | None = None
        if receiver.type == "this":
            owner_class = _enclosing_class(receiver)
            written = (owner_class,) if owner_class else None
        elif receiver.type == "identifier":
            written = scopes.local_type(receiver, _text(receiver))
        start = [receiver.start_point[0] + 1, receiver.start_point[1] + 1]
        path = list(written) if written is not None else []
        uses.append({"path": path, "at": [start for _ in path] + [[member.start_point[0] + 1, member.start_point[1] + 1]],
                     "member": _text(member), "kind": "call"})
    return uses


__all__ = ["declared_type", "module_bindings", "module_names", "pattern_names", "use_sites"]
