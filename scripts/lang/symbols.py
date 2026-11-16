"""Symbol index: resolve the use sites of a file to the module that defines each name.

Language-neutral. A file gives its definitions (entities), its module-level
import bindings and its use sites (interface.BindingPayload, UsePayload); the
bound language resolves a module as written to a module of the graph. A name is
followed through re-exports (an import binding of the module, a glob import) to
its definition, with cycle protection; a name with two candidate definitions is
left unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union

from .interface import FileResult, UsePayload

# Languages whose parsers give bindings and use sites, and how a child module is
# written; None: a member of a module is never a module of its own (TypeScript).
_SEPARATORS: dict[str, Optional[str]] = {
    "python": ".", "rust": "::", "typescript": None, "tsx": None, "javascript": None,
}
_MAX_DEPTH = 16
# Entity kinds whose methods a call can name: classes, structs, enums, interfaces, traits.
_MEMBER_KINDS = frozenset({"class", "enum", "interface"})


@dataclass(frozen=True)
class Symbol:
    module: str
    name: str


@dataclass(frozen=True)
class ModuleRef:
    """A module as written in one file: spec resolved from origin (a source path)."""

    spec: str
    origin: str
    language: str


Target = Union[Symbol, ModuleRef]
ResolveImport = Callable[[str, str, str], Optional[str]]  # (language, spec, source path) -> module id


@dataclass(frozen=True)
class ResolvedUse:
    module: str
    name: str
    line: int
    column: int


def _join(language: str, spec: str, name: str) -> Optional[str]:
    separator = _SEPARATORS[language]
    if separator is None:
        return None
    if separator == "." and spec.endswith("."):
        return spec + name
    return f"{spec}{separator}{name}" if spec else name


class SymbolIndex:
    """Definitions, bindings and globs of every module, resolved lazily and memoised."""

    def __init__(
        self,
        id_to_result: Mapping[str, FileResult],
        resolve_import: ResolveImport,
    ) -> None:
        self._results = id_to_result
        self._resolve_import = resolve_import
        self._modules: dict[tuple[str, str, str], Optional[str]] = {}
        self._defined = {
            module: frozenset(entity["name"] for entity in result.entities)
            for module, result in id_to_result.items()
        }
        self._entities = {
            module: {entity["name"]: entity for entity in result.entities}
            for module, result in id_to_result.items()
        }

    def module_id(self, ref: ModuleRef) -> Optional[str]:
        key = (ref.language, ref.spec, ref.origin)
        if key not in self._modules:
            self._modules[key] = self._resolve_import(ref.language, ref.spec, ref.origin)
        return self._modules[key]

    def child(self, ref: ModuleRef, name: str) -> Optional[ModuleRef]:
        """The child module name of ref (a file of its own), if there is one."""
        spec = _join(ref.language, ref.spec, name)
        if spec is None:
            return None
        child = ModuleRef(spec, ref.origin, ref.language)
        child_id = self.module_id(child)
        if child_id is not None and child_id != self.module_id(ref):
            return child
        return None

    def _binding_target(
        self, module: str, local: str, depth: int, seen: frozenset, in_scope: bool = False,
    ) -> Optional[Target]:
        """What local is bound to in module; in_scope: only bindings of the file's own scope."""
        result = self._results[module]
        language = result.language
        for binding in result.bindings:
            if binding["local"] != local or (in_scope and not binding["scope"]):
                continue
            if not binding["module"]:
                # export { k as K } or export default k: a name of the file itself.
                return self.export(module, binding["name"], depth + 1, seen)
            ref = ModuleRef(binding["module"], result.path, language)
            if not binding["name"]:
                return ref
            return self.member(ref, binding["name"], depth + 1, seen)
        return None

    def export(self, module: str, name: str, depth: int = 0, seen: frozenset = frozenset()) -> Optional[Target]:
        """What name means to an importer of module: its definition, or what a re-export names."""
        if (module, name) in seen or depth > _MAX_DEPTH or module not in self._results:
            return None
        seen = seen | {(module, name)}
        if name in self._defined[module]:
            return Symbol(module, name)
        bound = self._binding_target(module, name, depth, seen)
        if bound is not None:
            return bound
        return self._from_globs(module, name, depth, seen)

    def _from_globs(
        self, module: str, name: str, depth: int, seen: frozenset, in_scope: bool = False,
    ) -> Optional[Target]:
        result = self._results[module]
        if result.language == "python" and name.startswith("_"):
            return None
        found: set[Target] = set()
        for binding in result.bindings:
            if binding["local"] != "*" or (in_scope and not binding["scope"]):
                continue
            target = self.module_id(ModuleRef(binding["module"], result.path, result.language))
            if target is None:
                continue
            resolved = self.export(target, name, depth + 1, seen)
            if resolved is not None:
                found.add(resolved)
        return next(iter(found)) if len(found) == 1 else None

    def member(self, ref: ModuleRef, name: str, depth: int = 0, seen: frozenset = frozenset()) -> Optional[Target]:
        """name inside the module ref: a child module, or a name the module defines or re-exports."""
        child = self.child(ref, name)
        if child is not None:
            return child
        module = self.module_id(ref)
        return self.export(module, name, depth, seen) if module is not None else None

    def _head(self, module: str, name: str) -> Optional[Target]:
        """A name in the scope of a file: its own definition, an import binding, a glob import."""
        result = self._results[module]
        if name in self._defined[module]:
            return Symbol(module, name)
        bound = self._binding_target(module, name, 0, frozenset({(module, name)}), in_scope=True)
        if bound is not None:
            return bound
        if result.language == "rust" and name in ("crate", "self", "super"):
            return ModuleRef(name, result.path, "rust")
        glob = self._from_globs(module, name, 0, frozenset({(module, name)}), in_scope=True)
        if glob is not None:
            return glob
        if result.language == "rust" and name[:1].islower():
            # A child module declared with `mod name;` or a crate of the workspace.
            ref = ModuleRef(name, result.path, "rust")
            if self.module_id(ref) is not None:
                return ref
        return None

    def _member_owner(self, module: str, name: str, member: str, depth: int = 0) -> Optional[Symbol]:
        """The class that defines member: name itself or, through its bases, an ancestor."""
        entity = self._entities.get(module, {}).get(name)
        if entity is None or depth > _MAX_DEPTH or entity["kind"] not in _MEMBER_KINDS:
            return None
        if member in entity.get("methods", []):
            return Symbol(module, name)
        separator = "::" if self._results[module].language == "rust" else "."
        for base in entity.get("bases", []):
            segments = [segment for segment in base.split(separator) if segment]
            target = self._walk(module, segments) if segments else None
            if isinstance(target, Symbol):
                owner = self._member_owner(target.module, target.name, member, depth + 1)
                if owner is not None:
                    return owner
        return None

    def _walk(self, module: str, path: list[str]) -> Optional[Target]:
        current = self._head(module, path[0])
        for segment in path[1:]:
            if not isinstance(current, ModuleRef):
                return current if isinstance(current, Symbol) else None
            current = self.member(current, segment)
        return current

    def resolve_member(self, module: str, use: UsePayload) -> Optional[ResolvedUse]:
        """A method call: Class.method when the receiver's type is in sight and defines it."""
        path, at, member = use["path"], use["at"], use.get("member", "")
        if not path or not member or len(at) != len(path) + 1:
            return None
        target = self._walk(module, path)
        if not isinstance(target, Symbol):
            return None
        owner = self._member_owner(target.module, target.name, member)
        if owner is None:
            return None
        line, column = at[-1]
        return ResolvedUse(owner.module, f"{owner.name}.{member}", line, column)

    def resolve(self, module: str, use: UsePayload) -> Optional[ResolvedUse]:
        """The definition a use site names, with the position of the segment that names it."""
        if use.get("member"):
            return self.resolve_member(module, use)
        result = self._results[module]
        path, at = use["path"], use["at"]
        if not path or len(at) != len(path):
            return None
        written = use.get("module")
        if written:
            current: Optional[Target] = ModuleRef(written, result.path, result.language)
            start = 0
        else:
            current = self._head(module, path[0])
            start = 1
        index = start - 1
        while isinstance(current, ModuleRef) and index + 1 < len(path):
            index += 1
            current = self.member(current, path[index])
        if isinstance(current, Symbol) and index >= 0:
            line, column = at[index]
            return ResolvedUse(current.module, current.name, line, column)
        return None


def supports_symbols(language: str) -> bool:
    return language in _SEPARATORS


__all__ = ["ModuleRef", "ResolvedUse", "Symbol", "SymbolIndex", "supports_symbols"]
