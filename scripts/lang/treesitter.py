from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node


def node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def walk_named(node: Node) -> Iterator[Node]:
    """node and every named node below it, each before the nodes below it, in source order. What is
    still to visit is kept in a list, not on the call stack: a chain of thousands of calls is a tree
    that deep."""
    todo = [node]
    while todo:
        current = todo.pop()
        yield current
        todo.extend(reversed(current.named_children))


def unique_strings(values: Iterator[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
