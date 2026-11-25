from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..discovery import is_excluded_directory, is_excluded_path
from ..interface import DiscoveryExclusions, FileResult


_SOURCE_SUFFIXES = (".rb", ".rake")
_ACRONYM_PATTERN = re.compile(
    r"\b(?:inflect\.)?acronym\s*(?:\(\s*)?['\"]([A-Za-z][A-Za-z0-9]*)['\"]"
)
_AUTOLOAD_NAME_PATTERN = re.compile(r"autoload_(?:once_)?paths")
# The arguments of one `Rails.root.join(...)`: read in a window, so a bracket that never closes costs no
# more than the window.
_ROOT_JOIN_PATTERN = re.compile(r"(?:Rails|config)\.root\.join\(([^)\n]{0,1024})\)")
_QUOTED_SEGMENT_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")
# The singular of a Rails association name: the rules of ActiveSupport's English inflections, the last
# rule defined there first, as Rails applies them. A word that changes in no rule is left as it is.
_UNCOUNTABLE = frozenset({"equipment", "information", "rice", "money", "species", "series", "fish", "sheep", "jeans", "police"})
_SINGULAR_RULES = tuple((re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in (
    # The irregular words: zombie, move, sex, child, man, person.
    (r"(z)ombies$", r"\1ombie"),
    (r"(m)oves$", r"\1ove"),
    (r"(s)exes$", r"\1ex"),
    (r"(child)ren$", r"\1"),
    (r"(m)en$", r"\1an"),
    (r"(p)eople$", r"\1erson"),
    (r"(database)s$", r"\1"),
    (r"(quiz)zes$", r"\1"),
    (r"(matr)ices$", r"\1ix"),
    (r"(vert|ind)ices$", r"\1ex"),
    (r"^(ox)en", r"\1"),
    (r"(alias|status)(es)?$", r"\1"),
    (r"(octop|vir)(us|i)$", r"\1us"),
    (r"^(a)x[ie]s$", r"\1xis"),
    (r"(cris|test)(is|es)$", r"\1is"),
    (r"(shoe)s$", r"\1"),
    (r"(o)es$", r"\1"),
    (r"(bus)(es)?$", r"\1"),
    (r"^(m|l)ice$", r"\1ouse"),
    (r"(x|ch|ss|sh)es$", r"\1"),
    (r"(m)ovies$", r"\1ovie"),
    (r"(s)eries$", r"\1eries"),
    (r"([^aeiouy]|qu)ies$", r"\1y"),
    (r"([lr])ves$", r"\1f"),
    (r"(tive)s$", r"\1"),
    (r"(hive)s$", r"\1"),
    (r"([^f])ves$", r"\1fe"),
    (r"(^analy)(sis|ses)$", r"\1sis"),
    (r"((a)naly|(b)a|(d)iagno|(p)arenthe|(p)rogno|(s)ynop|(t)he)(sis|ses)$", r"\1sis"),
    (r"([ti])a$", r"\1um"),
    (r"(n)ews$", r"\1ews"),
    (r"(ss)$", r"\1"),
    (r"s$", ""),
))


def _freeze_index(index: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {name: tuple(sorted(targets)) for name, targets in index.items()}


def _read_source(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(
            f"[code-graph] Cannot read Ruby {label} from {path}: {error}",
            file=sys.stderr,
        )
        return ""


def _project_acronyms(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> dict[str, str]:
    inflections_path = root / "config" / "initializers" / "inflections.rb"
    if (
        is_excluded_path(discovery_exclusions, str(inflections_path))
        or not inflections_path.is_file()
    ):
        return {}
    content = _read_source(inflections_path, "inflections")
    return {acronym.lower(): acronym for acronym in _ACRONYM_PATTERN.findall(content)}


def _autoload_join_arguments(content: str) -> list[str]:
    """The arguments of the first `Rails.root.join(...)` (or `config.root.join`) after each mention of
    `autoload_paths` or `autoload_once_paths` on its line. Each line is read once from left to right."""
    found: list[str] = []
    for line in content.splitlines():
        position = 0
        while True:
            mention = _AUTOLOAD_NAME_PATTERN.search(line, position)
            if mention is None:
                break
            join = _ROOT_JOIN_PATTERN.search(line, mention.end())
            if join is None:
                break   # no path after this mention, so none after a later one
            found.append(join.group(1))
            position = join.end()
    return found


def _custom_autoload_roots(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[Path, ...]:
    config_paths = (
        root / "config" / "application.rb",
        root / "config" / "environment.rb",
    )
    roots: set[Path] = set()
    for config_path in config_paths:
        if (
            is_excluded_path(discovery_exclusions, str(config_path))
            or not config_path.is_file()
        ):
            continue
        content = _read_source(config_path, "autoload configuration")
        for arguments in _autoload_join_arguments(content):
            segments = _QUOTED_SEGMENT_PATTERN.findall(arguments)
            if not segments:
                continue
            candidate = root.joinpath(*segments).resolve()
            if (
                candidate.is_dir()
                and _is_within(candidate, root)
                and not is_excluded_path(discovery_exclusions, str(candidate))
            ):
                roots.add(candidate)
    return tuple(sorted(roots))


def _default_autoload_roots(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[Path, ...]:
    roots: set[Path] = set(_custom_autoload_roots(root, discovery_exclusions))
    app_root = root / "app"
    if app_root.is_dir() and not is_excluded_path(discovery_exclusions, str(app_root)):
        try:
            app_children = tuple(app_root.iterdir())
        except OSError as error:
            print(
                f"[code-graph] Cannot inspect Rails app roots in {app_root}: {error}",
                file=sys.stderr,
            )
            app_children = ()
        for child in app_children:
            if not child.is_dir() or is_excluded_directory(
                discovery_exclusions,
                str(app_root),
                child.name,
            ):
                continue
            roots.add(child.resolve())
            concerns = child / "concerns"
            if concerns.is_dir() and not is_excluded_directory(
                discovery_exclusions,
                str(child),
                concerns.name,
            ):
                roots.add(concerns.resolve())
    lib_root = root / "lib"
    if lib_root.is_dir() and not is_excluded_directory(
        discovery_exclusions,
        str(root),
        lib_root.name,
    ):
        roots.add(lib_root.resolve())
    return tuple(sorted(roots, key=lambda path: (-len(path.parts), str(path))))


def _camelize(segment: str, acronyms: Mapping[str, str]) -> str:
    parts = [part for part in segment.split("_") if part]
    return "".join(
        acronyms.get(part.lower(), part[:1].upper() + part[1:]) for part in parts
    )


def _expected_constant(
    path: Path,
    autoload_roots: tuple[Path, ...],
    acronyms: Mapping[str, str],
) -> str:
    for autoload_root in autoload_roots:
        try:
            relative = path.relative_to(autoload_root)
        except ValueError:
            continue
        without_suffix = relative
        for suffix in _SOURCE_SUFFIXES:
            if relative.name.endswith(suffix):
                without_suffix = relative.with_name(relative.name[: -len(suffix)])
                break
        return "::".join(
            _camelize(part, acronyms) for part in without_suffix.parts if part
        )
    return ""


def _source_namespaces(constants: tuple[str, ...], expected: str) -> tuple[str, ...]:
    candidates: set[str] = set()
    for constant in (*constants, expected):
        current = constant
        while current:
            candidates.add(current)
            current, separator, _ = current.rpartition("::")
            if not separator:
                break
    return tuple(
        sorted(candidates, key=lambda name: (-name.count("::"), -len(name), name))
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_variants(base: Path) -> tuple[Path, ...]:
    if base.suffix in _SOURCE_SUFFIXES:
        return (base,)
    return (
        base,
        Path(f"{base}.rb"),
        Path(f"{base}.rake"),
        base / "init.rb",
    )


def _singularize(name: str) -> str:
    """The singular Rails derives the model of `has_many :name` from (the last word of a name with `_`)."""
    head, separator, word = name.rpartition("_")
    if word.lower() in _UNCOUNTABLE:
        return name
    for pattern, replacement in _SINGULAR_RULES:
        singular, count = pattern.subn(replacement, word)
        if count:
            return f"{head}{separator}{singular}"
    return name


def _resolve_ancestor_names(
    bases: tuple[str, ...],
    namespaces: tuple[str, ...],
    constant_index: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    ancestors: list[str] = []
    for raw_base in bases:
        base = raw_base.removeprefix("::")
        candidates = [base]
        if not raw_base.startswith("::") and "::" not in base:
            candidates = [
                *(f"{namespace}::{base}" for namespace in namespaces),
                base,
            ]
        for candidate in candidates:
            if len(constant_index.get(candidate, ())) == 1:
                ancestors.append(candidate)
                break
    return tuple(dict.fromkeys(ancestors))


@dataclass(frozen=True)
class BoundRubyLanguage:
    root: Path
    path_to_id: Mapping[str, str]
    constant_to_ids: Mapping[str, tuple[str, ...]]
    source_namespaces: Mapping[str, tuple[str, ...]]
    source_ancestors: Mapping[str, tuple[str, ...]]
    autoload_roots: tuple[Path, ...]
    acronyms: Mapping[str, str]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundRubyLanguage:
        from .parser import ruby_declarations

        root_path = Path(root).resolve()
        normalized_paths = {
            str(Path(path).resolve()): node_id for path, node_id in path_to_id.items()
        }
        acronyms = _project_acronyms(root_path, discovery_exclusions)
        autoload_roots = _default_autoload_roots(
            root_path,
            discovery_exclusions,
        )
        constant_index: dict[str, set[str]] = {}
        namespaces: dict[str, tuple[str, ...]] = {}
        raw_bases: dict[str, tuple[str, ...]] = {}

        for raw_path, node_id in sorted(normalized_paths.items()):
            path = Path(raw_path)
            if path.suffix not in _SOURCE_SUFFIXES:
                continue
            content = _read_source(path, "declarations")
            declarations, declared_bases = ruby_declarations(content)
            expected = _expected_constant(path, autoload_roots, acronyms)
            for constant in declarations:
                constant_index.setdefault(constant, set()).add(node_id)
            if expected and (not declarations or expected in declarations):
                constant_index.setdefault(expected, set()).add(node_id)
            namespaces[raw_path] = _source_namespaces(declarations, expected)
            raw_bases[raw_path] = tuple(
                base for bases in declared_bases.values() for base in bases
            )

        frozen_constant_index = _freeze_index(constant_index)
        ancestors = {
            path: _resolve_ancestor_names(
                bases,
                namespaces.get(path, ()),
                frozen_constant_index,
            )
            for path, bases in raw_bases.items()
        }

        return cls(
            root=root_path,
            path_to_id=normalized_paths,
            constant_to_ids=frozen_constant_index,
            source_namespaces=namespaces,
            source_ancestors=ancestors,
            autoload_roots=autoload_roots,
            acronyms=acronyms,
        )

    def _resolve_path(self, base: Path) -> str | None:
        for candidate in _path_variants(base):
            resolved = candidate.resolve()
            if not _is_within(resolved, self.root):
                continue
            target = self.path_to_id.get(str(resolved))
            if target is not None:
                return target
        return None

    def _resolve_require_relative(self, name: str, source_path: str) -> str | None:
        return self._resolve_path(Path(source_path).resolve().parent / name)

    def _resolve_require(self, name: str) -> str | None:
        search_roots = (self.root, self.root / "lib", *self.autoload_roots)
        for search_root in search_roots:
            target = self._resolve_path(search_root / name)
            if target is not None:
                return target
        return None

    def _resolve_constant(self, name: str, source_path: str) -> str | None:
        """Resolve a constant as written in a file. A scoped name that resolves nowhere names at least
        its scope: `User::LIMIT`, with LIMIT defined in a concern that User includes, is resolved as
        `User`, the longest leading part that resolves. A name that several files define stays unlinked."""
        while True:
            target, ambiguous = self._lookup_constant(name, source_path)
            if target is not None or ambiguous or "::" not in name.removeprefix("::"):
                return target
            name = name.rpartition("::")[0]

    def _lookup_constant(self, name: str, source_path: str) -> tuple[str | None, bool]:
        """(the file that defines the constant, None when no single file does; whether several files do)."""
        normalized = name.removeprefix("::")
        if not normalized:
            return None, False
        source_key = str(Path(source_path).resolve())
        candidates: list[str] = []
        if name.startswith("::") or "::" in normalized:
            candidates.append(normalized)
        if not name.startswith("::"):
            candidates.extend(
                f"{namespace}::{normalized}"
                for namespace in self.source_namespaces.get(source_key, ())
                if namespace != normalized
                and not normalized.startswith(f"{namespace}::")
            )
            candidates.extend(
                f"{ancestor}::{normalized}"
                for ancestor in self.source_ancestors.get(source_key, ())
            )
        candidates.append(normalized)

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            targets = self.constant_to_ids.get(candidate, ())
            if len(targets) == 1:
                return targets[0], False
            if len(targets) > 1:
                return None, True
        return None, False

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        if import_name.startswith("ruby-require-relative:"):
            return self._resolve_require_relative(
                import_name.removeprefix("ruby-require-relative:"),
                source_path,
            )
        if import_name.startswith("ruby-require:"):
            return self._resolve_require(import_name.removeprefix("ruby-require:"))
        if import_name.startswith("ruby-association-one:"):
            constant = _camelize(
                import_name.removeprefix("ruby-association-one:"),
                self.acronyms,
            )
            return self._resolve_constant(constant, source_path)
        if import_name.startswith("ruby-association-many:"):
            association = _singularize(
                import_name.removeprefix("ruby-association-many:")
            )
            return self._resolve_constant(
                _camelize(association, self.acronyms),
                source_path,
            )
        if import_name.startswith("ruby-local-method:"):
            return self.path_to_id.get(str(Path(source_path).resolve()))
        return self._resolve_constant(
            import_name.removeprefix("ruby-constant:"),
            source_path,
        )

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
