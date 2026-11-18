"""Validation evidence of a Ruby file, read from its syntax tree.

Ruby binds no library names in a file, so the evidence is the construct
itself: the body of a class or module (or the `included do ... end` block of a
concern) calls `validates`, `validate` or a `validates_...` method without a
receiver (the declarations of ActiveModel::Validations). The evidence names the
method and the class.
Comments, strings, a call inside a method and a call on a receiver are never
evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..treesitter import node_text, walk_named

if TYPE_CHECKING:
    from tree_sitter import Node


def _is_declaration(method: str) -> bool:
    return method in ("validate", "validates") or method.startswith("validates_")


def _statements(body: Node | None) -> list[Node]:
    """The statements of a class or module body, with those of an `included do ... end` block of a concern."""
    found: list[Node] = []
    for statement in body.named_children if body is not None else []:
        found.append(statement)
        if (
            statement.type == "call"
            and statement.child_by_field_name("receiver") is None
            and node_text(statement.child_by_field_name("method")) == "included"
        ):
            block = statement.child_by_field_name("block")
            found.extend(_statements(block.child_by_field_name("body") if block is not None else None))
    return found


def validation_evidence(root: Node, content: str) -> str:
    """The first validation declaration in a class or module body; "" when there is none.

    content is read only to skip the walk of a file that cannot hold evidence.
    """
    if "validate" not in content:
        return ""
    for node in walk_named(root):
        if node.type not in ("class", "module"):
            continue
        for statement in _statements(node.child_by_field_name("body")):
            if statement.type != "call" or statement.child_by_field_name("receiver") is not None:
                continue
            method = node_text(statement.child_by_field_name("method"))
            arguments = statement.child_by_field_name("arguments")
            has_input = (arguments is not None and arguments.named_child_count > 0) or (
                statement.child_by_field_name("block") is not None
            )
            if _is_declaration(method) and has_input:
                return f"call {method} in {node_text(node.child_by_field_name('name'))}"
    return ""


__all__ = ["validation_evidence"]
