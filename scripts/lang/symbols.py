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
    # C and C++: an include is a glob import of the included file; a namespace is not a module.
    "cpp": None, "c": None,
}
# Languages whose only import is the inclusion of a whole file: no name is renamed or hidden on the way,
# so what a name means behind the includes is decided by the files they reach, not by the route to them.
_INCLUDE_LANGUAGES = frozenset({"cpp", "c"})
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp")
_MAX_DEPTH = 16
_ENDLESS = _MAX_DEPTH + 1  # the height of a chain of glob imports that no search follows to its end
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
    whose definitions or bindings it looked at (("m", id)), the module names
    it resolved (("q", spec)) and the modules whose glob imports it searched
    (("c", id)). Every memoised answer keeps what it read, so a later answer
    served from the memo still names it; begin() and end() collect what the
    uses of one file read, for an update that redoes only the files whose
    inputs changed.

    A name looked for behind the glob imports of a module (`from .a import *`,
    `export * from`, `pub use a::*`) may be defined by any module those imports
    reach, directly or through further globs: the answer reads all of them.
    One item, ("c", module), stands for that whole set (glob_record gives the
    part each module adds to it), so that an answer keeps a few items however
    many modules stand behind a facade, and the search itself visits only the
    glob imports that reach a module holding the name.
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
        # For the search behind glob imports: the modules that define or bind each name, the modules
        # the globs of a module reach, and through which of its glob imports each of them is reached.
        self._holders: Optional[dict[str, set[str]]] = None
        self._reached: dict[str, frozenset[str]] = {}
        self._routes: dict[str, dict[str, tuple[int, ...]]] = {}
        self._heights: dict[str, int] = {}
        self._tallest: dict[str, list[tuple[int, int]]] = {}

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
        """The modules a module glob-imports or re-exports with `*`, and whether in its own scope.

        What this reads (the module, the names of its glob imports) is part of the ("c", module) record
        and is kept out of the answer being traced."""
        found = self._glob_targets.get(module)
        if found is None:
            if self._trace:
                self._stack.append(set())
            result = self._results[module]
            found = []
            for binding in self._bindings_named(module, "*"):
                target = self.module_id(ModuleRef(binding["module"], result.path, result.language))
                if target is not None:
                    found.append((target, binding["scope"]))
            if self._trace:
                self._stack.pop()
            self._glob_targets[module] = found
        return found

    def glob_record(self, module: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What a module adds to the set that ("c", ...) stands for: the modules its glob imports name
        and the module names of those imports as written (also the ones that resolve to nothing), sorted.
        The whole set of ("c", m) is m, this record of m and of every module reached through the first part."""
        result = self._results.get(module)
        stars = [binding for binding in result.bindings if binding["local"] == "*"] if result is not None else []
        if not stars:
            return (), ()
        targets = sorted({target for target, _scope in self._globs_of(module)})
        return tuple(targets), tuple(sorted({binding["module"] for binding in stars}))

    def _holding(self, name: str) -> Optional[set[str]]:
        """The modules that define name or bind it by an import: the only ones `export` can answer for."""
        if self._holders is None:
            holders: dict[str, set[str]] = {}
            for module, result in self._results.items():
                for defined in self._defined[module]:
                    holders.setdefault(defined, set()).add(module)
                for binding in result.bindings:
                    holders.setdefault(binding["local"], set()).add(module)
            self._holders = holders
        return self._holders.get(name)

    def _reach(self, module: str) -> frozenset[str]:
        """Every module the glob imports of module reach, through any number of further glob imports."""
        known = self._reached.get(module)
        if known is not None:
            return known
        found: set[str] = set()
        todo = [target for target, _scope in self._globs_of(module)]
        while todo:
            current = todo.pop()
            if current in found:
                continue
            found.add(current)
            reached = self._reached.get(current)
            if reached is not None:
                found.update(reached)
            else:
                todo.extend(target for target, _scope in self._globs_of(current))
        self._reached[module] = frozenset(found)
        return self._reached[module]

    def _height(self, module: str) -> int:
        """How many glob imports the longest chain below module strings together; _ENDLESS when the chains
        reach a re-export cycle (or are longer than any search goes)."""
        heights = self._heights
        if module in heights:
            return heights[module]
        path = [module]
        on_path = {module}
        pending = [[target for target, _scope in self._globs_of(module)]]
        best = [0]
        while path:
            current, targets = path[-1], pending[-1]
            if targets:
                target = targets.pop()
                if target in on_path:
                    best[-1] = _ENDLESS
                elif target in heights:
                    best[-1] = max(best[-1], min(_ENDLESS, heights[target] + 1))
                else:
                    path.append(target)
                    on_path.add(target)
                    pending.append([inner for inner, _scope in self._globs_of(target)])
                    best.append(0)
                continue
            path.pop()
            pending.pop()
            on_path.discard(current)
            heights[current] = height = best.pop()
            if best:
                best[-1] = max(best[-1], min(_ENDLESS, height + 1))
        return heights[module]

    def _tallest_globs(self, module: str) -> list[tuple[int, int]]:
        """(height of the target, position in _globs_of) of every glob import of module, tallest first."""
        found = self._tallest.get(module)
        if found is None:
            found = sorted(
                ((self._height(target), position) for position, (target, _scope) in enumerate(self._globs_of(module))),
                reverse=True,
            )
            self._tallest[module] = found
        return found

    def _glob_routes(self, module: str) -> dict[str, tuple[int, ...]]:
        """Each module the globs of module reach -> the positions (in _globs_of) of the glob imports that reach it."""
        routes = self._routes.get(module)
        if routes is None:
            collected: dict[str, list[int]] = {}
            for position, (target, _scope) in enumerate(self._globs_of(module)):
                collected.setdefault(target, []).append(position)
                for reached in self._reach(target):
                    if reached != target:
                        collected.setdefault(reached, []).append(position)
            routes = {reached: tuple(positions) for reached, positions in collected.items()}
            self._routes[module] = routes
        return routes

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
        if result.language in _INCLUDE_LANGUAGES:
            return self._from_includes(module, name)
        if result.language == "python" and name.startswith("_"):
            return None
        globs = self._globs_of(module)
        if not globs and not self._bindings_named(module, "*"):
            return None
        # The answer depends on every module behind the globs: any of them may come to hold the name.
        self._touch(("c", module))
        # `export` answers None for a module when neither it nor a module its globs reach holds the name:
        # only the glob imports that reach a holder are followed, in their order.
        positions: set[int] = set()
        holders = self._holding(name)
        if holders:
            routes = self._glob_routes(module)
            if len(holders) <= len(routes):
                positions.update(position for holder in holders for position in routes.get(holder, ()))
            else:
                positions.update(
                    position for reached, through in routes.items() if reached in holders for position in through
                )
        # A glob import behind which the search would stop at a re-export cycle or at the depth limit is
        # followed as well: such a stop decides which answers are kept (see export), as it always did.
        within = _MAX_DEPTH - depth - 1
        for height, position in self._tallest_globs(module):
            if height <= within:
                break
            positions.add(position)
        found: set[Target] = set()
        for position in sorted(positions):
            target, scope = globs[position]
            if in_scope and not scope:
                continue
            resolved = self.export(target, name, depth + 1, seen)
            if resolved is not None:
                found.add(resolved)
        return next(iter(found)) if len(found) == 1 else None

    def _from_includes(self, module: str, name: str) -> Optional[Target]:
        """C and C++: the one file among those the includes of module reach, through any number of further
        includes, that defines name. A file between the two only passes the name on, so the files are not
        asked one by one: a translation unit reaches hundreds of headers, and every name it writes would
        walk them again. Two reachable files with the name leave it unresolved, as everywhere."""
        self._touch(("c", module))
        holders = self._holding(name)
        if not holders:
            return None
        reached = self._reach(module)
        if len(holders) <= len(reached):
            found = [holder for holder in holders if holder in reached and name in self._defined[holder]]
        else:
            found = [holder for holder in reached if holder in holders and name in self._defined[holder]]
        if len(found) == 1:
            return Symbol(found[0], name)
        if len(found) > 1 and all(self._entities.get(holder, {}).get(name, {}).get("kind") == "function" for holder in found):
            # A function is written twice by design: its prototype in a header and its definition in a
            # source file, or the same prototype in two headers. A file that reaches both (it includes
            # the source file, or both headers) uses that one function: the prototype of a header, the
            # first by path, stands for it.
            headers = sorted(holder for holder in found if self._results[holder].path.casefold().endswith(_HEADER_SUFFIXES))
            return Symbol((headers or sorted(found))[0], name)
        return None

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

    def _member_owner(self, module: str, name: str, member: str) -> Optional[Symbol]:
        """The class that defines member: name itself or, through its bases, an ancestor."""
        key = (module, name, member)
        if key in self._owners:
            self._hit(("o",) + key)
        else:
            self._open()
            self._owners[key] = self._find_member_owner(module, name, member)
            self._close(("o",) + key, True)
        return self._owners[key]

    def _find_member_owner(self, module: str, name: str, member: str) -> Optional[Symbol]:
        """Depth first through the bases in the order written, each class once: classes that name each
        other as bases (a name resolved to the wrong class, a cycle through re-exports) end the search."""
        seen: set[tuple[str, str]] = set()
        todo = [(module, name)]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            self._touch(("m", current[0]))
            entity = self._entities.get(current[0], {}).get(current[1])
            if entity is None or entity["kind"] not in _MEMBER_KINDS:
                continue
            if member in entity.get("methods", []):
                return Symbol(*current)
            bases: list[tuple[str, str]] = []
            for base in entity.get("bases", []):
                target = self._walk_written(current[0], base)
                if isinstance(target, Symbol):
                    bases.append((target.module, target.name))
            todo.extend(reversed(bases))  # the first base is searched first, with all above it
        return None

    def _walk_written(self, module: str, written: str) -> Optional[Target]:
        """What a dotted name written in module (a base class, the type of a variable) names."""
        separator = "::" if self._results[module].language == "rust" else "."
        segments = [segment for segment in written.split(separator) if segment]
        return self._walk(module, segments) if segments else None

    def _member_of(self, symbol: Symbol, member: str, follow_type: bool) -> Optional[Symbol]:
        """The class that defines member for a chain that stands at symbol: a class, an enum or a trait,
        or (follow_type) a module-level variable whose entity states the class it holds."""
        self._touch(("m", symbol.module))
        entity = self._entities.get(symbol.module, {}).get(symbol.name)
        if entity is None:
            return None
        if entity["kind"] not in _MEMBER_KINDS:
            held = self._walk_written(symbol.module, entity["type"]) if follow_type and entity.get("type") else None
            if not isinstance(held, Symbol):
                return None
            symbol = held
        return self._member_owner(symbol.module, symbol.name, member)

    def _walk(self, module: str, path: list[str], start: Optional[ModuleRef] = None) -> Optional[Target]:
        """What path names in the scope of module, or inside the module start when one is given."""
        current: Optional[Target] = start if start is not None else self._head(module, path[0])
        for segment in path if start is not None else path[1:]:
            if not isinstance(current, ModuleRef):
                return current if isinstance(current, Symbol) else None
            current = self._top_member(current, segment)
        return current

    def resolve_member(self, module: str, use: UsePayload) -> Optional[ResolvedUse]:
        """A method call: Class.method when the receiver's type is in sight and defines it."""
        path, at, member = use["path"], use["at"], use.get("member", "")
        if not path or not member or len(at) != len(path) + 1:
            return None
        result = self._results[module]
        written = use.get("module")
        target = self._walk(module, path, ModuleRef(written, result.path, result.language) if written else None)
        if not isinstance(target, Symbol):
            return None
        # Rust: the path may name a static or a constant the method is called on (GLOBAL.save()); in the
        # other languages it is always a type, and a call on a name that holds an instance builds nothing.
        owner = self._member_of(target, member, follow_type=result.language == "rust")
        if owner is None:
            return None
        line, column = at[-1]
        return ResolvedUse(owner.module, f"{owner.name}.{member}", line, column, "call")

    def resolve(self, module: str, use: UsePayload) -> Optional[ResolvedUse]:
        """The definition a use site names, with the position of the segment that names it."""
        found = self.resolve_all(module, use)
        return found[0] if found else None

    def resolve_all(self, module: str, use: UsePayload) -> tuple[ResolvedUse, ...]:
        """Every definition a use site names: the one resolve() answers and, when the path goes on for
        one more segment from a class, an enum, a trait or a module-level variable whose class is in
        sight, the member that segment names (Store.create(), Model::create(), db.query())."""
        if use.get("member"):
            resolved = self.resolve_member(module, use)
            return (resolved,) if resolved is not None else ()
        self._touch(("m", module))
        result = self._results[module]
        path, at = use["path"], use["at"]
        if not path or len(at) != len(path):
            return ()
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
        if not isinstance(current, Symbol) or index < 0:
            return ()
        line, column = at[index]
        # The kind belongs to the last segment: Foo in Foo::new() is a plain reference.
        kind = use.get("kind", "") if index == len(path) - 1 else ""
        found = [ResolvedUse(current.module, current.name, line, column, kind)]
        if index == len(path) - 2:
            owner = self._member_of(current, path[-1], follow_type=True)
            if owner is not None:
                line, column = at[-1]
                found.append(ResolvedUse(owner.module, f"{owner.name}.{path[-1]}", line, column, use.get("kind", "")))
        return tuple(found)


def supports_symbols(language: str) -> bool:
    return language in _SEPARATORS


__all__ = ["ModuleRef", "ResolvedUse", "Symbol", "SymbolIndex", "supports_symbols"]
