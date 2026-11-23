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
from .symbols import glob_bindings, use_sites

_CPP_LANGUAGE = _TreeSitterLanguage(_tscpp.language())
# Every include of the file, wherever it stands: at the top, in a conditional block, inside a
# namespace, or in the middle of a table that another file fills in.
_INCLUDES = Query(_CPP_LANGUAGE, "(preproc_include path: (_) @path)")

# Nodes that hold declarations of the scope around them.
_BLOCKS = frozenset({
    "declaration_list", "linkage_specification", "template_declaration", "export_declaration",
    "preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef",
})
_RECORDS = frozenset({"class_specifier", "struct_specifier", "union_specifier"})
_TEMPLATE_ARGUMENTS = re.compile(r"<[^<>]*>")
_PURE_VIRTUAL = re.compile(r"=\s*0\s*;?\s*$")
_FUNCTION_NAMES = frozenset({
    "identifier", "field_identifier", "qualified_identifier", "operator_name", "destructor_name", "template_function",
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
    if name is None or name.type not in _FUNCTION_NAMES:
        return ""
    segments = [segment for segment in _plain(node_text(name)).split("::") if segment]
    if not segments:
        return ""
    return f"{segments[-2]}.{segments[-1]}" if len(segments) > 1 else segments[-1]


def _declared_functions(node: Node) -> list[str]:
    """The functions a declaration declares (`int blend(int, int);`); none for a variable."""
    names = (_function_name(declarator) for declarator in node.children_by_field_name("declarator"))
    return [name for name in names if name]


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

    def record(self, node: Node) -> None:
        """A class, struct or union with a body; a forward declaration and a type without a name give nothing."""
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None or name_node is None:
            return
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
                name = _function_name(child.child_by_field_name("declarator"))
                if name:
                    self.entities.append(_entity(name, "function", child))
            elif kind == "declaration":
                type_node = child.child_by_field_name("type")
                if type_node is not None:
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
