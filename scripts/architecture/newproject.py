"""A new project: the styles offered for a language, in order of precedence.

Precedence (the first that applies is offered first; each choice says which):
the project's own declared rules > its .wingbox/config.yaml (both decide an
existing part, so a new project starts below them) > siblings: the style of
the other parts in the same language of the repository's .wingbox/config.yaml
> home: the favourite of $HOME/.wingbox/config.yaml for the language > the
library: every style that fits the language, in the order of index.toml.
Pure; the command line reads the files.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Optional

from .library import Library

LANGUAGES = ("typescript", "javascript", "python", "rust", "go", "java", "csharp", "ruby", "php", "elixir")
MANIFEST_LANGUAGE = (
    ("package.json", "typescript"), ("tsconfig.json", "typescript"), ("pyproject.toml", "python"), ("setup.py", "python"),
    ("Cargo.toml", "rust"), ("go.mod", "go"), ("pom.xml", "java"), ("settings.gradle", "java"), ("Gemfile", "ruby"),
    ("composer.json", "php"), ("mix.exs", "elixir"),
)


@dataclass(frozen=True)
class Choice:
    style: str
    origin: str     # siblings | home | library
    note: str


def language_of(files: tuple[str, ...]) -> Optional[str]:
    """The language of a folder from the manifests it holds (file names directly in it)."""
    names = set(files)
    if any(name.endswith((".sln", ".csproj")) for name in names):
        return "csharp"
    return next((language for manifest, language in MANIFEST_LANGUAGE if manifest in names), None)


def fits(library: Library, style_id: str, language: str) -> bool:
    style = library.style(style_id)
    return style is not None and ("any" in style.languages or language in style.languages)


def choices(
    library: Library, language: str, siblings: Mapping[str, str], home: Mapping[str, str],
) -> tuple[Choice, ...]:
    """Styles for a new project in `language`. `siblings`: part -> style of the parts in that language;
    `home`: language -> favourite style."""
    found: list[Choice] = []
    counts = Counter(style for style in siblings.values() if fits(library, style, language))
    if counts:
        style = counts.most_common(1)[0][0]
        same = sorted(part for part, other in siblings.items() if other == style)
        found.append(Choice(style, "siblings", "same as " + ", ".join(same)))
    favourite = home.get(language)
    if favourite and fits(library, favourite, language) and favourite not in {c.style for c in found}:
        found.append(Choice(favourite, "home", "$HOME/.wingbox/config.yaml"))
    for style in library.styles:
        if fits(library, style.id, language) and style.id not in {c.style for c in found}:
            found.append(Choice(style.id, "library", ""))
    return tuple(found)
