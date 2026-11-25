from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping

from ..interface import FileResult


def _freeze_index(index: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(sorted(targets))
        for name, targets in index.items()
    }


@dataclass(frozen=True)
class BoundJavaLanguage:
    type_to_ids: Mapping[str, tuple[str, ...]]
    folder_of: Mapping[str, str]   # node id -> the folder of its file

    @classmethod
    def create(cls, path_to_id: Mapping[str, str]) -> BoundJavaLanguage:
        from .parser import declared_java_types

        type_index: dict[str, set[str]] = {}
        for path, node_id in sorted(path_to_id.items()):
            if not path.endswith(".java"):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as source:
                    content = source.read()
            except OSError as error:
                print(
                    f"[code-graph] Cannot index Java declarations from {path}: {error}",
                    file=sys.stderr,
                )
                continue
            for full_name in declared_java_types(content):
                type_index.setdefault(full_name, set()).add(node_id)
        return cls(
            _freeze_index(type_index),
            {node_id: os.path.dirname(path) for path, node_id in path_to_id.items() if path.endswith(".java")},
        )

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        candidate = import_name.removesuffix(".*")
        while candidate:
            type_targets = self.type_to_ids.get(candidate, ())
            if len(type_targets) == 1:
                return type_targets[0]
            if len(type_targets) > 1 and "." not in candidate:
                # A type of the default package that several folders declare: the one beside the file.
                folder = os.path.dirname(source_path)
                beside = [target for target in type_targets if self.folder_of.get(target) == folder]
                if len(beside) == 1:
                    return beside[0]
            candidate, separator, _ = candidate.rpartition(".")
            if not separator:
                break
        return None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
