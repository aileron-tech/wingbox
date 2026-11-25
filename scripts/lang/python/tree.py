"""One walk of a Python syntax tree, shared by every reader of the file.

index_tree visits each node once and keeps the nodes in preorder (a node, then
its children in field order), the depth of each and where its subtree ends.
The nodes below any node are then a slice, and the order in which ``ast.walk``
yields them (level by level) is that slice sorted by depth: nodes of one level
come in the same order in both. The readers that used to walk the whole tree,
or the subtree of every function, again (imports, run-time module loads,
registry calls, the names a function or class body binds) take the nodes of the
classes they need from the index instead.

The context and operator nodes (``Load``, ``Store``, ``Add``, ...) are left
out: they have no children and no reader asks for them.
"""
from __future__ import annotations

import ast
from bisect import bisect_left
from typing import Iterable, Optional, Sequence

_AST = ast.AST
_LEAF_FIELDS = frozenset({"ctx", "op", "ops"})
# class -> its fields that may hold nodes. A name, a constant and an import alias hold none; they are half the tree.
_NODE_FIELDS: dict[type, tuple[str, ...]] = {ast.Name: (), ast.Constant: (), ast.alias: ()}
_BODY_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# The nodes whose inside binds nothing in the scope around them.
_SCOPES = (*_BODY_SCOPES, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_NAMED = (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)
_PLACED = (*_BODY_SCOPES, ast.For, ast.AsyncFor)
_STORES = (ast.Store, ast.Del)


class TreeIndex:
    """The nodes of one tree in preorder, with what the readers ask of them."""

    __slots__ = ("nodes", "depths", "ends", "by_class", "_locals", "_imports", "_where")

    def __init__(self, nodes: list[ast.AST], depths: list[int], ends: list[int], by_class: dict[type, list[int]]) -> None:
        self.nodes = nodes
        self.depths = depths
        self.ends = ends          # ends[i]: the position after the last node below nodes[i]
        self.by_class = by_class  # class -> positions of its nodes, ascending
        self._locals: Optional[dict[int, frozenset[str]]] = None
        self._imports: dict[int, dict[str, tuple[str, str]]] = {}
        self._where: Optional[dict[int, int]] = None

    def positions(self, classes: Iterable[type], start: int = 0, end: Optional[int] = None) -> list[int]:
        """Positions of the nodes of these classes in nodes[start:end], ascending."""
        stop = len(self.nodes) if end is None else end
        found: list[int] = []
        for cls in classes:
            bucket = self.by_class.get(cls)
            if bucket:
                found.extend(bucket[bisect_left(bucket, start):bisect_left(bucket, stop)])
        found.sort()
        return found

    def walk(self, classes: Sequence[type], start: int = 0) -> list[ast.AST]:
        """The nodes of these classes at and below nodes[start], in the order ``ast.walk(nodes[start])`` yields them."""
        found = self.positions(classes, start, self.ends[start])
        found.sort(key=self.depths.__getitem__)  # stable: one level keeps its preorder, which is its walk order
        nodes = self.nodes
        return [nodes[position] for position in found]

    def children(self, position: int) -> list[int]:
        """Positions of the child nodes of nodes[position], in field order."""
        found: list[int] = []
        ends = self.ends
        child, end = position + 1, ends[position]
        while child < end:
            found.append(child)
            child = ends[child]
        return found

    def position_of(self, node: ast.AST) -> int:
        """The position of a function, a class or a for loop of the tree."""
        if self._where is None:
            nodes = self.nodes
            self._where = {id(nodes[position]): position for position in self.positions(_PLACED)}
        return self._where[id(node)]

    def body_locals(self, scope: ast.AST) -> frozenset[str]:
        """The names the body of a function or class binds directly (its nested scopes excluded), without the
        names it declares global: symbols._body_locals(scope.body), for every scope of the tree in one pass."""
        if self._locals is None:
            self._locals, self._imports = _scope_locals(self)
        return self._locals[id(scope)]

    def body_imports(self, scope: ast.AST) -> dict[str, tuple[str, str]]:
        """The names that the imports of the body of a function or class bind (its nested scopes excluded),
        each with the module as written and the imported name ("" when the module itself is bound); the
        first import of a name counts: symbols._scope_imports(scope.body), from the same pass as body_locals."""
        if self._locals is None:
            self._locals, self._imports = _scope_locals(self)
        return self._imports.get(id(scope), {})


def index_tree(tree: ast.AST) -> TreeIndex:
    nodes: list[ast.AST] = []
    depths: list[int] = []
    ends: list[int] = []
    by_class: dict[type, list[int]] = {}
    stack: list = [tree]
    depth = 0
    while stack:
        node = stack.pop()
        if node.__class__ is int:  # every node below nodes[node] is done
            ends[node] = len(nodes)
            depth -= 1
            continue
        position = len(nodes)
        nodes.append(node)
        depths.append(depth)
        ends.append(position + 1)
        cls = node.__class__
        bucket = by_class.get(cls)
        if bucket is None:
            by_class[cls] = [position]
        else:
            bucket.append(position)
        fields = _NODE_FIELDS.get(cls)
        if fields is None:
            fields = _NODE_FIELDS[cls] = tuple(field for field in cls._fields if field not in _LEAF_FIELDS)
        children: list[ast.AST] = []
        for field in fields:
            value = getattr(node, field, None)
            if value.__class__ is list:
                for item in value:
                    if isinstance(item, _AST):
                        children.append(item)
            elif isinstance(value, _AST):
                children.append(value)
        if children:
            stack.append(position)
            children.reverse()  # the first child is taken first
            stack.extend(children)
            depth += 1
    return TreeIndex(nodes, depths, ends, by_class)


def child_nodes(node: ast.AST) -> list[ast.AST]:
    """The nodes directly below node in field order, as index_tree takes them (no context or operator node)."""
    cls = node.__class__
    fields = _NODE_FIELDS.get(cls)
    if fields is None:
        fields = _NODE_FIELDS[cls] = tuple(field for field in cls._fields if field not in _LEAF_FIELDS)
    children: list[ast.AST] = []
    for field in fields:
        value = getattr(node, field, None)
        if value.__class__ is list:
            for item in value:
                if isinstance(item, _AST):
                    children.append(item)
        elif isinstance(value, _AST):
            children.append(value)
    return children


def _body_range(index: TreeIndex, position: int) -> tuple[int, int]:
    """Where the body statements of the function or class at position lie: [first, after the last)."""
    body = index.nodes[position].body  # type: ignore[attr-defined]
    if not body:
        return position, position
    nodes, ends = index.nodes, index.ends
    first = position + 1  # the children before the body are few: arguments, bases, keywords
    while nodes[first] is not body[0]:
        first = ends[first]
    last = first
    for _ in range(len(body) - 1):
        last = ends[last]
    return first, ends[last]


def _scope_locals(index: TreeIndex) -> tuple[dict[int, frozenset[str]], dict[int, dict[str, tuple[str, str]]]]:
    """id(scope node) -> the names its body binds, for every function and class of the tree; and, for the
    scopes whose body holds an import, id(scope node) -> what each name bound by an import stands for.

    One sweep over the nodes that bind a name or open a scope, in preorder. A name belongs to the nearest
    function or class around it when it lies in the body of that scope (not in its decorators, arguments or
    bases) and no lambda or comprehension stands between them.
    """
    nodes, ends = index.nodes, index.ends
    name_stores = [
        position for position in index.by_class.get(ast.Name, ())
        if nodes[position].ctx.__class__ in _STORES  # type: ignore[attr-defined]
    ]
    events = index.positions((*_SCOPES, ast.Import, ast.ImportFrom, ast.Global, *_NAMED, ast.MatchMapping))
    events.extend(name_stores)
    events.sort()
    found: dict[int, frozenset[str]] = {}
    imports: dict[int, dict[str, tuple[str, str]]] = {}
    # Open scopes, innermost last: (end, first body position, after the body, stored names, global names, node);
    # a lambda or a comprehension has no sets.
    open_scopes: list[tuple[int, int, int, Optional[set[str]], Optional[set[str]], ast.AST]] = []

    def close(until: int) -> None:
        while open_scopes and open_scopes[-1][0] <= until:
            _end, _first, _after, stored, declared, scope = open_scopes.pop()
            if stored is not None and declared is not None:
                found[id(scope)] = frozenset(stored - declared)

    for position in events:
        close(position)
        node = nodes[position]
        cls = node.__class__
        stored: Optional[set[str]] = None
        declared: Optional[set[str]] = None
        scope: Optional[ast.AST] = None
        if open_scopes:
            _end, first, after, stored, declared, scope = open_scopes[-1]
            if not first <= position < after:
                stored = declared = None
        if stored is not None and declared is not None:
            if cls is ast.Name:
                stored.add(node.id)  # type: ignore[attr-defined]
            elif cls in _BODY_SCOPES:
                stored.add(node.name)  # type: ignore[attr-defined]
            elif cls is ast.Import or cls is ast.ImportFrom:
                bound = imports.setdefault(id(scope), {})
                module = "" if cls is ast.Import else "." * node.level + (node.module or "")  # type: ignore[attr-defined]
                for alias in node.names:  # type: ignore[attr-defined]
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name.split(".")[0]
                    stored.add(local)
                    if cls is ast.ImportFrom:
                        bound.setdefault(local, (module, alias.name))
                    else:
                        bound.setdefault(local, (alias.name if alias.asname else local, ""))
            elif cls is ast.Global:
                declared.update(node.names)  # type: ignore[attr-defined]
            elif cls in _NAMED:
                if node.name:  # type: ignore[attr-defined]
                    stored.add(node.name)  # type: ignore[attr-defined]
            elif cls is ast.MatchMapping:
                if node.rest:  # type: ignore[attr-defined]
                    stored.add(node.rest)  # type: ignore[attr-defined]
        if cls in _BODY_SCOPES:
            first, after = _body_range(index, position)
            open_scopes.append((ends[position], first, after, set(), set(), node))
        elif cls in _SCOPES:
            open_scopes.append((ends[position], 0, 0, None, None, node))
    close(len(nodes))
    return found, imports


__all__ = ["TreeIndex", "child_nodes", "index_tree"]
