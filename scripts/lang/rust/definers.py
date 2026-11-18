"""Rust: the file that defines an item a file names through a facade, a glob import or a type path.

A facade is a module that re-exports the items of other modules (`pub use child::*`, `pub use child::A`).
A file that names an item depends on the file that defines it; the edge to the facade stays. A member named
through a type path (`Type::member`, `Self::member`, `<Type>::member`) is defined by an inherent impl block
of the type, which may lie in another file than the type; the compiler finds it among all inherent impl
blocks of the type in the crate, by name. A method called on a value (`value.method()`) and a member a
trait gives need the type checker and give no edge here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..interface import FileResult
from .modules import ModuleLayout

Resolve = Callable[[str, str], str | None]
Definition = tuple[str, str]  # (module ID, name of the item in that module)
_FOLLOWED_ALIASES = 4


def _rest_of_path(spec: str, source: str, target: str, resolve: Resolve) -> list[str]:
    """The segments a path writes after the module it resolves to (crate::m::A::new -> [A, new])."""
    segments = [segment for segment in spec.split("::") if segment]
    for count in range(1, len(segments)):
        if resolve("::".join(segments[:count]), source) == target:
            return segments[count:]
    return []


def _named_item(spec: str, source: str, target: str, resolve: Resolve) -> str | None:
    """The item a path names after the module it resolves to (crate::m::A -> A), or None for a module path or
    a name inside an inline module of the target file."""
    rest = _rest_of_path(spec, source, target, resolve)
    inline_module = len(rest) > 1 and rest[0][:1].islower()
    return None if not rest or inline_module else rest[0]


def _has_variant(result: FileResult | None, enum: str, name: str) -> bool:
    return result is not None and any(
        entity["name"] == enum and entity["kind"] == "enum" and name in entity.get("methods", ())
        for entity in result.entities)


def _module_aliases(result: FileResult) -> dict[str, str]:
    """`use crate::store;` then `store::Cache`: the names a `use` binds, with the path each stands for."""
    return {
        str(binding["local"]): f"{binding.get('module')}::{binding.get('name')}" if binding.get("module") else str(binding.get("name"))
        for binding in result.bindings
        if binding.get("local") not in (None, "", "*") and binding.get("name") not in (None, "", "*")
    }


@dataclass
class Definers:
    """The definitions behind the names of one set of parse results. `resolve` is the resolver to ask (a traced
    one during a graph update); results are the parse results by module ID."""

    results: Mapping[str, FileResult]
    resolve: Resolve
    layout: ModuleLayout
    macros: Mapping[str, frozenset[str]]
    path_to_id: Mapping[str, str]
    _members: dict[Definition, dict[str, set[str]]] | None = field(default=None, repr=False)

    def named_imports(self, result: FileResult) -> tuple[dict[str, frozenset[str]], int]:
        """Files that define the names a file imports through a module facade or names as members of a type, and
        how many names could not be followed.

        `use crate::m::{A, B}` (or `m::A` after `use crate::m`) where m re-exports its children (`pub use child::*`,
        `pub use child::A`, also `pub(crate)` and through a grandchild) depends on the child that defines A: that
        edge carries the relation "item" (the file names an item of the module; `impact` follows it like any import,
        unlike the entity-level "reexport" of refs). A glob import `use crate::m::*` gives "item" and "candidate" to
        the children whose items the body names. A name the facade itself defines, or one in an inline module of it,
        stays with the facade. A name no file defines (made by a macro in a module a glob re-exports, or behind cfg)
        is counted; its importer keeps the facade edge, which is always kept. `Type::member` gives "item" to the
        file whose inherent impl block of the type defines the member."""
        resolve = self.resolve
        found: dict[str, set[str]] = {}
        not_followed = 0
        aliases = _module_aliases(result)
        for spec in result.imports:
            target = resolve(spec, result.path)
            head, _, rest = spec.partition("::")
            if target is None and rest and head in aliases:
                spec = f"{aliases[head]}::{rest}"
                target = resolve(spec, result.path)
            if target is None:
                continue
            facade, rest = self._through_module_reexports(target, _rest_of_path(spec, result.path, target, resolve))
            if facade != target:
                found.setdefault(facade, set()).add("item")
            if not rest or (len(rest) > 1 and rest[0][:1].islower()):
                continue  # a module path, or a name inside an inline module
            definition = self._definition(facade, rest[0], set(), self._inside(result, facade), result.path)
            if definition is None:
                not_followed += 1
            elif definition[0] != target:
                found.setdefault(definition[0], set()).add("item")
        # Names the body uses, alone or as the head of a path (`Handler::new()`, `Kind::A`). A name the file
        # defines or imports by name shadows a glob import.
        shadowing = {entity["name"] for entity in result.entities} | set(aliases)
        used = {use["path"][0] for use in result.uses if "module" not in use and use.get("kind") != "import"} - shadowing
        for binding in result.bindings:
            if binding.get("name") != "*" or binding.get("export"):
                continue
            facade = resolve(str(binding.get("module", "")), result.path)
            if facade is None:
                continue
            inside = self._inside(result, facade)
            for name in sorted(used):
                definition = self._definition(facade, name, set(), inside, result.path)
                if definition is not None and definition[0] != facade:
                    found.setdefault(definition[0], set()).update(("item", "candidate"))
        for target in self._member_files(result):
            found.setdefault(target, set()).add("item")
        return {target: frozenset(relations) for target, relations in found.items()}, not_followed

    def _through_module_reexports(self, module: str, rest: list[str]) -> tuple[str, list[str]]:
        """Follow a path through modules a facade re-exports by name (`pub use self::inner as api;`, then
        `facade::api::X`): the module that holds the rest of the path, and that rest."""
        for _step in range(len(rest)):
            result = self.results.get(module)
            if result is None or len(rest) < 2:
                break
            binding = next((item for item in result.bindings if item.get("export") and item.get("local") == rest[0]
                            and item.get("name") not in (None, "", "*")), None)
            if binding is None:
                break
            spec = f"{binding.get('module')}::{binding.get('name')}"
            child = self.resolve(spec, result.path)
            if child is None or child == module or _rest_of_path(spec, result.path, child, self.resolve):
                break  # not a module: an item of that name
            module, rest = child, rest[1:]
        return module, rest

    def _inside(self, result: FileResult, module: str) -> bool:
        """Whether the file of result lies inside module (a descendant sees the private imports of its ancestors)."""
        facade = self.results.get(module)
        if facade is None:
            return False
        return os.path.normpath(result.path).startswith(self.layout.module_directory(facade.path) + os.sep)

    def _visible(self, entity: Mapping[str, object], owner: FileResult, importer: str) -> bool:
        """Whether an item of restricted visibility (`pub(super)`, `pub(in path)`) is visible in the importer file."""
        scope = str(entity.get("visible_in", ""))
        if not scope or not importer:
            return True
        if scope == "super":
            directory = self.layout.parent_directory(owner.path)
        else:
            holder = self.results.get(self.resolve(scope, owner.path) or "")
            if holder is None:
                return True
            directory = self.layout.module_directory(holder.path)
        importer = os.path.normpath(importer)
        return importer.startswith(directory + os.sep) or self.layout.module_directory(importer) == directory

    def _definition(self, module: str, name: str, seen: set[str], inside: bool = False, importer: str = "") -> Definition | None:
        """The module that defines name as module sees it, and the name of the item there: module itself, or the
        file a `pub use` leads to; with inside (the importer is a descendant of module), also a private `use` of
        module itself. importer is the file that names the item: a re-export carries a `pub(super)` or
        `pub(in path)` item only into that scope."""
        if module in seen:
            return None
        seen.add(module)
        result = self.results.get(module)
        if result is None:
            return None
        # The module the path names may define the item with any visibility (a child module sees the private items of
        # its parent); a module reached through a re-export must make it visible to the facade (`pub`, `pub(crate)`,
        # `pub(super)`, `pub(in ...)`): a glob re-export does not carry a private item.
        reached = len(seen) > 1
        if any(entity["name"] == name and not (reached and (entity.get("private") or not self._visible(entity, result, importer)))
               for entity in result.entities):
            return module, name
        if name in self.macros.get(os.path.normpath(result.path), ()):
            return module, name  # a macro_rules! macro named by path (`pub(crate) use name;`)
        for binding in result.bindings:
            if not binding.get("export") and not (inside and not reached):
                continue
            original, local, source = binding.get("name", ""), binding.get("local", ""), binding.get("module", "")
            if original == "*":
                child = self.resolve(source, result.path)
                if child is None:
                    continue
                enum = _named_item(source, result.path, child, self.resolve)
                if enum is not None:
                    # `pub use kinds::Kind::*;` carries the variants of the enum, not the items of its module.
                    if _has_variant(self.results.get(child), enum, name):
                        return child, name
                    continue
                found = self._definition(child, name, seen, False, importer)
                if found is not None:
                    return found
            elif local == name and original:
                child = self.resolve(f"{source}::{original}", result.path)
                if child is not None and child != module:
                    # A name the child defines in no visible item (a macro invocation expands to it) stays with
                    # the child: the re-export names that module.
                    return self._definition(child, original, seen, False, importer) or (child, original)
        return None

    # ------------------------------------------------------------------ members of a type
    def _type(self, result: FileResult, written: tuple[str, ...], followed: int = 0) -> Definition | None:
        """The type a path written in a file names: the module that defines it and its name there, a type alias
        followed to the type it stands for."""
        module = self.path_to_id.get(os.path.normpath(result.path))
        if module is None or not written:
            return None
        found: Definition | None = None
        if len(written) == 1:
            name = written[0]
            binding = next((item for item in result.bindings
                            if item.get("local") == name and item.get("name") not in (None, "", "*")), None)
            if any(entity["name"] == name for entity in result.entities):
                found = (module, name)
            elif binding is not None:
                found = self._path_item(result, f"{binding.get('module')}::{binding.get('name')}")
            else:
                for glob in result.bindings:
                    facade = self.resolve(str(glob.get("module", "")), result.path) if glob.get("name") == "*" else None
                    found = self._definition(facade, name, set(), self._inside(result, facade), result.path) if facade is not None else None
                    if found is not None:
                        break
        else:
            aliases = _module_aliases(result)
            spec = "::".join((aliases.get(written[0], written[0]), *written[1:]))
            found = self._path_item(result, spec)
        if found is None or followed >= _FOLLOWED_ALIASES:
            return found
        owner = self.results.get(found[0])
        alias = next((entry for entry in (owner.type_members if owner is not None else ()) if entry.get("alias") == found[1]), None)
        if alias is not None and owner is not None:
            return self._type(owner, tuple(alias["type"]), followed + 1) or found
        return found

    def _path_item(self, result: FileResult, spec: str) -> Definition | None:
        target = self.resolve(spec, result.path)
        named = _named_item(spec, result.path, target, self.resolve) if target is not None else None
        if target is None or named is None:
            return None
        return self._definition(target, named, set(), self._inside(result, target), result.path)

    def _member_index(self) -> dict[Definition, dict[str, set[str]]]:
        """Type -> member name -> the modules whose inherent impl blocks of the type define the member."""
        if self._members is None:
            self._members = {}
            for module, result in self.results.items():
                for entry in result.type_members:
                    if "alias" in entry or "trait" in entry:
                        continue
                    owner = self._type(result, tuple(entry["type"]))
                    for member in entry["members"] if owner is not None else ():
                        self._members.setdefault(owner, {}).setdefault(member, set()).add(module)
        return self._members

    def _member_files(self, result: FileResult) -> set[str]:
        """The modules that define the members a file names through a type path (the module of the type too: a
        file that names the type through an alias of another module has no other edge to it)."""
        index = self._member_index()
        if not index:
            return set()
        types: dict[tuple[str, ...], tuple[Definition | None, ...]] = {}

        def owners(written: tuple[str, ...]) -> tuple[Definition | None, ...]:
            if written not in types:
                # `Self` is the type of the impl block around the path: any type the file gives members to.
                types[written] = tuple(self._type(result, tuple(entry["type"])) for entry in result.type_members
                                       if "alias" not in entry) if written == ("Self",) else (self._type(result, written),)
            return types[written]

        files: set[str] = set()
        for use in result.uses:
            path = use["path"]
            if len(path) < 2 or "module" in use or "member" in use or use.get("kind") in ("import", "re-export"):
                continue
            for owner in owners(tuple(path[:-1])):
                if owner is not None:
                    files.update(index.get(owner, {}).get(path[-1], ()))
        return files


__all__ = ["Definers", "Resolve"]
