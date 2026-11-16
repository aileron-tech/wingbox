"""Rust symbol table of one file: `use` bindings and the use sites of names and paths.

A use site is a bare name (`TierResult`, `w32(..)`) or a path (`screenshot::take()`,
`super::flow::Flow`) written in code, in a `use` declaration or inside macro
arguments. Names bound by a pattern (let, parameters, closures, match arms, for,
if let), generic parameters and items defined inside functions or inline modules
shadow a module-level name of the same spelling: they are not use sites.
"""
from __future__ import annotations

from typing import Callable, Iterator

from tree_sitter import Node

from ..interface import BindingPayload, UsePayload

Segment = tuple[str, int, int]

_ITEM_NODES = frozenset({
    "function_item", "struct_item", "enum_item", "union_item", "trait_item", "type_item",
    "const_item", "static_item", "mod_item", "macro_definition", "function_signature_item",
})
_SEGMENT_NODES = frozenset({"identifier", "type_identifier", "crate", "self", "super"})
_PATH_NODES = frozenset({"scoped_identifier", "scoped_type_identifier"})
_PATTERN_HOLDERS = {
    "let_declaration": "pattern",
    "parameter": "pattern",
    "for_expression": "pattern",
    "let_condition": "pattern",
}
# Children that name a declaration or a member, not a use: (parent type, field name).
_DECLARATION_FIELDS = frozenset({
    *((item, "name") for item in _ITEM_NODES),
    ("enum_variant", "name"), ("macro_invocation", "macro"), ("type_parameter", "name"),
    ("const_parameter", "name"), ("associated_type", "name"), ("field_expression", "field"),
    ("lifetime", "name"), ("label", "name"),
})


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _segment(node: Node) -> Segment:
    return (_text(node), node.start_point[0] + 1, node.start_point[1] + 1)


def _named(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _named(child)


def _is_binding_name(name: str) -> bool:
    # Upper-case names in a pattern are constants or unit variants, not new bindings.
    return bool(name) and not name[0].isupper()


def pattern_bindings(node: Node | None) -> Iterator[str]:
    """Names a pattern binds."""
    if node is None:
        return
    if node.type == "identifier":
        if _is_binding_name(_text(node)):
            yield _text(node)
        return
    if node.type == "shorthand_field_identifier":
        yield _text(node)
        return
    if node.type in _PATH_NODES or node.type in ("type_identifier", "field_identifier"):
        return
    skipped = {node.child_by_field_name("type"), node.child_by_field_name("name")} \
        if node.type in ("tuple_struct_pattern", "struct_pattern", "field_pattern") else set()
    for child in node.named_children:
        if child not in skipped:
            yield from pattern_bindings(child)


def _scope_names(node: Node) -> set[str]:
    """Names bound anywhere inside a function or closure, nested items excluded."""
    names: set[str] = set()

    def visit(current: Node, top: bool) -> None:
        if not top and current.type in _ITEM_NODES:
            return
        holder = _PATTERN_HOLDERS.get(current.type)
        if holder is not None:
            names.update(pattern_bindings(current.child_by_field_name(holder)))
        elif current.type == "match_pattern" or current.type == "closure_parameters":
            guard = current.child_by_field_name("condition")
            for child in current.named_children:
                if child != guard:  # the `if` guard of a match arm is an expression, it binds nothing
                    names.update(pattern_bindings(child.child_by_field_name("pattern") or child))
        elif current.type == "self_parameter":
            names.add("self")
        for child in current.named_children:
            visit(child, False)

    visit(node, True)
    return names


def _generic_names(node: Node) -> set[str]:
    parameters = node.child_by_field_name("type_parameters")
    if parameters is None:
        return set()
    names: set[str] = set()
    for parameter in parameters.named_children:
        name = parameter.child_by_field_name("name") or next(
            (child for child in parameter.named_children if child.type in ("type_identifier", "identifier")), None)
        if name is not None:
            names.add(_text(name))
    return names


def item_names(root: Node) -> tuple[frozenset[str], frozenset[str]]:
    """Names of items at the top of the file, and of items defined deeper (functions, inline modules).

    Associated items of impl and trait blocks are members, not names in scope.
    """
    top = {_text(child.child_by_field_name("name")) for child in root.named_children if child.type in _ITEM_NODES}
    nested: set[str] = set()
    for child in root.named_children:
        for node in _named(child):
            if node is child or node.type not in _ITEM_NODES:
                continue
            holder = node.parent.parent if node.parent is not None else None
            if node.parent is not None and node.parent.type == "declaration_list" and holder is not None \
                    and holder.type in ("impl_item", "trait_item"):
                continue
            nested.add(_text(node.child_by_field_name("name")))
    top.discard("")
    return frozenset(top), frozenset(nested - top - {""})


def _path_segments(node: Node) -> list[Segment] | None:
    """Segments of a path written as a scoped identifier; None when a part is not a plain name."""
    if node.type in _SEGMENT_NODES:
        return [_segment(node)]
    if node.type == "generic_type":
        inner = node.child_by_field_name("type")
        return _path_segments(inner) if inner is not None else None
    if node.type in _PATH_NODES:
        name = node.child_by_field_name("name")
        prefix = node.child_by_field_name("path")
        head = _path_segments(prefix) if prefix is not None else []
        if head is None or name is None or name.type not in _SEGMENT_NODES:
            return None
        return [*head, _segment(name)]
    return None


def _use_paths(node: Node, prefix: list[Segment]) -> Iterator[tuple[list[Segment], str | None, bool]]:
    """Leaves of a use tree: (segments, alias, glob)."""
    if node.type in _SEGMENT_NODES:
        yield [*prefix, _segment(node)], None, False
    elif node.type == "scoped_identifier":
        segments = _path_segments(node)
        if segments is not None:
            yield [*prefix, *segments], None, False
    elif node.type == "use_as_clause":
        path = node.child_by_field_name("path")
        alias = node.child_by_field_name("alias")
        if path is not None:
            for segments, _alias, glob in _use_paths(path, prefix):
                yield segments, _text(alias) or None, glob
    elif node.type == "scoped_use_list":
        path = node.child_by_field_name("path")
        head = _path_segments(path) if path is not None else []
        listed = node.child_by_field_name("list")
        if head is not None and listed is not None:
            yield from _use_paths(listed, [*prefix, *head])
    elif node.type == "use_list":
        for child in node.named_children:
            if child.type == "self":
                if prefix:
                    yield prefix, None, False
            else:
                yield from _use_paths(child, prefix)
    elif node.type == "use_wildcard":
        inner = next((child for child in node.named_children), None)
        head = _path_segments(inner) if inner is not None else []
        if head is not None and (prefix or head):
            yield [*prefix, *head], None, True


TypePath = tuple[str, ...]


def _type_path(node: Node | None, impl_type: TypePath | None) -> TypePath | None:
    """A type written as a path (Store, model::Store, &Store, &mut Self); None for anything else."""
    if node is None:
        return None
    if node.type == "reference_type":
        return _type_path(node.child_by_field_name("type"), impl_type)
    if node.type == "type_identifier" and _text(node) == "Self":
        return impl_type
    if node.type in ("type_identifier", "scoped_type_identifier", "identifier", "scoped_identifier"):
        segments = _path_segments(node)
        return tuple(name for name, _line, _col in segments) if segments else None
    return None


def _constructed_type(value: Node | None, impl_type: TypePath | None) -> TypePath | None:
    """The struct a let value builds in sight: Store { .. }, Store(..) or a unit Store."""
    if value is None:
        return None
    if value.type == "struct_expression":
        return _type_path(value.child_by_field_name("name"), impl_type)
    if value.type == "call_expression":
        function = value.child_by_field_name("function")
        if function is not None and function.type == "identifier" and _text(function)[:1].isupper():
            return (_text(function),)
        return None
    if value.type == "identifier" and _text(value)[:1].isupper():
        return (_text(value),)
    return None


def _local_types(function: Node, impl_type: TypePath | None) -> dict[str, TypePath | None]:
    """The type of each parameter and let binding of a function when it is in sight.

    self is the type of the impl; a name bound in two ways, or by a pattern, has none.
    """
    found: dict[str, set[TypePath | None]] = {}
    parameters = function.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters is not None else []:
        if parameter.type == "self_parameter":
            found.setdefault("self", set()).add(impl_type)
        elif parameter.type == "parameter":
            pattern = parameter.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                found.setdefault(_text(pattern), set()).add(
                    _type_path(parameter.child_by_field_name("type"), impl_type))

    def visit(current: Node) -> None:
        if current.type in _ITEM_NODES:
            return
        if current.type == "let_declaration":
            pattern = current.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                written = _type_path(current.child_by_field_name("type"), impl_type)
                if written is None:
                    written = _constructed_type(current.child_by_field_name("value"), impl_type)
                found.setdefault(_text(pattern), set()).add(written)
        for child in current.named_children:
            visit(child)

    body = function.child_by_field_name("body")
    if body is not None:
        visit(body)
    return {name: next(iter(types)) if len(types) == 1 else None for name, types in found.items()}


_TYPE_WRAPPERS = frozenset({"generic_type", "reference_type", "pointer_type", "array_type", "type_arguments"})


def _use_kind(node: Node) -> str:
    """How a name or path is used: call, type, extends (the trait of `impl Trait for`); "" otherwise."""
    parent = node.parent
    if parent is None:
        return ""
    if parent.type == "call_expression" and parent.child_by_field_name("function") == node:
        return "call"
    outer = node
    while outer.parent is not None and outer.parent.type in _TYPE_WRAPPERS:
        outer = outer.parent
    holder = outer.parent
    if holder is not None and holder.type == "impl_item" and holder.child_by_field_name("trait") == outer:
        return "extends"
    if node.type in ("type_identifier", "scoped_type_identifier") or outer is not node:
        return "type"
    return ""


def _seen_from_file(segments: list[Segment], inline_depth: int,
                    rewrite: Callable[[str, int], str | None]) -> list[Segment]:
    """A path written inside inline modules, as the file's own module sees it.

    A path that stays inside the file (super:: from mod tests) becomes the bare
    item path; positions of the kept segments are the written ones.
    """
    if inline_depth == 0:
        return segments
    rewritten = rewrite("::".join(name for name, _line, _col in segments), inline_depth)
    kept = [segment for segment in segments if segment[0] not in ("self", "super")]
    if segments and segments[0][0] not in ("self", "super"):
        return segments
    if rewritten is None:
        return kept
    names = rewritten.split("::")
    first = segments[0]
    extra = len(names) - len(kept)
    return [(name, first[1], first[2]) for name in names[:extra]] + kept


def use_bindings_and_sites(
    root: Node,
    rewrite: Callable[[str, int], str | None],
) -> tuple[list[BindingPayload], list[UsePayload]]:
    """Module-level `use` bindings and every use site of a name or a path in the file."""
    bindings: list[BindingPayload] = []
    uses: list[UsePayload] = []
    _top_items, nested_items = item_names(root)

    def add_use(segments: list[Segment], kind: str = "") -> None:
        if segments and segments[-1][0] not in ("self", "super", "crate"):
            use: UsePayload = {"path": [name for name, _line, _col in segments],
                               "at": [[line, col] for _name, line, col in segments]}
            if kind:
                use["kind"] = kind
            uses.append(use)

    def visit_use(node: Node, inline_depth: int) -> None:
        exported = any(child.type == "visibility_modifier" for child in node.children)
        argument = node.child_by_field_name("argument")
        if argument is None:
            return
        for written, alias, glob in _use_paths(argument, []):
            segments = _seen_from_file(written, inline_depth, rewrite)
            stays_inside = inline_depth > 0 and written and written[0][0] in ("self", "super") \
                and rewrite("::".join(name for name, _l, _c in written), inline_depth) is None
            names = [name for name, _line, _col in segments]
            if glob:
                if names and not stays_inside:
                    bindings.append({"local": "*", "module": "::".join(names), "name": "*", "export": exported, "scope": True})
                continue
            add_use(segments, "re-export" if exported else "import")
            if len(names) >= 2 and not stays_inside:
                bindings.append({"local": alias or names[-1], "module": "::".join(names[:-1]),
                                 "name": names[-1], "export": exported, "scope": True})

    def visit_tokens(node: Node, scopes: tuple[frozenset[str], ...]) -> None:
        children = node.children
        index = 0
        while index < len(children):
            child = children[index]
            if child.type == "token_tree":
                visit_tokens(child, scopes)
                index += 1
                continue
            if child.type not in _SEGMENT_NODES:
                index += 1
                continue
            previous = children[index - 1].type if index else ""
            path = [_segment(child)]
            while index + 2 < len(children) and children[index + 1].type == "::" \
                    and children[index + 2].type in _SEGMENT_NODES:
                path.append(_segment(children[index + 2]))
                index += 2
            following = children[index + 1].type if index + 1 < len(children) else ""
            index += 1
            if previous in ("$", ".", "::") or following in ("!", ":"):
                continue
            consider(path, scopes)

    def consider(path: list[Segment], scopes: tuple[frozenset[str], ...], kind: str = "") -> None:
        head = path[0][0]
        if any(head in scope for scope in scopes) or head in nested_items:
            return
        add_use(path, kind)

    impl_stack: list[TypePath | None] = []
    types_stack: list[dict[str, TypePath | None]] = []

    def method_call(node: Node, scopes: tuple[frozenset[str], ...]) -> None:
        """recv.m(..): a use of Type.m when the receiver's type is in sight (self in an impl,
        an annotated parameter or let, a struct literal); any other receiver stays unresolved."""
        function = node.child_by_field_name("function")
        if function is None or function.type != "field_expression":
            return
        receiver = function.child_by_field_name("value")
        member = function.child_by_field_name("field")
        if receiver is None or member is None or member.type != "field_identifier":
            return
        if receiver.type in ("string_literal", "raw_string_literal", "integer_literal", "float_literal",
                             "array_expression", "tuple_expression"):
            return
        written: TypePath | None = None
        if receiver.type in ("self", "identifier"):
            name = _text(receiver)
            if receiver.type == "self" or any(name in scope for scope in scopes):
                written = next((types[name] for types in reversed(types_stack) if name in types), None)
        start = [receiver.start_point[0] + 1, receiver.start_point[1] + 1]
        path = list(written) if written is not None else []
        uses.append({"path": path, "at": [start for _ in path] + [[member.start_point[0] + 1, member.start_point[1] + 1]],
                     "member": _text(member), "kind": "call"})

    def visit(node: Node, scopes: tuple[frozenset[str], ...], inline_depth: int) -> None:
        kind = node.type
        if kind == "call_expression":
            method_call(node, scopes)
        if kind == "impl_item":
            implemented = _type_path(node.child_by_field_name("type"), None)
            if implemented is None:
                type_node = node.child_by_field_name("type")
                if type_node is not None and type_node.type == "generic_type":
                    implemented = _type_path(type_node.child_by_field_name("type"), None)
            impl_stack.append(implemented)
            visit_children(node, scopes, inline_depth)
            impl_stack.pop()
            return
        if kind == "function_item":
            types_stack.append(_local_types(node, impl_stack[-1] if impl_stack else None))
            visit_children(node, scopes, inline_depth)
            types_stack.pop()
            return
        visit_children(node, scopes, inline_depth)

    def visit_children(node: Node, scopes: tuple[frozenset[str], ...], inline_depth: int) -> None:
        kind = node.type
        if kind == "use_declaration":
            visit_use(node, inline_depth)
            return
        if kind in ("attribute_item", "inner_attribute_item", "macro_definition", "line_comment",
                    "block_comment", "string_literal", "raw_string_literal"):
            return
        if kind == "token_tree":
            visit_tokens(node, scopes)
            return
        if kind in _PATH_NODES:
            segments = _path_segments(node)
            if segments is not None:
                segments = _seen_from_file(segments, inline_depth, rewrite) \
                    if segments[0][0] in ("self", "super") else segments
                if segments:
                    consider(segments, scopes, _use_kind(node))
                return
        if kind in ("identifier", "type_identifier"):
            parent = node.parent
            if parent is not None and parent.type in ("lifetime", "label"):
                return
            if parent is not None and parent.type in _PATTERN_HOLDERS and \
                    parent.child_by_field_name(_PATTERN_HOLDERS[parent.type]) == node:
                return
            if parent is not None and any(
                (parent.type, field) in _DECLARATION_FIELDS and parent.child_by_field_name(field) == node
                for field in ("name", "macro", "field")
            ):
                return
            consider([_segment(node)], scopes, _use_kind(node))
            return
        inner_scopes = scopes
        if kind in ("function_item", "closure_expression"):
            inner_scopes = (*scopes, frozenset(_scope_names(node) | _generic_names(node)))
        elif kind in ("impl_item", "trait_item", "struct_item", "enum_item", "union_item", "type_item"):
            generics = _generic_names(node)
            if generics:
                inner_scopes = (*scopes, frozenset(generics))
        is_inline_module = kind == "mod_item" and node.child_by_field_name("body") is not None
        for child in node.named_children:
            if kind in ("let_declaration", "parameter", "for_expression", "let_condition") and \
                    child == node.child_by_field_name("pattern"):
                visit_pattern(child, inner_scopes, inline_depth)
                continue
            if kind in ("match_pattern", "closure_parameters"):
                if kind == "match_pattern" and child == node.child_by_field_name("condition"):
                    visit(child, inner_scopes, inline_depth)
                else:
                    visit_pattern(child, inner_scopes, inline_depth)
                continue
            visit(child, inner_scopes, inline_depth + 1 if is_inline_module else inline_depth)

    def visit_pattern(node: Node, scopes: tuple[frozenset[str], ...], inline_depth: int) -> None:
        """Paths and upper-case names in a pattern are uses (Foo::Bar(x), CONST); bound names are not."""
        if node.type in _PATH_NODES:
            visit(node, scopes, inline_depth)
            return
        if node.type in ("identifier", "type_identifier"):
            if not _is_binding_name(_text(node)):
                consider([_segment(node)], scopes)
            return
        for child in node.named_children:
            if node.type in ("field_pattern",) and child == node.child_by_field_name("name"):
                continue
            visit_pattern(child, scopes, inline_depth)

    for child in root.named_children:
        visit(child, (), 0)
    return bindings, uses


__all__ = ["item_names", "pattern_bindings", "use_bindings_and_sites"]
