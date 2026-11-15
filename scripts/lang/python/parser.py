from __future__ import annotations

import ast
import re
import sys

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash


PY_ABC_RE = re.compile(r"""class\s+\w+\s*\(.*(?:ABC|ABCMeta|Protocol).*\)""")


def _detect_validation(content: str) -> bool:
    head = content[:4096]
    if re.search(
        r"pydantic|BaseModel|validator|field_validator|Annotated\[|attrs|@attr",
        head,
    ):
        return True
    if re.search(r"@dataclass", head) and re.search(
        r"frozen\s*=\s*True|def\s+__post_init__|:\s*(?:str|int|float|bool|list|dict|Optional)",
        head,
    ):
        return True
    return bool(re.search(r"TypedDict|NamedTuple", head))

def parse_python(path: str, content: str, repo: str, mtime: float) -> FileResult:
    imports: list[str] = []
    exports_abstract: list[str] = []
    implements: list[str] = []
    extends: list[str] = []
    entities: list[Entity] = []
    imported_names: list[ImportedName] = []
    exported_names: list[str] = []

    try:
        tree = ast.parse(content, filename=path)

        # Imports can be conditional or function-local, so walk the full tree.
        # module_of_name: the module path that each imported name stands for.
        module_of_name: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
                    local = alias.asname or alias.name.split(".")[-1]
                    imported_names.append(ImportedName(
                        local_name=local,
                        original_name=alias.name,
                        module=alias.name,
                    ))
                    if alias.asname:
                        module_of_name[alias.asname] = alias.name
                    else:
                        package = alias.name.split(".")[0]
                        module_of_name[package] = package
            elif isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                if mod:
                    imports.append(mod)
                for alias in node.names:
                    local = alias.asname or alias.name
                    imported_names.append(ImportedName(
                        local_name=local,
                        original_name=alias.name,
                        module=mod,
                    ))
                    if mod and alias.name != "*":
                        module_of_name[local] = _submodule(mod, alias.name)

        # Entities remain top-level so nested functions do not become modules.
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                decorators = [
                    _decorator_name(d) for d in node.decorator_list
                ]
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)
                bases_list = [_name_of(b) for b in node.bases if _name_of(b)]
                for base in bases_list:
                    qualified = _qualified_base(base, module_of_name)
                    if qualified is not None and qualified not in imported_names:
                        imported_names.append(qualified)
                entities.append(Entity(
                    name=node.name,
                    kind="class",
                    line=node.lineno,
                    methods=methods,
                    decorators=decorators,
                    bases=bases_list,
                ))
                exported_names.append(node.name)
                extends.extend(bases_list)
                for b in bases_list:
                    if b.rsplit(".", 1)[-1] in ("ABC", "Protocol") or "Base" in b:
                        implements.append(b)

            # -- functions --
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    _decorator_name(d) for d in node.decorator_list
                ]
                entities.append(Entity(
                    name=node.name,
                    kind="function",
                    line=node.lineno,
                    methods=[],
                    decorators=decorators,
                    bases=[],
                ))
                exported_names.append(node.name)

            # -- top-level assignments (constants, type aliases) --
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        exported_names.append(target.id)

    except SyntaxError as error:
        print(
            f"[code-graph] WARNING: AST parse failed for {path}: {error}",
            file=sys.stderr,
        )
        # Fallback: regex for imports only
        for m in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
            content, re.MULTILINE,
        ):
            imp = m.group(1) or m.group(2)
            if imp:
                imports.append(imp)

    # Detect ABC / Protocol definitions
    for m in PY_ABC_RE.finditer(content):
        name = m.group(0).split("(")[0].split()[-1]
        if name not in exports_abstract:
            exports_abstract.append(name)

    return FileResult(
        path=path, language="python", repo=repo, mtime=mtime,
        content_hash=file_hash(path),
        imports=imports, exports_abstract=exports_abstract,
        implements=implements, extends=extends,
        purpose=infer_purpose(path, content, "python", [e.to_dict() for e in entities]),
        naming_convention=detect_naming(path),
        has_validation=_detect_validation(content),
        entities=[e.to_dict() for e in entities],
        imported_names=[n.to_dict() for n in imported_names],
        exported_names=exported_names,
    )


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _name_of(node: ast.expr) -> str:
    """A base class as written: ``Base`` or ``package.module.Base``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _name_of(node.value)
        return f"{owner}.{node.attr}" if owner else ""
    return ""


def _submodule(module: str, name: str) -> str:
    return module + name if module.endswith(".") else f"{module}.{name}"


def _qualified_base(
    base: str,
    module_of_name: dict[str, str],
) -> ImportedName | None:
    """Describe ``module.Class`` as the name ``Class`` taken from that module.

    The module is the one that the first name is bound to by an import in this
    file. A first name that no import binds gives nothing: the base is then a
    local object and no other file provides it.
    """
    owner, _, class_name = base.rpartition(".")
    if not owner:
        return None
    first, _, inner = owner.partition(".")
    bound_module = module_of_name.get(first)
    if bound_module is None:
        return None
    module = _submodule(bound_module, inner) if inner else bound_module
    return ImportedName(local_name=base, original_name=class_name, module=module)
