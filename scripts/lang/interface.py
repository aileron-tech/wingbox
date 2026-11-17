from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, TypedDict


class EntityPayloadRequired(TypedDict):
    name: str
    kind: str
    line: int


class EntityPayload(EntityPayloadRequired, total=False):
    methods: list[str]
    decorators: list[str]
    bases: list[str]


class ImportedNamePayload(TypedDict):
    local: str
    original: str
    module: str


class BindingPayload(TypedDict):
    """A name that an import binds at module level.

    local: the name bound in the file, "*" for a glob import. module: the module
    as written (resolved by the language). name: the imported name, "" when the
    module itself is bound, "*" for a glob import. export: importers of the file
    see the name too (Rust `pub use`, Python imports at module level).
    """

    local: str
    module: str
    name: str
    export: bool


class UsePayloadRequired(TypedDict):
    path: list[str]
    at: list[list[int]]


class UsePayload(UsePayloadRequired, total=False):
    """A name or qualified path written in the file, not shadowed by a local binding.

    path: the segments as written (S, m.S, a::b::S). at: [line, column] of each
    segment, both 1-based. module: when set, path starts inside that module as
    written in an import statement (from m import S) instead of in the file's scope.
    member: a method call; path is then the receiver's type as written in sight
    (an annotation, a constructor, the class of self or this), empty when no type
    is in sight, and the last entry of at is the method name's position.
    kind: how the last segment is used: import, re-export, call, type, extends
    (extends or implements); absent for any other reference.
    """

    module: str
    member: str
    kind: str
class ModuleLoadPayload(TypedDict):
    """A module name with one variable part: ``{}`` marks the variable."""

    pattern: str
    line: int


class RegistryCallPayload(TypedDict):
    """A method called on an object that a registry module returns."""

    modules: list[str]
    method: str
    source_entity: str
    line: int
    fold: bool


class SourceRelationPayloadRequired(TypedDict):
    target: str
    relation: str


class SourceRelationPayload(SourceRelationPayloadRequired, total=False):
    source_entity: str
    target_entity: str


@dataclass
class Entity:
    name: str
    kind: str
    line: int
    methods: list[str]
    decorators: list[str]
    bases: list[str]

    def to_dict(self) -> EntityPayload:
        result: EntityPayload = {
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
        }
        if self.methods:
            result["methods"] = self.methods
        if self.decorators:
            result["decorators"] = self.decorators
        if self.bases:
            result["bases"] = self.bases
        return result


@dataclass
class ImportedName:
    """A single name imported from another module."""

    local_name: str
    original_name: str
    module: str

    def to_dict(self) -> ImportedNamePayload:
        return {
            "local": self.local_name,
            "original": self.original_name,
            "module": self.module,
        }


@dataclass
class FileResult:
    """Language-neutral source facts consumed by graph construction."""

    path: str
    language: str
    repo: str
    mtime: float
    content_hash: str
    imports: list[str]
    exports_abstract: list[str]
    implements: list[str]
    extends: list[str]
    purpose: str
    naming_convention: str
    has_validation: bool
    entities: list[EntityPayload]
    imported_names: list[ImportedNamePayload]
    exported_names: list[str]
    relations: list[SourceRelationPayload] = field(default_factory=list)
    # TypeScript: specifier -> "type" or "named" when the file imports it for names only
    import_forms: dict[str, str] = field(default_factory=dict)
    # Symbol index: module-level import bindings and the use sites of names.
    bindings: list[BindingPayload] = field(default_factory=list)
    uses: list[UsePayload] = field(default_factory=list)
    # Python: module names filled at run time and calls dispatched through a registry
    module_loads: list[ModuleLoadPayload] = field(default_factory=list)
    registry_calls: list[RegistryCallPayload] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveryExclusions:
    """Immutable exclusions shared by every source/configuration walk."""

    directory_names: frozenset[str]
    paths: frozenset[str]


@dataclass(frozen=True)
class LanguageDependency:
    """One exact runtime package required by a language parser."""

    distribution: str
    version: str

    @property
    def requirement(self) -> str:
        return f"{self.distribution}=={self.version}"


class BoundLanguage(Protocol):
    """A language implementation bound to one graph-build context."""

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        """Resolve one source import to a graph node ID when it is local."""
        ...

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        """Resolve language forms that carry more than one import target."""
        ...


class Language(Protocol):
    """Contract implemented by each source-language boundary."""

    @property
    def name(self) -> str: ...

    @property
    def result_languages(self) -> tuple[str, ...]: ...

    @property
    def lsp_result_languages(self) -> tuple[str, ...]: ...

    @property
    def project_config_names(self) -> tuple[str, ...]: ...

    @property
    def lsp_command(self) -> tuple[str, ...]: ...

    @property
    def lsp_label(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def cli_label(self) -> str: ...

    @property
    def dependencies(self) -> tuple[LanguageDependency, ...]: ...

    @property
    def source_extensions(self) -> tuple[str, ...]: ...

    def accepts_file(self, file_name: str) -> bool:
        """Return whether this implementation owns the source file."""
        ...

    def accepts_project_config(self, file_name: str) -> bool:
        """Return whether a project config belongs to this language."""
        ...

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        """Parse source into the shared, language-neutral result contract."""
        ...

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        """Bind import resolution to one repository graph."""
        ...

    def normalize_module_id(self, relative_path: str) -> str:
        """Convert a source path to this language's canonical module ID."""
        ...

    def output_language_code(self, result_language: str) -> str:
        """Return the compact language code written to the code graph."""
        ...

    def lsp_language_id(self, path: str) -> str:
        """Return the LSP languageId for a source path."""
        ...
