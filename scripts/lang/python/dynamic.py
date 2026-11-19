"""Modules that a Python file loads at run time, read from its syntax tree.

Three forms are recognised; each one is a pure function of the tree:

- a literal module name given to ``importlib.import_module`` or ``__import__``:
  an ordinary import, returned as an import string;
- a module name made of a literal package path and one variable
  (``f".plugins.{name}"`` or ``"app.plugins." + name``): a load pattern with
  ``{}`` in place of the variable; the graph fills it from configuration files;
- a method called, inside a loop, on an object that a registry module returns
  for the loop variable: a registry call, resolved by the graph to the modules
  that registry loads.
"""
from __future__ import annotations

import ast
from typing import Iterator, Optional

from ..interface import ModuleLoadPayload, RegistryCallPayload
from .tree import TreeIndex, index_tree

PLACEHOLDER = "{}"
_ASSIGN = (ast.Assign,)
_CALL = (ast.Call,)
_LOOPS = (ast.For, ast.AsyncFor)


def find_module_loads(
    tree: ast.AST,
    module_of_name: dict[str, str],
    index: Optional[TreeIndex] = None,
) -> tuple[list[str], list[ModuleLoadPayload]]:
    """Literal import strings and load patterns of every importlib call in the tree.

    index: the walk of the tree, when the caller has one (tree.index_tree)."""
    literal_imports: list[str] = []
    patterns: list[ModuleLoadPayload] = []
    for node in (index_tree(tree) if index is None else index).walk(_CALL):
        assert isinstance(node, ast.Call)
        if not node.args:
            continue
        loader = _loader_kind(node.func, module_of_name)
        if loader is None:
            continue
        package = _literal_package(node, loader)
        name = _module_name_form(node.args[0])
        if name is None:
            continue
        if loader == "__import__" and name.startswith("."):
            continue
        absolute = _absolute(name, package)
        if PLACEHOLDER not in absolute:
            if absolute.strip("."):
                literal_imports.append(absolute)
        elif _is_package_pattern(absolute):
            patterns.append({"pattern": absolute, "line": node.lineno})
    return literal_imports, patterns


def _loader_kind(func: ast.expr, module_of_name: dict[str, str]) -> Optional[str]:
    if isinstance(func, ast.Name):
        if func.id == "__import__":
            return "__import__"
        if module_of_name.get(func.id) == "importlib.import_module":
            return "import_module"
        return None
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and module_of_name.get(func.value.id) == "importlib"
    ):
        return "import_module"
    return None


def _literal_package(call: ast.Call, loader: str) -> str:
    """The package argument of import_module when it is a literal string."""
    if loader != "import_module":
        return ""
    candidates = list(call.args[1:2]) + [k.value for k in call.keywords if k.arg == "package"]
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
    return ""


def _module_name_form(node: ast.expr) -> Optional[str]:
    """The module name as a string, with ``{}`` for one variable part; None otherwise."""
    parts = _string_parts(node)
    if parts is None:
        return None
    variables = sum(1 for part in parts if part is None)
    literal_text = "".join(part for part in parts if part is not None)
    if variables > 1 or "{" in literal_text or "}" in literal_text:
        return None
    return "".join(PLACEHOLDER if part is None else part for part in parts)


def _string_parts(node: ast.expr) -> Optional[list[Optional[str]]]:
    """Literal pieces of a string expression; None marks a variable piece."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[Optional[str]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and value.format_spec is None and value.conversion == -1:
                parts.append(None)
            else:
                return None
        return parts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_parts(node.left)
        right = _string_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return [None]
    return None


def _absolute(name: str, package: str) -> str:
    """Resolve a relative name against a literal package; keep it relative without one."""
    level = len(name) - len(name.lstrip("."))
    if not level or not package:
        return name
    base = package.split(".")
    if level - 1 >= len(base):
        return name
    base = base[: len(base) - (level - 1)]
    return ".".join(base + [name[level:]]) if name[level:] else ".".join(base)


def _is_package_pattern(pattern: str) -> bool:
    """The variable fills the last segment, below at least one named package."""
    package, _, last = pattern.rpartition(".")
    if PLACEHOLDER not in last or PLACEHOLDER in package:
        return False
    return any(segment for segment in package.lstrip(".").split("."))


def find_registry_calls(
    tree: ast.Module,
    import_targets: dict[str, list[str]],
    index: Optional[TreeIndex] = None,
) -> list[RegistryCallPayload]:
    """Method calls on objects that an imported registry returns for a loop variable.

    ``import_targets`` maps each local name bound by an import to the import
    strings that may name its module, most specific first. index: the walk of
    the tree, when the caller has one (tree.index_tree).
    """
    calls: list[RegistryCallPayload] = []
    if not import_targets:
        return calls  # a registry is an imported name: without one no object comes from a registry
    if index is None:
        index = index_tree(tree)
    if not any(index.by_class.get(loop) for loop in _LOOPS):
        return calls
    for entity_name, function in _functions(tree):
        start = index.position_of(function)
        loops = [loop for loop in index.walk(_LOOPS, start) if isinstance(loop.target, ast.Name)]  # type: ignore[attr-defined]
        if not loops:
            continue
        bound = dict(import_targets)
        for node in index.walk(_ASSIGN, start):
            assert isinstance(node, ast.Assign)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                root = _root_name(node.value) if isinstance(node.value, ast.Call) else None
                if root in import_targets:
                    bound[node.targets[0].id] = import_targets[root]
        for loop in loops:
            assert isinstance(loop, (ast.For, ast.AsyncFor)) and isinstance(loop.target, ast.Name)
            calls.extend(_loop_calls(entity_name, loop, loop.target.id, bound, index))
    return calls


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Top-level functions and the methods of top-level classes, with the top-level name."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield node.name, item


def _loop_calls(
    entity_name: str,
    loop: ast.For | ast.AsyncFor,
    variable: str,
    bound: dict[str, list[str]],
    index: TreeIndex,
) -> list[RegistryCallPayload]:
    looked_up: dict[str, list[str]] = {}

    def source_of(expr: ast.expr) -> Optional[list[str]]:
        if isinstance(expr, ast.Name):
            return looked_up.get(expr.id)
        if isinstance(expr, ast.Call):
            called = source_of(expr.func)
            if called is not None:
                return called
            root = _root_name(expr.func)
            if root in bound and _passes(expr, variable):
                return bound[root]
        return None

    # The statements of the loop body, each walked as ast.walk does, one after the other: the body
    # statements are the children of the loop after its target and its iterable.
    statements = index.children(index.position_of(loop))[2:2 + len(loop.body)]
    assigns: list[ast.Assign] = [
        node for statement in statements for node in index.walk(_ASSIGN, statement)  # type: ignore[misc]
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)  # type: ignore[attr-defined]
    ]
    for _ in range(3):
        for node in assigns:
            found = source_of(node.value)
            if found is not None:
                looked_up[node.targets[0].id] = found  # type: ignore[attr-defined]

    folds = {
        id(node.value)
        for node in assigns
        if isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and _passes(node.value, node.targets[0].id)
    }
    calls: list[RegistryCallPayload] = []
    for statement in statements:
        for node in index.walk(_CALL, statement):
            assert isinstance(node, ast.Call)
            if isinstance(node.func, ast.Attribute):
                modules = source_of(node.func.value)
                if modules is not None:
                    calls.append({
                        "modules": list(modules),
                        "method": node.func.attr,
                        "source_entity": entity_name,
                        "line": node.lineno,
                        "fold": id(node) in folds,
                    })
    return calls


def _passes(call: ast.Call, name: str) -> bool:
    """Whether the call takes the bare name as an argument."""
    values = list(call.args) + [keyword.value for keyword in call.keywords]
    return any(isinstance(value, ast.Name) and value.id == name for value in values)


def _root_name(expr: ast.expr) -> Optional[str]:
    while isinstance(expr, (ast.Attribute, ast.Call, ast.Subscript)):
        expr = expr.func if isinstance(expr, ast.Call) else expr.value
    return expr.id if isinstance(expr, ast.Name) else None
