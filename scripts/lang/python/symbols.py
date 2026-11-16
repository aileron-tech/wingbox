"""Python symbol table of one file: module-level variables, import bindings, use sites.

Pure functions over an ``ast`` tree. A use site is a name or an attribute chain
(``m.S``) whose first name is not bound locally: a parameter, a local variable or
any other binding of a function, lambda, comprehension or class body shadows a
module-level name of the same spelling and is not a use.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterator

from ..interface import BindingPayload, Entity, UsePayload

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)


def _module_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements that run at module level, also inside if, try and with blocks."""
    for statement in body:
        yield statement
        if isinstance(statement, ast.If):
            yield from _module_statements(statement.body)
            yield from _module_statements(statement.orelse)
        elif isinstance(statement, ast.Try) or type(statement).__name__ == "TryStar":
            yield from _module_statements(statement.body)  # type: ignore[attr-defined]
            for handler in statement.handlers:  # type: ignore[attr-defined]
                yield from _module_statements(handler.body)
            yield from _module_statements(statement.orelse)  # type: ignore[attr-defined]
            yield from _module_statements(statement.finalbody)  # type: ignore[attr-defined]
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            yield from _module_statements(statement.body)


def _target_names(target: ast.expr) -> list[ast.Name]:
    if isinstance(target, ast.Name):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _dunder_all(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for statement in _module_statements(tree.body):
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in statement.targets
        ):
            value = statement.value
        elif isinstance(statement, (ast.AugAssign, ast.AnnAssign)) and isinstance(statement.target, ast.Name) \
                and statement.target.id == "__all__":
            value = statement.value
        if isinstance(value, (ast.List, ast.Tuple)):
            names.update(element.value for element in value.elts
                         if isinstance(element, ast.Constant) and isinstance(element.value, str))
    return frozenset(names)


def module_variables(tree: ast.Module, defined: frozenset[str]) -> list[Entity]:
    """Module-level names bound by assignment, public or listed in __all__, as function entities.

    Kind "function" as TypeScript consts and Rust const/static items: one entity
    kind for every named value that is not a class.
    """
    exported = _dunder_all(tree)
    seen = set(defined)
    variables: list[Entity] = []
    for statement in _module_statements(tree.body):
        names: list[ast.Name] = []
        if isinstance(statement, ast.Assign):
            names = [name for target in statement.targets for name in _target_names(target)]
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            names = _target_names(statement.target)
        elif _TYPE_ALIAS is not None and isinstance(statement, _TYPE_ALIAS):
            names = _target_names(statement.name)  # type: ignore[attr-defined]
        for name in names:
            if name.id in seen or (name.id.startswith("_") and name.id not in exported):
                continue
            seen.add(name.id)
            variables.append(Entity(name=name.id, kind="function", line=name.lineno,
                                    methods=[], decorators=[], bases=[]))
    return variables


def module_bindings(tree: ast.Module) -> list[BindingPayload]:
    """Names bound by imports at module level; importers of the file can import them in turn."""
    bindings: list[BindingPayload] = []
    for statement in _module_statements(tree.body):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname:
                    bindings.append({"local": alias.asname, "module": alias.name, "name": "", "export": True, "scope": True})
                else:
                    first = alias.name.split(".")[0]
                    bindings.append({"local": first, "module": first, "name": "", "export": True, "scope": True})
        elif isinstance(statement, ast.ImportFrom):
            module = "." * statement.level + (statement.module or "")
            for alias in statement.names:
                bindings.append({"local": alias.asname or alias.name, "module": module,
                                 "name": alias.name, "export": True, "scope": True})
    return bindings


def _stored_names(node: ast.AST) -> Iterator[str]:
    """Names bound directly in the scope whose body holds node (nested scopes excluded)."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
        return
    if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        yield node.id
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if alias.name != "*":
                yield alias.asname or alias.name.split(".")[0]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        yield node.name
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest
    for child in ast.iter_child_nodes(node):
        yield from _stored_names(child)


def _declared(node: ast.AST, kind: type) -> Iterator[str]:
    if isinstance(node, kind):
        yield from node.names  # type: ignore[attr-defined]
    if isinstance(node, _SCOPE_NODES):
        return
    for child in ast.iter_child_nodes(node):
        yield from _declared(child, kind)


def _arguments(args: ast.arguments) -> set[str]:
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return {arg.arg for arg in every}


def _body_locals(body: list[ast.stmt]) -> set[str]:
    names = {name for statement in body for name in _stored_names(statement)}
    declared_global = {name for statement in body for name in _declared(statement, ast.Global)}
    return names - declared_global


@dataclass(frozen=True)
class _Scope:
    names: frozenset[str]
    is_class: bool = False


class _UseCollector(ast.NodeVisitor):
    def __init__(self, wanted: frozenset[str], any_name: bool) -> None:
        self.wanted = wanted
        self.any_name = any_name
        self.scopes: tuple[_Scope, ...] = ()
        self.uses: list[UsePayload] = []

    def _shadowed(self, name: str) -> bool:
        return any(name in scope.names for scope in self.scopes)

    def _within(self, scope: _Scope, nodes: list) -> None:
        saved = self.scopes
        if not scope.is_class:
            self.scopes = tuple(outer for outer in saved if not outer.is_class)
        self.scopes = (*self.scopes, scope)
        for node in nodes:
            self.visit(node)
        self.scopes = saved

    def _visit_arguments(self, args: ast.arguments) -> None:
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                self.visit(arg.annotation)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._within(_Scope(frozenset(_arguments(node.args) | _body_locals(node.body))), node.body)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)
        self._within(_Scope(frozenset(_arguments(node.args))), [node.body])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        self._within(_Scope(frozenset(_body_locals(node.body)), is_class=True), node.body)

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
        self.visit(node.generators[0].iter)
        targets = frozenset(name.id for generator in node.generators for name in _target_names(generator.target))
        inner: list[ast.AST] = []
        for index, generator in enumerate(node.generators):
            if index:
                inner.append(generator.iter)
            inner.extend(generator.ifs)
        if isinstance(node, ast.DictComp):
            inner += [node.key, node.value]
        else:
            inner.append(node.elt)
        self._within(_Scope(targets), inner)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def visit_Import(self, node: ast.Import) -> None:
        return

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            if alias.name != "*" and hasattr(alias, "lineno"):
                self.uses.append({"module": module, "path": [alias.name],
                                  "at": [[alias.lineno, alias.col_offset + 1]]})

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain: list[ast.Attribute] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            chain.append(current)
            current = current.value
        if not isinstance(current, ast.Name):
            self.visit(current)
            return
        if self._shadowed(current.id) or not (self.any_name or current.id in self.wanted):
            return
        path, at = [current.id], [[current.lineno, current.col_offset + 1]]
        for attribute in reversed(chain):
            path.append(attribute.attr)
            end_line = attribute.end_lineno or attribute.lineno
            end_col = attribute.end_col_offset if attribute.end_col_offset is not None else 0
            at.append([end_line, end_col - len(attribute.attr) + 1])
        self.uses.append({"path": path, "at": at})

    def visit_Name(self, node: ast.Name) -> None:
        if self._shadowed(node.id) or not (self.any_name or node.id in self.wanted):
            return
        self.uses.append({"path": [node.id], "at": [[node.lineno, node.col_offset + 1]]})


def use_sites(tree: ast.Module, module_names: frozenset[str], has_glob: bool) -> list[UsePayload]:
    """Names and attribute chains that refer to a module-level name of the file.

    module_names: names the module binds (definitions and imports); with a glob
    import every free name may come from it.
    """
    collector = _UseCollector(module_names, has_glob)
    collector.visit(tree)
    return collector.uses


__all__ = ["module_bindings", "module_variables", "use_sites"]
