"""Validation evidence of a Python file, read from its syntax tree.

A file is a validation module when it defines a validator with a library that
checks values at run time: a class whose base is a pydantic model class, a
pydantic validator decorator, a pydantic schema call, or an attrs field given a
validator. Every such name must be bound by a module-level import of the
library and by nothing else in the file: a name that a parameter, an
assignment, a definition or another import also binds is left out, because the
tree alone does not say which one a use means. Comments, strings and type-only
declarations (TypedDict, NamedTuple, dataclasses) are never evidence.
"""
from __future__ import annotations

import ast

from ..interface import BindingPayload
from .symbols import _module_statements

_LIBRARIES = frozenset({"pydantic", "pydantic_settings", "attr", "attrs"})
_BASES = frozenset({
    "pydantic.BaseModel",
    "pydantic.RootModel",
    "pydantic.BaseSettings",
    "pydantic.generics.GenericModel",
    "pydantic.v1.BaseModel",
    "pydantic.v1.BaseSettings",
    "pydantic_settings.BaseSettings",
})
_DECORATORS = frozenset({
    "pydantic.validator",
    "pydantic.field_validator",
    "pydantic.model_validator",
    "pydantic.root_validator",
    "pydantic.validate_call",
    "pydantic.validate_arguments",
    "pydantic.dataclasses.dataclass",
    "pydantic.v1.validator",
    "pydantic.v1.root_validator",
    "pydantic.v1.validate_arguments",
    "pydantic.v1.dataclasses.dataclass",
})
_CALLS = frozenset({
    "pydantic.TypeAdapter",
    "pydantic.create_model",
    "pydantic.v1.create_model",
})
# attrs fields: evidence only when the call passes validator=...
_FIELD_CALLS = frozenset({"attr.ib", "attr.attrib", "attr.field", "attrs.field"})

def _library_names(bindings: list[BindingPayload]) -> dict[str, str]:
    """Local name -> qualified name, for the names module-level imports of a validation library bind.

    A later import of the same local name from another module takes the name away, and a
    later `from m import *` takes every name away: it may bind any of them again.
    """
    names: dict[str, str] = {}
    for binding in bindings:
        module, local = binding["module"], binding["local"]
        if local == "*":
            names.clear()
        elif module.split(".")[0] in _LIBRARIES:
            names[local] = f"{module}.{binding['name']}" if binding["name"] else module
        else:
            names.pop(local, None)
    return names


def _qualified(expression: ast.expr, names: dict[str, str]) -> tuple[ast.Name, str] | None:
    """The head name and the qualified name of a name, an attribute chain, a call or a subscript of one."""
    current = expression
    attributes: list[str] = []
    while isinstance(current, (ast.Call, ast.Subscript, ast.Attribute)):
        if isinstance(current, ast.Attribute):
            attributes.append(current.attr)
            current = current.value
        elif attributes:
            return None  # an attribute of a call result or of a subscript is not a library name
        else:
            current = current.func if isinstance(current, ast.Call) else current.value
    if not isinstance(current, ast.Name) or current.id not in names:
        return None
    return current, ".".join([names[current.id], *reversed(attributes)])


def _has_validator(call: ast.Call) -> bool:
    return any(
        keyword.arg == "validator" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
        for keyword in call.keywords
    )


def validation_evidence(tree: ast.Module, bindings: list[BindingPayload]) -> str:
    """The first validator the file defines, as "<kind> <qualified name>"; "" when it defines none.

    bindings: the module-level import bindings of the file.
    """
    names = _library_names(bindings)
    if not names:
        return ""
    module_imports = {
        id(statement) for statement in _module_statements(tree.body)
        if isinstance(statement, (ast.Import, ast.ImportFrom))
    }
    # One walk: the names bound by anything but a module-level import (definitions, parameters,
    # assignment and loop targets, `as` names, pattern captures, imports inside a function or a
    # class), and the places where a validator can be defined.
    bound: set[str] = set()
    places: list[tuple[ast.expr, str, frozenset[str], str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                bound.add(node.id)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            if isinstance(node, ast.ClassDef):
                places.extend((base, "base", _BASES, "") for base in node.bases)
            places.extend((decorator, "decorator", _DECORATORS, "") for decorator in node.decorator_list)
        elif isinstance(node, ast.Call):
            places.append((node.func, "call", _CALLS, ""))
            if _has_validator(node):
                places.append((node.func, "call", _FIELD_CALLS, " validator="))
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if id(node) not in module_imports:
                bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.MatchMapping):
            if node.rest:
                bound.add(node.rest)
    names = {local: qualified for local, qualified in names.items() if local not in bound}
    found: list[tuple[int, int, str]] = []
    for expression, kind, wanted, suffix in places:
        resolved = _qualified(expression, names)
        if resolved is not None and resolved[1] in wanted:
            found.append((resolved[0].lineno, resolved[0].col_offset, f"{kind} {resolved[1]}{suffix}"))
    return min(found)[2] if found else ""


__all__ = ["validation_evidence"]
