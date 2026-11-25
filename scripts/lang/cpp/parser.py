"""What a C or C++ file states: its includes, types, functions and macros.

One grammar reads both languages (tree-sitter-cpp): a header does not say which of the two it is
written in, and a C file that uses a C++ keyword as a name still gives its includes and the
declarations around that name.
"""
from __future__ import annotations

import re
from bisect import bisect_right

import tree_sitter_cpp as _tscpp
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser, Query, QueryCursor

from ..interface import Entity, FileResult, UsePayload
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from ..treesitter import node_text, unique_strings
from .symbols import brace_pairs, enclosing_brace, glob_bindings, text_classes, use_sites

_CPP_LANGUAGE = _TreeSitterLanguage(_tscpp.language())
# Every include of the file, wherever it stands: at the top, in a conditional block, inside a
# namespace, or in the middle of a table that another file fills in.
_INCLUDES = Query(_CPP_LANGUAGE, "(preproc_include path: (_) @path)")
_LOCAL_RECORDS = Query(_CPP_LANGUAGE, """
(compound_statement (class_specifier body: (_)) @record)
(compound_statement (struct_specifier body: (_)) @record)
(compound_statement (declaration type: (class_specifier body: (_)) @record))
(compound_statement (declaration type: (struct_specifier body: (_)) @record))
""")

# Nodes that hold declarations of the scope around them.
# ERROR: where the grammar gives up on a stretch of the file (a macro it cannot place), it wraps what
# follows in one error node; the functions and types inside it are still read.
_BLOCKS = frozenset({
    "declaration_list", "linkage_specification", "template_declaration", "export_declaration",
    "preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef", "ERROR",
})
_RECORDS = frozenset({"class_specifier", "struct_specifier", "union_specifier"})
_TEMPLATE_ARGUMENTS = re.compile(r"<[^<>]*>")
_PURE_VIRTUAL = re.compile(r"=\s*0\s*;?\s*$")
# A function heading at the margin: `static void name(int a,\n    int b) {`.
_HEADING = re.compile(
    r"^(?:[A-Za-z_][\w \t\*&:<>,]*?[ \t\*&])?(?P<name>[A-Za-z_~][\w:~]*)[ \t]*"
    r"\((?:[^;{}()]|\([^;{}()]*\))*\)[^;{}()=]*\{",
    re.M,
)
# The heading of a method, at any indentation: `  CONSTEXPR auto size() const noexcept -> int {`.
_METHOD_HEADING = re.compile(
    rb"^[ \t]*(?:[A-Za-z_][\w \t\*&:<>,\[\]]*?[ \t\*&])?(?P<name>~?[A-Za-z_]\w*)[ \t]*"
    rb"\((?:[^;{}()]|\([^;{}()]*\))*\)[^;{}()=]*\{",
    re.M,
)
_NO_FUNCTIONS = frozenset({"if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else", "catch"})
# type_identifier: what the grammar calls the name of a function whose declaration it read with an error
# (a macro before a return type that is a pointer).
_FUNCTION_NAMES = frozenset({
    "identifier", "field_identifier", "qualified_identifier", "operator_name", "destructor_name", "template_function",
    "type_identifier",
})


def _includes(root: Node) -> list[tuple[str, int]]:
    """(name as written, 1-based line) of every include, in the order of the file. A quoted name is
    given plain, an angle name in its brackets; an include whose name is a macro is left out."""
    found: list[tuple[int, str, int]] = []
    for node in QueryCursor(_INCLUDES).captures(root).get("path", []):
        text = node_text(node).strip()
        if node.type == "string_literal":
            name = text.strip('"').strip()
        elif node.type == "system_lib_string":
            name = "<" + text.strip("<>").strip() + ">"
        else:
            continue
        name = name.replace("\\", "/")
        if name not in ("", "<>"):
            found.append((node.start_byte, name, node.start_point[0] + 1))
    return [(name, line) for _position, name, line in sorted(found)]


def _plain(text: str) -> str:
    """A name as written, without spaces and template arguments."""
    text = "".join(text.split())
    while True:
        shorter = _TEMPLATE_ARGUMENTS.sub("", text)
        if shorter == text:
            return text
        text = shorter


def _last_segment(text: str) -> str:
    return _plain(text).rsplit("::", 1)[-1]


def _function_name(declarator: Node | None) -> str:
    """The name a function declarator declares: `paint`, `~Painter`, `operator==`; `Painter.paint` for
    a name with a scope (`Painter::paint`); "" when the declarator declares no function."""
    node = declarator
    while node is not None and node.type != "function_declarator":
        inner = node.child_by_field_name("declarator")
        if inner is None:
            inner = next((child for child in node.named_children if child.type.endswith("declarator")), None)
        node = inner
    if node is None:
        return ""
    name = node.child_by_field_name("declarator")
    if name is None and node.named_children and node.named_children[0].type in _FUNCTION_NAMES:
        name = node.named_children[0]   # after an error the grammar leaves the name in no field
    if name is None or name.type not in _FUNCTION_NAMES:
        return ""
    if name.type == "identifier" and node_text(name).upper() == node_text(name):
        # A function written inside a macro call, `HEAP_EXPORT(void heap_init(struct heap* heap)) {`: the
        # grammar reads the macro as the function and the real declaration as its one parameter.
        parameters = node.child_by_field_name("parameters")
        inner = [child for child in parameters.named_children if child.type == "parameter_declaration"] if parameters else []
        if len(inner) == 1:
            wrapped = _function_name(inner[0].child_by_field_name("declarator"))
            if wrapped:
                return wrapped
    # `MACRO Result name();`: the grammar takes the macro for the type and `Result name` for the name.
    written = node_text(name).split()
    segments = [segment for segment in _plain(written[-1] if written else "").split("::") if segment]
    if not segments:
        return ""
    return f"{segments[-2]}.{segments[-1]}" if len(segments) > 1 else segments[-1]


_MOCK_METHOD = re.compile(r"^MOCK_(?:CONST_)?METHOD(\d*)\w*\s*\((.*)\)\s*;?\s*$", re.S)


def _mock_method(text: str) -> str:
    """The method a googlemock macro declares: `MOCK_METHOD(void, name, (int));` (the second argument) or
    `MOCK_METHOD1(name, void(int));` (the first). "" for any other text."""
    match = _MOCK_METHOD.match(text.strip())
    if match is None:
        return ""
    arguments: list[str] = []
    depth, current = 0, []
    for character in match.group(2):
        if character in "(<[":
            depth += 1
        elif character in ")>]":
            depth -= 1
        if character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    arguments.append("".join(current).strip())
    position = 0 if match.group(1) else 1
    name = arguments[position] if position < len(arguments) else ""
    return name if name.isidentifier() else ""


_NAME_BEFORE_BRACKET = re.compile(r"\s*(~?[A-Za-z_]\w*)\s*\(")


def _attributed_name(definition: Node) -> str:
    """The name of a function whose heading has an attribute macro after the parameters,
    `void Retire()\n    LOCK_REQUIRED(mutex) { ... }`, when the grammar reads the macro as the function and
    puts `Retire()` into an error node before it. "" for any other definition."""
    declarator = definition.child_by_field_name("declarator")
    if declarator is None or declarator.type != "function_declarator":
        return ""
    macro = declarator.child_by_field_name("declarator")
    if macro is None or macro.type != "identifier" or node_text(macro).upper() != node_text(macro):
        return ""
    error = next((child for child in definition.children if child.type == "ERROR"), None)
    match = _NAME_BEFORE_BRACKET.match(node_text(error)) if error is not None else None
    return match.group(1) if match else ""


def macro_class_name(node: Node) -> str:
    """The name of a class the grammar read as a function: `class MACRO Name { ... }` is, to it, a function
    Name with the return type `class MACRO`. "" for a real function."""
    if node.type != "function_definition":
        return ""
    type_node = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    body = node.child_by_field_name("body")
    if type_node is None or declarator is None or body is None:
        return ""
    if type_node.type == "type_identifier":
        # `MACRO` on a line of its own, then `class Name {`: the grammar takes the macro for the return type
        # and puts the keyword into an error node.
        keyword = next((child for child in node.children if child.type == "ERROR"), None)
        words = node_text(keyword).split() if keyword is not None else []
        if words and words[0] in ("class", "struct", "union") and declarator.type in ("identifier", "type_identifier"):
            return node_text(declarator)
        return ""
    if type_node.type not in _RECORDS or type_node.child_by_field_name("body") is not None:
        return ""
    # With a base class, `class MACRO Name : public Base {`, the grammar puts `Name : public` into an error
    # node and takes the base for the declarator.
    error = next((child for child in node.children if child.type == "ERROR"), None)
    named = next((child for child in error.named_children if child.type in ("identifier", "type_identifier")), None) if error else None
    if named is not None:
        return node_text(named)
    return node_text(declarator) if declarator.type in ("identifier", "type_identifier") else ""


def macro_class_bases(node: Node) -> list[str]:
    """The base of a class read by macro_class_name, when it has one: the declarator of the grammar."""
    declarator = node.child_by_field_name("declarator")
    type_node = node.child_by_field_name("type")
    if declarator is None or type_node is None or type_node.type not in _RECORDS \
            or not any(child.type == "ERROR" for child in node.children):
        return []
    base = _plain(node_text(declarator))
    return [base] if base else []


def _declared_functions(node: Node) -> list[str]:
    """The functions a declaration declares (`int blend(int, int);`); none for a variable.

    A prototype that starts with a macro the grammar cannot place (`DEPOT_API crate_t crate_init(int);`)
    is read with an error node before its declarator, which then stands in no field: such a declarator
    is taken as well."""
    # `void Trace() LOCKED(mutex);`: the grammar reads `Trace()` as an error and the attribute macro as the
    # function that is declared. The function is the name in the error.
    error = next((child for child in node.children if child.type == "ERROR"), None)
    attributed = next((child for child in error.named_children if child.type == "init_declarator"), None) if error else None
    attributed_name = attributed.child_by_field_name("declarator") if attributed is not None else None
    if attributed_name is not None and attributed_name.type in ("identifier", "field_identifier"):
        return [node_text(attributed_name)]
    declarators = list(node.children_by_field_name("declarator"))
    names = [name for name in (_function_name(declarator) for declarator in declarators) if name]
    if names:
        return names
    # The declarator the error left outside a field, at most a few levels down (behind a pointer, or as the
    # "name" of what the grammar took for a qualified type).
    stack = [(child, 0) for child in node.named_children if child not in declarators]
    while stack:
        current, depth = stack.pop()
        if current.type == "function_declarator":
            name = _function_name(current)
            return [name] if name else []
        if depth < 3 and current.type not in ("parameter_list", "compound_statement", "field_declaration_list"):
            stack.extend((child, depth + 1) for child in current.named_children)
    return []


def _entity(name: str, kind: str, node: Node, methods: list[str] | None = None, bases: list[str] | None = None) -> Entity:
    return Entity(name=name, kind=kind, line=node.start_point[0] + 1, methods=methods or [], decorators=[], bases=bases or [])


def _members(body: Node) -> list[Node]:
    """The member declarations of a class body, those inside template and conditional blocks included."""
    members: list[Node] = []
    # A stack in place of recursion: a chain of a thousand `#elif` is a thousand blocks deep.
    pending = list(reversed(body.named_children))
    while pending:
        child = pending.pop()
        if child.type in _BLOCKS:
            pending.extend(reversed(child.named_children))
        else:
            members.append(child)
    return members


class _Collector:
    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.abstract: list[str] = []
        self.bases: list[str] = []
        self.macro_classes: list[tuple[int, int, str]] = []   # (index of the class entity, start of its body, name)
        self.class_bodies: list[tuple[int, int, str]] = []    # the same for the classes the grammar read as classes
        self.friends: set[int] = set()                         # id() of the functions defined as friends in a class
        self.seen_records: set[int] = set()

    def record(self, node: Node) -> None:
        """A class, struct or union with a body; a forward declaration and a type without a name give nothing."""
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None or name_node is None:
            return
        self.seen_records.add(node.id)
        name = _last_segment(node_text(name_node))
        class_index = len(self.entities)
        bases = unique_strings(
            _plain(node_text(base))
            for clause in node.named_children if clause.type == "base_class_clause"
            for base in clause.named_children
            if base.type in ("type_identifier", "qualified_identifier", "template_type")
        )
        methods: list[str] = []
        defined: list[Entity] = []
        nested: list[Node] = []
        abstract = False
        for member in _members(body):
            if member.type == "function_definition":
                method = _attributed_name(member) or _function_name(member.child_by_field_name("declarator"))
                if method:
                    methods.append(method)
                    defined.append(_entity(f"{name}.{method}", "function", member))
                # A class the grammar took for the return type of what follows it.
                returned = member.child_by_field_name("type")
                if returned is not None:
                    nested.append(returned)
            elif member.type == "friend_declaration":
                # A function defined as a friend inside the class is a function of the file, called by
                # its plain name.
                friends: list[Entity] = []
                for inner in member.named_children:
                    if inner.type == "function_definition":
                        friend = _function_name(inner.child_by_field_name("declarator"))
                        if friend:
                            friends.append(_entity(friend, "function", inner))
                    elif inner.type == "declaration":
                        friends.extend(_entity(friend, "function", inner) for friend in _declared_functions(inner))
                defined.extend(friends)
                self.friends.update(id(friend) for friend in friends)   # functions of the file: never adopted
            elif member.type in ("field_declaration", "declaration"):
                mocked = _mock_method(node_text(member))
                declared = [mocked] if mocked else _declared_functions(member)
                methods.extend(declared)
                if declared and _PURE_VIRTUAL.search(node_text(member)):
                    abstract = True
                type_node = member.child_by_field_name("type")
                if type_node is not None:
                    nested.append(type_node)
            else:
                nested.append(member)
        self.entities.append(_entity(name, "class", node, unique_strings(iter(methods)), bases))
        self.class_bodies.append((len(self.entities) - 1, body.start_byte, name))
        del class_index
        self.entities.extend(defined)
        self.bases.extend(bases)
        if abstract:
            self.abstract.append(name)
        for inner in nested:
            self.type_specifier(inner)

    def enum(self, node: Node) -> None:
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None or name_node is None:
            return
        members = unique_strings(
            node_text(member.child_by_field_name("name")) for member in body.named_children if member.type == "enumerator"
        )
        self.entities.append(_entity(_last_segment(node_text(name_node)), "enum", node, members))

    def type_specifier(self, node: Node) -> None:
        if node.type in _RECORDS:
            self.record(node)
        elif node.type == "enum_specifier":
            self.enum(node)

    def typedef(self, node: Node) -> None:
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            self.type_specifier(type_node)
        known = {entity.name for entity in self.entities}
        for declarator in node.children_by_field_name("declarator"):
            stack = [declarator]
            while stack:
                current = stack.pop()
                if current.type == "type_identifier":
                    name = node_text(current)
                    if name and name not in known:   # `typedef struct tape { ... } tape;` is one type
                        self.entities.append(_entity(name, "type", node))
                    break
                stack.extend(reversed(current.named_children))

    def macro_record(self, node: Node) -> None:
        """A class written with a macro between the keyword and its name (`class DEPOT_API Crate { ... };`),
        which the grammar reads as a function named Crate that returns `class DEPOT_API`. What the grammar
        takes for the body of that function is not the body of the class: it may end early (at a class
        with a macro nested in it) or run on over the classes that follow. So the statements of that body
        are read like the declarations of a file, and `adopt` then gives each class the functions that
        stand between its braces in the text."""
        name = macro_class_name(node)
        body = node.child_by_field_name("body")
        self.macro_classes.append((len(self.entities), body.start_byte, name))
        bases = macro_class_bases(node)
        self.entities.append(_entity(name, "class", node, [], bases))
        self.bases.extend(bases)
        self.scope(body)

    def adopt(self, source: bytes, with_all: bool = False) -> None:
        """The members of a class with a macro that the grammar put outside it. Such a class ends, for the
        grammar, at the first class with a macro nested in it; what follows up to the closing brace of the
        text stands at file level. A function there is a method of the class, not a function of the file.
        with_all: the same for every class of a file in which the grammar met an error, where an ordinary
        class may be cut short too."""
        classes = self.macro_classes + (self.class_bodies if with_all else [])
        if not classes:
            return
        pairs = brace_pairs(source)
        newlines = [position for position, byte in enumerate(source) if byte == 10]
        extents = []
        for index, body_start, name in classes:
            end = pairs.get(body_start, len(source))
            extents.append((bisect_right(newlines, body_start) + 1, bisect_right(newlines, end) + 1, index, name))
        kept: list[Entity] = []
        for position, entity in enumerate(self.entities):
            inside = [extent for extent in extents if extent[0] < entity.line <= extent[1] and extent[2] != position]
            if entity.kind == "function" and "." not in entity.name and inside and id(entity) not in self.friends:
                owner = self.entities[max(inside, key=lambda extent: extent[0])[2]]   # the innermost class
                if entity.name not in owner.methods:
                    owner.methods.append(entity.name)
                continue
            kept.append(entity)
        self.entities = kept

    def classes_of_the_text(self, source: bytes) -> None:
        """For a file in which the grammar met an error: the classes the text declares that the tree does
        not hold, and for every class of the text the methods whose heading stands directly in its body.
        `adopt` then gives each class the functions the grammar left at the level of the file."""
        pairs = brace_pairs(source)
        opens = sorted(pairs)
        newlines = [position for position, byte in enumerate(source) if byte == 10]
        by_body = {body_start: index for index, body_start, _name in self.class_bodies + self.macro_classes}
        for name, body, end in text_classes(source, pairs):
            index = by_body.get(body)
            if index is None:
                index = len(self.entities)
                line = bisect_right(newlines, body) + 1
                self.entities.append(Entity(name=name, kind="class", line=line, methods=[], decorators=[], bases=[]))
                self.class_bodies.append((index, body, name))
                by_body[body] = index
            owner = self.entities[index]
            for match in _METHOD_HEADING.finditer(source, body + 1, end):
                method = match.group("name").decode("utf-8", "replace")
                if method in _NO_FUNCTIONS or method.upper() == method or method in owner.methods:
                    continue
                if b"friend" in source[match.start():match.start("name")].split():
                    continue   # a friend defined in the class is a function of the file
                if enclosing_brace(opens, pairs, match.start("name")) == body:
                    owner.methods.append(method)

    def nested_records(self, root: Node) -> None:
        """Classes defined inside a function body (a comparator local to a test): read like any class."""
        for node in QueryCursor(_LOCAL_RECORDS).captures(root).get("record", ()):
            if node.id not in self.seen_records:
                self.record(node)

    def headings(self, node: Node) -> None:
        """The functions defined in a stretch the grammar could not read, by their heading: a line that
        starts at the margin, holds `name(` and whose parameter list is followed by `{`. Control words and
        macros in capitals are no functions; a name the tree already gave is not added again."""
        known = {entity.name for entity in self.entities}
        text = node_text(node)
        first_line = node.start_point[0] + 1
        for match in _HEADING.finditer(text):
            name = match.group("name")
            if name in _NO_FUNCTIONS or name.upper() == name or name in known:
                continue
            segments = [segment for segment in name.split("::") if segment]
            if not segments:
                continue
            entity_name = f"{segments[-2]}.{segments[-1]}" if len(segments) > 1 else segments[-1]
            if entity_name in known:
                continue
            known.add(entity_name)
            line = first_line + text.count("\n", 0, match.start("name"))
            self.entities.append(Entity(name=entity_name, kind="function", line=line, methods=[], decorators=[], bases=[]))

    def scope(self, node: Node) -> None:
        """The declarations of a file, a namespace or a block. The blocks inside (a namespace, a branch of
        a conditional) are read in place from a stack of (parent, declaration), not by recursion: a chain
        of a thousand `#elif` is a thousand blocks deep."""
        pending = [(node, child) for child in reversed(node.named_children)]
        while pending:
            node, child = pending.pop()
            kind = child.type
            if kind == "namespace_definition":
                body = child.child_by_field_name("body")
                if body is not None:
                    pending.extend((body, inner) for inner in reversed(body.named_children))
            elif kind in _BLOCKS or kind == "labeled_statement":
                # labeled_statement: `public:` with the declaration after it, in a class the grammar took
                # for a function (macro_record).
                pending.extend((child, inner) for inner in reversed(child.named_children))
            elif kind in _RECORDS or kind == "enum_specifier":
                self.type_specifier(child)
            elif kind == "type_definition":
                self.typedef(child)
            elif kind == "alias_declaration":
                name = node_text(child.child_by_field_name("name"))
                if name:
                    self.entities.append(_entity(name, "type", child))
            elif kind == "function_definition":
                if macro_class_name(child):
                    self.macro_record(child)
                    continue
                returned = child.child_by_field_name("type")
                if returned is not None:
                    self.type_specifier(returned)   # a class the grammar took for the return type
                declarator = child.child_by_field_name("declarator")
                body = child.child_by_field_name("body")
                if declarator is not None and declarator.type == "identifier" and node_text(declarator) == "namespace" and body is not None:
                    # `MACRO` on a line, then `namespace name {`: the grammar reads a function named
                    # `namespace`. Its body is the body of the namespace.
                    pending.extend((body, inner) for inner in reversed(body.named_children))
                    continue
                name = _attributed_name(child) or _function_name(child.child_by_field_name("declarator"))
                if name and name.upper() == name:
                    # `void Count()\n    LOCK_REQUIRED(mutex) { ... }`: an attribute macro between the
                    # parameters and the body. The grammar reads a variable `Count()` and a function named
                    # like the macro; the function is the name before it.
                    before = child.prev_named_sibling
                    if before is not None and before.type == "labeled_statement" and before.named_children:
                        before = before.named_children[-1]   # `public:` with the declaration after it
                    declarator = before.child_by_field_name("declarator") if before is not None and before.type in ("declaration", "field_declaration") else None
                    inner = declarator.child_by_field_name("declarator") if declarator is not None and declarator.type == "init_declarator" else None
                    if inner is not None and inner.type in ("identifier", "field_identifier"):
                        self.entities.append(_entity(node_text(inner), "function", before))
                        continue
                if name:
                    self.entities.append(_entity(name, "function", child))
            elif kind == "function_declarator" and node.type == "ERROR":
                # A prototype the grammar could not finish (`MACRO(void name(int));` at the margin).
                name = _function_name(child)
                if name and name.upper() != name:
                    self.entities.append(_entity(name, "function", child))
            elif kind in ("declaration", "field_declaration"):
                # `field_declaration` outside a class: what the grammar makes of a prototype after it lost
                # track of a block (an `extern "C" {` that an `#ifdef` opens and another one closes).
                type_node = child.child_by_field_name("type")
                if type_node is not None and kind == "declaration":
                    self.type_specifier(type_node)
                declared = _declared_functions(child)
                if not declared:
                    # `void Trace() LOCK_EXCLUDED(mutex);`: with an attribute macro after the parameters the
                    # grammar reads a variable `Trace` initialised with `()`. At the level of a file or a
                    # class, a name followed by empty brackets declares a function.
                    declarator = child.child_by_field_name("declarator")
                    inner = declarator.child_by_field_name("declarator") if declarator is not None and declarator.type == "init_declarator" else None
                    value = declarator.child_by_field_name("value") if inner is not None else None
                    if inner is not None and inner.type in ("identifier", "field_identifier") and value is not None \
                            and node_text(value).replace(" ", "") == "()":
                        declared = [node_text(inner)]
                for name in declared:
                    self.entities.append(_entity(name, "function", child))
            elif kind == "preproc_function_def" or (kind == "preproc_def" and node_text(child.child_by_field_name("value")).strip()):
                # A macro with a value is a name other files use; `#define GUARD_H` alone is an include guard.
                name = node_text(child.child_by_field_name("name"))
                if name:
                    self.entities.append(_entity(name, "function", child))


def parse_cpp(
    path: str,
    content: str,
    repo: str,
    mtime: float,
    result_language: str = "cpp",
) -> FileResult:
    root = Parser(_CPP_LANGUAGE).parse(content.encode("utf-8")).root_node
    includes = _includes(root)
    import_lines: dict[str, list[int]] = {}
    for name, line in includes:
        add_import_line(import_lines, name, line)
    collector = _Collector()
    collector.scope(root)
    collector.nested_records(root)
    if root.has_error:
        collector.classes_of_the_text(content.encode("utf-8"))
    collector.adopt(content.encode("utf-8"), with_all=root.has_error)
    if root.has_error:
        # Where the grammar lost track (a block that preprocessor branches open and close), the functions
        # below may stand inside a statement or an error node at any depth: their headings are read from
        # the text of the file.
        collector.headings(root)
    entity_payloads = [entity.to_dict() for entity in collector.entities]
    included = unique_strings(name for name, _line in includes)
    return FileResult(
        path=path,
        language=result_language,
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=included,
        # An abstract class is not reported as an interface with providers: that would add dependents that
        # no include states (a user of the base class listed under each derived class). The graph of C and
        # C++ files is their includes.
        exports_abstract=[],
        implements=[],
        extends=unique_strings(iter(collector.bases)),
        purpose=infer_purpose(path, content, result_language, entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=False,
        entities=entity_payloads,
        imported_names=[],
        exported_names=unique_strings(entity.name for entity in collector.entities),
        import_lines=import_lines,
        bindings=glob_bindings(included),
        uses=use_sites(root),
    )


def cpp_use_positions(path: str, content: str) -> list[UsePayload]:
    """Every use site of a C or C++ file, for the positions `usages` and `entity --positions` list."""
    del path
    return use_sites(Parser(_CPP_LANGUAGE).parse(content.encode("utf-8")).root_node)
