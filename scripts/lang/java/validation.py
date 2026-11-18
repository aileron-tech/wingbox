"""Validation evidence of a Java file, read from its syntax tree.

A file is a validation module when an annotation or a supertype belongs to
Bean Validation (jakarta.validation, javax.validation) or to Spring's
validation package: written with the package, named through a single-type
import, or, for a known name, used together with a wildcard import of the
package (the evidence then names that import, because a type of the file's own
package would win over it). Comments, strings, the import alone and an
annotation of the same simple name from another package are never evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..treesitter import node_text, walk_named

if TYPE_CHECKING:
    from tree_sitter import Node

_PACKAGES = ("jakarta.validation.", "javax.validation.", "org.springframework.validation.")
# Names a wildcard import of a validation package is trusted for.
_KNOWN_NAMES = frozenset({
    "AssertFalse", "AssertTrue", "DecimalMax", "DecimalMin", "Digits", "Email", "Future", "FutureOrPresent",
    "Max", "Min", "Negative", "NegativeOrZero", "NotBlank", "NotEmpty", "NotNull", "Null", "Past",
    "PastOrPresent", "Pattern", "Positive", "PositiveOrZero", "Size",
    "Valid", "Validated", "Constraint", "ConstraintValidator", "Validator",
})
_SUPERTYPES = frozenset({"superclass", "super_interfaces", "extends_interfaces"})
_DECLARATIONS = frozenset({
    "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
    "annotation_type_declaration",
})


def _resolved(name: str, explicit: dict[str, str], wildcards: list[str], declared: frozenset[str]) -> str:
    """The evidence name of a type name as written, "" when it is not a validation type."""
    if "." in name:
        return name if name.startswith(_PACKAGES) else ""
    qualified = explicit.get(name)
    if qualified is not None:
        return qualified if qualified.startswith(_PACKAGES) else ""
    if name in declared or name not in _KNOWN_NAMES:
        return ""
    package = next((package for package in wildcards if f"{package}.".startswith(_PACKAGES)), "")
    return f"{name} with import {package}.*" if package else ""


def validation_evidence(root: Node, content: str, explicit: dict[str, str], wildcards: list[str]) -> str:
    """The first validation annotation or supertype of the file; "" when it has none.

    explicit: simple name -> qualified name of the single-type imports.
    wildcards: the packages imported with `.*`. content is read only to skip the
    walk of a file that cannot hold evidence: every form names a validation package.
    """
    if ".validation" not in content:
        return ""
    declared = frozenset(
        node_text(node.child_by_field_name("name")) for node in walk_named(root) if node.type in _DECLARATIONS
    )
    for node in walk_named(root):
        if node.type in ("marker_annotation", "annotation"):
            found = _resolved(node_text(node.child_by_field_name("name")), explicit, wildcards, declared)
            if found:
                return f"annotation {found}"
        elif node.type in _SUPERTYPES:
            for descendant in walk_named(node):
                if descendant.type not in ("type_identifier", "scoped_type_identifier"):
                    continue
                if descendant.parent is not None and descendant.parent.type == "scoped_type_identifier":
                    continue  # a segment of a qualified name, read with the whole name
                if any(ancestor.type == "type_arguments" for ancestor in _ancestors(descendant, node)):
                    continue  # a type argument is not a supertype
                found = _resolved(node_text(descendant), explicit, wildcards, declared)
                if found:
                    return f"base {found}"
    return ""


def _ancestors(node: Node, stop: Node) -> list[Node]:
    found: list[Node] = []
    current = node.parent
    while current is not None and current != stop:
        found.append(current)
        current = current.parent
    return found


__all__ = ["validation_evidence"]
