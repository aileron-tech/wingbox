"""Reading tsconfig files (JSON with comments) and the options the resolver needs."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class TsPathOptions:
    """The path options of a tsconfig once its `extends` chain is applied."""

    base_directory: Optional[str]
    paths_directory: Optional[str]
    paths: tuple[tuple[str, tuple[str, ...]], ...]
    output_directory: Optional[str] = None
    source_directory: Optional[str] = None


_NO_TS_PATH_OPTIONS = TsPathOptions(None, None, ())


def strip_jsonc(text: str) -> str:
    """Drop comments and trailing commas; string literals are kept verbatim."""
    kept: list[str] = []
    pending_comma: Optional[int] = None
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            end = index + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            kept.append(text[index : end + 1])
            pending_comma = None
            index = end + 1
        elif text.startswith("//", index):
            line_end = text.find("\n", index)
            index = len(text) if line_end == -1 else line_end
        elif text.startswith("/*", index):
            block_end = text.find("*/", index + 2)
            index = len(text) if block_end == -1 else block_end + 2
        else:
            if char == ",":
                pending_comma = len(kept)
            elif char in "}]" and pending_comma is not None:
                kept[pending_comma] = ""
                pending_comma = None
            elif not char.isspace():
                pending_comma = None
            kept.append(char)
            index += 1
    return "".join(kept)


def read_json_object(path: str, purpose: str) -> Optional[dict[str, object]]:
    """Read a JSON or JSONC object; an unreadable file is reported and skipped."""
    try:
        # utf-8-sig also reads plain UTF-8 and drops the byte order mark editors add.
        with open(path, "r", encoding="utf-8-sig") as file:
            data = json.loads(strip_jsonc(file.read()))
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"[code-graph] Cannot read {purpose} from {path}: {error}",
            file=sys.stderr,
        )
        return None
    return data if isinstance(data, dict) else None


def _declared_directory(directory: str, value: object) -> Optional[str]:
    """Resolve a directory option against the tsconfig that declares it."""
    if not isinstance(value, str):
        return None
    return os.path.normpath(os.path.join(directory, value))


def _own_ts_path_options(directory: str, data: Mapping[str, object]) -> TsPathOptions:
    """Read the path options that one tsconfig declares itself."""
    compiler_options = data.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return _NO_TS_PATH_OPTIONS
    paths = compiler_options.get("paths")
    declared = paths.items() if isinstance(paths, dict) else None
    return TsPathOptions(
        base_directory=_declared_directory(directory, compiler_options.get("baseUrl")),
        paths_directory=None if declared is None else directory,
        paths=tuple(
            (pattern, tuple(target for target in targets if isinstance(target, str)))
            for pattern, targets in declared or ()
            if isinstance(targets, list)
        ),
        output_directory=_declared_directory(directory, compiler_options.get("outDir")),
        source_directory=_declared_directory(
            directory, compiler_options.get("rootDir")
        ),
    )


def _override_ts_path_options(
    inherited: TsPathOptions,
    nearer: TsPathOptions,
) -> TsPathOptions:
    """Apply `extends` as TypeScript does: the nearer value of an option wins whole."""
    declares_paths = nearer.paths_directory is not None
    return TsPathOptions(
        base_directory=nearer.base_directory or inherited.base_directory,
        paths_directory=(
            nearer.paths_directory if declares_paths else inherited.paths_directory
        ),
        paths=nearer.paths if declares_paths else inherited.paths,
        output_directory=nearer.output_directory or inherited.output_directory,
        source_directory=nearer.source_directory or inherited.source_directory,
    )


def _extended_tsconfig_paths(directory: str, extends: object) -> tuple[str, ...]:
    """List the files named by `extends`; presets from a package are not followed."""
    names = extends if isinstance(extends, list) else [extends]
    extended: list[str] = []
    for name in names:
        if not isinstance(name, str):
            continue
        if not name.startswith(("./", "../")) and not os.path.isabs(name):
            continue
        extended_path = os.path.normpath(os.path.join(directory, name))
        if not extended_path.endswith(".json") and not os.path.isfile(extended_path):
            extended_path += ".json"
        extended.append(extended_path)
    return tuple(extended)


def load_ts_path_options(
    tsconfig_path: str,
    loaded: dict[str, TsPathOptions],
    chain: tuple[str, ...] = (),
) -> TsPathOptions:
    """Load the path options of a tsconfig, the files it extends first."""
    if tsconfig_path in chain:
        print(
            f"[code-graph] Circular TS extends ends at {tsconfig_path}",
            file=sys.stderr,
        )
        return _NO_TS_PATH_OPTIONS
    if tsconfig_path in loaded:
        return loaded[tsconfig_path]

    options = _NO_TS_PATH_OPTIONS
    data = read_json_object(tsconfig_path, "TS aliases")
    if data is not None:
        directory = os.path.dirname(tsconfig_path)
        for extended_path in _extended_tsconfig_paths(directory, data.get("extends")):
            options = _override_ts_path_options(
                options,
                load_ts_path_options(extended_path, loaded, chain + (tsconfig_path,)),
            )
        options = _override_ts_path_options(
            options,
            _own_ts_path_options(directory, data),
        )
    loaded[tsconfig_path] = options
    return options
