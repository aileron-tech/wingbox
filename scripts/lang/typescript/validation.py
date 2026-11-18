"""Validation evidence of a TypeScript or JavaScript file, read from its syntax tree.

A file is a validation module when it calls a name that an import, or a
top-level `require`, of a schema validation library binds: `z.object(...)`,
`Joi.object(...)`, `object(...)` of yup, `new Ajv()`. A name shadowed by a
parameter or a local variable is not the imported one. Comments, strings,
type-only uses (`z.infer<...>`) and method names such as `.parse(` are never
evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..interface import BindingPayload, UsePayload
from ..treesitter import node_text
from .symbols import use_sites

if TYPE_CHECKING:
    from tree_sitter import Language, Node

# Package names; a subpath (zod/v4, ajv/dist/2020) belongs to its package.
_LIBRARIES = frozenset({"zod", "joi", "@hapi/joi", "yup", "ajv"})


def _library(specifier: str) -> str:
    """The specifier when it names a validation library or a subpath of one, else ""."""
    parts = specifier.split("/")
    package = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    return specifier if package in _LIBRARIES else ""


def _required(root: Node) -> dict[str, str]:
    """Local name -> specifier for top-level `const x = require("lib")` and `const { a, b: c } = require("lib")`."""
    names: dict[str, str] = {}
    for statement in root.named_children:
        if statement.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in statement.named_children:
            value = declarator.child_by_field_name("value") if declarator.type == "variable_declarator" else None
            if value is None or value.type != "call_expression":
                continue
            function = value.child_by_field_name("function")
            arguments = value.child_by_field_name("arguments")
            if function is None or function.type != "identifier" or node_text(function) != "require" or arguments is None:
                continue
            values = arguments.named_children
            if len(values) != 1 or values[0].type != "string":
                continue
            specifier = node_text(values[0]).strip("'\"`")
            target = declarator.child_by_field_name("name")
            if target is None:
                continue
            if target.type == "identifier":
                names[node_text(target)] = specifier
            elif target.type == "object_pattern":
                for part in target.named_children:
                    if part.type == "shorthand_property_identifier_pattern":
                        names[node_text(part)] = specifier
                    elif part.type == "pair_pattern":
                        local = part.child_by_field_name("value")
                        if local is not None and local.type == "identifier":
                            names[node_text(local)] = specifier
    return names


_Found = tuple[int, int, str]


def _calls(uses: list[UsePayload], libraries: dict[str, str]) -> list[_Found]:
    found: list[_Found] = []
    for use in uses:
        path = use["path"]
        if "member" in use or "module" in use or use.get("kind") != "call" or not path or path[0] not in libraries:
            continue
        found.append((use["at"][0][0], use["at"][0][1], f"call {'.'.join(path)} from {libraries[path[0]]}"))
    return found


def validation_evidence(root: Node, bindings: list[BindingPayload], language: Language) -> str:
    """The first call of a validation library's name, as "call <name as written> from <library>"; "" when none."""
    libraries: dict[str, str] = {}
    for local, specifier in _required(root).items():
        if _library(specifier):
            libraries[local] = specifier
    for binding in bindings:
        if binding.get("scope") and binding["local"] != "*" and _library(binding["module"]):
            libraries[binding["local"]] = binding["module"]
    if not libraries:
        return ""
    wanted = frozenset(libraries)
    # Calls through a member chain (z.object) come from the short pass of the use collector; a bare
    # call of an imported name (object(...) of yup, new Ajv()) needs the full pass.
    found = _calls(use_sites(root, wanted, language, full=False, namespaces=wanted), libraries)
    if not found:
        found = _calls(use_sites(root, wanted, language), libraries)
    return min(found)[2] if found else ""


__all__ = ["validation_evidence"]
