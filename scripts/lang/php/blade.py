"""Blade template scanner: views, anonymous components, and Livewire tags.

Blade has no tree-sitter grammar on PyPI, and the references that matter to a
code graph are lexical: ``@extends``/``@include`` directives, ``<x-*>``
component tags, and ``<livewire:*>``/``@livewire(...)`` calls.  The template is
walked once from left to right; each match is handled immediately and the
argument or tag text it needs is read from a short bounded window, so the cost
stays linear in the size of the file.

``{{-- ... --}}`` comments and ``@verbatim ... @endverbatim`` blocks are
skipped: nothing inside them is compiled by Blade.  Escaped directives
(``@@include``) are literal output and are skipped too.  Control directives
(``@section``, ``@push``, ``@stack``, ``@php``, ``@if``, ...) and Blade's slot
tags (``<x-slot>``) carry no view reference and produce nothing.

The scanner never reads the file system apart from the content hash, and it
never resolves a reference: output values are the pseudo-imports documented in
``scripts/lang/interface.py`` and the resolver owns path lookups.
"""

from __future__ import annotations

import re

from ..interface import FileResult, SourceRelationPayload
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash


# Bounded reads keep one malformed directive or tag from rescanning the tail of
# the file on every match; real Blade arguments and tags are far shorter.
_MAX_ARGUMENT_SCAN = 4096
_MAX_TAG_SCAN = 4096

# One forward search finds the next construct worth handling.  The branches are
# plain literals and character classes: no nested quantifier runs over the
# template, so there is no backtracking blow-up on long lines.
_TOKEN = re.compile(
    r"(?P<comment>\{\{--)"
    r"|(?P<verbatim>@verbatim\b)"
    r"|(?P<directive>@(?:includeFirst|includeUnless|includeWhen|includeIf"
    r"|extends|include|component|each)\b)"
    r"|(?P<livewire_call>@livewire\b)"
    r"|(?P<livewire_tag><livewire:)"
    r"|(?P<component_tag><x-)"
    r"|(?P<class_directive>@(?:inject|use)\b)"
    # A qualified class name before `::` (`\App\Models\Post::count()`). It starts where no name is running
    # (the look-behind), so a long run of name characters is read once, not once from each of its characters.
    r"|(?P<class_use>(?<![\w\\$>])\\?(?:[A-Za-z_]\w*\\)+[A-Za-z_]\w*(?=::))"
)
# The class a directive names in a string: 'App\Models\Post', or with doubled backslashes in double quotes.
_CLASS_LITERAL = re.compile(r"""(['"])\\{0,2}((?:[A-Za-z_]\w*\\{1,2})*[A-Za-z_]\w*)\1""")
# @inject('name', 'Class') names the class second, @use('Class') first.
_CLASS_DIRECTIVES = {"inject": 1, "use": 0}

# Directive -> (relation, argument positions in preference order).  The view is
# the first argument of @include/@extends/@component/@each/@includeFirst, but
# the second argument of @includeWhen/@includeUnless; the first position is
# still tried as a fallback so a call that passes only the view is not lost.
_VIEW_DIRECTIVES: dict[str, tuple[str, tuple[int, ...]]] = {
    "extends": ("extends", (0,)),
    "include": ("include", (0,)),
    "includeIf": ("include", (0,)),
    "includeWhen": ("include", (1, 0)),
    "includeUnless": ("include", (1, 0)),
    "includeFirst": ("include", (0,)),
    "component": ("component", (0,)),
    "each": ("component", (0,)),
}

_LIVEWIRE_NAME = re.compile(r"[A-Za-z0-9_.:\-]+")
_COMPONENT_NAME = re.compile(r"[A-Za-z0-9_.\-:]+")
_QUOTED_LITERAL = re.compile(r"'([^'\\$]*)'|\"([^\"\\$]*)\"")
_DYNAMIC_COMPONENT_ATTRIBUTE = re.compile(
    r":component\s*=\s*(?:\"([^\"]*)\"|'([^']*)')"
)
_STATIC_COMPONENT_ATTRIBUTE = re.compile(
    r"(?<![\w:-])component\s*=\s*(?:\"([^\"]*)\"|'([^']*)')"
)
_STATIC_COMPONENT_REJECT = frozenset("$ {}<>'\"")


def _is_escaped(content: str, start: int) -> bool:
    """True for a directive written as ``@@name``: Blade outputs it literally."""
    return start > 0 and content[start - 1] == "@"


def _string_literal(raw: str) -> str:
    """Return the value of a plain quoted PHP string, or "" when it is dynamic."""
    value = raw.strip()
    if len(value) < 2:
        return ""
    quote = value[0]
    if quote not in "'\"" or value[-1] != quote:
        return ""
    if "\\" in value or "$" in value:
        return ""
    inner = value[1:-1]
    if quote in inner or "\n" in inner or "\r" in inner:
        return ""
    return inner.strip()


def _call_arguments(content: str, start: int) -> tuple[list[str], int]:
    """Split the top-level arguments of a directive call that starts at start.

    Returns the arguments and the position just past the call.  Both are empty
    (and the position is still past the directive name) when no parenthesized
    argument list follows, and the position advances by the whole bounded
    window when the list is not closed inside it, so unclosed calls cannot make
    the scan quadratic.
    """
    index = start
    limit = min(len(content), start + _MAX_ARGUMENT_SCAN)
    while index < limit and content[index].isspace():
        index += 1
    if index >= limit or content[index] != "(":
        return [], index

    arguments: list[str] = []
    depth = 0
    begin = index + 1
    quote = ""
    cursor = index
    while cursor < limit:
        character = content[cursor]
        if quote:
            if character == "\\":
                cursor += 2
                continue
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                arguments.append(content[begin:cursor])
                return [argument.strip() for argument in arguments], cursor + 1
        elif character == "," and depth == 1:
            arguments.append(content[begin:cursor])
            begin = cursor + 1
        cursor += 1
    return [], limit


def _array_literals(raw: str) -> list[str]:
    """Return the quoted string elements of a literal ``['a', 'b']`` array."""
    value = raw.strip()
    if not value.startswith("["):
        return []
    literals: list[str] = []
    for match in _QUOTED_LITERAL.finditer(value):
        literal = (
            match.group(1) if match.group(1) is not None else match.group(2)
        ).strip()
        if literal:
            literals.append(literal)
    return literals


def _tag_name(pattern: re.Pattern[str], content: str, start: int) -> str:
    match = pattern.match(content, start)
    return match.group(0) if match is not None else ""


def _tag_end(content: str, start: int) -> int:
    """Index of the ``>`` that closes the tag, ignoring quoted attribute text."""
    limit = min(len(content), start + _MAX_TAG_SCAN)
    quote = ""
    index = start
    while index < limit:
        character = content[index]
        if quote:
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character == ">":
            return index
        index += 1
    return limit


def _attribute_value(match: re.Match[str] | None) -> str:
    if match is None:
        return ""
    return match.group(1) if match.group(1) is not None else match.group(2)


def _dynamic_component_literal(attributes: str) -> str:
    """The view of ``<x-dynamic-component>`` when its target is a literal."""
    bound = _DYNAMIC_COMPONENT_ATTRIBUTE.search(attributes)
    if bound is not None:
        return _string_literal(_attribute_value(bound))
    static = _attribute_value(_STATIC_COMPONENT_ATTRIBUTE.search(attributes)).strip()
    if not static:
        return ""
    if any(character in _STATIC_COMPONENT_REJECT for character in static):
        return ""
    return static


def _component_target(name: str) -> str:
    """Map ``<x-name>``/``<x-ns::name>`` to its view pseudo-import."""
    namespace, separator, remainder = name.partition("::")
    if separator:
        if not namespace or not remainder:
            return ""
        return f"php-view:{namespace}::components.{remainder}"
    return f"php-view:components.{name}"


def _scan_template(content: str) -> tuple[list[str], list[SourceRelationPayload]]:
    """Collect pseudo-imports and relations from one template, in order."""
    imports: list[str] = []
    relations: list[SourceRelationPayload] = []
    seen_imports: set[str] = set()
    seen_relations: set[tuple[str, str]] = set()

    def record(target: str, relation: str) -> None:
        if not target or target.endswith(":"):
            return
        if target not in seen_imports:
            seen_imports.add(target)
            imports.append(target)
        key = (target, relation)
        if key not in seen_relations:
            seen_relations.add(key)
            relations.append({"target": target, "relation": relation})

    def record_import(target: str) -> None:
        if target not in seen_imports:
            seen_imports.add(target)
            imports.append(target)

    position = 0
    length = len(content)
    while position < length:
        match = _TOKEN.search(content, position)
        if match is None:
            break

        if match.group("comment") is not None:
            closing = content.find("--}}", match.end())
            position = length if closing < 0 else closing + len("--}}")
            continue

        if match.group("verbatim") is not None:
            closing = content.find("@endverbatim", match.end())
            position = length if closing < 0 else closing + len("@endverbatim")
            continue

        if content[match.start()] == "@" and _is_escaped(content, match.start()):
            position = match.end()
            continue

        directive = match.group("directive")
        if directive is not None:
            name = directive[1:]
            arguments, call_end = _call_arguments(content, match.end())
            if name == "includeFirst":
                if arguments:
                    for literal in _array_literals(arguments[0]):
                        record(f"php-view:{literal}", "include")
            else:
                relation, positions = _VIEW_DIRECTIVES[name]
                for index in positions:
                    literal = (
                        _string_literal(arguments[index])
                        if index < len(arguments)
                        else ""
                    )
                    if literal:
                        record(f"php-view:{literal}", relation)
                        break
            position = max(match.end(), call_end)
            continue

        if match.group("class_directive") is not None:
            arguments, call_end = _call_arguments(content, match.end())
            index = _CLASS_DIRECTIVES[match.group("class_directive")[1:]]
            literal = _CLASS_LITERAL.fullmatch(arguments[index].strip()) if index < len(arguments) else None
            if literal is not None:
                record_import("php-class:" + literal.group(2).replace("\\\\", "\\"))
            position = max(match.end(), call_end)
            continue

        if match.group("class_use") is not None:
            record_import("php-class:" + match.group("class_use").lstrip("\\"))
            position = match.end()
            continue

        if match.group("livewire_call") is not None:
            arguments, call_end = _call_arguments(content, match.end())
            if arguments:
                literal = _string_literal(arguments[0])
                if literal:
                    record(f"php-livewire:{literal}", "livewire")
            position = max(match.end(), call_end)
            continue

        if match.group("livewire_tag") is not None:
            alias = _tag_name(_LIVEWIRE_NAME, content, match.end())
            if alias:
                record(f"php-livewire:{alias}", "livewire")
            position = match.end()
            continue

        # The only branch left is a component tag (<x-...>).
        name = _tag_name(_COMPONENT_NAME, content, match.end())
        if name == "dynamic-component":
            name_end = match.end() + len(name)
            attributes_end = _tag_end(content, name_end)
            literal = _dynamic_component_literal(content[name_end:attributes_end])
            if literal:
                record(f"php-view:{literal}", "component")
            position = max(match.end(), attributes_end)
            continue
        if name and name != "slot" and not name.startswith("slot:"):
            # <x-slot> is Blade's slot API, not a component view file.
            target = _component_target(name)
            if target:
                record(target, "component")
            if target and "::" not in name:
                # The class of the component, when the application has one (the resolver looks).
                record_import(f"php-component:{name}")
        position = match.end()

    return imports, relations


def parse_blade(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    """Parse one Blade template into the shared FileResult contract."""
    imports, relations = _scan_template(content)
    return FileResult(
        path=path,
        language="blade",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=[],
        implements=[],
        extends=[],
        purpose=infer_purpose(path, content, "blade", []),
        naming_convention=detect_naming(path),
        has_validation=False,
        entities=[],
        imported_names=[],
        exported_names=[],
        relations=relations,
    )