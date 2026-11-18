"""Validation evidence of a Go file, read from its syntax tree.

A file is a validation module when a struct field tag carries the key
`validate` (go-playground/validator) or `binding` (gin), or when it calls
`New` of an imported github.com/go-playground/validator package. Comments,
strings outside a field tag and the import alone are never evidence.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..treesitter import node_text, walk_named

if TYPE_CHECKING:
    from tree_sitter import Node

_VALIDATOR_PACKAGE = re.compile(r"^github\.com/go-playground/validator(?:/v\d+)?$")
# A struct tag is a list of key:"value" pairs separated by spaces.
_TAG_KEY = re.compile(r'(?:^|\s)(validate|binding):"')


def _package_name(import_path: str) -> str:
    """The name an import binds without an alias: the last element, a /vN version suffix left out."""
    parts = import_path.split("/")
    if len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts.pop()
    return parts[-1]


def validation_evidence(root: Node, content: str, import_specs: list[tuple[str, str]]) -> str:
    """The first validation rule or validator of the file; "" when it has none.

    import_specs: (import path, alias) of every import of the file. content is
    read only to skip the walk of a file that cannot hold evidence.
    """
    packages = {
        alias or _package_name(import_path): import_path
        for import_path, alias in import_specs
        if alias not in ("_", ".") and _VALIDATOR_PACKAGE.match(import_path)
    }
    if not packages and "validate:" not in content and "binding:" not in content:
        return ""
    for node in walk_named(root):
        if node.type == "field_declaration":
            tag = node.child_by_field_name("tag")
            content = node_text(tag)[1:-1] if tag is not None else ""
            if tag is not None and tag.type == "interpreted_string_literal":
                content = content.replace('\\"', '"')
            key = _TAG_KEY.search(content)
            if key is not None:
                return f"tag {key.group(1)}"
        elif node.type == "call_expression" and packages:
            function = node.child_by_field_name("function")
            if function is None or function.type != "selector_expression":
                continue
            operand = function.child_by_field_name("operand")
            field = function.child_by_field_name("field")
            if operand is None or operand.type != "identifier" or node_text(field) != "New":
                continue
            import_path = packages.get(node_text(operand))
            if import_path is not None:
                return f"call {node_text(operand)}.New from {import_path}"
    return ""


__all__ = ["validation_evidence"]
