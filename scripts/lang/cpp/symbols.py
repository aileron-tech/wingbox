"""Import bindings and use sites of a C or C++ file, for the symbol index (lang/symbols.py).

An `#include` puts every name of the included file, and of the files that one includes, into the
scope of the including file: one glob binding per include. A use site is a name written in the file
that is not a local of the function around it: the function of a call, a type, a base class, a name
with a scope (`Mode::Lit`, `Painter::create`), a macro or a constant in an expression. A method
called on an object (`painter.paint()`) is a use whose receiver type is not in sight: it is kept
as unresolved, never guessed.

Namespaces are not followed: `studio::render::blend` is looked up as `blend`, and as the member
`blend` of `render` for the case that `render` is a class or an enum.

The names are found with one query of the grammar (the tree is walked by tree-sitter, not here):
a build reads every name of every file.
"""
from __future__ import annotations

from bisect import bisect_right

import tree_sitter_cpp as _tscpp
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Query, QueryCursor

from ..interface import BindingPayload, UsePayload

_CPP_LANGUAGE = _TreeSitterLanguage(_tscpp.language())
_NAMES = Query(_CPP_LANGUAGE, """
(identifier) @name
(type_identifier) @type
(qualified_identifier) @scoped
(function_definition) @function

; A name that is being declared is no use.
(_ declarator: (identifier) @declared)
(reference_declarator (identifier) @declared)
(variadic_declarator (identifier) @declared)
(structured_binding_declarator (identifier) @declared)
(enumerator name: (identifier) @declared)
(preproc_def name: (identifier) @declared)
(preproc_function_def name: (identifier) @declared)
(preproc_params (identifier) @declared)
(namespace_alias_definition name: (_) @declared)
(class_specifier name: (type_identifier) @declared body: (_))
(struct_specifier name: (type_identifier) @declared body: (_))
(union_specifier name: (type_identifier) @declared body: (_))
(enum_specifier name: (type_identifier) @declared body: (_))
(alias_declaration name: (type_identifier) @declared)
(type_definition declarator: (type_identifier) @declared)
(type_parameter_declaration (type_identifier) @declared)
(optional_type_parameter_declaration name: (type_identifier) @declared)

; The parts of a name with a scope are read with the whole name.
(qualified_identifier name: (identifier) @inside)
(qualified_identifier name: (type_identifier) @inside)
(qualified_identifier name: (qualified_identifier) @inside)
(qualified_identifier name: (template_function name: (identifier) @inside))
(qualified_identifier name: (template_type name: (type_identifier) @inside))
(qualified_identifier scope: (template_type name: (type_identifier) @inside))

; `void Painter::paint()` outside its class declares paint and names Painter.
(function_declarator declarator: (qualified_identifier) @definition)

(call_expression function: (identifier) @call)
(call_expression function: (template_function name: (identifier) @call))
(call_expression function: (qualified_identifier) @call)
(call_expression function: (field_expression field: (field_identifier) @method))
(call_expression function: (field_expression field: (template_method name: (field_identifier) @method)))

(base_class_clause (type_identifier) @base)
(base_class_clause (qualified_identifier) @base)
(base_class_clause (template_type name: (type_identifier) @base))
""")
_SEGMENT_NAMES = frozenset({"identifier", "type_identifier", "namespace_identifier", "field_identifier"})
_TEMPLATES = frozenset({"template_type", "template_function", "template_method"})


def glob_bindings(includes: list[str]) -> list[BindingPayload]:
    """One glob binding per included file: its names are in the scope of this file and of its includers."""
    return [
        {"local": "*", "module": name, "name": "*", "export": True, "scope": True}  # type: ignore[typeddict-unknown-key]
        for name in includes
    ]


def _position(node: Node) -> list[int]:
    return [node.start_point[0] + 1, node.start_point[1] + 1]


def _text(node: Node) -> str:
    text = node.text
    return text.decode("utf-8", "replace") if text is not None else ""


def _segments(node: Node) -> list[Node]:
    """The name nodes of `a::b<T>::c`, in their order."""
    names: list[Node] = []
    current: Node | None = node
    while current is not None:
        kind = current.type
        if kind == "qualified_identifier":
            scope = current.child_by_field_name("scope")
            if scope is not None:
                names.extend(_segments(scope))
            current = current.child_by_field_name("name")
        elif kind in _TEMPLATES:
            current = current.child_by_field_name("name")
        elif kind == "dependent_name":
            current = current.named_children[0] if current.named_children else None
        else:
            if kind in _SEGMENT_NAMES:
                names.append(current)
            current = None
    return names


def use_sites(root: Node) -> list[UsePayload]:
    """Every use site of the file, in the order of the file."""
    captures = QueryCursor(_NAMES).captures(root)

    def ids(name: str) -> set[int]:
        return {node.id for node in captures.get(name, ())}

    declared = captures.get("declared", [])
    declared_ids = {node.id for node in declared}
    inside = ids("inside")
    definitions = ids("definition")
    calls = ids("call")
    bases = ids("base")

    # The locals of a function: every name declared between its first and its last byte (parameters,
    # variables, the variables of a lambda), looked up through the outermost function around a position.
    functions = sorted((node.start_byte, node.end_byte) for node in captures.get("function", ()))
    spans: list[tuple[int, int]] = []
    for start, end in functions:
        if not spans or start >= spans[-1][1]:
            spans.append((start, end))
    starts = [start for start, _end in spans]
    local_names: list[set[str]] = [set() for _span in spans]

    def span_of(position: int) -> int:
        index = bisect_right(starts, position) - 1
        return index if index >= 0 and position < spans[index][1] else -1

    for node in declared:
        if node.type == "identifier":
            index = span_of(node.start_byte)
            if index >= 0:
                local_names[index].add(_text(node))

    found: list[UsePayload] = []

    def plain(node: Node, kind: str, check_local: bool) -> None:
        name = _text(node)
        if not name:
            return
        if check_local:
            index = span_of(node.start_byte)
            if index >= 0 and name in local_names[index]:
                return
        use: UsePayload = {"path": [name], "at": [_position(node)]}
        if kind:
            use["kind"] = kind
        found.append(use)

    for node in captures.get("name", ()):
        if node.id in declared_ids or node.id in inside:
            continue
        plain(node, "call" if node.id in calls else "", True)
    for node in captures.get("type", ()):
        if node.id in declared_ids or node.id in inside:
            continue
        plain(node, "extends" if node.id in bases else "type", False)
    for node in captures.get("scoped", ()):
        if node.id in inside:
            continue   # the inner part of a longer name
        names = _segments(node)
        if not names:
            continue
        last = names[-1]
        if node.id in definitions:
            # The name is being defined; its scope is a reference to the class.
            if len(names) > 1:
                plain(names[-2], "", False)
            continue
        kind = "call" if node.id in calls else "extends" if node.id in bases else "type" if last.type == "type_identifier" else ""
        plain(last, kind, False)
        if len(names) > 1:
            scope = names[-2]
            plain(scope, "", False)
            found.append({"path": [_text(scope)], "member": _text(last), "at": [_position(scope), _position(last)]})
    for node in captures.get("method", ()):
        found.append({"path": [], "member": _text(node), "at": [_position(node)]})
    found.sort(key=lambda use: (use["at"][-1], use.get("member", ""), use["path"]))
    return found
