"""Validation evidence of a C# file, read from its syntax tree.

A file is a validation module when it carries a validation attribute of
System.ComponentModel.DataAnnotations, or a base type ValidationAttribute,
IValidatableObject or FluentValidation's AbstractValidator. The name is
written in full (or through a using alias), or it stands together with the
using directive of its namespace; the evidence then names that directive,
because a using directive binds every type of a namespace and a type of the
file's own namespace would win over it. Comments, strings, the using alone and
attributes that state no rule (Key, Display) are never evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..treesitter import node_text, walk_named

if TYPE_CHECKING:
    from tree_sitter import Node

_ANNOTATIONS = "System.ComponentModel.DataAnnotations"
_FLUENT = "FluentValidation"
_RULE_ATTRIBUTES = frozenset({
    "AllowedValues", "Base64String", "Compare", "CreditCard", "CustomValidation", "DeniedValues", "EmailAddress",
    "FileExtensions", "Length", "MaxLength", "MinLength", "Phone", "Range", "RegularExpression", "Required",
    "StringLength", "Url",
})
_BASES = {
    "ValidationAttribute": _ANNOTATIONS,
    "IValidatableObject": _ANNOTATIONS,
    "AbstractValidator": _FLUENT,
}
_DECLARATIONS = frozenset({
    "class_declaration", "interface_declaration", "struct_declaration", "record_declaration",
    "record_struct_declaration", "enum_declaration", "delegate_declaration",
})


def _written_name(node: Node | None) -> str:
    """A type name as written, generic arguments left out."""
    if node is None:
        return ""
    if node.type == "generic_name":
        return node_text(node.named_children[0]) if node.named_children else ""
    if node.type == "qualified_name":
        qualifier = _written_name(node.child_by_field_name("qualifier"))
        name = _written_name(node.child_by_field_name("name"))
        return f"{qualifier}.{name}" if qualifier and name else ""
    return node_text(node) if node.type == "identifier" else ""


def _evidence(
    written: str, namespace_of: dict[str, str], is_attribute: bool,
    namespaces: frozenset[str], aliases: dict[str, str], declared: frozenset[str],
) -> str:
    if not written:
        return ""
    qualifier, _, name = written.rpartition(".")
    rule = name.removesuffix("Attribute") if is_attribute and name not in namespace_of else name
    namespace = namespace_of.get(rule)
    if namespace is None:
        return ""
    if qualifier:
        first, _, rest = qualifier.partition(".")
        full = ".".join(part for part in (aliases.get(first, first), rest) if part)
        return f"{namespace}.{rule}" if full == namespace else ""
    if name in declared or f"{name}Attribute" in declared or namespace not in namespaces:
        return ""
    return f"{rule} with using {namespace}"


def validation_evidence(root: Node, content: str, namespaces: frozenset[str], aliases: dict[str, str]) -> str:
    """The first validation attribute or base type of the file; "" when it has none.

    namespaces: the namespaces of the using directives. aliases: alias -> namespace
    of the `using A = N;` directives. content is read only to skip the walk of a
    file that cannot hold evidence: every form names one of the two namespaces.
    """
    if "DataAnnotations" not in content and _FLUENT not in content:
        return ""
    declared = frozenset(
        node_text(node.child_by_field_name("name")) for node in walk_named(root) if node.type in _DECLARATIONS
    )
    attributes = {name: _ANNOTATIONS for name in _RULE_ATTRIBUTES}
    for node in walk_named(root):
        if node.type == "attribute":
            found = _evidence(
                _written_name(node.child_by_field_name("name")), attributes, True, namespaces, aliases, declared,
            )
            if found:
                return f"attribute {found}"
        elif node.type == "base_list":
            for base in node.named_children:
                found = _evidence(_written_name(base), _BASES, False, namespaces, aliases, declared)
                if found:
                    return f"base {found}"
    return ""


__all__ = ["validation_evidence"]
