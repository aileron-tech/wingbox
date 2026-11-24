"""What a C or C++ file states: its includes, types, functions and macros.

One grammar reads both languages (tree-sitter-cpp): a header does not say which of the two it is
written in, and a C file that uses a C++ keyword as a name still gives its includes and the
declarations around that name.
"""
from __future__ import annotations

import re

import tree_sitter_cpp as _tscpp
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser, Query, QueryCursor

from ..interface import Entity, FileResult, UsePayload
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from ..treesitter import node_text, unique_strings
from .symbols import glob_bindings, matching_brace, use_sites

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
    r"\((?:[^;{}()]|\([^;{}()]*\))*\)[ \t\r\n]*(?:const[ \t\r\n]*)?(?:noexcept[ \t\r\n]*)?\{",
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
    segments = [segment for segment in _plain(node_text(name)).split("::") if segment]
    if not segments:
        return ""
    return f"{segments[-2]}.{segments[-1]}" if len(segments) > 1 else segments[-1]


def macro_class_name(node: Node) -> str:
    """The name of a class the grammar read as a function: `class MACRO Name { ... }` is, to it, a function
    Name with the return type `class MACRO`. "" for a real function."""
    if node.type != "function_definition":
        return ""
    type_node = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    body = node.child_by_field_name("body")
    if type_node is None or declarator is None or body is None or type_node.type not in _RECORDS:
        return ""
    if type_node.child_by_field_name("body") is not None or declarator.type not in ("identifier", "type_identifier"):
        return ""
    return node_text(declarator)


def _declared_functions(node: Node) -> list[str]:
    """The functions a declaration declares (`int blend(int, int);`); none for a variable.

    A prototype that starts with a macro the grammar cannot place (`DEPOT_API crate_t crate_init(int);`)
    is read with an error node before its declarator, which then stands in no field: such a declarator
    is taken as well."""
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
    for child in body.named_children:
        if child.type in _BLOCKS:
            members.extend(_members(child))
        else:
            members.append(child)
    return members


class _Collector:
    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.abstract: list[str] = []
        self.bases: list[str] = []
        self.macro_classes: list[tuple[int, int, str]] = []   # (index of the class entity, start of its body, name)
        self.seen_records: set[int] = set()

    def record(self, node: Node) -> None:
        """A class, struct or union with a body; a forward declaration and a type without a name give nothing."""
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None or name_node is None:
            return
        self.seen_records.add(node.id)
        name = _last_segment(node_text(name_node))
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
                method = _function_name(member.child_by_field_name("declarator"))
                if method:
                    methods.append(method)
                    defined.append(_entity(f"{name}.{method}", "function", member))
            elif member.type in ("field_declaration", "declaration"):
                declared = _declared_functions(member)
                methods.extend(declared)
                if declared and _PURE_VIRTUAL.search(node_text(member)):
                    abstract = True
                type_node = member.child_by_field_name("type")
                if type_node is not None:
                    nested.append(type_node)
            else:
                nested.append(member)
        self.entities.append(_entity(name, "class", node, unique_strings(iter(methods)), bases))
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
        which the grammar reads as a function named Crate that returns `class DEPOT_API`: its members are
        the statements of that body."""
        name = macro_class_name(node)
        self.macro_classes.append((len(self.entities), node.child_by_field_name("body").start_byte, name))
        methods: list[str] = []
        defined: list[Entity] = []
        stack = list(reversed(node.child_by_field_name("body").named_children))
        while stack:
            member = stack.pop()
            if member.type == "labeled_statement":          # `public:` with the declaration after it
                stack.extend(reversed(member.named_children))
            elif member.type == "function_definition":
                method = _function_name(member.child_by_field_name("declarator"))
                if method:
                    methods.append(method)
                    defined.append(_entity(f"{name}.{method}", "function", member))
            elif member.type in ("declaration", "field_declaration"):
                methods.extend(_declared_functions(member))
                type_node = member.child_by_field_name("type")
                if type_node is not None:
                    self.type_specifier(type_node)
            elif member.type in _RECORDS or member.type == "enum_specifier":
                self.type_specifier(member)
        self.macro_classes[-1] = (len(self.entities), self.macro_classes[-1][1], name)
        self.entities.append(_entity(name, "class", node, unique_strings(iter(methods)), []))
        self.entities.extend(defined)

    def adopt(self, source: bytes) -> None:
        """The members of a class with a macro that the grammar put outside it. Such a class ends, for the
        grammar, at the first class with a macro nested in it; what follows up to the closing brace of the
        text stands at file level. A function there is a method of the class, not a function of the file."""
        extents = []
        for index, body_start, name in self.macro_classes:
            end = matching_brace(source, body_start)
            extents.append((source.count(b"\n", 0, body_start) + 1, source.count(b"\n", 0, end) + 1, index, name))
        if not extents:
            return
        kept: list[Entity] = []
        for position, entity in enumerate(self.entities):
            inside = [extent for extent in extents if extent[0] < entity.line <= extent[1] and extent[2] != position]
            if entity.kind == "function" and "." not in entity.name and inside:
                owner = self.entities[max(inside, key=lambda extent: extent[0])[2]]   # the innermost class
                if entity.name not in owner.methods:
                    owner.methods.append(entity.name)
                continue
            kept.append(entity)
        self.entities = kept

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
        """The declarations of a file, a namespace or a block."""
        for child in node.named_children:
            kind = child.type
            if kind == "namespace_definition":
                body = child.child_by_field_name("body")
                if body is not None:
                    self.scope(body)
            elif kind in _BLOCKS:
                self.scope(child)
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
                name = _function_name(child.child_by_field_name("declarator"))
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
                for name in _declared_functions(child):
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
    collector.adopt(content.encode("utf-8"))
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
