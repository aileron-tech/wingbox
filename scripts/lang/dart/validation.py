"""Validation evidence of a Dart file, read from its syntax tree.

Dart imports bind no single names, so the evidence is the construct itself: a
call that passes the named argument `validator` (a Flutter form field takes its
validator this way). The evidence names the callee. Comments, strings, a
`validator: null`, a type such as FormState and an input formatter are never
evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..treesitter import node_text

if TYPE_CHECKING:
    from tree_sitter import Node


def _callee(named_argument: Node) -> str:
    """The name called with the argument list that holds the named argument, as written; "" when it is no name.

    A call is a name followed by selectors (`Field(...)`, `ui.Field(...)`, `Field.named(...)`);
    `const Field(...)` and `new Field(...)` hold the type beside the arguments.
    """
    arguments = named_argument.parent
    if arguments is None or arguments.type != "arguments":
        return ""
    owner = arguments.parent
    if owner is None:
        return ""
    if owner.type in ("const_object_expression", "new_expression"):
        return ".".join(
            node_text(child) for child in owner.named_children if child.type in ("type_identifier", "identifier")
        )
    selector = owner.parent if owner.type == "argument_part" else None
    if selector is None or selector.type != "selector":
        return ""
    parts: list[str] = []
    previous = selector.prev_named_sibling
    while previous is not None and previous.type == "selector":
        member = previous.named_children[0] if previous.named_child_count == 1 else None
        name = member.named_children[0] if member is not None and member.named_child_count == 1 else None
        if member is None or member.type != "unconditional_assignable_selector" or name is None or name.type != "identifier":
            return ".".join(reversed(parts))  # the result of a call or an index: only the names after it
        parts.append(node_text(name))
        previous = previous.prev_named_sibling
    if previous is not None and previous.type in ("identifier", "type_identifier"):
        parts.append(node_text(previous))
    return ".".join(reversed(parts))


def _descendants(node: Node) -> list[Node]:
    found: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(reversed(current.named_children))
    return found


def validation_evidence(root: Node, content: str) -> str:
    """The first call that is given a validator, as "argument validator of <callee>"; "" when there is none.

    content is read only to skip the walk of a file that cannot hold evidence.
    """
    if "validator" not in content:
        return ""
    for node in _descendants(root):
        if node.type != "named_argument":
            continue
        label = next((child for child in node.named_children if child.type == "label"), None)
        if label is None or node_text(label).rstrip(":").strip() != "validator":
            continue
        values = [child for child in node.named_children if child.type != "label"]
        if not values or values[0].type == "null_literal":
            continue
        callee = _callee(node)
        if callee:
            return f"argument validator of {callee}"
    return ""


__all__ = ["validation_evidence"]
