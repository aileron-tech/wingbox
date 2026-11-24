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

(parameter_declaration type: (type_identifier) @vexing declarator: (abstract_function_declarator))

; The classes of the file, to know which class a position is in.
(class_specifier name: (_) @classname body: (field_declaration_list) @classbody)
(struct_specifier name: (_) @classname body: (field_declaration_list) @classbody)

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


def matching_brace(source: bytes, open_at: int) -> int:
    """The position after the brace that closes the one at open_at, by the text: braces in comments, strings
    and character literals do not count. The end of the text when the brace is never closed."""
    depth, i, n = 0, open_at, len(source)
    while i < n:
        c = source[i:i + 1]
        two = source[i:i + 2]
        if two == b"//":
            j = source.find(b"\n", i)
            i = n if j < 0 else j
        elif two == b"/*":
            j = source.find(b"*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif c in (b'"', b"'"):
            j = i + 1
            while j < n and source[j:j + 1] != c and source[j:j + 1] != b"\n":
                j += 2 if source[j:j + 1] == b"\\" else 1
            i = j
        elif c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _macro_class(node: Node) -> str:
    """The name of a class the grammar read as a function (`class MACRO Name { ... }`), else ""."""
    type_node = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    if type_node is None or declarator is None or node.child_by_field_name("body") is None:
        return ""
    if type_node.type not in ("class_specifier", "struct_specifier", "union_specifier") or type_node.child_by_field_name("body") is not None:
        return ""
    return _text(declarator) if declarator.type in ("identifier", "type_identifier") else ""


def use_sites(root: Node) -> list[UsePayload]:
    """Every use site of the file, in the order of the file."""
    captures = QueryCursor(_NAMES).captures(root)
    source = root.text or b""

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
    # A class written with a macro before its name is, to the grammar, a function (see macro_class_name
    # of the parser): it is a class here, not a function with locals.
    macro_classes = {node.id: _macro_class(node) for node in captures.get("function", ())}
    functions = sorted((node.start_byte, node.end_byte) for node in captures.get("function", ()) if not macro_classes[node.id])
    spans: list[tuple[int, int]] = []
    for start, end in functions:
        if not spans or start >= spans[-1][1]:
            spans.append((start, end))
    starts = [start for start, _end in spans]
    local_names: list[set[str]] = [set() for _span in spans]

    def span_of(position: int) -> int:
        index = bisect_right(starts, position) - 1
        return index if index >= 0 and position < spans[index][1] else -1

    # The name of a function is declared inside the span of its own definition and is no local of it:
    # a function that calls itself, or hands itself on as a callback, uses the function.
    own_names: set[int] = set()
    for function in captures.get("function", ()):
        declarator = function.child_by_field_name("declarator")
        if macro_classes[function.id] and declarator is not None:
            own_names.add(declarator.id)
            continue
        while declarator is not None and declarator.type != "function_declarator":
            declarator = declarator.child_by_field_name("declarator")
        name = declarator.child_by_field_name("declarator") if declarator is not None else None
        if name is not None:
            own_names.add(name.id)
    # `label:` followed by a preprocessor line and a call: the grammar reads the directive as an error and
    # `NAME call(args);` as the declaration of a function. The name is the function of a call.
    misread: set[int] = set()
    for node in declared:
        declarator = node.parent
        declaration = declarator.parent if declarator is not None else None
        before = declaration.prev_sibling if declaration is not None else None
        if declarator is not None and declarator.type == "function_declarator" and declaration is not None \
                and declaration.type == "declaration" and before is not None and before.type == "ERROR" \
                and _text(before).lstrip().startswith("#"):
            misread.add(node.id)
    declared_ids -= misread
    calls |= misread
    for node in declared:
        if node.type == "identifier" and node.id not in own_names and node.id not in misread:
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
    # The class a position is in: the body of a class, or the definition of `Class::method` outside it.
    # Inside it a call without an object, `helper()`, and `this->helper()` are calls of a member of the class
    # (or of one of its bases): they are recorded as `Class.helper`, which resolves when the class has it.
    class_spans: list[tuple[int, int, str]] = []
    for body in captures.get("classbody", ()):
        owner = body.parent.child_by_field_name("name") if body.parent is not None else None
        names = _segments(owner) if owner is not None else []
        if names:
            class_spans.append((body.start_byte, body.end_byte, _text(names[-1])))
    for function in captures.get("function", ()):
        if macro_classes[function.id]:
            # The grammar may end such a class early (at a class with a macro nested in it): its extent is
            # read from the braces of the text.
            body = function.child_by_field_name("body")
            class_spans.append((function.start_byte, matching_brace(source, body.start_byte), macro_classes[function.id]))
            continue
        declarator = function.child_by_field_name("declarator")
        while declarator is not None and declarator.type != "function_declarator":
            # `Status& Status::operator=(...)`: the declarator of a reference or a pointer stands in no field.
            declarator = declarator.child_by_field_name("declarator") or next(
                (child for child in declarator.named_children if child.type.endswith("declarator")), None)
        scoped = declarator.child_by_field_name("declarator") if declarator is not None else None
        # `Status::assign` and `Status::operator=`: the class is the scope of the name.
        scope = scoped.child_by_field_name("scope") if scoped is not None and scoped.type == "qualified_identifier" else None
        names = _segments(scope) if scope is not None else []
        if names:
            class_spans.append((function.start_byte, function.end_byte, _text(names[-1])))
        elif scoped is not None and scoped.type == "identifier" and _text(scoped).upper() == _text(scoped):
            # A body written after a macro call, `TEST_F(Fixture, Name) { ... }`: the frameworks that do
            # this make it a method of a class derived from the first argument. A call inside it is tried
            # as a member of that class; it stays a plain call when the class has no such member.
            parameters = declarator.child_by_field_name("parameters") if declarator is not None else None
            first = parameters.named_children[0] if parameters is not None and parameters.named_children else None
            if first is not None and _text(first).isidentifier():
                class_spans.append((function.start_byte, function.end_byte, _text(first)))
    class_spans.sort()
    class_starts = [start for start, _end, _name in class_spans]

    def class_at(position: int) -> str:
        index = bisect_right(class_starts, position) - 1
        while index >= 0:
            start, end, name = class_spans[index]
            if position < end:
                return name
            index -= 1
        return ""

    if class_spans:
        # `Model model(Options());` reads as the declaration of a function `model` with a parameter of the
        # type `Options()`: the name before the brackets is the function of a call.
        for node in [*captures.get("call", ()), *captures.get("vexing", ())]:
            if node.type not in ("identifier", "type_identifier"):
                continue
            owner = class_at(node.start_byte)
            if owner:
                found.append({"path": [owner], "member": _text(node), "at": [_position(node), _position(node)]})
    for node in captures.get("method", ()):
        expression = node.parent
        if expression is not None and expression.type != "field_expression":
            expression = expression.parent
        receiver = expression.child_by_field_name("argument") if expression is not None and expression.type == "field_expression" else None
        owner = class_at(node.start_byte) if receiver is not None and receiver.type == "this" and class_spans else ""
        if owner:
            found.append({"path": [owner], "member": _text(node), "at": [_position(node), _position(node)]})
        else:
            found.append({"path": [], "member": _text(node), "at": [_position(node)]})
    found.sort(key=lambda use: (use["at"][-1], use.get("member", ""), use["path"]))
    return found
