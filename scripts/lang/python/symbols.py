"""Python symbol table of one file: module-level variables, import bindings, use sites.

Pure functions over an ``ast`` tree. A use site is a name or an attribute chain
(``m.S``) whose first name is not bound locally: a parameter, a local variable or
any other binding of a function, lambda, comprehension or class body shadows a
module-level name of the same spelling and is not a use. A name that an import
inside a function or a class body binds is a use of what that import names.

No function here calls itself along the tree: a file with thousands of chained
calls, operands or ``elif`` branches is read like any other.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

from ..interface import BindingPayload, Entity, UsePayload
from .tree import TreeIndex, child_nodes

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)


def _module_statements(body: list[ast.stmt], loops: bool = False) -> Iterator[ast.stmt]:
    """Statements that run at module level, also inside if, try and with blocks, in source order.
    loops: also inside for, while and match blocks (every statement of a scope but those of the
    functions and classes it defines)."""
    todo = body[::-1]
    while todo:
        statement = todo.pop()
        yield statement
        blocks: list[list[ast.stmt]] = []
        if isinstance(statement, ast.If):
            blocks = [statement.body, statement.orelse]
        elif isinstance(statement, ast.Try) or type(statement).__name__ == "TryStar":
            blocks = [statement.body, *(handler.body for handler in statement.handlers),  # type: ignore[attr-defined]
                      statement.orelse, statement.finalbody]  # type: ignore[attr-defined]
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            blocks = [statement.body]
        elif loops and isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            blocks = [statement.body, statement.orelse]
        elif loops and isinstance(statement, ast.Match):
            blocks = [case.body for case in statement.cases]
        for block in reversed(blocks):
            todo.extend(reversed(block))


def _target_names(target: ast.expr) -> list[ast.Name]:
    names: list[ast.Name] = []
    todo = [target]
    while todo:
        current = todo.pop()
        if isinstance(current, ast.Name):
            names.append(current)
        elif isinstance(current, (ast.Tuple, ast.List)):
            todo.extend(reversed(current.elts))
        elif isinstance(current, ast.Starred):
            todo.append(current.value)
    return names


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
    """Module-level names bound by assignment, public or listed in __all__, as variable entities.

    The graph keeps them apart from classes and functions: the use file next to it holds
    them (`var` facts), which entity and usages read.
    """
    exported = _dunder_all(tree)
    seen = set(defined)
    variables: list[Entity] = []
    # The class each name holds when it is in sight: the one annotation (`db: Store = ...`) or the one
    # constructor call (`db = Store()`) of its assignments. A name bound in two ways has none.
    held: dict[str, set[TypePath | None]] = {}
    for statement in _module_statements(tree.body):
        names: list[ast.Name] = []
        if isinstance(statement, ast.Assign):
            names = [name for target in statement.targets for name in _target_names(target)]
            single = len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
            constructed = _type_path(statement.value.func) if single and isinstance(statement.value, ast.Call) else None
            for name in names:
                held.setdefault(name.id, set()).add(constructed)
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            names = _target_names(statement.target)
            for name in names:
                held.setdefault(name.id, set()).add(_type_path(statement.annotation))
        elif _TYPE_ALIAS is not None and isinstance(statement, _TYPE_ALIAS):
            names = _target_names(statement.name)  # type: ignore[attr-defined]
        for name in names:
            if name.id in seen or (name.id.startswith("_") and name.id not in exported):
                continue
            seen.add(name.id)
            variables.append(Entity(name=name.id, kind="variable", line=name.lineno,
                                    methods=[], decorators=[], bases=[]))
    for variable in variables:
        types = held.get(variable.name, set())
        written = next(iter(types)) if len(types) == 1 else None
        if written is not None:
            variable.value_type = ".".join(written)
    return variables


def _import_bindings(statements: Iterator[ast.stmt]) -> Iterator[tuple[str, str, str]]:
    """(local name, module as written, imported name or "" when the module itself is bound) of every
    import among the statements."""
    for statement in statements:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname:
                    yield alias.asname, alias.name, ""
                else:
                    first = alias.name.split(".")[0]
                    yield first, first, ""
        elif isinstance(statement, ast.ImportFrom):
            module = "." * statement.level + (statement.module or "")
            for alias in statement.names:
                yield alias.asname or alias.name, module, alias.name


def module_bindings(tree: ast.Module) -> list[BindingPayload]:
    """Names bound by imports at module level; importers of the file can import them in turn."""
    return [
        {"local": local, "module": module, "name": name, "export": True, "scope": True}
        for local, module, name in _import_bindings(_module_statements(tree.body))
    ]


ScopeImport = tuple[str, str]  # the module as written, the imported name ("" when the module itself is bound)


def _scope_imports(body: list[ast.stmt]) -> dict[str, ScopeImport]:
    """The names that the imports of a function or class body bind, each with what it stands for; the
    first import of a name counts, as for the imports of the module. This is the definition; a collector
    given the walk of the tree reads the same from it (TreeIndex.body_imports)."""
    found: dict[str, ScopeImport] = {}
    for local, module, name in _import_bindings(_module_statements(body, loops=True)):
        if local != "*":
            found.setdefault(local, (module, name))
    return found


def _stored_names(node: ast.AST) -> Iterator[str]:
    """Names bound directly in the scope whose body holds node (nested scopes excluded)."""
    todo = [node]
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name
            continue
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
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
        todo.extend(ast.iter_child_nodes(node))


def _declared(node: ast.AST, kind: type) -> Iterator[str]:
    todo = [node]
    while todo:
        node = todo.pop()
        if isinstance(node, kind):
            yield from node.names  # type: ignore[attr-defined]
        if not isinstance(node, _SCOPE_NODES):
            todo.extend(ast.iter_child_nodes(node))


def _arguments(args: ast.arguments) -> set[str]:
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return {arg.arg for arg in every}


def _body_locals(body: list[ast.stmt]) -> set[str]:
    """The names a function or class body binds. This is the definition; a collector given the walk of the
    tree reads the same sets from it (TreeIndex.body_locals), worked out once for every scope of the file."""
    names = {name for statement in body for name in _stored_names(statement)}
    declared_global = {name for statement in body for name in _declared(statement, ast.Global)}
    return names - declared_global


TypePath = tuple[str, ...]
_LITERALS = (ast.Constant, ast.JoinedStr, ast.List, ast.Dict, ast.Set, ast.Tuple,
             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _type_path(node: ast.expr | None) -> TypePath | None:
    """A type written as a name or a dotted name (Store, models.Store); None for anything else."""
    attributes: list[str] = []
    while isinstance(node, ast.Attribute):
        attributes.append(node.attr)
        node = node.value
    return (node.id, *reversed(attributes)) if isinstance(node, ast.Name) else None


def _local_types(function: ast.FunctionDef | ast.AsyncFunctionDef, receiver: TypePath | None) -> dict[str, TypePath | None]:
    """The class of each local name when it is in sight: an annotation, a constructor call, self.

    A name bound in two ways, or in any other way, has no type in sight (None).
    """
    found: dict[str, set[TypePath | None]] = {}
    arguments = [*function.args.posonlyargs, *function.args.args]
    for index, arg in enumerate([*arguments, *function.args.kwonlyargs]):
        written = _type_path(arg.annotation)
        if index == 0 and receiver is not None and arg in arguments and written is None:
            written = receiver
        found.setdefault(arg.arg, set()).add(written)

    todo: list[ast.AST] = list(function.body)
    while todo:
        node = todo.pop()
        if isinstance(node, _SCOPE_NODES):
            continue
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.setdefault(node.target.id, set()).add(_type_path(node.annotation))
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            constructed = _type_path(value.func) if isinstance(value, ast.Call) else None
            found.setdefault(node.targets[0].id, set()).add(constructed)
            todo.append(value)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.setdefault(node.id, set()).add(None)
        todo.extend(ast.iter_child_nodes(node))
    return {name: next(iter(types)) if len(types) == 1 else None for name, types in found.items()}


@dataclass(frozen=True)
class _Scope:
    names: frozenset[str]
    is_class: bool = False
    types: tuple[tuple[str, TypePath | None], ...] = ()
    class_name: str = ""
    imports: Mapping[str, ScopeImport] = field(default_factory=dict)  # the names of the scope that an import binds


_Scopes = tuple[_Scope, ...]
_LOCAL: ScopeImport = ("", "")  # a name a scope binds in any way but an import


class _UseCollector:
    """Walks the tree with a list of what is still to visit, never by calling itself: each entry is a node
    with how it is used (kind) and the scopes around it. A handler emits the uses of its node and asks for
    the nodes below it; they are taken in the order asked, each with everything below it before the next."""

    def __init__(
        self, wanted: frozenset[str], any_name: bool, is_package: bool = False,
        full: bool = True, imported: frozenset[str] = frozenset(), index: TreeIndex | None = None,
    ) -> None:
        self.wanted = wanted
        self.any_name = any_name
        self.is_package = is_package
        # full=False: only the uses that can name another file (a build needs no more): import
        # statements, chains headed by an imported name, names a glob import may provide.
        self.full = full
        self.imported = imported
        self.index = index
        self.scopes: _Scopes = ()
        self.uses: list[UsePayload] = []
        self.kind = ""  # how the expression being visited is used: call, type, extends; "" any other way
        self._todo: list[tuple[ast.AST, str, _Scopes]] = []

    def collect(self, tree: ast.AST) -> list[UsePayload]:
        todo = self._todo = [(tree, "", ())]
        handlers = _HANDLERS
        while todo:
            node, self.kind, self.scopes = todo.pop()
            asked = len(todo)
            handler = handlers.get(node.__class__)
            if handler is None:
                kind, scopes = self.kind, self.scopes
                todo.extend([(child, kind, scopes) for child in child_nodes(node)])
            else:
                handler(self, node)
            if len(todo) - asked > 1:
                todo[asked:] = todo[asked:][::-1]  # the first asked is the next taken
        return self.uses

    def visit(self, node: ast.AST) -> None:
        self._todo.append((node, self.kind, self.scopes))

    def _visit_as(self, node: ast.AST | None, kind: str) -> None:
        if node is not None:
            self._todo.append((node, kind, self.scopes))

    def _add(self, use: UsePayload) -> None:
        if self.kind:
            use["kind"] = self.kind
        self.uses.append(use)

    def _binding(self, name: str) -> ScopeImport | None:
        """What binds name in the scopes around: None for the module, _LOCAL for a parameter or a local
        variable, and for an import of a function or class body the module and the name it imports."""
        for scope in reversed(self.scopes):
            if name in scope.names:
                return scope.imports.get(name, _LOCAL)
        return None

    def _add_imported(self, bound: ScopeImport, head: list[int], names: list[str], at: list[list[int]]) -> None:
        """A chain headed by a name that an import of the scope binds: the same use as behind an import
        of the module, written from the module that import names."""
        module, name = bound
        path = [name, *names] if name else names
        if path:
            self._add({"module": module, "path": path, "at": [head, *at] if name else at})

    def _within(self, scope: _Scope, nodes: list) -> None:
        outer = self.scopes if scope.is_class else tuple(outer for outer in self.scopes if not outer.is_class)
        scopes = (*outer, scope)
        kind = self.kind
        self._todo.extend([(node, kind, scopes) for node in nodes])

    def _visit_arguments(self, args: ast.arguments) -> None:
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                self._visit_as(arg.annotation, "type")

    def _visit_type_parameters(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        # `class Box[T: Store]`, `def f[T: Store]()` (Python 3.12): the bound of a type parameter is a type.
        for parameter in getattr(node, "type_params", ()):
            self._visit_as(parameter, "type")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_type_parameters(node)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self._visit_as(node.returns, "type")
        # self (or cls) of a method of a class defined at module level is that class.
        owner = self.scopes[-1] if self.scopes and self.scopes[-1].is_class else None
        is_static = any(_type_path(decorator) == ("staticmethod",) for decorator in node.decorator_list)
        receiver = (owner.class_name,) if owner is not None and owner.class_name and not is_static else None
        # The types of the local names serve the method calls, which only the full list holds.
        types = tuple(sorted(_local_types(node, receiver).items())) if self.full else ()
        names = frozenset(_arguments(node.args) | self._locals(node))
        self._within(_Scope(names, types=types, imports=self._imports(node)), node.body)

    def _locals(self, scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> frozenset[str] | set[str]:
        return self.index.body_locals(scope) if self.index is not None else _body_locals(scope.body)

    def _imports(self, scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> Mapping[str, ScopeImport]:
        return self.index.body_imports(scope) if self.index is not None else _scope_imports(scope.body)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)
        self._within(_Scope(frozenset(_arguments(node.args))), [node.body])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.keywords]:
            self._visit_as(expression, "")
        for base in node.bases:
            self._visit_as(base, "extends")
        self._visit_type_parameters(node)
        class_name = node.name if not self.scopes else ""
        scope = _Scope(frozenset(self._locals(node)), is_class=True, class_name=class_name, imports=self._imports(node))
        self._within(scope, node.body)

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

    def visit_Import(self, node: ast.Import) -> None:
        return

    def _local_type(self, name: str) -> TypePath | None:
        for scope in reversed(self.scopes):
            if name in scope.names:
                return dict(scope.types).get(name)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        """recv.method(...): a use of Class.method when the receiver's class is in sight.

        Module-level names and their attributes are paths, not method calls (the index reads a member
        of a class or of a module-level instance from the path); a literal receiver has a type of the
        language, not of the repository.
        """
        function = node.func
        if self.full and isinstance(function, ast.Attribute) and not isinstance(function.value, _LITERALS):
            receiver = function.value
            head = receiver
            while isinstance(head, ast.Attribute):
                head = head.value
            bound = self._binding(head.id) if isinstance(head, ast.Name) else _LOCAL
            is_path = bound is not _LOCAL and (bound is not None or self.any_name or head.id in self.wanted)  # type: ignore[attr-defined]
            if not is_path:
                written = self._local_type(receiver.id) if isinstance(receiver, ast.Name) else None
                end_line = function.end_lineno or function.lineno
                end_col = function.end_col_offset if function.end_col_offset is not None else 0
                member_at = [end_line, end_col - len(function.attr) + 1]
                path = list(written) if written is not None else []
                # The class may be one an import of the function binds: `from m import Store` above `s = Store()`.
                imported = self._binding(path[0]) if path else None
                if imported is not None and imported is not _LOCAL:
                    path = [imported[1], *path[1:]] if imported[1] else path[1:]
                start = [receiver.lineno, receiver.col_offset + 1]
                use: UsePayload = {"path": path, "at": [start for _ in path] + [member_at], "member": function.attr,
                                   "kind": "call"}
                if imported is not None and imported is not _LOCAL:
                    use["module"] = imported[0]
                self.uses.append(use)
        self._visit_as(function, "call" if isinstance(function, (ast.Name, ast.Attribute)) else "")
        for argument in [*node.args, *node.keywords]:
            self._visit_as(argument, "")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        # An import in a package __init__.py is what importers of the package see: a re-export.
        kind = "re-export" if self.is_package else "import"
        for alias in node.names:
            if alias.name != "*" and hasattr(alias, "lineno"):
                self.uses.append({"module": module, "path": [alias.name],
                                  "at": [[alias.lineno, alias.col_offset + 1]], "kind": kind})

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_as(node.target, "")
        self._visit_as(node.annotation, "type")
        self._visit_as(node.value, "")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain: list[ast.Attribute] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            chain.append(current)
            current = current.value
        if not isinstance(current, ast.Name):
            self._visit_as(current, "")
            return
        bound = self._binding(current.id)
        if bound is _LOCAL or (bound is None and not self._wanted(current.id)):
            return
        path, at = [current.id], [[current.lineno, current.col_offset + 1]]
        for attribute in reversed(chain):
            path.append(attribute.attr)
            end_line = attribute.end_lineno or attribute.lineno
            end_col = attribute.end_col_offset if attribute.end_col_offset is not None else 0
            at.append([end_line, end_col - len(attribute.attr) + 1])
        if bound is not None:
            self._add_imported(bound, at[0], path[1:], at[1:])
        else:
            self._add({"path": path, "at": at})

    def visit_Name(self, node: ast.Name) -> None:
        # Binding a name (x = ..., del x) defines or rebinds it; it is not a use of another definition.
        if not isinstance(node.ctx, ast.Load):
            return
        bound = self._binding(node.id)
        if bound is not None:
            # The import statement itself is the use a build needs; the positions are for the full list.
            if bound is not _LOCAL and self.full:
                self._add_imported(bound, [node.lineno, node.col_offset + 1], [], [])
            return
        if not self._wanted(node.id, bare=True):
            return
        self._add({"path": [node.id], "at": [[node.lineno, node.col_offset + 1]]})

    def _wanted(self, name: str, bare: bool = False) -> bool:
        if self.full:
            return self.any_name or name in self.wanted
        if bare:
            return self.any_name and name not in self.wanted
        return name in self.imported or (self.any_name and name not in self.wanted)


_HANDLERS: dict[type, Callable[[_UseCollector, Any], None]] = {
    ast.FunctionDef: _UseCollector._visit_function, ast.AsyncFunctionDef: _UseCollector._visit_function,
    ast.Lambda: _UseCollector.visit_Lambda, ast.ClassDef: _UseCollector.visit_ClassDef,
    ast.ListComp: _UseCollector._visit_comprehension, ast.SetComp: _UseCollector._visit_comprehension,
    ast.DictComp: _UseCollector._visit_comprehension, ast.GeneratorExp: _UseCollector._visit_comprehension,
    ast.Import: _UseCollector.visit_Import, ast.ImportFrom: _UseCollector.visit_ImportFrom,
    ast.Call: _UseCollector.visit_Call, ast.AnnAssign: _UseCollector.visit_AnnAssign,
    ast.Attribute: _UseCollector.visit_Attribute, ast.Name: _UseCollector.visit_Name,
}


def use_sites(
    tree: ast.Module, module_names: frozenset[str], has_glob: bool, is_package: bool = False,
    full: bool = True, imported: frozenset[str] = frozenset(), index: TreeIndex | None = None,
) -> list[UsePayload]:
    """Names and attribute chains that refer to a module-level name of the file.

    module_names: names the module binds (definitions and imports); with a glob
    import every free name may come from it. full=False keeps only the uses that
    can name another file; imported: the names import statements bind. index:
    the walk of the tree (tree.index_tree), when the caller has one: the names
    each function and class binds are then read from it, not walked for again.
    """
    return _UseCollector(module_names, has_glob, is_package, full, imported, index).collect(tree)


__all__ = ["module_bindings", "module_variables", "use_sites"]
