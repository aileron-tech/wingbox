from __future__ import annotations

import re
import sys

import tree_sitter_typescript as _tst
from tree_sitter import Language as _TsLanguage
from tree_sitter import Node, Parser as _TsParser

from ..interface import BindingPayload, Entity, FileResult, ImportedName, UsePayload
from ..purpose import infer_purpose
from ..source import add_import_line, detect_naming, file_hash
from .javascript import is_javascript_file
from .symbols import declared_type, module_bindings, module_names, use_sites
from .validation import validation_evidence


_TS_LANG = _TsLanguage(_tst.language_typescript())
_TSX_LANG = _TsLanguage(_tst.language_tsx())


def _node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _ts_get_decorators(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract @Decorator names from a node's preceding decorator nodes."""
    decorators = []
    for child in node.children:
        if child.type == "decorator":
            # decorator -> @expression -- get the identifier
            for dchild in child.children:
                if dchild.type == "identifier":
                    decorators.append(dchild.text.decode("utf-8"))
                elif dchild.type == "call_expression":
                    fn = dchild.child_by_field_name("function")
                    if fn:
                        decorators.append(fn.text.decode("utf-8").split(".")[-1])
    return decorators


def _ts_get_heritage(node) -> tuple[list[str], list[str]]:  # type: ignore[no-untyped-def]
    """Extract extends and implements from class_heritage."""
    extends: list[str] = []
    implements: list[str] = []
    for child in node.children:
        if child.type == "class_heritage":
            for hchild in child.children:
                if hchild.type == "extends_clause":
                    for t in hchild.children:
                        if t.type in ("type_identifier", "identifier"):
                            extends.append(t.text.decode("utf-8"))
                elif hchild.type == "implements_clause":
                    for t in hchild.children:
                        if t.type in ("type_identifier", "identifier"):
                            implements.append(t.text.decode("utf-8"))
    return extends, implements


_PLAIN_MEMBER_NAMES = frozenset({
    "property_identifier", "identifier", "private_property_identifier", "shorthand_property_identifier", "number",
})


def _member_name(node: Node | None) -> str:
    """The name of a member as the graph keeps it: an identifier, a number, or a string key written on
    one line without an escape. A computed key (`[expr]`, a template literal) is an expression, not a
    name, and gives "": its text, with its line breaks, never becomes a fact of the graph."""
    if node is None or node.type not in _PLAIN_MEMBER_NAMES and node.type != "string":
        return ""
    name = _node_text(node)
    return name if name.isprintable() and "\\" not in name else ""


def _ts_get_methods(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract method/property names from a class or interface body."""
    methods = []
    body = node.child_by_field_name("body")
    if not body:
        return methods
    skip = {"constructor", "if", "for", "while", "switch", "return"}
    for member in body.children:
        if member.type in ("method_definition", "method_signature",
                           "public_field_definition", "property_signature",
                           "abstract_method_signature"):
            name = _member_name(member.child_by_field_name("name"))
            if name and name not in skip:
                methods.append(name)
    return methods


# NestJS decorators that imply constructor-parameter dependency injection.
_NESTJS_INJECTION_DECOS: frozenset[str] = frozenset({
    "Injectable", "Controller", "Module", "Guard", "Interceptor", "Pipe", "Resolver",
})


def _ts_get_constructor_injections(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract interface/type names from constructor parameter types and @Inject decorators.

    Handles NestJS constructor injection patterns where typed constructor parameters
    declare interface dependencies. Only PascalCase names are returned to avoid
    tracking primitive or built-in types.
    """
    injected: list[str] = []
    body = node.child_by_field_name("body")
    if not body:
        return injected
    for member in body.children:
        if member.type != "method_definition":
            continue
        name_node = member.child_by_field_name("name")
        if not name_node or not name_node.text:
            continue
        if name_node.text.decode("utf-8") != "constructor":
            continue
        params = member.child_by_field_name("parameters")
        if not params:
            continue
        for param in params.children:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            # Check for @Inject(TOKEN) decorator on the parameter
            for child in param.children:
                if child.type == "decorator":
                    for dc in child.children:
                        if dc.type == "call_expression":
                            fn = dc.child_by_field_name("function")
                            if fn and fn.text and fn.text.decode("utf-8") == "Inject":
                                args = dc.child_by_field_name("arguments")
                                if args:
                                    for arg in args.children:
                                        if arg.type in ("identifier", "string", "template_string"):
                                            text = arg.text
                                            if text:
                                                token = text.decode("utf-8").strip("'\"`")
                                                if token and token[0].isupper():
                                                    injected.append(token)
            # Check type annotation on the parameter
            type_ann = param.child_by_field_name("type")
            if type_ann:
                for tc in type_ann.children:
                    if tc.type in ("type_identifier", "identifier"):
                        ttext = tc.text
                        if ttext:
                            tname = ttext.decode("utf-8")
                            if tname and tname[0].isupper():
                                injected.append(tname)
    return injected


def _ts_get_obj_keys(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract top-level property names from an object literal."""
    keys = []
    for child in node.children:
        if child.type in ("pair", "method_definition", "shorthand_property_identifier"):
            key = _member_name(child if child.type == "shorthand_property_identifier" else child.child_by_field_name("key"))
            if key:
                keys.append(key)
    return keys


def _ts_import_statement_form(node) -> str:  # type: ignore[no-untyped-def]
    """Form of one import statement: "type", "named" or "value".

    "type": `import type ...`, or named imports that all carry `type`.
    "named": only named imports, at least one without `type`.
    "value": a default, namespace or side-effect import.
    """
    clause = next((child for child in node.children if child.type == "import_clause"), None)
    if clause is None:
        return "value"
    if any(child.type == "type" for child in node.children):
        return "type"
    if any(child.type in ("identifier", "namespace_import") for child in clause.children):
        return "value"
    specifiers = [
        specifier
        for child in clause.children
        if child.type == "named_imports"
        for specifier in child.children
        if specifier.type == "import_specifier"
    ]
    if not specifiers:
        return "value"
    if all(any(part.type == "type" for part in specifier.children) for specifier in specifiers):
        return "type"
    return "named"


# Calls whose first literal argument names a module: loading it, or mocking it in a test,
# which breaks when the module moves or changes its exports.
_MODULE_CALLS = frozenset({
    "import", "require", "import.meta.resolve", "require.resolve",
    "vi.mock", "vi.doMock", "vi.importActual", "vi.importMock",
    "jest.mock", "jest.doMock", "jest.requireActual", "jest.requireMock",
})


_LONGEST_MODULE_CALL = max(len(name.encode("utf-8")) for name in _MODULE_CALLS)


def _ts_dependency_occurrences(root) -> list[tuple[str, str, int]]:  # type: ignore[no-untyped-def]
    """Every static and literal runtime dependency in source order, with the form of its statement and the
    1-based line where the statement (or the call that loads the module) starts."""
    occurrences: list[tuple[str, str, int]] = []

    def _append_literal(node, form: str, line: int) -> None:  # type: ignore[no-untyped-def]
        if node is None or not node.text:
            return
        if node.type == "string":
            specifier = node.text.decode("utf-8").strip("'\"")
        elif node.type == "template_string":
            if any(
                child.type == "template_substitution"
                for child in node.named_children
            ):
                return
            specifier = node.text.decode("utf-8").strip("`")
        else:
            return
        if specifier:
            occurrences.append((specifier, form, line))

    # Every named node, each before the nodes below it: a list of what is still to visit, not the call
    # stack, so that a chain of thousands of calls is read like any other expression.
    todo = [root]
    while todo:
        node = todo.pop()
        if node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            form = _ts_import_statement_form(node) if node.type == "import_statement" else "value"
            if source is None and node.type == "import_statement":
                import_require = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "import_require_clause"
                    ),
                    None,
                )
                if import_require is not None:
                    source = import_require.child_by_field_name("source")
                    form = "value"
            _append_literal(source, form, node.start_point[0] + 1)
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            # A name longer than every name of _MODULE_CALLS is none of them; its text is not read (the
            # function of the last call of a long chain is the whole chain before it).
            function_name = (
                function.text.decode("utf-8")
                if function is not None and function.end_byte - function.start_byte <= _LONGEST_MODULE_CALL and function.text
                else ""
            )
            if function_name in _MODULE_CALLS:
                arguments = node.child_by_field_name("arguments")
                if arguments is not None:
                    # A comment may stand before the literal: import(/* gated */ "./x").
                    first_argument = next(
                        (child for child in arguments.named_children if child.type != "comment"), None
                    )
                    _append_literal(first_argument, "value", node.start_point[0] + 1)

        todo.extend(reversed(node.named_children))

    return occurrences


# A line that starts an import or a re-export; the quoted module of `... from "x"`; `import "x"`. Each
# is matched against one line, and no part can give back what it took to the part after it.
_STATIC_STATEMENT = re.compile(r"[ \t]*(?:import(?![\w$])|export[ \t]*(?:type[ \t]*)?[{*])")
_FROM_MODULE = re.compile(r"""(?<![\w$])from[ \t]*(['"])([^'"\\]+)\1""")
_BARE_IMPORT = re.compile(r"""[ \t]*import[ \t]*(['"])([^'"\\]+)\1""")
_NOT_CODE = frozenset({"comment", "string", "string_fragment", "template_string", "regex", "regex_pattern"})


def _text_static_imports(root: Node, content: str) -> list[tuple[str, int]]:
    """(module, line) of the import and re-export statements of a file, read from its lines.

    For a file with a syntax error: the tree of such a file ends in an error node, and what stands after
    the error is not in it. A statement starts on a line with `import` or `export {` / `export *` and
    names its module on the first line from there that holds a quote; a line inside a comment or a string
    of the tree starts none. Every line is read once.
    """
    found: list[tuple[str, int]] = []
    lines = content.split("\n")
    number = 0
    while number < len(lines):
        first, line = number, lines[number]
        number += 1
        start = _STATIC_STATEMENT.match(line) if "port" in line else None
        if start is None:
            continue
        column = len(line) - len(line.lstrip(" \t"))
        node: Node | None = root.descendant_for_point_range((first, column), (first, column))
        if any(ancestor is not None and ancestor.type in _NOT_CODE for ancestor in (node, node.parent if node is not None else None)):
            continue
        bare = _BARE_IMPORT.match(line)
        if bare is not None:
            found.append((bare.group(2), first + 1))
            continue
        while "'" not in line and '"' not in line and ";" not in line and number < len(lines):
            line = lines[number]
            number += 1
        module = _FROM_MODULE.search(line)
        if module is not None:
            found.append((module.group(2), first + 1))
    return found


_Occurrences = list[tuple[str, str, int]]   # what _ts_dependency_occurrences returns: one walk serves the three below


def _ts_dependency_specifiers(occurrences: _Occurrences) -> list[str]:
    """Extract static and literal runtime dependencies in source order."""
    return list(dict.fromkeys(specifier for specifier, _form, _line in occurrences))


def _ts_import_lines(occurrences: _Occurrences) -> dict[str, list[int]]:
    """Specifier -> the lines of the statements and calls that name it."""
    lines: dict[str, list[int]] = {}
    for specifier, _form, line in occurrences:
        add_import_line(lines, specifier, line)
    return lines


def _ts_import_forms(occurrences: _Occurrences) -> dict[str, str]:
    """Specifiers that this file imports for names only, never as a value.

    "type" when every statement that names the specifier is a type import,
    "named" when every statement binds only named imports; a specifier with a
    default, namespace, side-effect, re-export or runtime import is left out.
    """
    forms: dict[str, set[str]] = {}
    for specifier, form, _line in occurrences:
        forms.setdefault(specifier, set()).add(form)
    return {
        specifier: "type" if seen == {"type"} else "named"
        for specifier, seen in forms.items()
        if "value" not in seen
    }


def _use_sites(root: Node, bindings: list[BindingPayload], language: _TsLanguage, full: bool) -> list[UsePayload]:
    in_scope = [binding for binding in bindings if binding["scope"] and binding["local"] != "*"]
    return use_sites(
        root,
        module_names(root) | {binding["local"] for binding in in_scope},
        language,
        full,
        frozenset(binding["local"] for binding in in_scope if not binding["name"]),
    )


def typescript_use_positions(path: str, content: str) -> list[UsePayload]:
    """Every use site of the file (names, chains, method calls), for `usages` and `entity --positions`.

    A build keeps only the uses that can name another file; this is the full list, computed
    when a query first needs positions.
    """
    language = _TSX_LANG if path.endswith(".tsx") or is_javascript_file(path) else _TS_LANG
    root = _TsParser(language).parse(content.encode("utf-8")).root_node
    return _use_sites(root, module_bindings(root), language, full=True)


def parse_typescript_treesitter(
    path: str, content: str, repo: str, mtime: float,
) -> FileResult:
    """Parse a TypeScript file using tree-sitter for precise entity extraction."""
    # JavaScript may hold JSX in any of its extensions; the TSX grammar reads both.
    is_javascript = is_javascript_file(path)
    lang = _TSX_LANG if path.endswith(".tsx") or is_javascript else _TS_LANG
    parser = _TsParser(lang)
    tree = parser.parse(content.encode("utf-8"))
    root = tree.root_node

    occurrences = _ts_dependency_occurrences(root)
    if root.has_error:
        # What stands after a place the grammar cannot read is not in the tree: the import and re-export
        # statements of the file are read from its text as well, so that it costs no dependency. The cause
        # is more often syntax the grammar does not know yet (`export type * from`, a tagged template with
        # type arguments) than an error in the file, so the message does not call it one.
        known = {specifier for specifier, _form, _line in occurrences}
        occurrences.extend((specifier, "value", line) for specifier, line in _text_static_imports(root, content)
                           if specifier not in known)
        print(
            f"[code-graph] WARNING: the TypeScript parser could not read all of {path}: its imports are read "
            "from the text as well; a declaration after the part it could not read may be missing",
            file=sys.stderr,
        )
    imports = _ts_dependency_specifiers(occurrences)
    exports_abstract: list[str] = []
    implements_list: list[str] = []
    extends_list: list[str] = []
    entities: list[Entity] = []
    imported_names: list[ImportedName] = []
    exported_names: list[str] = []

    for node in root.children:
        is_export = node.type == "export_statement"
        target = node
        if is_export:
            # The actual declaration is a child of export_statement.
            # Skip keywords, decorators, and punctuation to find the declaration.
            for child in node.children:
                if child.type in ("export", "default", "comment", "decorator",
                                  "{", "}", ",", ";"):
                    continue
                target = child
                break
        if target.type == "ambient_declaration":
            # declare const x: T; declare function f(); declare class C {}
            target = next((child for child in target.named_children), target)
        elif target.type == "expression_statement" and target.named_children \
                and target.named_children[0].type == "internal_module":
            target = target.named_children[0]

        # -- Imports --
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                specifier = _node_text(source_node).strip("'\"")
                # Extract named imports
                for child in node.children:
                    if child.type == "import_clause":
                        for ic in child.children:
                            if ic.type == "identifier":
                                # default import
                                imported_names.append(ImportedName(
                                    local_name=_node_text(ic),
                                    original_name="default",
                                    module=specifier,
                                    line=node.start_point[0] + 1,
                                ))
                            elif ic.type == "named_imports":
                                for spec in ic.children:
                                    if spec.type == "import_specifier":
                                        name_node = spec.child_by_field_name("name")
                                        alias_node = spec.child_by_field_name("alias")
                                        if name_node:
                                            orig = _node_text(name_node)
                                            local = _node_text(alias_node) or orig
                                            imported_names.append(ImportedName(
                                                local_name=local,
                                                original_name=orig,
                                                module=specifier,
                                                line=node.start_point[0] + 1,
                                            ))

        # -- Class --
        elif target.type == "class_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            decorators = _ts_get_decorators(node if is_export else target)
            ext, impl = _ts_get_heritage(target)
            methods = _ts_get_methods(target)
            extends_list.extend(ext)
            implements_list.extend(impl)
            # NestJS @Injectable/@Controller/@Module: constructor param types are
            # interface dependencies that should create CALLS_API edges.
            if _NESTJS_INJECTION_DECOS.intersection(decorators):
                constructor_deps = _ts_get_constructor_injections(target)
                implements_list.extend(constructor_deps)
            # Check if abstract
            is_abstract = any(c.type == "abstract" for c in target.children)
            if is_abstract:
                exports_abstract.append(name)
            entities.append(Entity(
                name=name, kind="class", line=line,
                methods=methods, decorators=decorators, bases=ext + impl,
            ))
            exported_names.append(name)

        # -- Abstract class: an entity like a class; its heritage stays out of the
        # interface edges so that module dependencies do not change.
        elif target.type == "abstract_class_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            entities.append(Entity(
                name=name, kind="class", line=target.start_point[0] + 1,
                methods=_ts_get_methods(target),
                decorators=_ts_get_decorators(node if is_export else target), bases=[],
            ))
            exported_names.append(name)

        # -- Namespace --
        elif target.type == "internal_module":
            name_node = target.child_by_field_name("name")
            if not name_node or name_node.type != "identifier":
                continue
            name = _node_text(name_node)
            entities.append(Entity(
                name=name, kind="module", line=target.start_point[0] + 1,
                methods=[], decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Interface --
        elif target.type == "interface_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            methods = _ts_get_methods(target)
            exports_abstract.append(name)
            entities.append(Entity(
                name=name, kind="interface", line=line,
                methods=methods, decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Type alias --
        elif target.type == "type_alias_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            entities.append(Entity(
                name=name, kind="type", line=line,
                methods=[], decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Enum (its members are listed as methods, as Rust variants) --
        elif target.type == "enum_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            body = target.child_by_field_name("body")
            members = [
                _member_name(member.child_by_field_name("name") if member.type == "enum_assignment" else member)
                for member in (body.named_children if body is not None else [])
                if member.type in ("property_identifier", "enum_assignment")
            ]
            entities.append(Entity(
                name=name, kind="enum", line=line,
                methods=[member for member in members if member], decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Function --
        elif target.type in ("function_declaration", "generator_function_declaration", "function_signature"):
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            decorators = _ts_get_decorators(node if is_export else target)
            entities.append(Entity(
                name=name, kind="function", line=line,
                methods=[], decorators=decorators, bases=[],
            ))
            exported_names.append(name)

        # -- Lexical declaration (const/let) --
        elif target.type in ("lexical_declaration", "variable_declaration"):
            for declarator in target.children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                if not name_node or name_node.type != "identifier":
                    continue
                name = _node_text(name_node)
                line = declarator.start_point[0] + 1
                value = declarator.child_by_field_name("value")
                # The class the variable holds (`const db: Database`, `const db = new Database()`): a method
                # called on it from any file is a use of that class's method.
                held = ".".join(declared_type(declarator) or ())
                if not value:
                    # let x; declare const x: T;
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[], value_type=held,
                    ))
                    exported_names.append(name)
                    continue
                # Object literal: extract keys as methods
                if value.type == "object":
                    obj_keys = _ts_get_obj_keys(value)
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=obj_keys, decorators=[], bases=[], value_type=held,
                    ))
                # new Constructor(...)
                elif value.type == "new_expression":
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[], value_type=held,
                    ))
                # Arrow function or call expression
                elif value.type in ("arrow_function", "call_expression"):
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[], value_type=held,
                    ))
                # Other const (e.g., z.object, string literal, etc.)
                else:
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[], value_type=held,
                    ))
                exported_names.append(name)

    bindings = module_bindings(root)
    validation = validation_evidence(root, bindings, lang)
    file_lang = "tsx" if path.endswith(".tsx") else "typescript"
    if is_javascript:
        file_lang = "javascript"
    return FileResult(
        path=path, language=file_lang, repo=repo, mtime=mtime,
        content_hash=file_hash(path),
        imports=imports, exports_abstract=exports_abstract,
        implements=implements_list, extends=extends_list,
        purpose=infer_purpose(path, content, "typescript", [e.to_dict() for e in entities]),
        naming_convention=detect_naming(path),
        has_validation=bool(validation),
        entities=[e.to_dict() for e in entities],
        imported_names=[n.to_dict() for n in imported_names],
        exported_names=exported_names,
        import_forms=_ts_import_forms(occurrences),
        import_lines=_ts_import_lines(occurrences),
        bindings=bindings,
        uses=_use_sites(root, bindings, lang, full=False),
        validation_evidence=validation,
    )
