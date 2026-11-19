"""`wingbox add`: kept so that a script written for 0.12 still runs; it adds nothing.

    wingbox add [LANGUAGE ...] [--from PATH] [--sexp]
    wingbox add --list [--sexp]

In 0.12 this command installed the parser of a language. The parsers of every language are dependencies of
the package, so there is nothing to add: every form the command took then (languages, --from, --list) is
accepted, the answer is one line, or one fact with --sexp, and the status is 0. Nothing is downloaded and
nothing is written; a parser folder that 0.12 left in ~/.wingbox/parsers is not read and not removed.

`--list` says for each language whether its parser is in the environment that runs Wingbox, by the check a
build makes (the pinned version of every package the parser needs). `listed` is pure over the languages
and a version lookup; `installed_version` is the real lookup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

from wingbox.facts import Fact, make_fact

USAGE = """usage: wingbox add [LANGUAGE ...] [--from PATH] [--sexp]
       wingbox add --list [--sexp]

Kept for scripts written for 0.12; it adds nothing. The parsers of every language are installed
with Wingbox, so every form of the command says so and exits 0. It downloads and writes nothing.

  --list        Each language: whether its parser is installed in the environment that runs Wingbox
  --sexp        Print the facts as S-expressions
  --from PATH   Accepted and ignored"""
NOTHING_TO_ADD = make_fact(
    "nothing-to-add",
    reason="the parsers of every language are installed with Wingbox since this version",
    next="wingbox build",
)
CORE_DISTRIBUTION = "tree-sitter"   # the parser library itself; the other packages of a language are its grammar


class Dependency(Protocol):
    @property
    def distribution(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def requirement(self) -> str: ...


class Language(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def dependencies(self) -> Sequence[Dependency]: ...


@dataclass(frozen=True)
class Options:
    listing: bool
    sexp: bool


def parse_arguments(args: list[str]) -> Options:
    """Only --list and --sexp change the answer; languages, --from and anything else are accepted and ignored."""
    return Options("--list" in args, "--sexp" in args)


def _unavailable(language: Language, installed_version: Callable[[str], Optional[str]]) -> list[str]:
    """The packages of a parser the environment does not hold in the pinned version, each with what is there."""
    problems = []
    for dependency in language.dependencies:
        installed = installed_version(dependency.distribution)
        if installed is None:
            problems.append(f"{dependency.requirement} (missing)")
        elif installed != dependency.version:
            problems.append(f"{dependency.requirement} (installed {installed})")
    return problems


def listed(
    languages: Sequence[Language], installed_version: Callable[[str], Optional[str]], extras: Sequence[str]
) -> list[Fact]:
    """One fact per language: its parser is installed, or missing with the command that installs it."""
    facts = []
    for language in languages:
        package = " ".join(dep.requirement for dep in language.dependencies if dep.distribution != CORE_DISTRIBUTION)
        if not language.dependencies:
            facts.append(make_fact("parser-installed", language.name, **{"from": "built-in"}))
            continue
        problems = _unavailable(language, installed_version)
        if not problems:
            facts.append(make_fact("parser-installed", language.name, package=package, **{"from": "environment"}))
            continue
        instead = f'pip install "wingbox[{language.name}]"' if language.name in extras else "pip install --force-reinstall wingbox"
        facts.append(make_fact("parser-missing", language.name, package=package, reason=", ".join(problems), instead=instead))
    return facts


def render_text(facts: list[Fact]) -> str:
    """One line per language (state, package), then the line that there is nothing to add."""
    lines: list[str] = []
    for fact in facts:
        if fact.head == NOTHING_TO_ADD.head:
            lines.append(
                "Nothing to add: the parsers of every language are installed with Wingbox since this version. "
                "A graph that an earlier build left files out of is completed by wingbox build."
            )
            continue
        state = "installed" if fact.head == "parser-installed" else "not installed"
        detail = "built in" if fact.get("from") == "built-in" else str(fact.get("package"))
        lines.append(f"{fact.args[0]:<11} {state:<14} {detail}".rstrip())
        if fact.get("reason"):
            lines.append(f"            {fact.get('reason')}")
        if fact.get("instead"):
            lines.append(f"            run: {fact.get('instead')}")
    return "".join(line + "\n" for line in lines)


def installed_version(distribution: str) -> Optional[str]:
    """The version of a distribution in the environment that runs Wingbox; None when it is absent."""
    import importlib.metadata as metadata

    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
