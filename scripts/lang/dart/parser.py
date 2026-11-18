from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..interface import Entity, EntityPayload, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash
from ..treesitter import node_text, unique_strings


# tree-sitter-dart node types (package tree-sitter-dart 0.1.0). A class, a
# `mixin class` and a `class A = B with M` all come out as class_definition;
# mixin_declaration and extension_declaration are the only other containers.
_CLASS_NODES = frozenset({"class_definition", "mixin_declaration"})
_EXTENSION_NODES = frozenset({
    "extension_declaration",
    "extension_type_declaration",
})
_ENUM_NODES = frozenset({"enum_declaration"})
# Containers whose members are reachable by name from importing libraries.
_MEMBER_NODES = frozenset({"class_body", "extension_body", "enum_body"})
# A method signature always carries a function or accessor signature inside it.
_SIGNATURE_NODES = frozenset({
    "function_signature",
    "getter_signature",
    "setter_signature",
    "operator_signature",
})
# Constructor forms: declarations and their function bodies, never methods.
_CONSTRUCTOR_NODES = frozenset({
    "constructor_signature",
    "constant_constructor_signature",
    "factory_constructor_signature",
    "redirecting_factory_constructor_signature",
})
# The `abstract` modifier is a named child of class_definition only; abstract
# members are `abstract` keywords followed by an identifier list.
_ABSTRACT_MODIFIER = "abstract"
_PART_OF = "part_of_directive"
_PART = "part_directive"
_IMPORT_OR_EXPORT = "import_or_export"
_STRING_LITERAL = "string_literal"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
from .imports import DART_EXTENSION
# Dart escapes inside a single-quoted or double-quoted URI (\\, \', \$).
_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)


@dataclass(frozen=True)
class DartDirective:
    """One import, export or part URI with what the parser knows about it.

    kind is "import", "export", "part" or "part_of", plus the "conditional"
    flag when the directive carries `if (...) '...'` alternatives: those are
    platform variants of the same library, not additional dependencies.
    """

    uri: str
    kind: str
    conditional: bool = False

    @property
    def is_codegen(self) -> bool:
        """True for a URI in the .../codegen folder of Dart's macros proposal."""
        return "/codegen/" in self.uri


def _unquote_uri(text: str) -> str:
    if len(text) < 2:
        return ""
    return _ESCAPE_RE.sub(r"\1", text[1:-1])


def _uri_texts(node) -> list[str]:
    """Every single-quoted or double-quoted URI under one directive node."""
    return [
        uri
        for descendant in _descendants(node)
        if descendant.type == _STRING_LITERAL
        if (uri := _unquote_uri(node_text(descendant)))
    ]


def _descendants(node) -> list:
    found = []
    stack = list(reversed(node.named_children))
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(reversed(current.named_children))
    return found


def _is_conditional(node) -> bool:
    return any(
        descendant.type == "configuration_uri"
        for descendant in _descendants(node)
    )


def _directives(root) -> list[DartDirective]:
    directives: list[DartDirective] = []
    for child in root.named_children:
        if child.type == _PART_OF:
            directives.extend(
                DartDirective(uri, "part_of")
                for uri in _uri_texts(child)
            )
            continue
        if child.type == _PART:
            directives.extend(
                DartDirective(uri, "part")
                for uri in _uri_texts(child)
            )
            continue
        if child.type != _IMPORT_OR_EXPORT:
            continue
        kind = (
            "export"
            if any(
                descendant.type == "library_export"
                for descendant in _descendants(child)
            )
            else "import"
        )
        conditional = _is_conditional(child)
        directives.extend(
            DartDirective(uri, kind, conditional)
            for uri in _uri_texts(child)
        )
    return directives


def _exported_names(root, directives: list[DartDirective]) -> list[str]:
    """Names this library re-exports: `show` lists, and every name of a bare export.

    A bare export re-exports its library; the graph then links the exporting
    module to it as an import edge, so the names need not be listed again.
    """
    shown: list[str] = []
    for child in root.named_children:
        if child.type != _IMPORT_OR_EXPORT:
            continue
        if not any(
            descendant.type == "library_export"
            for descendant in _descendants(child)
        ):
            continue
        for combinator in _descendants(child):
            if combinator.type != "combinator":
                continue
            if "hide" in [
                node_text(grandchild)
                for grandchild in combinator.children
                if not grandchild.is_named
            ]:
                continue
            shown.extend(
                node_text(grandchild)
                for grandchild in combinator.named_children
                if grandchild.type == "identifier"
            )
    return list(dict.fromkeys(shown))


# Entity kinds of the shared contract: a Dart extension is neither a class
# nor an interface, and its members reach a receiver type through its `on`.
_KIND_BY_CONTAINER = {
    "class_definition": "class",
    "mixin_declaration": "mixin",
    "extension_declaration": "extension",
    "extension_type_declaration": "class",
}


def _is_abstract(node) -> bool:
    """True when the container carries the `abstract` modifier.

    tree-sitter-dart 0.1.0 makes `abstract` a named token child of
    class_definition (it is a keyword that also starts abstract members), so
    the check is on the token text, not on its named-ness.
    """
    return any(
        node_text(child) == _ABSTRACT_MODIFIER for child in node.children
    )


def _class_entity(node) -> Entity | None:
    """One class, mixin or extension type declaration."""
    if node.type == "class_definition":
        application = next(
            (
                child
                for child in node.named_children
                if child.type == "mixin_application_class"
            ),
            None,
        )
        if application is not None:
            name_node = next(
                (
                    child
                    for child in application.named_children
                    if child.type == "identifier"
                ),
                None,
            )
            bases = _mixin_application_bases(application)
        else:
            name_node = node.child_by_field_name("name")
            bases = _heritage_bases(node)
    else:
        name_node = node.child_by_field_name("name") or _declared_name(node)
        bases = _heritage_bases(node)
    name = node_text(name_node)
    if not name:
        return None
    return Entity(
        name=name,
        kind=_KIND_BY_CONTAINER[node.type],
        line=node.start_point[0] + 1,
        methods=_member_names(node),
        decorators=[],
        bases=bases,
    )


def _declared_name(node) -> Optional[object]:
    """The identifier that names a mixin, enum, extension or type alias."""
    for child in node.named_children:
        if child.type == "identifier":
            return child
    return node.child_by_field_name("name")


def _simple_type_name(type_node) -> str:
    """The type name without its arguments and nullability (Repository<User> -> Repository)."""
    text = node_text(type_node)
    names = _IDENTIFIER_RE.findall(text)
    return names[0] if names else ""


def _heritage_bases(node) -> list[str]:
    """Bases in `extends B with M implements I` order, then a mixin's `on` types.

    superclass holds the extended type and its `mixins`; interfaces holds the
    implemented types. A mixin_declaration has `on` types (plain type_identifier
    children) and its own `interfaces` node.
    """
    bases: list[str] = []
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        bases.extend(
            _simple_type_name(child)
            for child in superclass.named_children
            if child.type == "type_identifier"
        )
        for child in superclass.named_children:
            if child.type != "mixins":
                continue
            bases.extend(
                _simple_type_name(mixin)
                for mixin in child.named_children
                if mixin.type == "type_identifier"
            )
    interfaces = node.child_by_field_name("interfaces")
    for interfaces_node in (
        interfaces,
        *(
            child
            for child in node.named_children
            if child.type == "interfaces" and child is not interfaces
        ),
    ):
        if interfaces_node is None:
            continue
        bases.extend(
            _simple_type_name(child)
            for child in interfaces_node.named_children
            if child.type == "type_identifier"
        )
    if node.type in ("mixin_declaration", "extension_declaration"):
        # A mixin's `on` types and an extension's extended type are plain
        # type_identifier children outside superclass and interfaces.
        bases.extend(
            _simple_type_name(child)
            for child in node.named_children
            if child.type == "type_identifier"
        )
    return list(dict.fromkeys(name for name in bases if name))


def _mixin_application_bases(application) -> list[str]:
    """`class A = B with M1, M2` names B, then M1 and M2."""
    bases: list[str] = []
    for child in application.named_children:
        if child.type != "mixin_application":
            continue
        bases.extend(
            _simple_type_name(part)
            for part in child.named_children
            if part.type == "type_identifier"
        )
        for part in child.named_children:
            if part.type != "mixins":
                continue
            bases.extend(
                _simple_type_name(mixin)
                for mixin in part.named_children
                if mixin.type == "type_identifier"
            )
    return list(dict.fromkeys(name for name in bases if name))


def _member_names(node) -> list[str]:
    """Class members that an importing library reaches by name.

    getters and setters of one name count once; constructors are not members
    of the type's API; abstract members are `abstract` followed by an
    identifier list, and both field and method declarations name themselves
    through that list or a signature.
    """
    names: list[str] = []
    for body in node.named_children:
        if body.type not in _MEMBER_NODES:
            continue
        for member in body.named_children:
            if member.type in _CONSTRUCTOR_NODES:
                continue
            if _contains_constructor(member):
                continue
            for name in _signature_names(member):
                if name not in names:
                    names.append(name)
    return names


def _contains_constructor(member) -> bool:
    if any(
        descendant.type in _CONSTRUCTOR_NODES
        for descendant in _descendants(member)
    ):
        return True
    return any(
        descendant.type == "method_signature"
        and any(
            child.type in _CONSTRUCTOR_NODES
            for child in descendant.named_children
        )
        for descendant in member.named_children
    )


def _signature_names(member) -> list[str]:
    """Member names of one body child: a signature, or a declaration list."""
    names: list[str] = []
    for descendant in member.named_children:
        if descendant.type in _SIGNATURE_NODES:
            name = _IDENTIFIER_RE.search(node_text(descendant.child_by_field_name("name")))
            if name:
                names.append(name.group(0))
        elif descendant.type == "identifier_list":
            names.extend(
                node_text(identifier)
                for identifier in descendant.named_children
                if identifier.type == "identifier"
            )
        elif descendant.type == "function_signature":
            # `abstract void run(int x);` is an abstract keyword, the type and
            # the name directly: no signature wrapper, so the name is read by
            # its field when the declaration carries one.
            name = node_text(descendant.child_by_field_name("name"))
            if name:
                names.append(name)
    if not names and member.type == "function_signature":
        name = node_text(member.child_by_field_name("name"))
        if name:
            names.append(name)
    return names


def _abstract_member_names(node) -> list[str]:
    names: list[str] = []
    for body in node.named_children:
        if body.type not in _MEMBER_NODES:
            continue
        for member in body.named_children:
            declarations = [member, *member.named_children]
            for declaration in declarations:
                if not any(
                    not child.is_named
                    and node_text(child) == _ABSTRACT_MODIFIER
                    for child in declaration.children
                ):
                    continue
                for identifier in declaration.named_children:
                    if identifier.type == "identifier_list":
                        names.extend(
                            node_text(item)
                            for item in identifier.named_children
                            if item.type == "identifier"
                        )
    return list(dict.fromkeys(name for name in names if name))


def _top_level_entities(root) -> list[Entity]:
    """Functions and type aliases declared at the top level of a library."""
    entities: list[Entity] = []
    for child in root.named_children:
        if child.type == "function_signature":
            name = node_text(child.child_by_field_name("name"))
            if name:
                entities.append(Entity(
                    name=name,
                    kind="function",
                    line=child.start_point[0] + 1,
                    methods=[],
                    decorators=[],
                    bases=[],
                ))
        elif child.type in ("getter_signature", "setter_signature"):
            name = node_text(child.child_by_field_name("name"))
            if name and all(entity.name != name for entity in entities):
                entities.append(Entity(
                    name=name,
                    kind="function",
                    line=child.start_point[0] + 1,
                    methods=[],
                    decorators=[],
                    bases=[],
                ))
        elif child.type == "type_alias":
            name = node_text(
                next(
                    (
                        item
                        for item in child.named_children
                        if item.type == "type_identifier"
                    ),
                    None,
                )
            )
            if name:
                entities.append(Entity(
                    name=name,
                    kind="type",
                    line=child.start_point[0] + 1,
                    methods=[],
                    decorators=[],
                    bases=[],
                ))
    return entities


def _type_entities(root) -> list[Entity]:
    entities: list[Entity] = []
    for child in root.named_children:
        if child.type in _CLASS_NODES or child.type in _EXTENSION_NODES:
            entity = _class_entity(child)
            if entity is not None:
                entities.append(entity)
        elif child.type in _ENUM_NODES:
            name = node_text(child.child_by_field_name("name"))
            if name:
                entities.append(Entity(
                    name=name,
                    kind="enum",
                    line=child.start_point[0] + 1,
                    methods=_member_names(child),
                    decorators=[],
                    bases=[],
                ))
    return entities


def _is_abstract_container(child) -> bool:
    return (
        child.type in _CLASS_NODES
        and (
            _is_abstract(child)
            or bool(_abstract_member_names(child))
        )
    )


def _imported_names(directives: list[DartDirective]) -> list[ImportedName]:
    """A Dart import binds a whole library; there is no per-name alias syntax.

    The library's last path segment is the conventional prefix a reader uses
    (utils/format.dart -> format), so the name line of the graph stays useful
    without claiming a binding the language does not have.
    """
    names: list[ImportedName] = []
    for directive in directives:
        if directive.kind != "import" or directive.conditional:
            continue
        if directive.uri.startswith(("dart:", "package:")):
            continue
        stem = directive.uri.rsplit("/", 1)[-1]
        if stem.endswith(DART_EXTENSION):
            stem = stem[: -len(DART_EXTENSION)]
        if stem:
            names.append(ImportedName(
                local_name=stem,
                original_name=stem,
                module=directive.uri,
            ))
    return names


def _part_directive_imports(directives: list[DartDirective]) -> list[str]:
    """Import targets of part and part of directives.

    A part directive names a file the library owns and a part of directive
    names the library that owns the file; both become edges when they
    resolve. Every URI is kept, whatever its form: a `package:` part names a
    file of this package, so resolution finds it, and a `dart:` or a
    third-party `package:` part is simply never resolved. The codegen folder
    of the macros proposal is left out: on disk it holds a copy of the
    library, not another module.
    """
    return [
        directive.uri
        for directive in directives
        if directive.kind in ("part", "part_of")
        if not (directive.kind == "part" and directive.is_codegen)
    ]


def parse_dart_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    import tree_sitter_dart as _tsd
    from tree_sitter import Language as _TreeSitterLanguage
    from tree_sitter import Parser

    language = _TreeSitterLanguage(_tsd.language())
    root = Parser(language).parse(content.encode("utf-8")).root_node
    directives = _directives(root)
    type_entities = _type_entities(root)
    top_level = _top_level_entities(root)
    entities = [*type_entities, *top_level]

    exports_abstract = [
        node_text(name)
        for child in root.named_children
        if child.type in _CLASS_NODES
        if _is_abstract_container(child)
        if (name := child.child_by_field_name("name")) is not None
        if node_text(name)
    ]
    extends = unique_strings(iter(
        _extends_of(child)
        for child in root.named_children
        if child.type in _CLASS_NODES | _EXTENSION_NODES
    ))
    implements = unique_strings(iter(
        _simple_type_name(item)
        for child in root.named_children
        if child.type in _CLASS_NODES
        for item in _implemented_types(child)
    ))

    entity_payloads = [entity.to_dict() for entity in entities]
    imported_names = _imported_names(directives)
    exported_names = unique_strings(iter(
        [
            *(entity.name for entity in entities),
            *_exported_names(root, directives),
        ]
    ))
    return FileResult(
        path=path,
        language="dart",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=unique_strings(iter(
            [
                *(directive.uri for directive in directives
                  if directive.kind in ("import", "export")),
                *_part_directive_imports(directives),
            ]
        )),
        exports_abstract=unique_strings(iter(exports_abstract)),
        implements=implements,
        extends=extends,
        purpose=infer_purpose(
            path,
            content,
            "dart",
            [entity for entity in entity_payloads if entity["kind"] != "type"],
        ),
        naming_convention=detect_naming(path),
        has_validation=_has_validation(content),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=exported_names,
    )


def _extends_of(node) -> str:
    """The extended type of a class, a mixin application or an extension.

    class_definition carries the extended type as the first type_identifier of
    its superclass field; `class A = B with M` keeps it inside
    mixin_application_class instead; an extension declares the type it
    extends in its `class` field.
    """
    if node.type == "extension_declaration":
        extension_type = node.child_by_field_name("class")
        return _simple_type_name(extension_type) if extension_type is not None else ""
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        return next(
            (
                _simple_type_name(child)
                for child in superclass.named_children
                if child.type == "type_identifier"
            ),
            "",
        )
    application = next(
        (
            child
            for child in node.named_children
            if child.type == "mixin_application_class"
        ),
        None,
    )
    return _mixin_application_bases(application)[0] if application is not None and _mixin_application_bases(application) else ""


def _implemented_types(node) -> list:
    """The `implements` types of a class or mixin.

    The interfaces field is set on a class and empty on a mixin_declaration,
    where it is only a named child, so both places are read.
    """
    blocks = [
        block
        for block in (node.child_by_field_name("interfaces"),)
        if block is not None
    ]
    blocks.extend(
        child for child in node.named_children if child.type == "interfaces"
    )
    seen: list = []
    for block in blocks:
        for child in block.named_children:
            if child.type == "type_identifier" and child not in seen:
                seen.append(child)
    return seen


def _has_validation(content: str) -> bool:
    """True for a file that declares a validator, a form, or a validator rule."""
    return bool(re.search(
        r"\bFormState\b|\bTextFormField\b|\bvalidator\s*:"
        r"|\bInputFormatter\b|\bRegisterValidators\b"
        r"|\bYupValidator\b|\bFieldValidator\b",
        content,
    ))


__all__ = ["DartDirective", "parse_dart_treesitter"]