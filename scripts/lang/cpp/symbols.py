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

import re
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
(call_expression function: (parenthesized_expression (field_expression field: (field_identifier) @method)))
(call_expression function: (field_expression field: (dependent_name (template_method name: (field_identifier) @method))))

(parameter_declaration type: (type_identifier) @vexing declarator: (abstract_function_declarator))

(function_declarator declarator: (field_identifier) @methodname)

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


# A comment that nothing closes runs to the end of the file, as it does for a compiler: read once, it
# cannot be read again from every `/*` inside it.
_BRACE_TOKENS = re.compile(rb"//[^\n]*|/\*.*?(?:\*/|\Z)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|[{}]", re.S)


def brace_pairs(source: bytes) -> dict[int, int]:
    """For every opening brace of the text, the position after the brace that closes it: braces in comments,
    strings and character literals do not count. One pass over the file."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for match in _BRACE_TOKENS.finditer(source):
        token = match.group(0)
        if token == b"{":
            stack.append(match.start())
        elif token == b"}" and stack:
            pairs[stack.pop()] = match.end()
    for position in stack:
        pairs[position] = len(source)
    return pairs


_TEXT_CLASS = re.compile(
    rb"(?<![A-Za-z0-9_])(?P<enum>enum[ \t]+)?(?:class|struct)[ \t\r\n]+(?:[A-Z_][A-Z0-9_]*[ \t\r\n]+)?"
    # The template arguments and the base classes are read in a window: a heading that never reaches its
    # brace costs the window, not the rest of the file.
    rb"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*(?:<[^;{}]{0,1024}>[ \t\r\n]*)?(?:final[ \t\r\n]*)?(?::[^;{}()]{0,1024})?\{"
)


def text_classes(source: bytes, pairs: dict[int, int]) -> list[tuple[str, int, int]]:
    """(name, the position of the opening brace, the position after the closing one) of every class and
    struct the text of a file declares with a body, found without the grammar: for a file in which the
    grammar met an error and may have lost a class. `enum class` is no class."""
    found: list[tuple[str, int, int]] = []
    for match in _TEXT_CLASS.finditer(source):
        if match.group("enum"):
            continue
        body = match.end() - 1
        if body not in pairs:
            continue   # the brace stands in a comment or a string: an example, not a class
        found.append((match.group("name").decode("utf-8", "replace"), body, pairs[body]))
    return found


def enclosing_brace(opens: list[int], pairs: dict[int, int], position: int) -> int:
    """The opening brace of the innermost block around a position, or -1 at the level of the file."""
    index = bisect_right(opens, position) - 1
    while index >= 0:
        if pairs[opens[index]] > position:
            return opens[index]
        index -= 1
    return -1


def matching_brace(source: bytes, open_at: int) -> int:
    """The position after the brace that closes the one at open_at, by the text."""
    return brace_pairs(source).get(open_at, len(source))


def _attributed_class(function: Node) -> str:
    """The class of a method whose heading the grammar split at an attribute macro: the declaration before
    the function is `Type Class::name(...)`, read as a variable. "" when there is no such declaration."""
    before = function.prev_named_sibling
    if before is None or before.type != "declaration":
        return ""
    declarator = before.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "qualified_identifier":
        declarator = declarator.child_by_field_name("declarator") or next(
            (child for child in declarator.named_children if child.type.endswith("declarator") or child.type == "qualified_identifier"), None)
    scope = declarator.child_by_field_name("scope") if declarator is not None else None
    names = _segments(scope) if scope is not None else []
    return _text(names[-1]) if names else ""


def _has_function_declarator(function: Node) -> bool:
    declarator = function.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator") or next(
            (child for child in declarator.named_children if child.type.endswith("declarator")), None)
    return declarator is not None


def _macro_class(node: Node) -> str:
    """The name of a class the grammar read as a function (`class MACRO Name { ... }`), else ""."""
    type_node = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    if type_node is None or declarator is None or node.child_by_field_name("body") is None:
        return ""
    if type_node.type == "type_identifier":   # `MACRO` on its own line, then `class Name {`
        keyword = next((child for child in node.children if child.type == "ERROR"), None)
        words = _text(keyword).split() if keyword is not None else []
        if words and words[0] in ("class", "struct", "union") and declarator.type in ("identifier", "type_identifier"):
            return _text(declarator)
        return ""
    if type_node.type not in ("class_specifier", "struct_specifier", "union_specifier") or type_node.child_by_field_name("body") is not None:
        return ""
    error = next((child for child in node.children if child.type == "ERROR"), None)
    named = next((child for child in error.named_children if child.type in ("identifier", "type_identifier")), None) if error else None
    if named is not None:   # `class MACRO Name : public Base {`
        return _text(named)
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
    # A function has a function declarator. What the grammar calls a function without one is a block it
    # misread (a namespace after a macro line): the names declared in it are no locals.
    functions = sorted((node.start_byte, node.end_byte) for node in captures.get("function", ())
                       if not macro_classes[node.id] and _has_function_declarator(node))
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
    # Where the grammar lost track it may end a class early: the extent of a class is then read from the
    # braces of the text.
    pairs = brace_pairs(source) if root.has_error or any(macro_classes.values()) else {}
    for body in captures.get("classbody", ()):
        owner = body.parent.child_by_field_name("name") if body.parent is not None else None
        names = _segments(owner) if owner is not None else []
        if names:
            class_spans.append((body.start_byte, max(body.end_byte, pairs.get(body.start_byte, 0)), _text(names[-1])))
    for function in captures.get("function", ()):
        if macro_classes[function.id]:
            # The grammar may end such a class early (at a class with a macro nested in it): its extent is
            # read from the braces of the text.
            body = function.child_by_field_name("body")
            class_spans.append((function.start_byte, pairs.get(body.start_byte, body.end_byte), macro_classes[function.id]))
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
        elif scoped is not None and scoped.type == "identifier" and _text(scoped).upper() == _text(scoped) \
                and _attributed_class(function):
            # `bool Mock::Verify(void* mock)\n    LOCK_REQUIRED(mutex) { ... }`: the body the grammar gives to
            # a function named like the macro is the body of Mock::Verify.
            class_spans.append((function.start_byte, function.end_byte, _attributed_class(function)))
        elif scoped is not None and scoped.type == "identifier" and _text(scoped).upper() == _text(scoped):
            # A body written after a macro call, `TEST_F(Fixture, Name) { ... }`: the frameworks that do
            # this make it a method of a class derived from the first argument. A call inside it is tried
            # as a member of that class; it stays a plain call when the class has no such member.
            parameters = declarator.child_by_field_name("parameters") if declarator is not None else None
            first = parameters.named_children[0] if parameters is not None and parameters.named_children else None
            if first is not None and _text(first).isidentifier():
                class_spans.append((function.start_byte, function.end_byte, _text(first)))
    if root.has_error:
        # Classes the grammar lost: read from the text.
        known_starts = {start for start, _end, _name in class_spans}
        for name, body, end in text_classes(source, pairs):
            if body not in known_starts:
                class_spans.append((body, end, name))
    class_spans.sort()
    class_starts = [start for start, _end, _name in class_spans]

    def classes_at(position: int) -> list[str]:
        """The classes around a position, innermost first: a member of an outer class is in sight too."""
        found_names: list[str] = []
        index = bisect_right(class_starts, position) - 1
        while index >= 0:
            _start, end, name = class_spans[index]
            if position < end and name not in found_names:
                found_names.append(name)
            index -= 1
        return found_names

    def class_at(position: int) -> str:
        names = classes_at(position)
        return names[0] if names else ""

    if class_spans:
        # `Model model(Options());` reads as the declaration of a function `model` with a parameter of the
        # type `Options()`: the name before the brackets is the function of a call.
        called = {node.id for node in captures.get("call", ())}
        for node in [*captures.get("call", ()), *captures.get("vexing", ())]:
            if node.type not in ("identifier", "type_identifier"):
                continue
            for owner in classes_at(node.start_byte):
                found.append({"path": [owner], "member": _text(node), "at": [_position(node), _position(node)]})
        # A method named without a call (`os << Describe`, a method handed on as a value): only the names
        # that a class of this file declares as a method are tried, to keep the list of uses short.
        own_methods = {_text(node) for node in captures.get("methodname", ())}
        for node in captures.get("name", ()):
            if node.id in called or node.id in declared_ids or node.id in inside:
                continue
            # Also a name handed on as an argument of a call (`CountIf(list, ShouldRun)`): a static method of
            # the class around it, declared in another file.
            parent = node.parent
            argument = parent is not None and (
                parent.type == "argument_list" or (parent.type == "pointer_expression" and parent.parent is not None
                                                   and parent.parent.type == "argument_list"))
            if not argument and _text(node) not in own_methods:
                continue
            index = span_of(node.start_byte)
            if index >= 0 and _text(node) in local_names[index]:
                continue
            for owner in classes_at(node.start_byte):
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
