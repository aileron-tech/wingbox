"""Import resolution for the PHP/Laravel adapter.

The parser emits path-free pseudo-imports; this module binds them to graph node
IDs for one repository:

    php-class:App\\Models\\User     declaration index, then PSR-4
    php-symbol:User                 alias, namespace chain, then global name,
                                    relative to the source file
    php-view:admin::nav             Blade view directories and registered
                                    view namespaces
    php-livewire:counter            Livewire registrations, conventions, then
                                    a `livewire.<alias>` Blade view
    php-function:format_money       the function of the namespace of the source
                                    file, then the global one
    php-const:\\App\\LIMIT          the constant a `use const` names in full
    php-file:../Support/helpers.php a path from the folder of the source file
                                    (`require __DIR__ . '/...'`)
    php-component:alert             the class `View\\Components\\Alert` of the
                                    application (`<x-alert>`)
    php-controller:HomeController   a route action 'HomeController@index': the
                                    class in full, else the one below
                                    `Http\\Controllers` of the application

Ambiguous lookups are settled only by Composer's PSR-4 autoload
contract (the file Composer would load owns the class); otherwise they
resolve to None and the graph never guesses.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from ..discovery import is_excluded_path
from ..interface import DiscoveryExclusions, FileResult
from .psr4 import Psr4Map


_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOAD_VIEWS_CALL = re.compile(r"\bloadViewsFrom\s*\(")
_ADD_NAMESPACE_CALL = re.compile(r"\bView\s*::\s*addNamespace\s*\(")
_ADD_LOCATION_CALL = re.compile(r"\bView\s*::\s*addLocation\s*\(")
_LIVEWIRE_COMPONENT_CALL = re.compile(r"\bLivewire\s*::\s*component\s*\(")
_CLASS_CONSTANT_PATTERN = re.compile(
    r"(\\?[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)*)\s*::\s*class\b"
)
_LIVEWIRE_COMPONENT_IMPORT = re.compile(r"\buse\s+Livewire\\Component\b")
# Helper functions whose first argument is a repository-relative directory;
# base_path() itself means the repository root.
_PATH_FUNCTIONS = {
    "base_path": "",
    "resource_path": "resources",
    "app_path": "app",
    "public_path": "public",
    "storage_path": "storage",
}
# The convention alias of a module component: App\Livewire\<Name> and
# <Prefix>\Livewire\<Name> for every local composer prefix.
_LIVEWIRE_LOGICAL_NAME = "Livewire"
_VIEW_SUFFIX = "/resources/views"


class _Ambiguous:
    """Marker for a name that has more than one candidate file."""


_AMBIGUOUS = _Ambiguous()


def _freeze_index(index: Mapping[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {name: tuple(sorted(targets)) for name, targets in index.items()}


def _path_key(path: str) -> str:
    """Use one absolute spelling for every graph path and candidate path."""
    return os.path.normpath(os.path.realpath(path))


def _rooted_key(path: str, root: Path) -> str:
    """Normalize a graph path; a relative one is read against the repository."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return _path_key(str(candidate))


def _relative_posix(path: str, root: Path) -> str | None:
    """Return a repository-relative POSIX path, or None when it leaves the root."""
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return None
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    return relative.replace(os.sep, "/")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _kebab(name: str) -> str:
    """Convert a StudlyCase module name to its kebab-case namespace alias."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def _studly(segment: str) -> str:
    """Convert one Livewire alias segment to the StudlyCase part of a class."""
    parts = re.split(r"[-_\s]+", segment)
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _studly_class_path(alias: str) -> str:
    """Convert a dotted Livewire alias to its nested class-name path.

    `diary.analytics` names the class `Diary\\Analytics`; a hyphen or an
    underscore inside one alias segment is part of one StudlyCase word.
    """
    segments = (_studly(segment) for segment in alias.split("."))
    return "\\".join(segment for segment in segments if segment)


def _php_string_literal(argument: str) -> str | None:
    """Return the value of an argument that is exactly one quoted string."""
    text = argument.strip()
    if len(text) < 2 or text[0] not in "'\"" or text[-1] != text[0]:
        return None
    body = text[1:-1]
    body = body.replace("\\\\", "\\")
    return body.replace("\\" + text[0], text[0])


def _read_quoted(text: str, index: int) -> tuple[str, int]:
    """Read one quoted PHP string, returning its value and the next index."""
    quote = text[index]
    value: list[str] = []
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            value.append(text[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(value), index + 1
        value.append(char)
        index += 1
    return "".join(value), index


def _read_parenthesized(content: str, start: int) -> tuple[str, int]:
    """Read a parenthesized argument list, quotes and nesting respected."""
    depth = 1
    index = start
    quote = ""
    while index < len(content):
        char = content[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[start:index], index + 1
        index += 1
    return content[start:], len(content)


def _split_arguments(arguments: str) -> tuple[str, ...]:
    """Split a PHP argument list on its top-level commas."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
        index += 1
    tail = arguments[start:].strip()
    if tail:
        parts.append(tail)
    return tuple(parts)


def _iter_call_arguments(
    content: str,
    pattern: re.Pattern[str],
) -> tuple[tuple[str, ...], ...]:
    """Return the argument lists of every call the pattern finds."""
    calls: list[tuple[str, ...]] = []
    for match in pattern.finditer(content):
        arguments, _ = _read_parenthesized(content, match.end())
        calls.append(_split_arguments(arguments))
    return tuple(calls)


def _scan_registrations(
    content: str,
    source_relative: str,
    source_key: str,
    view_registrations: list[tuple[str, str, str]],
    location_registrations: list[tuple[str, str]],
    livewire_registrations: list[tuple[str, str, str]],
) -> None:
    """Collect view-namespace and Livewire registrations from one PHP source."""
    # Every pattern requires its literal token, so a substring test safely
    # skips the regex scan of the many files that register nothing.
    if "loadViewsFrom" in content:
        for arguments in _iter_call_arguments(content, _LOAD_VIEWS_CALL):
            if len(arguments) < 2:
                continue
            namespace = _php_string_literal(arguments[1])
            if namespace:
                view_registrations.append(
                    (namespace, arguments[0], source_relative)
                )
    if "addNamespace" in content:
        for arguments in _iter_call_arguments(content, _ADD_NAMESPACE_CALL):
            if len(arguments) < 2:
                continue
            namespace = _php_string_literal(arguments[0])
            if namespace:
                view_registrations.append(
                    (namespace, arguments[1], source_relative)
                )
    if "addLocation" in content:
        for arguments in _iter_call_arguments(content, _ADD_LOCATION_CALL):
            if arguments and arguments[0]:
                location_registrations.append((arguments[0], source_relative))
    if "Livewire" not in content:
        return
    for arguments in _iter_call_arguments(content, _LIVEWIRE_COMPONENT_CALL):
        if len(arguments) < 2:
            continue
        alias = _php_string_literal(arguments[0])
        if not alias:
            continue
        class_constant = _CLASS_CONSTANT_PATTERN.search(arguments[1])
        if class_constant is not None:
            livewire_registrations.append(
                (alias, class_constant.group(1), source_key)
            )


def _literal_arguments_value(expression: str) -> str:
    """Concatenate the quoted literals of an expression, ignoring the rest."""
    values: list[str] = []
    index = 0
    while index < len(expression):
        if expression[index] in "'\"":
            literal, index = _read_quoted(expression, index)
            values.append(literal)
        else:
            index += 1
    return "".join(values)


def _path_expression_value(expression: str, source_relative: str) -> tuple[str, bool]:
    """Evaluate a PHP path expression to a value and whether it is anchored.

    Anchored values (magic constants and path helpers) are already relative to
    the repository root; a plain literal is relative to the source directory.
    """
    source_directory = posixpath.dirname(source_relative)
    result = ""
    anchored = False
    found = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char in "'\"":
            literal, index = _read_quoted(expression, index)
            result += literal
            found = True
            continue
        if not (char.isalpha() or char == "_"):
            index += 1
            continue
        match = _IDENTIFIER_PATTERN.match(expression, index)
        if match is None:
            index += 1
            continue
        word = match.group(0)
        index = match.end()
        if word == "__DIR__":
            result = source_directory
            anchored = True
            found = True
            continue
        if word == "__FILE__":
            result = source_relative
            anchored = True
            found = True
            continue
        cursor = index
        while cursor < len(expression) and expression[cursor].isspace():
            cursor += 1
        if cursor >= len(expression) or expression[cursor] != "(":
            continue
        inner, index = _read_parenthesized(expression, cursor + 1)
        if word == "dirname":
            arguments = _split_arguments(inner)
            if not arguments:
                continue
            inner_value, inner_anchored = _path_expression_value(
                arguments[0],
                source_relative,
            )
            if not inner_value:
                continue
            result = posixpath.dirname(inner_value.rstrip("/"))
            anchored = True
            found = True
            continue
        directory = _PATH_FUNCTIONS.get(word)
        if directory is None:
            continue
        argument = _literal_arguments_value(inner)
        result = posixpath.join(directory, argument) if argument else directory
        anchored = True
        found = True
    return (result, anchored) if found else ("", False)


def _view_root_from_expression(
    expression: str,
    source_relative: str,
    root: Path,
) -> str | None:
    """Resolve a loadViewsFrom/addNamespace expression to a directory ID."""
    value, anchored = _path_expression_value(expression, source_relative)
    if not value:
        return None
    text = value.replace("\\", "/")
    if text.startswith("/"):
        try:
            text = os.path.relpath(text, str(root)).replace(os.sep, "/")
        except ValueError:
            return None
    elif not anchored:
        text = posixpath.join(posixpath.dirname(source_relative), text)
    text = posixpath.normpath(text)
    if text == ".":
        return ""
    if text.startswith("/") or text == os.pardir or text.startswith(os.pardir + "/"):
        return None
    return text.rstrip("/")


def _collect_view_roots(relative: str, roots: set[str]) -> None:
    """Record every `.../resources/views` directory a source file lives under."""
    parts = relative.split("/")
    for index in range(len(parts) - 2):
        if parts[index] == "resources" and parts[index + 1] == "views":
            roots.add("/".join(parts[: index + 2]))


def _livewire_view_name(fqcn: str) -> str | None:
    """Derive the conventional Blade view of a Livewire component class.

    App\\Livewire\\GlobalSearch -> livewire.global-search
    App\\Livewire\\Chat\\FileUpload -> livewire.chat.file-upload
    Modules\\Wellness\\Livewire\\Journal\\Insights -> livewire.journal.insights
    """
    parts = fqcn.strip().lstrip("\\").split("\\")
    if "Livewire" not in parts:
        return None
    remainder = parts[parts.index("Livewire") + 1:]
    names = tuple(_kebab(segment) for segment in remainder if segment)
    if not names or not all(names):
        return None
    return "livewire." + ".".join(names)


def _is_livewire_component(content: str, declarations: list[object]) -> bool:
    """True when a PHP file declares a class extending Livewire's Component."""
    for declaration in declarations:
        for base in getattr(declaration, "bases", ()):
            name = str(base).strip().lstrip("\\")
            if name.rsplit("\\", 1)[-1] == "Component":
                return True
    return (
        "Livewire" in content
        and _LIVEWIRE_COMPONENT_IMPORT.search(content) is not None
    )


def _livewire_prefixes(psr4: Psr4Map, root: Path) -> tuple[str, ...]:
    """List the class-name prefixes tried for a Livewire alias convention."""
    prefixes = ["App\\"]
    for prefix, directories in psr4.prefixes:
        if not any(_is_within(directory, root) for directory in directories):
            continue
        normalized = prefix if not prefix or prefix.endswith("\\") else prefix + "\\"
        if normalized not in prefixes:
            prefixes.append(normalized)
    return tuple(prefixes)


def _is_view_directory(bound: BoundPhpLanguage, directory: str) -> bool:
    """True when a registration points at a view root or a directory below one."""
    if not directory:
        return False
    if directory in bound.view_roots:
        return True
    if not any(directory.startswith(f"{view_root}/") for view_root in bound.view_roots):
        return False
    return (bound.root / directory).is_dir()


def _module_view_root(bound: BoundPhpLanguage, source_relative: str) -> str | None:
    """Find the Laravel-modules root above a provider and its views directory.

    A provider often builds its view path from a `$modulePath` variable, which
    cannot be evaluated statically (for example
    `loadViewsFrom($modulePath . 'resources/views', 'wellness')`). The
    module root
    is the first ancestor of the provider that contains a `src` directory; its
    `resources/views` is the namespace directory when that was discovered.
    """
    directory = posixpath.dirname(source_relative)
    checked: set[str] = set()
    while directory and directory not in checked:
        checked.add(directory)
        if (bound.root / directory / "src").is_dir():
            candidate = f"{directory}/resources/views"
            return candidate if candidate in bound.view_roots else None
        parent = posixpath.dirname(directory)
        if parent == directory:
            break
        directory = parent
    if (bound.root / "src").is_dir() and "resources/views" in bound.view_roots:
        return "resources/views"
    return None


def _freeze_view_namespaces(
    bound: BoundPhpLanguage,
    registrations: list[tuple[str, str, str]],
    exclusions: DiscoveryExclusions,
) -> dict[str, tuple[str, ...]]:
    """Map view namespaces to their directories: registrations, module fallback,
    then the module-directory convention."""
    exact: dict[str, list[str]] = {}
    fallback: dict[str, list[str]] = {}
    for namespace, expression, source_relative in registrations:
        directory = _view_root_from_expression(expression, source_relative, bound.root)
        valid = (
            directory is not None
            and _is_view_directory(bound, directory)
            and not (
                directory
                and is_excluded_path(exclusions, str(bound.root / directory))
            )
        )
        if valid:
            roots = exact.setdefault(namespace, [])
            if directory not in roots:
                roots.append(directory)
            continue
        candidate = _module_view_root(bound, source_relative)
        if candidate is None:
            continue
        if is_excluded_path(exclusions, str(bound.root / candidate)):
            continue
        roots = fallback.setdefault(namespace, [])
        if candidate not in roots:
            roots.append(candidate)

    namespaces: dict[str, list[str]] = {
        namespace: list(roots) for namespace, roots in exact.items()
    }
    for namespace, roots in fallback.items():
        if namespace in namespaces:
            # A registration that resolved cleanly owns the namespace; do not
            # add a convention directory next to it.
            continue
        target = namespaces.setdefault(namespace, [])
        for root in roots:
            if root not in target:
                target.append(root)
    for view_root in bound.view_roots:
        if not view_root.endswith(_VIEW_SUFFIX):
            continue
        module_directory = view_root[: -len(_VIEW_SUFFIX)]
        if not module_directory:
            continue
        module_name = module_directory.rsplit("/", 1)[-1]
        convention = _kebab(module_name)
        if not convention:
            continue
        roots = namespaces.setdefault(convention, [])
        if view_root not in roots:
            roots.append(view_root)
    return {namespace: tuple(roots) for namespace, roots in namespaces.items()}


def _freeze_default_locations(
    bound: BoundPhpLanguage,
    registrations: list[tuple[str, str]],
    exclusions: DiscoveryExclusions,
) -> set[str]:
    """Directories added to Laravel's default view paths (`View::addLocation`)."""
    roots: set[str] = set()
    for expression, source_relative in registrations:
        directory = _view_root_from_expression(expression, source_relative, bound.root)
        if directory is None or not _is_view_directory(bound, directory):
            directory = _module_view_root(bound, source_relative)
        if directory is None or not _is_view_directory(bound, directory):
            continue
        if is_excluded_path(exclusions, str(bound.root / directory)):
            continue
        roots.add(directory)
    return roots


def _freeze_livewire_classes(
    bound: BoundPhpLanguage,
    registrations: list[tuple[str, str, str]],
) -> dict[str, tuple[str, ...]]:
    """Resolve Livewire::component registrations to the class nodes they name."""
    classes: dict[str, set[str]] = {}
    for alias, raw_class, source_key in registrations:
        node_id = bound._resolve_symbol(raw_class, source_key)
        if node_id is None:
            continue
        classes.setdefault(alias.casefold(), set()).add(node_id)
    return _freeze_index(classes)


@dataclass(frozen=True)
class BoundPhpLanguage:
    """PHP import resolution bound to one repository graph."""

    root: Path
    path_to_id: Mapping[str, str]
    declaration_to_ids: Mapping[str, tuple[str, ...]]
    exact_declaration_to_ids: Mapping[str, tuple[str, ...]]
    module_ids: Mapping[str, tuple[str, ...]]
    source_namespaces: Mapping[str, str]
    source_uses: Mapping[str, tuple[tuple[str, str], ...]]
    view_roots: tuple[str, ...]
    default_view_roots: tuple[str, ...]
    view_namespaces: Mapping[str, tuple[str, ...]]
    livewire_classes: Mapping[str, tuple[str, ...]]
    livewire_view_names: Mapping[str, tuple[str, ...]]
    livewire_prefixes: tuple[str, ...]
    psr4: Psr4Map
    # Qualified name -> the files that declare the function (casefolded: PHP function names ignore case)
    # or the constant (as written), outside a class.
    function_to_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    constant_to_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundPhpLanguage:
        from .parser import php_metadata

        root_path = Path(root).resolve()
        normalized_paths = {
            _rooted_key(path, root_path): node_id
            for path, node_id in path_to_id.items()
        }
        psr4 = Psr4Map.create(root_path, discovery_exclusions)

        declaration_index: dict[str, set[str]] = {}
        exact_index: dict[str, set[str]] = {}
        function_index: dict[str, set[str]] = {}
        constant_index: dict[str, set[str]] = {}
        module_index: dict[str, set[str]] = {}
        namespaces: dict[str, str] = {}
        uses_by_source: dict[str, tuple[tuple[str, str], ...]] = {}
        view_root_set: set[str] = set()
        view_registrations: list[tuple[str, str, str]] = []
        location_registrations: list[tuple[str, str]] = []
        livewire_registrations: list[tuple[str, str, str]] = []
        livewire_views: dict[str, tuple[str, ...]] = {}

        # One pass: every indexed PHP file is read and parsed exactly once.
        for path_key in sorted(normalized_paths):
            node_id = normalized_paths[path_key]
            relative = _relative_posix(path_key, root_path)
            if relative is None:
                continue
            if relative.endswith(".blade.php"):
                module_index.setdefault(
                    relative[: -len(".blade.php")],
                    set(),
                ).add(node_id)
                _collect_view_roots(relative, view_root_set)
                continue
            if not relative.endswith(".php"):
                continue
            module_index.setdefault(relative[: -len(".php")], set()).add(node_id)
            _collect_view_roots(relative, view_root_set)
            try:
                content = Path(path_key).read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                metadata = php_metadata(content)
            except Exception as error:
                print(
                    f"[code-graph] Cannot read PHP declarations from {path_key}: "
                    f"{error}",
                    file=sys.stderr,
                )
                continue
            try:
                namespace = (
                    str(getattr(metadata, "namespace", "") or "")
                    .strip()
                    .lstrip("\\")
                )
                namespaces[path_key] = namespace
                uses: list[tuple[str, str]] = []
                for entry in getattr(metadata, "uses", ()):
                    alias, original = entry[0], entry[1]
                    if not alias or not original:
                        continue
                    uses.append(
                        (str(alias), str(original).strip().lstrip("\\"))
                    )
                uses_by_source[path_key] = tuple(uses)
                declarations = list(getattr(metadata, "declarations", ()))
                for declaration in declarations:
                    declared = (
                        str(getattr(declaration, "name", ""))
                        .strip()
                        .lstrip("\\")
                    )
                    if not declared:
                        continue
                    declaration_index.setdefault(
                        declared.casefold(),
                        set(),
                    ).add(node_id)
                    exact_index.setdefault(declared, set()).add(node_id)
                for function in getattr(metadata, "functions", ()):
                    function_index.setdefault(str(function).casefold(), set()).add(node_id)
                for constant in getattr(metadata, "constants", ()):
                    constant_index.setdefault(str(constant), set()).add(node_id)
                if _is_livewire_component(content, declarations):
                    view_names = tuple(
                        dict.fromkeys(
                            view_name
                            for view_name in (
                                _livewire_view_name(
                                    str(getattr(declaration, "name", ""))
                                )
                                for declaration in declarations
                            )
                            if view_name
                        )
                    )
                    if view_names:
                        livewire_views[path_key] = view_names
                _scan_registrations(
                    content,
                    relative,
                    path_key,
                    view_registrations,
                    location_registrations,
                    livewire_registrations,
                )
            except Exception as error:
                print(
                    f"[code-graph] Cannot index PHP declarations from {path_key}: "
                    f"{error}",
                    file=sys.stderr,
                )
                continue

        bound = cls(
            root=root_path,
            path_to_id=normalized_paths,
            declaration_to_ids=_freeze_index(declaration_index),
            exact_declaration_to_ids=_freeze_index(exact_index),
            module_ids=_freeze_index(module_index),
            source_namespaces=namespaces,
            source_uses=uses_by_source,
            view_roots=tuple(sorted(view_root_set)),
            default_view_roots=tuple(sorted(view_root_set)),
            view_namespaces={},
            livewire_classes={},
            livewire_view_names=livewire_views,
            livewire_prefixes=_livewire_prefixes(psr4, root_path),
            psr4=psr4,
            function_to_ids=_freeze_index(function_index),
            constant_to_ids=_freeze_index(constant_index),
        )
        view_namespaces = _freeze_view_namespaces(
            bound,
            view_registrations,
            discovery_exclusions,
        )
        # A module's views are reachable through its registered namespace, not
        # as an un-namespaced Laravel view path, unless a provider explicitly
        # adds them to the default paths with View::addLocation.
        registered_roots = {
            view_root
            for roots in view_namespaces.values()
            for view_root in roots
        }
        location_roots = _freeze_default_locations(
            bound,
            location_registrations,
            discovery_exclusions,
        )
        ordered_defaults = [
            view_root
            for view_root in bound.view_roots
            if view_root not in registered_roots or view_root in location_roots
        ]
        ordered_defaults.extend(
            sorted(location_roots.difference(ordered_defaults))
        )
        default_roots = tuple(ordered_defaults)
        return replace(
            bound,
            default_view_roots=default_roots,
            view_namespaces=view_namespaces,
            livewire_classes=_freeze_livewire_classes(bound, livewire_registrations),
        )

    def _psr4_node_ids(self, name: str) -> tuple[str, ...]:
        """List the graph nodes Composer's PSR-4 map would autoload for a name."""
        matched: list[str] = []
        for candidate in self.psr4.file_for_class(name):
            node_id = self.path_to_id.get(_path_key(str(candidate)))
            if node_id is not None and node_id not in matched:
                matched.append(node_id)
        return tuple(matched)

    def _lookup_class(self, fqcn: str) -> str | _Ambiguous | None:
        """Resolve one fully qualified class name; ambiguity is not guessed."""
        name = fqcn.strip().lstrip("\\")
        if not name:
            return None
        declared = self.declaration_to_ids.get(name.casefold(), ())
        if len(declared) == 1:
            return declared[0]
        if declared:
            # Several files declare the name (for example an ide-helper stub
            # next to the real class). PSR-4 is the autoload contract: when it
            # names exactly one of those files, that file owns the class.
            autoloaded = self._psr4_node_ids(name)
            if len(autoloaded) == 1 and autoloaded[0] in declared:
                return autoloaded[0]
            return _AMBIGUOUS

        matched = self._psr4_node_ids(name)
        if len(matched) == 1:
            return matched[0]
        if matched:
            return _AMBIGUOUS

        # A file whose declared class spelling equals the request wins when it
        # is the only one, even when its path does not follow PSR-4.
        exact = self.exact_declaration_to_ids.get(name, ())
        if len(exact) == 1:
            return exact[0]
        if exact:
            return _AMBIGUOUS
        return None

    def _resolve_class(self, fqcn: str) -> str | None:
        result = self._lookup_class(fqcn)
        return None if isinstance(result, _Ambiguous) else result

    @staticmethod
    def _lookup_alias(
        uses: tuple[tuple[str, str], ...],
        segment: str,
    ) -> str | _Ambiguous | None:
        """Look one name up in a file's `use` map, case-insensitively."""
        matches = {
            target for alias, target in uses if alias.casefold() == segment.casefold()
        }
        if len(matches) == 1:
            return next(iter(matches))
        if matches:
            return _AMBIGUOUS
        return None

    def _source_key(self, source_path: str) -> str:
        """Key one source path without a filesystem call when it is indexed."""
        if source_path in self.source_namespaces:
            return source_path
        normalized = os.path.normpath(source_path)
        if normalized in self.source_namespaces:
            return normalized
        return _rooted_key(source_path, self.root)

    def _resolve_symbol(self, raw_name: str, source_path: str) -> str | None:
        """Resolve a class name as written in one source file."""
        name = raw_name.strip()
        if not name:
            return None
        absolute = name.startswith("\\")
        name = name.lstrip("\\")
        if not name:
            return None
        source_key = self._source_key(source_path)
        namespace = self.source_namespaces.get(source_key, "")
        uses = self.source_uses.get(source_key, ())

        candidates: list[str] = []
        if not absolute:
            # An explicit `use` import is authoritative: PHP resolves the
            # written name through it even when an unimported class with the
            # same short name exists in the current namespace or globally.
            if "\\" in name:
                first, _, rest = name.partition("\\")
                alias = self._lookup_alias(uses, first)
                if isinstance(alias, _Ambiguous):
                    return None
                if alias:
                    return self._resolve_class(f"{alias}\\{rest}")
            else:
                alias = self._lookup_alias(uses, name)
                if isinstance(alias, _Ambiguous):
                    return None
                if alias:
                    return self._resolve_class(alias)
            current = namespace
            while current:
                candidates.append(f"{current}\\{name}")
                parent, separator, _ = current.rpartition("\\")
                if not separator:
                    break
                current = parent
        candidates.append(name)

        seen: set[str] = set()
        for candidate in candidates:
            folded = candidate.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            result = self._lookup_class(candidate)
            if isinstance(result, _Ambiguous):
                return None
            if result is not None:
                return result
        return None

    def _resolve_view(
        self,
        reference: str,
        roots: tuple[str, ...] | None = None,
    ) -> str | None:
        """Resolve a Blade view reference to its single template node.

        Explicit roots let a caller own the search space (Livewire template
        fallbacks). Otherwise a registered namespace uses only its registered
        directories; an unregistered namespace conservatively falls back to
        the default roots; a plain reference uses the default roots, which
        exclude module directories owned by a registered namespace.
        """
        value = reference.strip()
        namespace = ""
        if "::" in value:
            namespace, _, value = value.partition("::")
        for suffix in (".blade.php", ".php"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                break
        path = value.replace(".", "/").strip("/")
        if not path:
            return None
        if roots is None:
            if namespace:
                registered = self.view_namespaces.get(namespace)
                if registered:
                    roots = registered
                else:
                    # An unregistered namespace (for example a Blade component
                    # prefix nobody called addNamespace for) is read as the
                    # directory the namespace names under a default root.
                    roots = self.default_view_roots
                    path = f"{namespace}/{path}".strip("/")
            else:
                roots = self.default_view_roots
        if not roots:
            return None
        hits: list[str] = []
        for view_root in roots:
            base = f"{view_root}/{path}" if view_root else path
            for candidate in (base, f"{base}/index", f"{base}.blade"):
                node_ids = self.module_ids.get(candidate, ())
                if node_ids:
                    hits.extend(node_ids)
                    break
        unique = list(dict.fromkeys(hits))
        return unique[0] if len(unique) == 1 else None

    def _resolve_livewire(self, alias: str) -> str | None:
        """Resolve a Livewire alias: registration, convention, then its view."""
        name = alias.strip()
        if not name:
            return None
        registered = self.livewire_classes.get(name.casefold(), ())
        if len(registered) == 1:
            return registered[0]
        if registered:
            return None

        studly = _studly_class_path(name)
        if not studly:
            return None
        candidates: list[str] = []
        seen: set[str] = set()
        for prefix in self.livewire_prefixes:
            candidate = f"{prefix}{_LIVEWIRE_LOGICAL_NAME}\\{studly}"
            folded = candidate.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            candidates.append(candidate)
        matches: list[str] = []
        for candidate in candidates:
            result = self._lookup_class(candidate)
            if isinstance(result, _Ambiguous):
                return None
            if result is not None and result not in matches:
                matches.append(result)
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
        return self._resolve_view(f"livewire.{name}", self.view_roots)

    def _resolve_function(self, raw_name: str, source_path: str) -> str | None:
        """Resolve a function name as written in a call: `\\A\\f` in full; `A\\f` through a `use` of its first
        segment, else below the namespace of the file; `f` in that namespace, then among the global
        functions (PHP falls back to the global function). A name two files declare is not guessed."""
        name = raw_name.strip()
        absolute = name.startswith("\\")
        name = name.lstrip("\\")
        if not name:
            return None
        candidates = [name]
        if not absolute:
            source_key = self._source_key(source_path)
            namespace = self.source_namespaces.get(source_key, "")
            if "\\" in name:
                first, _, rest = name.partition("\\")
                alias = self._lookup_alias(self.source_uses.get(source_key, ()), first)
                if isinstance(alias, _Ambiguous):
                    return None
                candidates = [f"{alias}\\{rest}" if alias else f"{namespace}\\{name}" if namespace else name]
            elif namespace:
                candidates = [f"{namespace}\\{name}", name]
        for candidate in candidates:
            declared = self.function_to_ids.get(candidate.casefold(), ())
            if declared:
                return declared[0] if len(declared) == 1 else None
        return None

    def _resolve_constant(self, raw_name: str) -> str | None:
        declared = self.constant_to_ids.get(raw_name.strip().lstrip("\\"), ())
        return declared[0] if len(declared) == 1 else None

    def _resolve_file(self, relative: str, source_path: str) -> str | None:
        """Resolve the path of a `require __DIR__ . '/...'`, taken from the folder of the requiring file."""
        folder = os.path.dirname(self._source_key(source_path))
        return self.path_to_id.get(_path_key(os.path.join(folder, *relative.split("/"))))

    def _resolve_convention(self, logical_name: str, class_path: str) -> str | None:
        """Resolve `<prefix><logical name>\\<class path>` for the class prefixes of the application
        (`App\\` and the local Composer prefixes): the one class that has such a name, or none."""
        matches: list[str] = []
        seen: set[str] = set()
        for prefix in self.livewire_prefixes:
            candidate = f"{prefix}{logical_name}\\{class_path}"
            if candidate.casefold() in seen:
                continue
            seen.add(candidate.casefold())
            result = self._lookup_class(candidate)
            if isinstance(result, _Ambiguous):
                return None
            if result is not None and result not in matches:
                matches.append(result)
        return matches[0] if len(matches) == 1 else None

    def _resolve_component(self, name: str) -> str | None:
        """Resolve `<x-forms.date-input>` to the class `View\\Components\\Forms\\DateInput` of the application."""
        studly = _studly_class_path(name.strip())
        return self._resolve_convention("View\\Components", studly) if studly else None

    def _resolve_controller(self, raw_name: str) -> str | None:
        """Resolve the controller of a route action string: the class of that full name, else
        `Http\\Controllers\\<name>` of the application (the namespace Laravel gives such strings)."""
        name = raw_name.strip().lstrip("\\")
        if not name:
            return None
        if "\\" in name:
            result = self._lookup_class(name)
            if isinstance(result, _Ambiguous):
                return None
            if result is not None:
                return result
        return self._resolve_convention("Http\\Controllers", name)

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        if ":" not in import_name:
            return None
        prefix, _, remainder = import_name.partition(":")
        if prefix == "php-class":
            return self._resolve_class(remainder)
        if prefix == "php-symbol":
            return self._resolve_symbol(remainder, source_path)
        if prefix == "php-view":
            return self._resolve_view(remainder)
        if prefix == "php-livewire":
            return self._resolve_livewire(remainder)
        if prefix == "php-function":
            return self._resolve_function(remainder, source_path)
        if prefix == "php-const":
            return self._resolve_constant(remainder)
        if prefix == "php-file":
            return self._resolve_file(remainder, source_path)
        if prefix == "php-component":
            return self._resolve_component(remainder)
        if prefix == "php-controller":
            return self._resolve_controller(remainder)
        return None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        """Add the Blade half of a Livewire use, so both nodes gain the edge.

        A Blade template that mounts a component depends on the component class
        (resolve_import) and on its own template; a Livewire component class
        depends on the template named after its class.
        """
        language = getattr(result, "language", "")
        source_path = getattr(result, "path", "")
        added: list[str] = []
        if language == "blade":
            for import_name in getattr(result, "imports", ()):
                if not import_name.startswith("php-livewire:"):
                    continue
                alias = import_name[len("php-livewire:"):].strip()
                if not alias:
                    continue
                view_id = self._resolve_view(f"livewire.{alias}", self.view_roots)
                if view_id is None:
                    continue
                if view_id == self.resolve_import(import_name, source_path):
                    continue
                added.append(view_id)
        elif language == "php":
            source_key = self._source_key(source_path)
            for view_name in self.livewire_view_names.get(source_key, ()):
                view_id = self._resolve_view(view_name, self.view_roots)
                if view_id is not None:
                    added.append(view_id)
        return tuple(sorted(set(added)))