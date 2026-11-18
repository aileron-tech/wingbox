"""Validation evidence of a PHP file, read from its syntax tree.

A file is a validation module when a class extends Laravel's FormRequest, when
it calls `make` on the Validator facade or `validate` on a parameter typed
`Illuminate\\Http\\Request`, or when it carries Livewire's `Validate`
attribute. A class name counts only when a `use` statement binds it to the
framework class or when it is written in full; class names compare without
case, as in PHP. Comments, strings, the `use` alone and a class of the same
short name from another namespace are never evidence.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..treesitter import node_text, walk_named

if TYPE_CHECKING:
    from tree_sitter import Node

_FORM_REQUEST = "Illuminate\\Foundation\\Http\\FormRequest"
_VALIDATOR = "Illuminate\\Support\\Facades\\Validator"
_REQUEST = "Illuminate\\Http\\Request"
_LIVEWIRE_VALIDATE = "Livewire\\Attributes\\Validate"
_FUNCTIONS = frozenset({
    "method_declaration", "function_definition", "anonymous_function", "anonymous_function_creation_expression",
    "arrow_function",
})
_PARAMETERS = frozenset({"simple_parameter", "property_promotion_parameter"})


class _Names:
    """Class names as written, resolved through the `use` statements of the file."""

    def __init__(self, uses: list[tuple[str, str]], has_namespace: bool) -> None:
        self._uses = {alias.casefold(): fqcn.lstrip("\\") for alias, fqcn in uses}
        self._has_namespace = has_namespace

    def resolve(self, written: str) -> str:
        """The full class name, "" when the file does not say which class the name is."""
        name = written.strip()
        if name.startswith("\\"):
            return name.lstrip("\\")
        first, _, rest = name.partition("\\")
        bound = self._uses.get(first.casefold())
        if bound is not None:
            return f"{bound}\\{rest}" if rest else bound
        return name if rest and not self._has_namespace else ""

    def names(self, written: str, target: str) -> bool:
        return self.resolve(written).casefold() == target.casefold()


def _typed_request_parameters(function: Node, names: _Names) -> set[str]:
    """Variables ($name) of the function's parameters whose declared type is Illuminate\\Http\\Request."""
    found: set[str] = set()
    parameters = function.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters is not None else []:
        declared = parameter.child_by_field_name("type") if parameter.type in _PARAMETERS else None
        if declared is None:
            continue
        if declared.type == "optional_type" and declared.named_child_count == 1:
            declared = declared.named_children[0]
        if declared.type == "named_type" and names.names(node_text(declared), _REQUEST):
            found.add(node_text(parameter.child_by_field_name("name")))
    return found


def _enclosing_function(node: Node) -> Node | None:
    current = node.parent
    while current is not None and current.type not in _FUNCTIONS:
        current = current.parent
    return current


def validation_evidence(root: Node, content: str, uses: list[tuple[str, str]]) -> str:
    """The first validator the file defines or calls; "" when it has none.

    uses: (alias, full class name) of the `use` statements of the file. content is
    read only to skip the walk of a file that cannot hold evidence: every form
    names FormRequest, Validator, Validate or the method validate.
    """
    lowered = content.casefold()
    if "formrequest" not in lowered and "validat" not in lowered:
        return ""
    names = _Names(uses, any(node.type == "namespace_definition" for node in root.named_children))
    for node in walk_named(root):
        if node.type == "base_clause":
            if any(names.names(node_text(base), _FORM_REQUEST) for base in node.named_children):
                return f"base {_FORM_REQUEST}"
        elif node.type == "attribute":
            written = next((child for child in node.named_children if child.type in ("name", "qualified_name")), None)
            if written is not None and names.names(node_text(written), _LIVEWIRE_VALIDATE):
                return f"attribute {_LIVEWIRE_VALIDATE}"
        elif node.type == "scoped_call_expression":
            scope = node.child_by_field_name("scope")
            if (
                scope is not None
                and scope.type in ("name", "qualified_name")
                and node_text(node.child_by_field_name("name")).casefold() == "make"
                and names.names(node_text(scope), _VALIDATOR)
            ):
                return f"call {_VALIDATOR}::make"
        elif node.type == "member_call_expression":
            receiver = node.child_by_field_name("object")
            if (
                receiver is None
                or receiver.type != "variable_name"
                or node_text(node.child_by_field_name("name")).casefold() != "validate"
            ):
                continue
            function = _enclosing_function(node)
            if function is not None and node_text(receiver) in _typed_request_parameters(function, names):
                return f"call {_REQUEST}->validate"
    return ""


__all__ = ["validation_evidence"]
