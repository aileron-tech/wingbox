"""Partial update of a published code graph: redo only the modules a change can affect.

A build with a fragment store (build_code_graph.write_fragments) keeps, for
every module, its facts (node purpose, outgoing edges, resolved uses,
unresolved calls) and what producing them read: the other modules whose parse
results it looked at, the module names as written that it resolved, the
provider names it looked up, the method names of its unresolved calls, and
the modules behind whose glob imports it looked for a name. Such a module
stands for every module its glob imports reach, through any number of further
glob imports: the store keeps the glob imports of each module once (the
modules they name, their module names as written), not the whole set in every
reader.

update_graph(root, changed) then:

1. classifies the changed paths into modified, created and deleted sources;
2. re-parses the modified and created ones;
3. redoes the modules that changed, and every module that read a changed or
   deleted module, that resolved a name which a created or deleted path could
   now answer (a segment of the name equals the file stem or a folder of the
   path), that looked behind glob imports which reach such a module or carry
   such a name, that looked up a provider name the change added or removed, or
   whose unresolved calls name a method the change added or removed;
4. keeps the stored facts of every other module, assembles the graph exactly as
   a build does, and publishes the cache, the graph, the use file, the layer
   guesses of its purposes and the store.

The result is the graph a build of the same tree publishes, byte for byte. It
does not apply (NotApplicable, the caller runs an incremental build) when the
store does not match the published graph, when a resolution config changed
(package.json, tsconfig*.json, pyproject.toml, .gitignore, ...), when a
changed source is not Python or TypeScript/JavaScript (the other languages
read the contents of other files to resolve imports), when module IDs of
unchanged files move, when more than MAX_CHANGED_FILES files changed or more
than MAX_AFFECTED_SHARE of the modules (and more than MIN_AFFECTED_LIMIT)
would be redone.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional, Union

if __package__:
    from . import build_code_graph as builder
    from .git_ignore import GitIgnoreUnavailable, git_ignored_among
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_code_graph as builder
    from git_ignore import GitIgnoreUnavailable, git_ignored_among

PARTIAL_LANGUAGES = frozenset({"python", "typescript"})
MAX_CHANGED_FILES = 200
MAX_AFFECTED_SHARE = 0.25
MIN_AFFECTED_LIMIT = 100  # a small project is always patched
_TOKEN = re.compile(r"[^A-Za-z0-9_$-]+")
_MODE = "syntax"


@dataclass(frozen=True)
class Updated:
    changed: int  # modified, created and deleted sources
    redone: int  # modules whose facts were computed again
    modules: int
    seconds: float


@dataclass(frozen=True)
class NotApplicable:
    reason: str


@dataclass(frozen=True)
class UpdateFailed:
    error: str


UpdateResult = Union[Updated, NotApplicable, UpdateFailed]


@dataclass(frozen=True)
class Changes:
    modified: tuple[str, ...]  # absolute paths
    created: tuple[str, ...]
    deleted: tuple[str, ...]


def tokens(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _TOKEN.split(text) if token and token not in (".", ".."))


def path_tokens(relative: str) -> frozenset[str]:
    """The names a module name could use for a file: its stem and every folder on its path."""
    parts = relative.replace(os.sep, "/").split("/")
    stem = parts[-1].split(".")[0]
    return frozenset(part.lower() for part in parts[:-1] + [stem] if part)


def _repo_of(root: str, path: str) -> str:
    """The repository name discovery gives a file: the deepest folder under root holding .git, else root's name."""
    repo = os.path.basename(root)
    folder = root
    for part in os.path.relpath(os.path.dirname(path), root).split(os.sep):
        if part in ("", "."):
            continue
        folder = os.path.join(folder, part)
        if part != ".git" and os.path.exists(os.path.join(folder, ".git")):
            repo = part
    return repo


def _discovered_language(root: str, path: str, skip_directories: frozenset[str]) -> Optional[str]:
    """The language discovery would give the file, ignoring the Git ignore rules; None when it is skipped."""
    exclusions = builder.DiscoveryExclusions(directory_names=skip_directories, paths=frozenset())
    parent = root
    for part in os.path.relpath(os.path.dirname(path), root).split(os.sep):
        if part in ("", "."):
            continue
        if builder.is_excluded_directory(exclusions, parent, part):
            return None
        parent = os.path.join(parent, part)
    language = builder.language_for_file(os.path.basename(path))
    if language is None:
        return None
    if language.name == "typescript" and builder.is_minified_javascript(path):
        return None
    return language.name


def classify(
    root: str,
    changed: list[str],
    cached: dict[str, object],
    skip_directories: frozenset[str],
) -> Union[Changes, NotApplicable]:
    modified: list[str] = []
    created: list[str] = []
    deleted: list[str] = []
    # The configuration files beside a module loader name the modules it loads (registry-wires edges).
    loaders = {os.path.dirname(path) for path, result in cached.items()
               if isinstance(result, builder.FileResult) and result.module_loads}
    for relative in sorted(set(changed)):
        path = os.path.join(root, *relative.split("/"))
        name = os.path.basename(path)
        if builder.is_resolution_config(name):
            return NotApplicable(f"resolution config changed: {relative}")
        if name.endswith(builder.CONFIG_SUFFIXES) and os.path.dirname(path) in loaders:
            return NotApplicable(f"module loader config changed: {relative}")
        language = _discovered_language(root, path, skip_directories) if os.path.isfile(path) else None
        old = cached.get(path)
        if language is None:
            if old is not None:
                deleted.append(path)
            continue
        if language not in PARTIAL_LANGUAGES:
            return NotApplicable(f"{language} source changed: {relative}")
        (modified if old is not None else created).append(path)
    for path in deleted:
        old = cached[path]
        language = old.language if isinstance(old, builder.FileResult) else ""
        if builder.language_for_result(language).name not in PARTIAL_LANGUAGES:
            return NotApplicable(f"{language} source deleted: {os.path.relpath(path, root)}")
    return Changes(tuple(modified), tuple(created), tuple(deleted))


def _git_kept(root: str, created: tuple[str, ...]) -> Union[tuple[str, ...], NotApplicable]:
    """The created files Git ignore rules do not exclude (a file and every folder on its way are checked)."""
    if not created:
        return created
    candidates: set[str] = set()
    for path in created:
        candidates.add(path)
        folder = os.path.dirname(path)
        while folder.startswith(root + os.sep):
            candidates.add(folder + os.sep)
            folder = os.path.dirname(folder)
    found = git_ignored_among(root, frozenset(candidates))
    if isinstance(found, GitIgnoreUnavailable):
        if "not a git repository" in found.reason:
            return created
        return NotApplicable(f"git ignore rules unavailable: {found.reason}")
    ignored = found.paths
    kept = []
    for path in created:
        on_way = [path] + [folder for folder in _folders(root, path)]
        if not any(os.path.normcase(os.path.realpath(item)) in ignored for item in on_way):
            kept.append(path)
    return tuple(kept)


def _folders(root: str, path: str) -> list[str]:
    folders = []
    folder = os.path.dirname(path)
    while folder.startswith(root + os.sep):
        folders.append(folder)
        folder = os.path.dirname(folder)
    return folders


def _load_cache(root: str) -> Union[tuple[str, str, dict[str, builder.FileResult]], NotApplicable]:
    """(builder fingerprint, digest of the resolution configs, parse results by path) of the build cache."""
    try:
        with open(builder.cache_path(root), "r", encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError) as error:
        return NotApplicable(f"no readable build cache: {error}")
    if not isinstance(cache, dict) or cache.get("schema_version") != builder.CACHE_SCHEMA_VERSION:
        return NotApplicable("build cache of another schema")
    entries = cache.get("entries")
    fingerprint = cache.get("builder_fingerprint")
    configs = cache.get(builder.RESOLUTION_CONFIGS_FIELD, "")
    if not isinstance(entries, dict) or not isinstance(fingerprint, str) or not isinstance(configs, str):
        return NotApplicable("build cache without entries")
    results: dict[str, builder.FileResult] = {}
    for path, entry in entries.items():
        decoded = builder.decode_cache_entry(entry)
        if isinstance(decoded, builder.Err):
            return NotApplicable(f"invalid cache entry {path}: {decoded.error}")
        results[path] = decoded.value
    return fingerprint, configs, results


def _relative_key(root: str):
    return lambda result: os.path.relpath(result.path, root).replace(os.sep, "/")


def _published_key(output_path: str) -> Optional[str]:
    try:
        with open(builder.uses_path(output_path), "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("(code-graph-uses "):
                    match = re.search(r":key (\S+) ", line)
                    return match.group(1) if match else None
    except OSError:
        return None
    return None


def _write_cache(root: str, fingerprint: str, configs: str, results: list[builder.FileResult]) -> None:
    """The build cache as save_cache writes it, with the fingerprint and the resolution configs of the build
    it extends (a changed resolution config is never patched in, so they are the ones of that build)."""
    cache = {
        "schema_version": builder.CACHE_SCHEMA_VERSION,
        "builder_fingerprint": fingerprint,
        builder.RESOLUTION_CONFIGS_FIELD: configs,
        "entries": {result.path: builder._serialize_file_result(result) for result in results},
    }
    builder._atomic_write_text(
        builder.cache_path(root), json.dumps(cache, sort_keys=True, separators=(",", ":")) + "\n"
    )


def affected_modules(
    stored: dict[str, builder.StoredFragment],
    old_results: dict[str, builder.FileResult],
    new_context: builder.GraphContext,
    changed_ids: set[str],
    deleted_ids: set[str],
    moved_paths: list[str],
    old_methods: frozenset[str],
) -> set[str]:
    """The modules of the new graph whose facts may differ from the stored ones."""
    redo = set(changed_ids)
    read_changed = changed_ids | deleted_ids
    created_or_deleted = frozenset().union(*(path_tokens(path) for path in moved_paths)) if moved_paths else frozenset()
    names: set[str] = set()
    for node_id in changed_ids | deleted_ids:
        before = builder.provider_names(old_results[node_id]) if node_id in old_results else set()
        after = builder.provider_names(new_context.id_to_result[node_id]) if node_id in new_context.id_to_result else set()
        names |= before ^ after
    methods = old_methods ^ new_context.methods
    searched = glob_readers(stored, read_changed, created_or_deleted)
    for node_id in new_context.id_to_result:
        if node_id in redo:
            continue
        fragment = stored.get(node_id)
        if fragment is None:
            redo.add(node_id)
            continue
        if (
            not fragment.modules.isdisjoint(read_changed)
            or not fragment.closures.isdisjoint(searched)
            or not fragment.names.isdisjoint(names)
            or not fragment.members.isdisjoint(methods)
            or (created_or_deleted and any(not tokens(spec).isdisjoint(created_or_deleted) for spec in fragment.specs))
        ):
            redo.add(node_id)
    return redo


def glob_readers(
    stored: dict[str, builder.StoredFragment],
    read_changed: set[str],
    created_or_deleted: frozenset[str],
) -> set[str]:
    """The modules whose glob imports reach, through any number of further glob imports, a changed or deleted
    module or a glob import whose module name a created or deleted path could now answer. A module that looked
    for a name behind the glob imports of one of them (fragment.closures) read what changed."""
    importers: dict[str, list[str]] = {}  # a module -> the modules that glob-import it
    found: set[str] = set()
    for node_id, fragment in stored.items():
        targets, specs = fragment.globs
        for target in targets:
            importers.setdefault(target, []).append(node_id)
        if created_or_deleted and any(not tokens(spec).isdisjoint(created_or_deleted) for spec in specs):
            found.add(node_id)
    todo = list(found) + [node_id for node_id in read_changed if node_id not in found]
    found.update(read_changed)
    while todo:
        for importer in importers.get(todo.pop(), ()):
            if importer not in found:
                found.add(importer)
                todo.append(importer)
    return found


def update_graph(root: str, changed: list[str], fragments_path: str, jobs: int = 1) -> UpdateResult:
    """Patch the published graph of root for the changed paths (relative, with /); see the module docstring."""
    started = time.monotonic()
    root = os.path.realpath(root)
    output_path = builder.index_path(root)
    if len(changed) > MAX_CHANGED_FILES:
        return NotApplicable(f"{len(changed)} files changed (more than {MAX_CHANGED_FILES})")
    loaded = _load_cache(root)
    if isinstance(loaded, NotApplicable):
        return loaded
    fingerprint, configs, cached = loaded
    old_list = sorted(cached.values(), key=_relative_key(root))
    old_key = builder.symbol_index_key(old_list, fingerprint, _MODE, configs)
    if _published_key(output_path) != old_key:
        return NotApplicable("the published graph does not match the build cache")
    store = builder.read_fragments(fragments_path, root)
    if isinstance(store, builder.Err):
        return NotApplicable(store.error)
    store_key, exclusions, stored = store.value
    skip_directories = exclusions.directory_names
    if store_key != old_key:
        return NotApplicable("the fragment store does not match the published graph")

    classified = classify(root, changed, dict(cached), skip_directories)
    if isinstance(classified, NotApplicable):
        return classified
    created = _git_kept(root, classified.created)
    if isinstance(created, NotApplicable):
        return created
    if not classified.modified and not created and not classified.deleted:
        return Updated(0, 0, len(cached), time.monotonic() - started)

    new_by_path = dict(cached)
    for path in classified.deleted:
        del new_by_path[path]
    for path in classified.modified + created:
        old = cached.get(path)
        language = builder.language_for_file(os.path.basename(path))
        assert language is not None
        if old is not None and isinstance(builder._cache_entry_result(path, builder._serialize_file_result(old)), builder.Ok):
            continue  # same content: nothing to redo
        repo = old.repo if old is not None else _repo_of(root, path)
        new_by_path[path] = builder.parse_file((path, language.name, repo, root))
    if {result.language for result in new_by_path.values()} != {result.language for result in cached.values()}:
        return NotApplicable("the set of languages changed")
    new_list = sorted(new_by_path.values(), key=_relative_key(root))
    if not new_list:
        return NotApplicable("no source file left")

    context_result = builder.graph_context(new_list, root, exclusions)
    if isinstance(context_result, builder.Err):
        return NotApplicable("module ID collision")
    context = context_result.value
    old_ids = {os.path.join(root, *fragment.path.split("/")): node_id for node_id, fragment in stored.items()}
    for path, node_id in context.path_to_id.items():
        if path in old_ids and old_ids[path] != node_id:
            return NotApplicable(f"module ID of {os.path.relpath(path, root)} changed")
    if set(old_ids) != set(cached):
        return NotApplicable("the fragment store does not cover the build cache")

    changed_ids = {
        context.path_to_id[path]
        for path in classified.modified + created
        if new_by_path[path] is not cached.get(path)
    }
    deleted_ids = {old_ids[path] for path in classified.deleted}
    old_results = {old_ids[path]: result for path, result in cached.items()}
    old_methods = frozenset(method for result in cached.values() for method in builder.class_methods(result))
    moved = [os.path.relpath(path, root) for path in created + classified.deleted]
    redo = affected_modules(stored, old_results, context, changed_ids, deleted_ids, moved, old_methods)
    if len(redo) > max(MIN_AFFECTED_LIMIT, MAX_AFFECTED_SHARE * len(context.id_to_result)):
        return NotApplicable(f"{len(redo)} of {len(context.id_to_result)} modules affected")

    fresh = builder.module_fragments(context, [node_id for node_id in context.id_to_result if node_id in redo], True, jobs)
    fragments = {
        node_id: fresh[node_id] if node_id in fresh else stored[node_id].fragment(node_id, result, root)
        for node_id, result in context.id_to_result.items()
    }
    graph = builder.assemble_graph(context, new_list, fragments)
    built = builder.project_architecture(root, exclusions, new_list, graph.nodes)
    if isinstance(built, builder.Err):
        return NotApplicable(built.error)
    content = builder.render_sexp(graph.nodes, graph.edges, root, _MODE, len(new_list), built.value[0],
                                  builder.built_from(root, new_list))
    new_key = builder.symbol_index_key(new_list, fingerprint, _MODE, configs)
    uses = builder.render_uses_sexp(graph.nodes, new_key, content)
    _write_cache(root, fingerprint, configs, new_list)
    builder._atomic_write_text(output_path, content)
    builder._atomic_write_text(builder.uses_path(output_path), uses)
    builder.publish_layer_guesses(root, graph.nodes)
    unchanged = {node_id: stored[node_id].line for node_id in context.id_to_result if node_id not in redo}
    builder.write_fragments(fragments_path, new_key, fragments, root, exclusions, unchanged)
    return Updated(
        len(classified.modified) + len(created) + len(classified.deleted),
        len(redo),
        len(context.id_to_result),
        time.monotonic() - started,
    )
