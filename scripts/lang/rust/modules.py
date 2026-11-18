"""Which file a Rust module lives in: the layout rules of the compiler.

`mod name;` is `name.rs` or `name/mod.rs` in the directory of the declaring module. That directory is the
folder of the file when the file is a crate root, a `mod.rs` or a file loaded through `#[path]`; for any
other file `a.rs` it is the folder `a/` next to it. An inline module adds its name to the directory.
`#[path = "f.rs"]` replaces the lookup: the file is relative to the folder of the declaring file (to the
inline module's directory when the declaration is inside one), and the loaded file owns its folder like a
`mod.rs`. `#[cfg_attr(cond, path = "f.rs")]` names one candidate per condition; all of them are kept.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

_PATH_ATTRIBUTE = re.compile(r'(?<![A-Za-z0-9_])path\s*=\s*"((?:[^"\\]|\\.)*)"')
_MAY_DECLARE_PATH = re.compile(r'#\s*\[[^\]]*?(?<![A-Za-z0-9_])path\s*=\s*"')
_OWNING_FILE_NAMES = ("lib.rs", "main.rs", "mod.rs")


@dataclass(frozen=True)
class PathModule:
    """A module declared with a path attribute."""

    declaring_file: str
    inline: tuple[str, ...]  # the inline modules around the declaration, the outermost first
    name: str
    paths: tuple[str, ...]  # as written, one per attribute
    has_body: bool  # an inline module: the attribute names its directory


def path_modules(file_path: str, text: str) -> tuple[PathModule, ...]:
    """The `#[path]` module declarations of one file (none without parsing when the text has no such attribute)."""
    if _MAY_DECLARE_PATH.search(text) is None:
        return ()
    import tree_sitter_rust
    from tree_sitter import Language, Node, Parser

    found: list[PathModule] = []

    def visit(container: Node, inline: tuple[str, ...]) -> None:
        attributes: list[str] = []
        for child in container.named_children:
            if child.type == "attribute_item":
                attributes.append((child.text or b"").decode("utf-8"))
                continue
            if child.type in ("line_comment", "block_comment"):
                continue
            if child.type == "mod_item":
                name_node = child.child_by_field_name("name")
                name = (name_node.text or b"").decode("utf-8").removeprefix("r#") if name_node is not None else ""
                body = child.child_by_field_name("body")
                paths = tuple(match.group(1) for attribute in attributes
                              if re.match(r"#\s*\[\s*(?:path|cfg_attr)\b", attribute)
                              for match in _PATH_ATTRIBUTE.finditer(attribute))
                if name and paths:
                    found.append(PathModule(file_path, inline, name, paths, body is not None))
                if name and body is not None:
                    visit(body, (*inline, name))
            attributes = []

    visit(Parser(Language(tree_sitter_rust.language())).parse(text.encode("utf-8")).root_node, ())
    return tuple(found)


@dataclass(frozen=True)
class ModuleLayout:
    """Module directories and files of a tree, with the `#[path]` declarations applied."""

    crate_roots: frozenset[str]
    loaded: Mapping[str, str] = field(default_factory=dict)  # file loaded through #[path] -> directory of its parent module
    files: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)  # (directory, name) -> files
    directories: Mapping[tuple[str, str], str] = field(default_factory=dict)  # (directory, name) -> its directory
    declared: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # declaring file -> the files it loads

    @classmethod
    def create(cls, crate_roots: Iterable[str], declarations: Iterable[PathModule]) -> ModuleLayout:
        roots = frozenset(os.path.normpath(root) for root in crate_roots)
        declarations = tuple(declarations)
        layout = cls(roots)
        # The directory of a declaring file depends on whether a `#[path]` loaded it: repeat until stable.
        for _round in range(len(declarations) + 1):
            loaded: dict[str, str] = {}
            files: dict[tuple[str, str], tuple[str, ...]] = {}
            directories: dict[tuple[str, str], str] = {}
            declared: dict[str, list[str]] = {}
            for declaration in sorted(declarations, key=lambda item: (item.declaring_file, len(item.inline))):
                own = layout.module_directory(declaration.declaring_file)
                attribute_base = os.path.dirname(declaration.declaring_file)
                for name in declaration.inline:
                    own = directories.get((own, name), os.path.join(own, name))
                    attribute_base = own
                targets = tuple(os.path.normpath(os.path.join(attribute_base, path)) for path in declaration.paths)
                if declaration.has_body:
                    directories[(own, declaration.name)] = targets[0]
                    continue
                files[(own, declaration.name)] = targets
                directories[(own, declaration.name)] = os.path.dirname(targets[0])
                for target in targets:
                    loaded.setdefault(target, own)
                    declared.setdefault(declaration.declaring_file, []).append(target)
            stable = loaded == layout.loaded
            layout = cls(roots, loaded, files, directories, {path: tuple(targets) for path, targets in declared.items()})
            if stable:
                break
        return layout

    def module_directory(self, file_path: str) -> str:
        """The directory where the modules a file declares are looked up."""
        file_path = os.path.normpath(file_path)
        if os.path.basename(file_path) in _OWNING_FILE_NAMES or file_path in self.crate_roots or file_path in self.loaded:
            return os.path.dirname(file_path)
        return os.path.splitext(file_path)[0]

    def parent_directory(self, file_path: str) -> str:
        """The module directory of the parent of a file's module (what `super` names)."""
        file_path = os.path.normpath(file_path)
        return self.loaded.get(file_path) or os.path.dirname(self.module_directory(file_path))

    def candidates(self, directory: str, segments: tuple[str, ...]) -> tuple[str, ...]:
        """The files that can define the module a path names below a module directory."""
        for name in segments[:-1]:
            directory = self.directories.get((directory, name), os.path.join(directory, name))
        name = segments[-1]
        module_path = os.path.join(directory, name)
        return self.files.get((directory, name)) or (f"{module_path}.rs", os.path.join(module_path, "mod.rs"))


__all__ = ["ModuleLayout", "PathModule", "path_modules"]
