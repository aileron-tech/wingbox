"""IMPORTS edges of a Wingbox graph (.code_graph.sexp), at module granularity, for PHP.

Usage: python3 -I graph_edges.py <root> <.code_graph.sexp> <files.json> <out.json>

<files.json> is the list of absolute PHP/Blade sources of the same checkout that
php_reference.php read. A module ID of the graph is a path without an extension:
`app/Models/User` for `app/Models/User.php`, `resources/views/home` for
`resources/views/home.blade.php`. Two files can share one module ID (a single-file
Livewire component `x.php` and its `x.blade.php` template); the graph and the
reference are compared as modules, because the product answers with a module.

The output holds the mapped PHP/Blade modules, the IMPORTS edges as module-ID
pairs, the modules that several files share, and the counts of modules and edges
that could not be mapped (JavaScript and other languages, configuration nodes).
"""
from __future__ import annotations

import json
import os
import re
import sys

LEAF = re.compile(r"^\((\S+) (.+)\)$")
DEFINITION = re.compile(r'^\(def (\S+) "(.*)"\)$')
NON_PHP_EXTENSIONS = {
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".json": "JSON", ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte",
    ".rs": "Rust", ".cs": "C#", ".java": "Java", ".go": "Go", ".rb": "Ruby", ".pas": "Pascal",
    ".md": "Markdown", ".yml": "YAML", ".yaml": "YAML",
}


def module_id(path: str) -> str:
    """The module ID of a source file: strip `.blade.php` before `.php`, never both."""
    if path.endswith(".blade.php"):
        return path[: -len(".blade.php")]
    if path.endswith(".php"):
        return path[: -len(".php")]
    return path


def read_path_tree(graph_path: str) -> dict[str, str]:
    """The module ID -> path map of a graph (the compressed ``paths`` tree)."""
    id_to_path: dict[str, str] = {}
    section = ""
    stack: list[str] = []
    in_tree = False
    with open(graph_path, encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text.startswith("; ---"):
                section = text.strip("; -").strip()
                continue
            if section != "paths":
                continue
            if text == "(paths":
                in_tree = True
                continue
            declared = DEFINITION.match(text)
            if declared:
                id_to_path[declared.group(1)] = declared.group(2)
                continue
            if not in_tree or not text:
                continue
            if text == ")":
                if stack:
                    stack.pop()
                else:
                    in_tree = False
                continue
            leaf = LEAF.match(text)
            if leaf and not text.startswith("(def "):
                name = leaf.group(2).strip('"')
                id_to_path[leaf.group(1)] = "/".join(stack) if name == ":self" else "/".join(stack + [name])
                continue
            if text.startswith("(") and not text.endswith(")"):
                stack.append(text[1:].strip('"'))
                continue
            raise SystemExit(f"unparsed paths line: {text!r}")
    return id_to_path


def read_imports(graph_path: str) -> list[tuple[str, str]]:
    """The IMPORTS edges of a graph, as module-ID pairs."""
    edges: list[tuple[str, str]] = []
    section = ""
    with open(graph_path, encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text.startswith("; ---"):
                section = text.strip("; -").strip()
                continue
            if section != "edges" or not text or text.startswith(";") or not text.startswith("(imports "):
                continue
            parts = text.strip("()").split()
            edges.append((parts[1].split("::")[0], parts[2].split("::")[0]))
    return edges


def relative_sources(root: str, files_path: str) -> set[str]:
    """The PHP/Blade files of the reference, relative to the graph root."""
    with open(files_path, encoding="utf-8") as handle:
        listed = json.load(handle)
    root = os.path.realpath(root)
    relatives = set()
    for item in listed:
        if not isinstance(item, str):
            continue
        absolute = os.path.realpath(item)
        relative = os.path.relpath(absolute, root).replace(os.sep, "/")
        if relative.startswith("../"):
            # The list may come from the plain export of the same commit while
            # the graph was built in a second export; keep the suffix that exists.
            parts = absolute.replace(os.sep, "/").split("/")
            for cut in range(1, len(parts)):
                candidate = "/".join(parts[cut:])
                if os.path.isfile(os.path.join(root, candidate)):
                    relative = candidate
                    break
        if not relative.startswith(".."):
            relatives.add(relative)
    return relatives


def unmapped_cause(path: str, root: str, modules: set[str]) -> str:
    """Why a module ID of the graph is outside the PHP/Blade file list."""
    if module_id(path) in modules:
        return ""
    for extension in (".blade.php", ".php"):
        if os.path.isfile(os.path.join(root, path + extension)):
            return "PHP file not in the reference list"
    own_suffix = os.path.splitext(path)[1]
    if own_suffix in NON_PHP_EXTENSIONS:
        return "non-PHP (" + NON_PHP_EXTENSIONS[own_suffix] + ")"
    for extension, language in NON_PHP_EXTENSIONS.items():
        if os.path.isfile(os.path.join(root, path + extension)):
            return "non-PHP (" + language + ")"
        if os.path.isfile(os.path.join(root, path, "index" + extension)):
            return "non-PHP (" + language + ")"
    if os.path.isdir(os.path.join(root, path)):
        return "directory with no listed source"
    return "unmapped module"


def main(arguments: list[str]) -> int:
    if len(arguments) < 4:
        print("usage: graph_edges.py <root> <.code_graph.sexp> <files.json> <out.json>", file=sys.stderr)
        return 2
    root, graph_path, files_path, out_path = arguments[:4]
    files = relative_sources(root, files_path)
    module_files: dict[str, set[str]] = {}
    for path in files:
        module_files.setdefault(module_id(path), set()).add(path)
    graph_modules = read_path_tree(graph_path)
    modules = set(module_files)
    mapped: dict[str, str] = {}
    unmapped_modules: list[str] = []
    for sid, path in graph_modules.items():
        normalized = module_id(path)
        if normalized in module_files:
            mapped[sid] = normalized
        elif path.endswith(".php") or "/app/" in "/" + path:
            unmapped_modules.append(path)
    edges: set[tuple[str, str]] = set()
    unmapped_edges = 0
    unmapped_by_cause: dict[str, int] = {}
    for source, target in read_imports(graph_path):
        source_path, target_path = mapped.get(source), mapped.get(target)
        if source_path is None or target_path is None:
            unmapped_edges += 1
            causes = sorted({
                cause for cause in (
                    unmapped_cause(graph_modules.get(source, source), root, modules),
                    unmapped_cause(graph_modules.get(target, target), root, modules),
                ) if cause
            })
            key = " + ".join(causes) if causes else "unmapped module"
            unmapped_by_cause[key] = unmapped_by_cause.get(key, 0) + 1
            continue
        if source_path != target_path:
            edges.add((source_path, target_path))
    shared = {module: sorted(paths) for module, paths in module_files.items() if len(paths) > 1}
    result = {
        "modules": len(set(mapped.values())),
        "nodes": len(mapped),
        "edges": [list(pair) for pair in sorted(edges)],
        "shared_modules": shared,
        "unmapped_modules": sorted(unmapped_modules),
        "unmapped_edges": unmapped_edges,
        "unmapped_by_cause": dict(sorted(unmapped_by_cause.items(), key=lambda item: -item[1])),
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
        handle.write("\n")
    causes = ", ".join(f"{count} {cause}" for cause, count in
                       sorted(unmapped_by_cause.items(), key=lambda item: -item[1])[:4])
    print(f"graph: {len(set(mapped.values()))} PHP modules ({len(mapped)} nodes) mapped, {len(edges)} IMPORTS edges, "
          f"{len(shared)} module(s) shared by two files, "
          f"{len(unmapped_modules)} PHP modules and {unmapped_edges} edges outside the reference"
          + (f" ({causes})" if causes else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))