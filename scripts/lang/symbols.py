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
    kind: str = ""  # import, re-export, call, type, extends; "" any other reference


def _join(language: str, spec: str, name: str) -> Optional[str]:
    separator = _SEPARATORS[language]
    if separator is None:
        return None
    if separator == "." and spec.endswith("."):
        return spec + name
    return f"{spec}{separator}{name}" if spec else name


class SymbolIndex:
    """Definitions, bindings and globs of every module, resolved lazily and memoised.

    With trace=True the index also records what each answer read: the modules
    whose definitions or bindings it looked at (("m", id)) and the module names
    it resolved (("q", spec)). Every memoised answer keeps what it read, so a
    later answer served from the memo still names it; begin() and end() collect
    what the uses of one file read, for an update that redoes only the files
    whose inputs changed.
    """

    def __init__(
        self,
        id_to_result: Mapping[str, FileResult],
        resolve_import: ResolveImport,
        trace: bool = False,
    ) -> None:
        self._trace = trace
        self._stack: list[set] = []
        self._deps: dict[tuple, frozenset] = {}
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
        # Lookups that start afresh (no re-export chain in progress) depend only on the index:
        # memoised, as one file names the same few imports many times.
        self._heads: dict[tuple[str, str], Optional[Target]] = {}
        self._members: dict[tuple[ModuleRef, str], Optional[Target]] = {}
        self._owners: dict[tuple[str, str, str], Optional[Symbol]] = {}
        # What a module exports, memoised when no re-export cycle cut the answer short
        # (barrels of `export *` are walked once, not once per import through them).
        self._exports: dict[tuple[str, str], Optional[Target]] = {}
        self._cuts = 0
        self._by_local: dict[str, dict[str, list]] = {}
        self._glob_targets: dict[str, list[tuple[str, bool]]] = {}

    # -- tracing ------------------------------------------------------------

    def begin(self) -> None:
        self._stack.append(set())

    def end(self) -> frozenset:
        return frozenset(self._stack.pop())

    def _touch(self, item: tuple) -> None:
        if self._trace and self._stack:
            self._stack[-1].add(item)

    def _open(self) -> None:
        if self._trace:
            self._stack.append(set())

    def _close(self, key: tuple, memoised: bool) -> None:
        if not self._trace:
            return
        read = self._stack.pop()
        if memoised:
            self._deps[key] = frozenset(read)
        if self._stack:
            self._stack[-1].update(read)

    def _hit(self, key: tuple) -> None:
        if self._trace and self._stack:
            self._stack[-1].update(self._deps.get(key, ()))

    # -- lookups --------------------------------------------------------------

    def _bindings_named(self, module: str, local: str) -> list:
        self._touch(("m", module))
        if module not in self._by_local:
            index: dict[str, list] = {}
            for binding in self._results[module].bindings:
                index.setdefault(binding["local"], []).append(binding)
            self._by_local[module] = index
        return self._by_local[module].get(local, [])

    def _globs_of(self, module: str) -> list[tuple[str, bool]]:
        """The modules a module glob-imports or re-exports with `*`, and whether in its own scope."""
        key = ("g", module)
        if module in self._glob_targets:
            self._hit(key)
            return self._glob_targets[module]
        self._open()
        result = self._results[module]
        found: list[tuple[str, bool]] = []
        for binding in self._bindings_named(module, "*"):
            target = self.module_id(ModuleRef(binding["module"], result.path, result.language))
            if target is not None:
                found.append((target, binding["scope"]))
        self._glob_targets[module] = found
        self._close(key, True)
        return found

    def module_id(self, ref: ModuleRef) -> Optional[str]:
        self._touch(("q", ref.spec))
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
        self._touch(("m", module))
        result = self._results[module]
        language = result.language
        for binding in self._bindings_named(module, local):
            if in_scope and not binding["scope"]:
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
        key = (module, name)
        if key in self._exports:
            self._hit(("e",) + key)
            return self._exports[key]
        self._touch(("m", module))
        if module not in self._results:
            return None
        if key in seen or depth > _MAX_DEPTH:
            self._cuts += 1
            return None
        self._open()
        self._touch(("m", module))  # also in the memoised answer's own record
        cuts = self._cuts
        seen = seen | {key}
        found: Optional[Target]
        if name in self._defined[module]:
            found = Symbol(module, name)
        else:
            found = self._binding_target(module, name, depth, seen)
            if found is None:
                found = self._from_globs(module, name, depth, seen)
        memoised = self._cuts == cuts
        if memoised:
            self._exports[key] = found
        self._close(("e",) + key, memoised)
        return found

    def _from_globs(
        self, module: str, name: str, depth: int, seen: frozenset, in_scope: bool = False,
    ) -> Optional[Target]:
        self._touch(("m", module))
        result = self._results[module]
        if result.language == "python" and name.startswith("_"):
            return None
        found: set[Target] = set()
        for target, scope in self._globs_of(module):
            if in_scope and not scope:
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
        key = (module, name)
        if key in self._heads:
            self._hit(("h",) + key)
        else:
            self._open()
            self._heads[key] = self._find_head(module, name)
            self._close(("h",) + key, True)
        return self._heads[key]

    def _top_member(self, ref: ModuleRef, name: str) -> Optional[Target]:
        key = (ref, name)
        if key in self._members:
            self._hit(("t",) + key)
        else:
            self._open()
            self._members[key] = self.member(ref, name)
            self._close(("t",) + key, True)
        return self._members[key]

    def _find_head(self, module: str, name: str) -> Optional[Target]:
        self._touch(("m", module))
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
        if depth == 0:
            key = (module, name, member)
            if key in self._owners:
                self._hit(("o",) + key)
            else:
                self._open()
                self._owners[key] = self._find_member_owner(module, name, member, 0)
                self._close(("o",) + key, True)
            return self._owners[key]
        return self._find_member_owner(module, name, member, depth)

    def _find_member_owner(self, module: str, name: str, member: str, depth: int) -> Optional[Symbol]:
        self._touch(("m", module))
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
            current = self._top_member(current, segment)
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
        return ResolvedUse(owner.module, f"{owner.name}.{member}", line, column, "call")

    def resolve(self, module: str, use: UsePayload) -> Optional[ResolvedUse]:
        """The definition a use site names, with the position of the segment that names it."""
        if use.get("member"):
            return self.resolve_member(module, use)
        self._touch(("m", module))
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
            current = self._top_member(current, path[index])
        if isinstance(current, Symbol) and index >= 0:
            line, column = at[index]
            # The kind belongs to the last segment: Foo in Foo::new() is a plain reference.
            kind = use.get("kind", "") if index == len(path) - 1 else ""
            return ResolvedUse(current.module, current.name, line, column, kind)
        return None


def supports_symbols(language: str) -> bool:
    return language in _SEPARATORS


__all__ = ["ModuleRef", "ResolvedUse", "Symbol", "SymbolIndex", "supports_symbols"]
