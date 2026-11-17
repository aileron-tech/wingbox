"""Command-line entry point for Wingbox.

    wingbox init [PATH] [--force] [--sexp] [--synonyms] [--language L --style S --yes --scaffold]
                                            Detect the parts of a repository and the architecture style of each,
                                            print the candidates and write .wingbox/config.yaml (kept if present
                                            unless --force); then wingbox learn settles the open points
    wingbox advice [--sexp] [--learned]     The open recommendations (or what was learned from past ones);
    wingbox advice accept|reject <id> [--reason TEXT]   record the decision in .wingbox/decisions.sexp
    wingbox align [PART] [--to STYLE] [--learn] [--sexp]
                                            Architecture gaps of each part against its style (or another one);
                                            --learn writes learned folders to .wingbox/config.yaml and the rest
                                            to .wingbox/baseline.sexp
    wingbox place "<what>" [--part PART] [--sexp]
                                            The layer and folder for new code, by the placement rules of the style
    wingbox compare [--sexp]                The parts of a monorepo side by side per language: styles, layers,
                                            differences, and advice for a part that deviates from the majority
    wingbox learn [PART] [--agent|--human] [--sexp] [--again] [--yes]
                                            A coding agent gets a task: read the folders the style cannot place and
                                            answer in a fixed format; a person gets the deterministic learning to confirm
    wingbox learn --apply [FILE]            Validate the agent's answer (stdin or FILE) and record it in .wingbox/
    wingbox build [PATH] [build options]    Build the code graph of a project (default: current directory)
                                            into PATH/.wingbox/: index.sexp, REPORT.md and map.html
    wingbox open [PATH]                     Open .wingbox/map.html of the project in the browser
    wingbox <command> [arguments]           Query the code graph: impact, quick, analyze, search, ...
    wingbox infra [arguments]               Build the infrastructure topology (Kustomize, Helm, Terraform)
    wingbox install [--global|--project] [--assistant NAME ...] [--dry-run] [--sexp]
                                            Put the wingbox skill, an always-on block in CLAUDE.md and
                                            AGENTS.md, and the Claude Code search hook in place
    wingbox uninstall [--global|--project] [--assistant NAME ...] [--sexp]
                                            Remove exactly what install added
    wingbox update [--check] [--dry-run] [--sexp]
                                            Upgrade with the installer that installed Wingbox (pipx, uv
                                            or pip), then refresh what install put in place
    wingbox hook pre-search                 The Claude Code PreToolUse hook (reads the hook input on stdin)
    wingbox hook dirty                      The Claude Code PostToolUse hook on Edit and Write: marks the
                                            changed file so the graph is refreshed before its next use
    wingbox mcp [--root DIR]                Run the MCP server on stdio for an MCP client (default: current directory)
    wingbox --version

Assistants: claude-code, codex, gemini, cursor, copilot, agents (the shared
~/.agents/skills folder). Without --assistant, install uses those whose
configuration folder exists in the home folder.

`wingbox build` runs scripts/build_code_graph.py and every query command runs
scripts/code_graph_search.py, so the options and the output are the ones
documented for those scripts. Relative values of --root and --map are resolved
against the current directory.

Everything Wingbox writes into a project lives in .wingbox/, which also marks
the project: a command finds the nearest folder at or above the current one
(or --root) that holds .wingbox/index.sexp. The code graph (.wingbox/index.sexp)
is the file agents query. REPORT.md and map.html say the same thing to a
person: the report in text, the map as an interactive page that opens in a
browser without a network connection. .wingbox/.gitignore keeps the generated
files out of git; the whole folder can be ignored as well.

.wingbox/config.yaml states the architecture style of each part of the
repository; `wingbox build` reads it when present. It, decisions.sexp,
learned.sexp and baseline.sexp are written by people or agents and stay
visible to git.

Query commands, the MCP tools and the search hook bring the graph up to date
before they read it: the files changed since the last build (marked by the
hooks, or found by comparing sizes and times with the last build's snapshot)
are patched in, or an incremental build runs. WINGBOX_AUTO_REFRESH=0 turns
this off; a stale graph is then reported as such.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Optional

_PATH_OPTIONS = ("--root", "--map")
_VALUE_OPTIONS = _PATH_OPTIONS + ("--lsp-timeout", "--exclude-dir")
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_USAGE = """usage: wingbox build [PATH] [--full] [--exclude-dir NAME ...] [--lsp] [--lsp-timeout SECONDS]

Build the code graph of the project at PATH (default: the current folder).
Everything goes into PATH/.wingbox/: index.sexp (the graph agents query),
REPORT.md and map.html (for people; `wingbox open` shows the map), the build
cache and what keeps the graph fresh. A later build parses only the files
whose content changed.

  --full                 Parse every file again instead of reusing the cache
  --exclude-dir NAME     Skip every folder with this name (repeatable)
  --lsp                  Add the edges a language server finds (slower)
  --lsp-timeout SECONDS  Time limit of one language-server request (default 15)"""


@dataclass(frozen=True)
class OutputsWritten:
    report: str
    page: str


@dataclass(frozen=True)
class OutputsFailed:
    error: str


OutputsResult = OutputsWritten | OutputsFailed


def absolutize_path_options(args: list[str], cwd: str) -> list[str]:
    """Resolve relative --root and --map values against cwd."""
    resolved: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        resolved.append(argument)
        if argument in _PATH_OPTIONS and index + 1 < len(args):
            resolved.append(os.path.normpath(os.path.join(cwd, args[index + 1])))
            index += 2
            continue
        index += 1
    return resolved


def build_arguments(args: list[str], cwd: str) -> list[str]:
    """Arguments of `wingbox build` for the builder: PATH (default cwd) becomes --root."""
    options: list[str] = []
    positional: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in _VALUE_OPTIONS and index + 1 < len(args):
            options.extend([argument, args[index + 1]])
            index += 2
            continue
        (options if argument.startswith("-") else positional).append(argument)
        index += 1
    if "--root" not in options:
        options = ["--root", positional[0] if positional else "."] + options
        positional = positional[1:]
    return absolutize_path_options(options + positional, cwd)


def scripts_directory(package_dir: str) -> str:
    """Directory of the bundled scripts: inside the installed package, or scripts/ in a checkout."""
    bundled = os.path.join(package_dir, "_scripts")
    if os.path.isdir(bundled):
        return bundled
    return os.path.normpath(os.path.join(package_dir, os.pardir, os.pardir, "scripts"))


def skill_directory(package_dir: str) -> str:
    """Directory of the packaged skill (SKILL.md, references/): inside the package, or the checkout root."""
    bundled = os.path.join(package_dir, "_skill")
    if os.path.isdir(bundled):
        return bundled
    return os.path.normpath(os.path.join(package_dir, os.pardir, os.pardir))


def read_skill_files(directory: str) -> dict[str, bytes]:
    """SKILL.md and references/*.md, keyed by their path inside the skill folder."""
    files: dict[str, bytes] = {}
    names = ["SKILL.md"] + sorted(
        "references/" + name
        for name in os.listdir(os.path.join(directory, "references"))
        if name.endswith(".md")
    )
    for name in names:
        with open(os.path.join(directory, name), "rb") as handle:
            files[name] = handle.read()
    return files


def global_hook_command(argv0: str, python: str) -> str:
    """The hook command for a global install: the absolute `wingbox` program, else this Python."""
    program = os.path.abspath(argv0) if os.path.basename(argv0) == "wingbox" and os.path.isfile(argv0) else None
    program = program or shutil.which("wingbox")
    if program:
        return f"{shlex.quote(program)} hook pre-search"
    return f"{shlex.quote(python)} -m wingbox.cli hook pre-search"


def load_script(name: str) -> ModuleType:
    directory = scripts_directory(_PACKAGE_DIR)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return import_module(name)


def _version() -> str:
    # Imported here: importlib.metadata costs more than the whole search hook needs.
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("wingbox")
    except PackageNotFoundError:
        return "0+unknown"


def _store(root: str) -> tuple[str, str]:
    """(path of .wingbox/decisions.sexp, its text or "")."""
    load_script("architecture")
    from architecture.decisions import STORE_FILE

    path = os.path.join(root, STORE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return path, handle.read()
    except FileNotFoundError:
        return path, ""


def _prepare_wingbox(root: str) -> None:
    """Create ROOT/.wingbox/ with the .gitignore of the builder (it leaves the files people decide on visible)."""
    load_script("build_code_graph").prepare_wingbox_directory(root)


def _append(path: str, lines: list[str]) -> Optional[str]:
    if not lines:
        return None
    try:
        _prepare_wingbox(os.path.dirname(os.path.dirname(path)))
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as error:
        return f"cannot write {path}: {error}"
    return None


def _learned_removed_by_config(root: str, store, today: str) -> list[str]:
    """`learned-layer` folders that .wingbox/config.yaml does not list: the config undid them."""
    from architecture.config import ConfigOk, read_config
    from architecture.library import load_library
    from architecture.repository import text_reader
    from architecture.sexp import q

    if not store.learned:
        return []
    config = read_config(text_reader(root), load_library())
    if not isinstance(config, ConfigOk):
        return []
    listed = {(part.path, folder) for part in config.parts for _layer, folders in part.layers for folder in folders}
    return [
        f"(learned-removed {q(item.part)} {q(item.folder)} :by config :at {today})"
        for item in store.learned if (item.part, item.folder) not in listed
    ]


def record_decisions(root: str, graph_path: str, advice: tuple) -> list | str:
    """Log the advice of this build and the outcome of the open advice in .wingbox/decisions.sexp;
    return the advice still worth showing, best first (or an error)."""
    import hashlib

    builder = load_script("build_code_graph")
    from architecture.decisions import read_store, reconcile, visible
    from architecture.repository import list_files

    path, text = _store(root)
    with open(graph_path, "rb") as handle:
        graph_hash = hashlib.sha256(handle.read()).hexdigest()[:12]
    paths = list_files(root, frozenset(builder.SKIP_DIRS), frozenset())
    store = read_store(text)
    lines = reconcile(store, advice, paths, date.today().isoformat(), graph_hash)
    lines += _learned_removed_by_config(root, store, date.today().isoformat())
    error = _append(path, lines)
    if error:
        return error
    return visible(read_store(text + "\n".join(lines)), advice)


def _advice_command(args: list[str], cwd: str) -> int:
    """`wingbox advice [--sexp] [--learned]`, `wingbox advice accept|reject <id> [--reason TEXT]`."""
    load_script("architecture")
    from architecture.advice import to_sexp
    from architecture.decisions import read_store, user_outcome, visible
    from architecture.sexp import q

    root = _find_root(cwd)
    if root is None:
        _no_project(cwd)
        return 1
    path, text = _store(root)
    store = read_store(text)
    if args and args[0] in ("accept", "reject"):
        if len(args) < 2:
            print(f"[wingbox] ERROR: wingbox advice {args[0]} <id> [--reason TEXT]", file=sys.stderr)
            return 2
        reason = args[args.index("--reason") + 1] if "--reason" in args and args.index("--reason") + 1 < len(args) else ""
        line = user_outcome(store, args[1], args[0] == "accept", reason, date.today().isoformat())
        if line is None:
            print(f"[wingbox] ERROR: no advice with id {args[1]} in {path}", file=sys.stderr)
            return 1
        error = _append(path, [line])
        if error:
            print(f"[wingbox] ERROR: {error}", file=sys.stderr)
            return 1
        print(line)
        return 0
    sexp = "--sexp" in args
    if "--learned" in args:
        for item in store.learned:
            fact = f"(learned-layer {q(item.part)} {q(item.folder)} {item.layer} :from {item.source})"
            print(fact if sexp else f"{item.part}: folder {item.folder}/ is {item.layer} (learned from {item.source})")
        if not store.learned:
            print("(learned-none)" if sexp else "Nothing learned yet.")
        return 0
    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    loaded = graph.load(_graph_file(root), root)
    if isinstance(loaded, search.MapLoadErr):
        print(f"[wingbox] ERROR: {loaded.error}", file=sys.stderr)
        return 1
    advice, quiet = search.advice_items(graph)
    for item in visible(store, advice):
        print(to_sexp(item) if sexp else f"{item.id}  {item.kind}  {item.target}\n  {item.reason} [{item.style}: {item.rule}]")
    if sexp:
        for part, why in quiet:
            print(f"(advice-none {q(part)} :reason {q(why)})")
    elif quiet:
        unchecked = [(part, why) for part, why in quiet if why.startswith("not checked: ")]
        for part, why in unchecked:
            print(f"not checked: part {part} - {why[len('not checked: '):]}")
        others = [part for part, why in quiet if not why.startswith("not checked: ")]
        if others:
            print(f"no advice for {len(others)} parts without a determined style: {', '.join(others)}")
    return 0


def _find_root(cwd: str) -> Optional[str]:
    """The project at or above cwd: the nearest folder whose .wingbox/ holds the graph."""
    from wingbox import freshness

    return freshness.find_project(cwd)


def _no_project(cwd: str) -> None:
    from wingbox import freshness

    print(f"[wingbox] ERROR: {freshness.no_project_message(cwd)}", file=sys.stderr)


def _graph_file(root: str) -> str:
    """The code graph of the project at root (.wingbox/index.sexp)."""
    from wingbox import freshness

    return freshness.graph_path(root)


def _atom(text: str) -> str:
    import re

    load_script("architecture")
    from architecture.sexp import q

    return text if re.fullmatch(r"[A-Za-z0-9_./@+-]+", text) else q(text)


def _align_command(args: list[str], cwd: str) -> int:
    """`wingbox align [PART] [--to STYLE] [--learn] [--sexp]`: gaps, migration view, learning."""
    import dataclasses

    load_script("architecture")
    from architecture import align
    from architecture.advice import PartRules
    from architecture.config import CONFIG_FILE, render_config
    from architecture.facts import BASELINE_FILE
    from architecture.library import load_library
    from architecture.plan import LayerFolder, RepositoryPlan
    from architecture.sexp import q
    from architecture.words import normalise

    sexp, learn = "--sexp" in args, "--learn" in args
    if learn and "--to" not in args and not sexp:
        from architecture import agent_learn

        if agent_learn.learn_mode(tuple(args), os.environ, sys.stdin.isatty(), sys.stdout.isatty()).mode == "human":
            # A person at a terminal: the questionnaire of `wingbox learn` (an agent gets the task there).
            return _learn_command(["--human", *[a for a in args if a != "--learn"]], cwd)
    to_style = args[args.index("--to") + 1] if "--to" in args and args.index("--to") + 1 < len(args) else ""
    rest = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--to")]
    root = _find_root(cwd)
    if root is None:
        _no_project(cwd)
        return 1
    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    loaded = graph.load(_graph_file(root), root)
    if isinstance(loaded, search.MapLoadErr):
        print(f"[wingbox] ERROR: {loaded.error}", file=sys.stderr)
        return 1
    library = load_library()
    target = library.style(to_style) if to_style else None
    if to_style and target is None:
        print(f"[wingbox] ERROR: unknown style {to_style}", file=sys.stderr)
        return 2
    parts, modules, _dependents, dependencies = search.architecture_inputs(graph)
    project_baseline = {pair: file for pair, file in graph.known.items() if file != BASELINE_FILE}
    type_only = frozenset(
        (source, target) for source, targets in dependencies.items() for target in targets
        if search.is_type_only_import(graph, source, target)
    )
    facts: list[str] = []
    learned_layers: dict[str, list] = {}
    baseline_lines: list[str] = []
    bases: dict[str, object] = {}
    learn_context = _learn_context(root, library) if learn and target is None else None
    for part in parts:
        if rest and part.path not in rest:
            continue
        if not part.denies:
            facts.append(f"(align-none {q(part.path)} :reason {q(f'style {part.style} ({part.state}): no rules to align with')})")
            continue
        rules, mapping, layers = part, None, tuple(sorted(part.ranks, key=lambda l: (-part.ranks[l], l)))
        label = f":style {part.style}"
        if target is not None:
            mapping = target.mapping_from(part.style)
            if mapping is None:
                facts.append(f"(align-none {q(part.path)} :reason {q(f'no mapping from {part.style} to {target.id}')})")
                continue
            rules = PartRules(part.path, target.id, part.state, {l.id: l.rank for l in target.layers},
                              {(d.source, d.target): (d.reason, d.severity, d.fix) for d in target.denies})
            layers = tuple(l.id for l in target.layers)
            label = f":style {target.id} :from {part.style}"
        result = align.align_part(rules, modules, dependencies, layers, mapping, type_only)
        facts.append(
            f"(align-part {q(part.path)} {label} :placed {result.placed} :files {result.files} "
            f":following {result.following} :dependencies {result.dependencies} "
            f":present {q(' '.join(result.layers_present))} :missing {q(' '.join(result.layers_missing))})"
        )
        for gap in result.gaps:
            example = f" :example {q(gap.example)}" if gap.example else ""
            facts.append(f"(align-gap {q(part.path)} :kind {gap.kind} :target {q(gap.target)} :count {gap.count}{example})")
        if not learn or target is not None:
            continue
        if part.style == "declared":
            facts.append(f"(align-learned-none {q(part.path)} :reason {q('declared rules: nothing is guessed, the baseline comes from them')})")
            learned = ()
        else:
            learned = _learn_part(part, library, modules, dependencies, learn_context, facts, bases)
            learned_layers[part.path] = list(learned)
        for entry in align.baseline(part, modules, dependencies, {i.folder: i.layer for i in learned}, project_baseline, type_only):
            baseline_lines.append(
                f"(baseline {q(entry.part)} {_atom(entry.source)} {_atom(entry.target)} :rule {q(entry.rule)} "
                f":from {_atom(entry.origin) if entry.origin == 'align' else q(entry.origin)})"
            )
    if learn and target is None:
        # What files do that their layer may not (SQL in a service, ...) is accepted the same way.
        baseline_lines += [
            f"(baseline-capability {q(item.part)} {_atom(item.module)} {item.capability} :line {item.line} :from align)"
            for item in search.capability_violations(graph, set(graph.nodes))
            if item.severity == "violation" and (not rest or item.part in rest)
        ]
        error = _write_learned(root, learned_layers, bases, sorted(set(baseline_lines)), facts)
        if error:
            print(f"[wingbox] ERROR: {error}", file=sys.stderr)
            return 1
    print("\n".join(facts) if sexp else _align_text(facts))
    return 0


def _align_text(facts: list[str]) -> str:
    """The align facts for people."""
    from architecture.sexp import keyword, parse, positional

    lines: list[str] = []
    for fact in facts:
        t = parse(fact)
        head, part = t[0], (positional(t) or ["."])[0]
        if head == "align-part":
            lines.append(f"{part}  {keyword(t, 'style')}{' (mapped from ' + keyword(t, 'from') + ')' if keyword(t, 'from') else ''}: "
                         f"{keyword(t, 'placed')} of {keyword(t, 'files')} files placed, {keyword(t, 'following')} of "
                         f"{keyword(t, 'dependencies')} dependencies follow the rules"
                         + (f"; missing layers: {keyword(t, 'missing')}" if keyword(t, 'missing') else ""))
        elif head == "align-gap":
            kind, target, count = keyword(t, "kind"), keyword(t, "target"), keyword(t, "count")
            if kind == "rule":
                lines.append(f"  {target}: {count} breaks, e.g. {keyword(t, 'example')}")
            elif kind == "unplaced-folder":
                lines.append(f"  {target}/: {count} files without a layer")
        elif head == "align-style":
            lines.append(f"  closest style: {keyword(t, 'style')} {keyword(t, 'score')} "
                         f"(runner-up {keyword(t, 'runner-up')} {keyword(t, 'runner-up-score')})")
        elif head == "align-placed":
            lines.append(f"  {keyword(t, 'layer')} (by the style): {keyword(t, 'folders')}")
        elif head == "align-learned":
            lines.append(f"  learned: {keyword(t, 'folder')}/ -> {keyword(t, 'layer')} ({keyword(t, 'by')}: {keyword(t, 'why')})")
        elif head in ("align-none", "align-learned-none"):
            lines.append(f"{part}: {keyword(t, 'reason')}")
        elif head == "align-config-written":
            lines.append(f"wrote {t[1]}")
        elif head == "align-baseline":
            lines.append(f"wrote {t[1]} ({keyword(t, 'entries')} known breaks; later builds report only new ones)")
    return "\n".join(lines)


def _learn_context(root: str, library) -> tuple:
    """(plan, parts by path) of the repository, for learning."""
    builder = load_script("build_code_graph")
    from architecture.detect import split_parts
    from architecture.repository import list_files

    skip = frozenset(builder.SKIP_DIRS)
    plan = builder.architecture_plan(root, skip, frozenset())
    if isinstance(plan, builder.Err):
        return None
    paths = list_files(root, skip, frozenset())
    return plan.value, {part.path: part for part in split_parts(paths, library, roots=plan.value.roots)}


def _learn_part(part, library, modules, dependencies, context, facts: list[str], bases: dict) -> tuple:
    """Closest style of the library as the base; the folders it does not place derived by names, patterns
    and imports (architecture/learn.py)."""
    import dataclasses

    from architecture import align
    from architecture.detect import judge
    from architecture.learn import closest_style, derive, unplaced_folders
    from architecture.plan import layer_folders
    from architecture.sexp import q
    from architecture.words import DEFAULT_SETTINGS

    if context is None or part.path not in context[1]:
        return ()
    plan, by_path = context
    files = by_path[part.path]
    current = next((p for p in plan.parts if p.path == part.path), None)
    closest = closest_style(judge(files, library))
    if closest is None or current is None:
        facts.append(f"(align-learned-none {q(part.path)} :reason {q('no style of the library scores on this part')})")
        return ()
    style = library.style(closest.style)
    layers, routing = layer_folders(style, files, DEFAULT_SETTINGS, library)
    base = dataclasses.replace(current, style=style.id, state="configured", layers=layers, routing_root=routing)
    facts.append(
        f"(align-style {q(part.path)} :style {style.id} :score {closest.score:.2f} :coverage {closest.coverage:.2f} "
        f":runner-up {closest.runner_up} :runner-up-score {closest.runner_up_score:.2f})"
    )
    by_layer: dict[str, list[str]] = {}
    for folder in layers:
        by_layer.setdefault(folder.layer, []).append(folder.folder)
    for layer, folders in by_layer.items():
        facts.append(f"(align-placed {q(part.path)} :layer {layer} :folders {q(' '.join(folders))} :by style)")
    by_imports = align.layers_by_imports(part, modules, dependencies)
    derived = [d for d in (derive(folder, members, style, library, by_imports)
                           for folder, members in unplaced_folders(base, files.files, library).items()) if d]
    for item in derived:
        facts.append(f"(align-learned {q(part.path)} :folder {q(item.folder)} :layer {item.layer} :by {item.by} :why {q(item.why)})")
    if derived or style.id != current.style or current.origin != "config":
        bases[part.path] = base
    return tuple(align.Learned(part.path, d.folder, d.layer, f"{d.by}: {d.why}") for d in derived)


def _write_learned(root: str, learned: dict, bases: dict, baseline_lines: list[str], facts: list[str]) -> Optional[str]:
    """Write the base style and the learned folders into .wingbox/config.yaml (with a comment) when
    something changed, the baseline, and the decisions log."""
    import dataclasses

    builder = load_script("build_code_graph")
    from architecture.config import CONFIG_FILE, render_config
    from architecture.facts import BASELINE_FILE
    from architecture.library import load_library
    from architecture.plan import LayerFolder, RepositoryPlan
    from architecture.repository import list_files
    from architecture.sexp import q

    skip = frozenset(builder.SKIP_DIRS)
    plan = builder.architecture_plan(root, skip, frozenset())
    if isinstance(plan, builder.Err):
        return plan.error
    parts = []
    for part in plan.value.parts:
        base = bases.get(part.path, part)
        taught = tuple(LayerFolder(item.folder, item.layer, "learned", f"align: {item.why}", 0) for item in learned.get(part.path, []))
        parts.append(dataclasses.replace(base, layers=taught + tuple(f for f in base.layers if f.match != "learned")))
    library = load_library()
    today = date.today().isoformat()
    try:
        _prepare_wingbox(root)
        if bases:   # nothing learned and no style change: the config stays as it is
            with open(os.path.join(root, CONFIG_FILE), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(render_config(RepositoryPlan(tuple(parts), plan.value.roots), library, today,
                                           list_files(root, skip, frozenset())))
            facts.append(f"(align-config-written {q(CONFIG_FILE)})")
        with open(os.path.join(root, BASELINE_FILE), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"; written by wingbox align --learn on {today}: rule breaks known at that time\n")
            handle.write("\n".join(baseline_lines) + ("\n" if baseline_lines else ""))
    except OSError as error:
        return str(error)
    facts.append(f"(align-baseline {q(BASELINE_FILE)} :entries {len(baseline_lines)})")
    path, _ = _store(root)
    return _append(path, [
        f"(learned-layer {q(part)} {q(item.folder)} {item.layer} :from align :at {today})"
        for part, items in sorted(learned.items()) for item in items
    ])


def _place_command(args: list[str], cwd: str) -> int:
    """`wingbox place "<what>" [--part PART] [--sexp]`: the layer and folder for new code."""
    builder = load_script("build_code_graph")
    from architecture.detect import is_source, is_test_file
    from architecture.library import load_library
    from architecture.place import choose_placement, place_declared
    from architecture.repository import list_files
    from architecture.sexp import q
    from architecture.target import target_layout

    sexp = "--sexp" in args
    part_name = args[args.index("--part") + 1] if "--part" in args and args.index("--part") + 1 < len(args) else ""
    words = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--part")]
    if not words:
        print('[wingbox] ERROR: wingbox place "<what you add>" [--part PART] [--sexp]', file=sys.stderr)
        return 2
    request = " ".join(words)
    root = _find_root(cwd) or cwd
    probe = cwd
    while probe != os.path.dirname(probe) and not os.path.isfile(os.path.join(probe, ".wingbox", "config.yaml")):
        probe = os.path.dirname(probe)
    if os.path.isfile(os.path.join(probe, ".wingbox", "config.yaml")):
        root = probe
    skip = frozenset(builder.SKIP_DIRS)
    plan = builder.architecture_plan(root, skip, frozenset())
    if isinstance(plan, builder.Err):
        print(f"[wingbox] ERROR: {plan.error}", file=sys.stderr)
        return 1
    library = load_library()
    paths = list_files(root, skip, frozenset())
    here = os.path.relpath(cwd, root).replace(os.sep, "/")
    usable = [p for p in plan.value.parts if p.has_rules]
    if part_name:
        usable = [p for p in usable if p.path == part_name]
    else:
        inside = [p for p in usable if p.path == "." or here == p.path or here.startswith(p.path + "/")]
        usable = sorted(inside, key=lambda p: (-len(p.path), p.path)) if inside else sorted(usable, key=lambda p: (-p.files, p.path))
    if not usable:
        return _say(f"(place-none {q(part_name or '.')} :reason {q('no part with rules: a determined style or declared rules')})", sexp)
    part = usable[0]
    prefix = "" if part.path == "." else part.path + "/"
    part_files = [p for p in paths if p.startswith(prefix) and is_source(p) and not is_test_file(p[len(prefix):], library)]

    def examples(folder: str) -> str:
        inside = sorted(p for p in part_files if folder and p.startswith(folder + "/") and "/" not in p[len(folder) + 1:])
        return " ".join(inside[:3])

    if part.style == "declared":
        found = place_declared(part, part_files, request)
        if found is None:
            return _say(f"(place-none {q(part.path)} :reason {q('no folder of the part shares a word with the request')})", sexp)
        folder, layer = found
        return _say(
            f"(place {q(part.path)} :layer {layer} :folder {q(folder)} :present :style declared "
            f":rule {q(part.declared.layers.file + ': layer ' + layer)} :may-import \"not stated\" :examples {q(examples(folder))})",
            sexp,
        )
    style = library.style(part.style)
    choice = choose_placement(style, request)
    if choice is None:
        return _say(f"(place-none {q(part.path)} :reason {q(f'no placement or layer word of {style.id} matches the request')})", sexp)
    items = [i for i in target_layout(part, paths, library) if i.layer == choice.layer and not i.block]
    present = next((i.present[0] for i in items if i.present), "")
    folder = present or (items[0].folder if items else "")
    if folder and part.path != ".":
        folder = f"{part.path}/{folder}"
    exists = bool(folder) and os.path.isdir(os.path.join(root, folder))   # an existing folder, empty or not, is not new
    state = ":present" if present or exists else (":new" if folder else ":not-stated")
    may = " ".join(choice.may_import) if choice.may_import is not None else "not stated"
    where = f" :where {q(choice.where)}" if choice.where and " " in choice.where else ""
    return _say(
        f"(place {q(part.path)} :layer {choice.layer or '-'} :folder {q(folder)} {state} :style {style.id} "
        f":rule {q('placement: ' + choice.what if choice.what else 'layer words of ' + style.id)} :may-import {q(may)}"
        f"{where} :examples {q(examples(folder) if present else '')})",
        sexp,
    )


def _say(fact: str, sexp: bool) -> int:
    """Print a fact, or its text for people (the printer of `place`)."""
    if sexp:
        print(fact)
        return 0
    load_script("architecture")
    from architecture.sexp import keyword, parse, positional

    tokens = parse(fact)
    if tokens[0] != "place":
        print(f"no place: {keyword(tokens, 'reason')}")
        return 0
    folder = keyword(tokens, "folder") or "(no folder stated)"
    state = "a new folder" if ":new" in tokens else ("" if ":present" in tokens else "no folder stated")
    print(f"{folder}/  {keyword(tokens, 'layer')} layer of {keyword(tokens, 'style')}{', ' + state if state else ''}")
    print(f"  because: {keyword(tokens, 'rule')}")
    if keyword(tokens, "where"):
        print(f"  where: {keyword(tokens, 'where')}")
    print(f"  may import: {keyword(tokens, 'may-import')}")
    if keyword(tokens, "examples"):
        print(f"  like: {keyword(tokens, 'examples').replace(' ', ', ')}")
    return 0


def _compare_command(args: list[str], cwd: str) -> int:
    """`wingbox compare [--sexp]`: the parts of a monorepo side by side, per language."""
    builder = load_script("build_code_graph")
    from architecture.advice import to_sexp
    from architecture.compare import compare
    from architecture.newproject import language_of
    from architecture.sexp import q

    root = cwd
    probe = cwd
    while probe != os.path.dirname(probe):
        if os.path.isfile(os.path.join(probe, ".wingbox", "config.yaml")) or os.path.isfile(_graph_file(probe)):
            root = probe
            break
        probe = os.path.dirname(probe)
    plan = builder.architecture_plan(root, frozenset(builder.SKIP_DIRS), frozenset())
    if isinstance(plan, builder.Err):
        print(f"[wingbox] ERROR: {plan.error}", file=sys.stderr)
        return 1
    languages = {}
    for part in plan.value.parts:
        folder = os.path.join(root, part.path)
        languages[part.path] = language_of(tuple(os.listdir(folder))) if os.path.isdir(folder) else None
    parts, differences, advice = compare(plan.value.parts, {k: v for k, v in languages.items() if v})
    sexp = "--sexp" in args
    for item in parts:
        line = f"(compare-part {q(item.part)} :language {item.language} :style {item.style} :layers {q(' '.join(item.layers))})"
        print(line if sexp else f"{item.part:<30} {item.language:<11} {item.style:<15} {' '.join(item.layers)}")
    for item in differences:
        print(f"(compare-diff {item.language} :kind {item.kind} :detail {q(item.detail)})" if sexp
              else f"  {item.language}: {item.kind}: {item.detail}")
    for item in advice:
        print(to_sexp(item) if sexp else f"  advice: {item.reason}")
    return 0


def _learn_command(args: list[str], cwd: str) -> int:
    """`wingbox learn [PART] [--agent|--human] [--sexp] [--again] [--yes]`, `wingbox learn --apply [FILE]`."""
    import hashlib

    builder = load_script("build_code_graph")
    from architecture import agent_learn
    from architecture.detect import is_test_file, judge, split_parts
    from architecture.learn import closest_style, derive, unplaced_folders
    from architecture.library import load_library
    from architecture.plan import layer_folders
    from architecture.repository import list_files
    from architecture.sexp import q
    from architecture.words import DEFAULT_SETTINGS

    sexp, again = "--sexp" in args, "--again" in args
    apply_from = None
    if "--apply" in args:
        index = args.index("--apply")
        apply_from = args[index + 1] if index + 1 < len(args) and not args[index + 1].startswith("--") else "-"
    wanted = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--apply")]
    mode = agent_learn.learn_mode(tuple(args), os.environ, sys.stdin.isatty(), sys.stdout.isatty())
    root = _find_root(cwd)
    if root is None:
        _no_project(cwd)
        return 1
    library = load_library()
    skip = frozenset(builder.SKIP_DIRS)
    plan = builder.architecture_plan(root, skip, frozenset())
    if isinstance(plan, builder.Err):
        print(f"[wingbox] ERROR: {plan.error}", file=sys.stderr)
        return 1
    paths = list_files(root, skip, frozenset())
    by_path = {part.path: part for part in split_parts(paths, library, roots=plan.value.roots)}
    styled = [p for p in plan.value.parts if p.path in by_path and p.style not in ("declared", "none")
              and (not wanted or p.path in wanted)]

    def top_folders(files: tuple[str, ...]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for relative in files:
            segments = relative.split("/")[:-1]
            if segments and segments[0] == "src":
                segments = segments[1:]
            if segments:
                found.setdefault(segments[0], []).append(relative)
        return found

    def folder_paths(files: tuple[str, ...]) -> dict[str, list[str]]:
        """Every folder of the part below its source root (lib/hooks), with the files directly in it."""
        found: dict[str, list[str]] = {}
        for relative in files:
            directory = relative.rsplit("/", 1)[0] if "/" in relative else ""
            directory = directory[4:] if directory.startswith("src/") else ("" if directory == "src" else directory)
            if directory:
                found.setdefault(directory, []).append(relative)
        return found

    if apply_from is not None:
        text = sys.stdin.read() if apply_from == "-" else open(os.path.join(cwd, apply_from), encoding="utf-8").read()
        parts = {p.path: (p.style, frozenset(top_folders(by_path[p.path].files)) | frozenset(folder_paths(by_path[p.path].files)))
                 for p in styled}
        answer = agent_learn.parse_answer(text, parts, library)
        if isinstance(answer, agent_learn.AnswerErr):
            for error in answer.errors:
                print(f"(learn-error {q(error)})" if sexp else f"error: {error}")
            print("(learn-rejected :saved nothing)" if sexp else "The answer was rejected as a whole; nothing was saved. Fix it and send it again.")
            return 2
        return _learn_record(root, plan.value, answer.entries, mode, by_path, top_folders, library, sexp, "--verbose" in args)
    print(f"(learn-mode {mode.mode} :because {q(mode.because)})" if sexp or mode.mode == "agent"
          else f"learn: interactive ({mode.because}); inside a coding agent, /wingbox learn lets the agent read the code and decide the unclear folders")
    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    graph.load(_graph_file(root), root)
    known = agent_learn.read_learned(_read_text(os.path.join(root, agent_learn.LEARNED_FILE)))
    file_module = {search.module_file(graph, module): module for module in graph.nodes}   # once: it probes the disk
    def part_task(part, style_id: str = ""):
        """What to ask about one part: the folders its style (the closest one unless style_id) leaves open."""
        files = by_path[part.path]
        closest = closest_style(judge(files, library))
        if closest is None:
            return None
        style = library.style(style_id or closest.style)
        layers, routing = layer_folders(style, files, DEFAULT_SETTINGS, library)
        base = _replace(part, style=style.id, state="configured", layers=layers, routing_root=routing)
        folders = top_folders(files.files)
        unplaced = unplaced_folders(base, files.files, library)
        weak = {f.folder: f"{f.match} of {f.via}" for f in layers if f.match in ("spelling", "synonym")}
        prefix = "" if part.path == "." else part.path + "/"
        infos, known_rows = [], []
        # Nested folders whose files break a rule, or rest on a guess, at the deepest folder that holds them.
        nested = folder_paths(files.files)
        breaks: dict[str, int] = {}

        module_path = {module: path for path, module in file_module.items()}

        def nested_folder(module: str) -> str:
            path = module_path.get(module, module)[len(prefix):]
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            return directory[4:] if directory.startswith("src/") else directory

        for item in search.boundary_violations(graph, {m for m in graph.nodes if m.startswith(prefix) or part.path == "."}):
            if item.part != part.path or item.known:
                continue
            for module in (item.source, item.target):
                breaks[nested_folder(module)] = breaks.get(nested_folder(module), 0) + 1
        for module in (m for m in graph.nodes if m.startswith(prefix) or part.path == "."):
            label = search.module_label(graph, module)
            if label.part == part.path and label.match in ("role", "synonym", "spelling"):
                breaks.setdefault(nested_folder(module), 0)
        asked = sorted(set(unplaced) | (set(weak) & set(folders)))
        deep = sorted((f for f in breaks if f and f in nested and f not in asked), key=lambda f: (-breaks[f], f))
        more = max(0, len(asked) + len(deep) - agent_learn.MAX_FOLDERS)
        asked += deep[: max(0, agent_learn.MAX_FOLDERS - len(asked))]
        for folder in asked:
            members = folders.get(folder) or nested.get(folder, [])
            seen = known.get((part.path, folder))
            if seen and not again and not agent_learn.changed_a_lot(seen[2], len(members)):
                known_rows.append((folder, seen[0], seen[1]))
                continue
            modules = [file_module[prefix + f] for f in members if prefix + f in file_module]
            out_layers: dict[str, int] = {}
            in_layers: dict[str, int] = {}
            ids = set(modules)
            for module in modules:
                for target in search.module_dependencies(graph, module):
                    if target not in ids:
                        layer = search.module_label(graph, target).layer
                        out_layers[layer] = out_layers.get(layer, 0) + 1
                for source in search.module_dependents(graph, module):
                    if source not in ids:
                        layer = search.module_label(graph, source).layer
                        in_layers[layer] = in_layers.get(layer, 0) + 1
            guess = derive(folder.rsplit("/", 1)[-1], tuple(members), style, library, {})
            infos.append(agent_learn.FolderInfo(
                folder, tuple(prefix + f for f in sorted(m for m in members if not is_test_file(m, library))[:3]),
                tuple(sorted(out_layers.items(), key=lambda kv: (-kv[1], kv[0]))),
                tuple(sorted(in_layers.items(), key=lambda kv: (-kv[1], kv[0]))),
                guess.layer if guess else "", weak.get(folder, ""), breaks.get(folder, 0),
            ))
        meanings = tuple(
            (layer.id, "; ".join(p.what for p in style.placements if p.layer == layer.id) or layer.meaning or "-")
            for layer in style.layers
        )
        placed: dict[str, list[str]] = {}
        for f in layers:
            placed.setdefault(f.layer, []).append(f.folder)
        return agent_learn.PartTask(part.path, style.id, closest.score, closest.runner_up, closest.runner_up_score,
                                    meanings, tuple((k, tuple(v)) for k, v in placed.items()), tuple(infos), tuple(known_rows),
                                    more)

    if mode.mode == "human":
        entries = _questionnaire(styled, by_path, part_task, library, "--yes" in args)
        if not entries:
            print("Nothing to learn.")
            return 0
        status = _learn_record(root, plan.value, entries, mode, by_path, top_folders, library, sexp, "--verbose" in args)
        if status == 0 and "--yes" not in args:
            _offer_editor(os.path.join(root, ".wingbox", "config.yaml"))
        return status
    tasks = [task for task in (part_task(part) for part in styled) if task is not None]
    facts = agent_learn.task_facts(tuple(tasks), "wingbox learn --apply < answer.sexp   (or: wingbox learn --apply answer.sexp)")
    print("\n".join(facts) if sexp else agent_learn.task_text(facts), end="\n" if sexp else "")
    return 0


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


class _quiet:
    """Silence stdout and stderr at the file descriptor level (the parser processes print there too)."""

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.saved: list[int] = []

    def __enter__(self) -> None:
        if self.verbose:
            return
        sys.stdout.flush()
        sys.stderr.flush()
        null = os.open(os.devnull, os.O_WRONLY)
        for fd in (1, 2):
            self.saved.append(os.dup(fd))
            os.dup2(null, fd)
        os.close(null)
        self.streams = (sys.stdout, sys.stderr)
        self.sink = open(os.devnull, "w", encoding="utf-8", newline="\n")
        sys.stdout = sys.stderr = self.sink

    def __exit__(self, *exc: object) -> None:
        if self.verbose:
            return
        sys.stdout, sys.stderr = self.streams
        self.sink.close()
        for fd, saved in zip((1, 2), self.saved):
            os.dup2(saved, fd)
            os.close(saved)


def _learn_record(root: str, plan, entries: tuple, mode, by_path: dict, top_folders, library, sexp: bool,
                  verbose: bool = False) -> int:
    """Write an accepted answer: config (never over a user entry), learned.sexp, decisions, .gitignore; rebuild."""
    from architecture import agent_learn
    from architecture.config import CONFIG_FILE, ConfigOk, read_config, render_config
    from architecture.detect import judge
    from architecture.learn import closest_style
    from architecture.plan import LayerFolder, RepositoryPlan
    from architecture.repository import list_files, text_reader
    from architecture.sexp import q

    builder = load_script("build_code_graph")
    config = read_config(text_reader(root), library)
    stated = {(p.path, folder): layer for p in config.parts for layer, folders in p.layers for folder in folders} \
        if isinstance(config, ConfigOk) else {}
    today = date.today().isoformat()
    out: list[str] = []
    parts = []
    for part in plan.parts:
        mine = [e for e in entries if e.part == part.path]
        style = next((e.value for e in mine if e.kind == "style"), part.style)
        taught = []
        for e in mine:
            if e.kind != "layer":
                continue
            if (part.path, e.folder) in stated:
                out.append(f"(learn-kept {q(part.path)} {q(e.folder)} {stated[(part.path, e.folder)]} :reason \"stated in {CONFIG_FILE}\")")
                continue
            taught.append(LayerFolder(e.folder, e.value, "learned", f"{'agent' if mode.mode == 'agent' else 'person'}: {e.because}", 0))
            out.append(f"(learn-learned {q(part.path)} {q(e.folder)} {e.value} :because {q(e.because)})")
        if style != part.style:
            out.append(f"(learn-style {q(part.path)} {style})")
        parts.append(_replace(part, style=style, layers=tuple(taught) + tuple(f for f in part.layers if f.folder not in {t.folder for t in taught})))
    styles = {}
    for part in plan.parts:
        files = by_path.get(part.path)
        closest = closest_style(judge(files, library)) if files else None
        styles[part.path] = (part.style, closest.score if closest else 0.0)
    counts = {(e.part, e.folder): sum(1 for f in by_path[e.part].files
                                      if f.startswith(e.folder + "/") or f.startswith("src/" + e.folder + "/"))
              for e in entries if e.folder}
    skip = frozenset(builder.SKIP_DIRS)
    try:
        _prepare_wingbox(root)
        with open(os.path.join(root, CONFIG_FILE), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_config(RepositoryPlan(tuple(parts), plan.roots), library, today, list_files(root, skip, frozenset())))
        with open(os.path.join(root, agent_learn.LEARNED_FILE), "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(agent_learn.learned_lines(entries, today, mode, styles, counts)) + "\n")
    except OSError as error:
        print(f"[wingbox] ERROR: {error}", file=sys.stderr)
        return 1
    _append(_store(root)[0], [
        f"(learned-by-agent {q(e.part)} {q(e.folder)} {e.value} :because {q(e.because)} :mode {mode.mode} :at {today})"
        for e in entries if e.kind == "layer" and (e.part, e.folder) not in stated
    ])
    with _quiet(verbose):
        status = _build(build_arguments([root], root))
    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    graph.load(_graph_file(root), root)
    breaks = search.boundary_violations(graph, set(graph.nodes))
    violations = len({(b.source, b.target) for b in breaks if b.severity != "candidate" and not b.known
                      and search._layer_is_certain(graph, b.source) and search._layer_is_certain(graph, b.target)
                      and not search.is_type_only_import(graph, b.source, b.target)})
    out.append(f"(learn-result :build {'ok' if status == 0 else 'failed'} :violations {violations})")
    out.append(f"(learn-saved {q('.wingbox/')} :commit \"config.yaml learned.sexp decisions.sexp baseline.sexp\")")
    if sexp:
        print("\n".join(out))
        return 0
    from architecture.sexp import keyword, parse

    for fact in out:
        t = parse(fact)
        if t[0] == "learn-learned":
            print(f"learned {t[2]}/ -> {t[3]} ({keyword(t, 'because')})")
        elif t[0] == "learn-kept":
            print(f"kept {t[2]}/ -> {t[3]} ({keyword(t, 'reason')})")
        elif t[0] == "learn-style":
            print(f"style of {t[1]}: {t[2]}")
        elif t[0] == "learn-result":
            print(f"rebuilt: violations: {keyword(t, 'violations')}")
        elif t[0] == "learn-saved":
            print("saved in .wingbox/ - commit config.yaml, learned.sexp, decisions.sexp and baseline.sexp to share it")
    return 0


def _ask(prompt: str, default: str) -> str:
    """One answer from the person; an empty line or the end of input means the default."""
    print(prompt, end="", flush=True)
    line = sys.stdin.readline()
    if not line:
        print()
    return line.strip() or default


def _questionnaire(parts, by_path, part_task, library, accept: bool) -> tuple:
    """A person in a terminal: the style of each part among its candidates, then one question per open folder.

    accept (--yes) takes the closest style and each folder's guess (unknown without one) without asking.
    Returns the answer as the entries an agent's answer gives.
    """
    from architecture import agent_learn
    from architecture.detect import judge

    entries: list = []
    for part in parts:
        judgement = judge(by_path[part.path], library)
        scores = sorted({s.style: s for s in (*judgement.candidates, *judgement.near)}.values(),
                        key=lambda s: (judgement.best is None or s.style != judgement.best.style, -s.score, s.style))[:4]
        task = part_task(part)
        if task is None or not scores:
            continue
        style_id = task.style
        if not accept:
            print(f"\nPart {part.path}: which style does it follow?")
            for number, score in enumerate(scores, start=1):
                style = library.style(score.style)
                summary = (style.summary.split(". ")[0].rstrip(".") + ".") if style and style.summary else ""
                print(f"  {number}) {score.style} {score.score:.2f} - {summary}")
            names = [score.style for score in scores]
            while True:
                answer = _ask(f"Style [1 = {style_id}]: ", "1")
                if answer.isdigit() and 1 <= int(answer) <= len(names):
                    chosen = names[int(answer) - 1]
                    break
                if library.style(answer) is not None:
                    chosen = library.style(answer).id
                    break
                print(f"  answer a number from 1 to {len(names)} or a style name")
            if chosen != style_id:
                task = part_task(part, chosen) or task
                style_id = chosen
        entries.append(agent_learn.Entry("style", part.path, "", style_id,
                                         "operator: closest style (--yes)" if accept else "operator: chosen in the questionnaire"))
        layers = [layer for layer, _ in task.layers]
        for info in task.folders:
            default = info.guess or "unknown"
            if accept:
                answer = default
            else:
                print(f"\n  {part.path}: folder {info.folder}/  files: {' '.join(info.files) or '-'}")
                print(f"    imports: {agent_learn._counts(info.imports) or 'none'}; "
                      f"imported by: {agent_learn._counts(info.imported_by) or 'none'}")
                while True:
                    answer = _ask(f"    layer ({', '.join(layers)}, unknown) [{default}]: ", default)
                    if answer in layers or answer == "unknown":
                        break
                    print(f"    one of: {', '.join(layers)}, unknown")
            if answer == "unknown":
                entries.append(agent_learn.Entry("unknown", part.path, info.folder, "", "operator: does not know"))
            else:
                why = "operator: guess accepted (--yes)" if accept else "operator: answered in the questionnaire"
                entries.append(agent_learn.Entry("layer", part.path, info.folder, answer, why))
    return tuple(entries)


def _offer_editor(path: str) -> None:
    """At the end of the questionnaire: open the config in $VISUAL or $EDITOR for a final look."""
    import shlex
    import subprocess

    if _ask(f"\nOpen {path} in your editor for a final look? [y/N]: ", "n").lower() not in ("y", "yes"):
        return
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        print(f"no editor set (EDITOR or VISUAL); the file is {path}")
        return
    try:
        subprocess.run([*shlex.split(editor), path], check=False)
    except OSError as error:
        print(f"[wingbox] ERROR: cannot start {editor}: {error}", file=sys.stderr)


def write_outputs(root: str, graph_path: str) -> OutputsResult:
    """Write .wingbox/REPORT.md and .wingbox/map.html from a built code graph."""
    from wingbox import freshness, map_page, overview

    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    loaded = graph.load(graph_path, root)
    if isinstance(loaded, search.MapLoadErr):
        return OutputsFailed(f"cannot read {graph_path}: {loaded.error}")
    shown = record_decisions(root, graph_path, search.advice_items(graph)[0])
    if isinstance(shown, str):
        return OutputsFailed(shown)
    view = overview.build_overview(graph, search, os.path.basename(os.path.normpath(root)) or root, advice=shown)
    generated = date.today().isoformat()
    report = freshness.report_path(root)
    page = freshness.map_path(root)
    directory = os.path.dirname(page)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(report, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(overview.render_report(view, generated, _version()))
        with open(page, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(map_page.render_map(view, generated, _version(), map_page.read_assets()))
    except OSError as error:
        return OutputsFailed(f"cannot write {directory}: {error}")
    return OutputsWritten(report=report, page=page)


@dataclass(frozen=True)
class InitArguments:
    root: str
    force: bool
    sexp: bool
    synonyms: bool
    language: str = ""
    style: str = ""
    yes: bool = False
    scaffold: bool = False
    details: bool = False


def init_arguments(args: list[str], cwd: str) -> InitArguments | str:
    """Arguments of `wingbox init [PATH] [--force] [--sexp] [--synonyms]`, or an error."""
    flags = ("--force", "--sexp", "--synonyms", "--yes", "--scaffold", "--details")
    values: dict[str, str] = {}
    rest: list[str] = []
    index = 0
    while index < len(args):
        if args[index] in ("--language", "--style"):
            if index + 1 >= len(args):
                return f"{args[index]} requires a value"
            values[args[index]] = args[index + 1]
            index += 2
            continue
        if args[index] not in flags:
            rest.append(args[index])
        index += 1
    unknown = [argument for argument in rest if argument.startswith("-")]
    if unknown:
        return f"unknown option {unknown[0]}"
    if len(rest) > 1:
        return "init takes one PATH"
    root = os.path.realpath(os.path.join(cwd, rest[0])) if rest else os.path.realpath(cwd)
    if not os.path.isdir(root):
        return f"{root} is not a directory"
    return InitArguments(
        root, "--force" in args, "--sexp" in args, "--synonyms" in args, values.get("--language", ""),
        values.get("--style", ""), "--yes" in args, "--scaffold" in args, "--details" in args,
    )


def _graph_advice(root: str) -> tuple:
    """Advice of the last build (move, fix-violation), when .wingbox/index.sexp exists."""
    path = _graph_file(root)
    if not os.path.isfile(path):
        return ()
    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    loaded = graph.load(path, root)
    if isinstance(loaded, search.MapLoadErr):
        print(f"[wingbox] WARNING: {path} not read: {loaded.error}", file=sys.stderr)
        return ()
    return search.advice_items(graph)[0]


def _home_defaults(library) -> dict[str, str]:
    """language -> favourite style, from $HOME/.wingbox/config.yaml (`defaults:`)."""
    path = os.path.join(str(Path.home()), ".wingbox", "config.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ImportError, ValueError) as error:
        print(f"[wingbox] WARNING: {path} not read: {error}", file=sys.stderr)
        return {}
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in defaults.items() if library.style(str(v)) is not None} if isinstance(defaults, dict) else {}


def _siblings(root: str, library) -> tuple[dict[str, str], str]:
    """(part -> style, language of each part) of the nearest parent repository with .wingbox/config.yaml."""
    from architecture.config import ConfigOk, parse_config
    from architecture.newproject import language_of

    parent = os.path.dirname(root)
    while parent != os.path.dirname(parent):
        config = os.path.join(parent, ".wingbox", "config.yaml")
        if os.path.isfile(config):
            with open(config, "r", encoding="utf-8") as handle:
                parsed = parse_config(handle.read(), library)
            if not isinstance(parsed, ConfigOk):
                return {}, parent
            found = {}
            for part in parsed.parts:
                folder = os.path.join(parent, part.path)
                names = tuple(os.listdir(folder)) if os.path.isdir(folder) else ()
                if os.path.realpath(folder) != root:
                    found[part.path] = (part.style, language_of(names))
            return {p: v for p, v in found.items()}, parent
        parent = os.path.dirname(parent)
    return {}, ""


def _init_new_project(parsed: "InitArguments", library) -> int:
    """`wingbox init` in a folder without source files: choose a language and a style, write the config."""
    from architecture.config import CONFIG_FILE, render_config, with_header
    from architecture.init import NEXT_STEP
    from architecture.newproject import LANGUAGES, choices
    from architecture.plan import PartPlan, RepositoryPlan
    from architecture.sexp import q
    from architecture.target import target_layout

    from architecture.newproject import language_of

    interactive = sys.stdin.isatty()
    language = parsed.language or language_of(tuple(os.listdir(parsed.root))) or ""
    if not language and interactive:
        print(f"Language of the new project ({', '.join(LANGUAGES)}): ", end="")
        language = sys.stdin.readline().strip()
    if not language:
        print(f"(init-languages {q(' '.join(LANGUAGES))})")
        print('(init-needs "--language LANGUAGE --style STYLE --yes")')
        return 2
    siblings, repo_root = _siblings(parsed.root, library)
    same_language = {part: style for part, (style, lang) in siblings.items() if lang in (language, None)}
    offered = choices(library, language, same_language, _home_defaults(library))
    style_id, origin = parsed.style, "--style"
    if not style_id and parsed.yes and offered:
        style_id, origin = offered[0].style, offered[0].origin   # --yes takes the first choice (siblings, home, library)
    if not style_id and interactive:
        for number, choice in enumerate(offered, start=1):
            style = library.style(choice.style)
            note = f" ({choice.origin}: {choice.note})" if choice.note else (f" ({choice.origin})" if choice.origin != "library" else "")
            print(f"  {number}. {choice.style}: {style.name if style else ''}{note}")
        print("Style number [1]: ", end="")
        answer = sys.stdin.readline().strip() or "1"
        if not answer.isdigit() or not 1 <= int(answer) <= len(offered):
            print(f"[wingbox] ERROR: no style number {answer}", file=sys.stderr)
            return 2
        style_id, origin = offered[int(answer) - 1].style, offered[int(answer) - 1].origin
    if not style_id:
        for number, choice in enumerate(offered, start=1):
            style = library.style(choice.style)
            note = f" :note {q(choice.note)}" if choice.note else ""
            print(f"(init-choice {number} {choice.style} :language {language} :from {choice.origin}{note} "
                  f":name {q(style.name if style else '')} :source {q(style.cite() if style else '')})")
        print(f"(init-needs {q(f'--language {language} --style STYLE --yes')})")
        return 2
    style = library.style(style_id)
    if style is None:
        print(f"[wingbox] ERROR: unknown style {style_id}", file=sys.stderr)
        return 2
    # Inside a repository with a root .wingbox/config.yaml the new package is a part of that file.
    in_repo = bool(repo_root) and os.path.isfile(os.path.join(repo_root, CONFIG_FILE))
    base = repo_root if in_repo else parsed.root
    part_path = os.path.relpath(parsed.root, base).replace(os.sep, "/") if in_repo else "."
    config_path = os.path.join(base, CONFIG_FILE)
    if not in_repo and os.path.exists(config_path) and not parsed.force:
        print(f"(arch-config-kept {q(CONFIG_FILE)} :reason {q('the file exists; wingbox init --force rewrites it')})")
        return 0
    if interactive and not parsed.yes:
        print(f"Write {style.id} for {part_path if in_repo else 'this project'} into {CONFIG_FILE}? [Y/n]: ", end="")
        if sys.stdin.readline().strip().lower() not in ("", "y", "yes"):
            return 1
    parent = part_path.split("/")[-2] if "/" in part_path else ""
    level = "component" if parent.lower() in {n.lower() for n in library.c4.component_folders} else "container"
    part = PartPlan(path=part_path, kind=f"new {language} project", level=level, files=0, style=style.id, state="configured",
                    origin="config", score=None, judgement=None, layers=(), routing_root=(), declared=None)
    text = render_config(RepositoryPlan(parts=(part,), roots=("",)), library, date.today().isoformat(), (),
                         with_target=True, chosen=origin)
    try:
        _prepare_wingbox(os.path.dirname(os.path.dirname(config_path)))
        if in_repo:
            with open(config_path, "r", encoding="utf-8") as handle:
                existing = handle.read()
            block = text.split("parts:\n", 1)[1]
            with open(config_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(with_header(existing.rstrip("\n") + "\n" + block, library, date.today().isoformat()))
        else:
            with open(config_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
    except OSError as error:
        print(f"[wingbox] ERROR: cannot write {config_path}: {error}", file=sys.stderr)
        return 1
    fact = (f"(init-new-project {q(part_path)} :language {language} :style {style.id} :from {q(origin)} "
            f":level {level} :config {q(os.path.relpath(config_path, parsed.root))})")
    print(fact if parsed.sexp else
          f"{part_path if in_repo else 'new project'}: {style.id} ({origin}), {language}, {level}\n"
          f"wrote {os.path.relpath(config_path, parsed.root)}\n{NEXT_STEP}")
    if parsed.scaffold:
        for item in target_layout(_replace(part, path="."), (), library):
            if "<" in item.folder:
                continue
            os.makedirs(os.path.join(parsed.root, item.folder), exist_ok=True)
            print(f"(init-scaffold {q(item.folder)})" if parsed.sexp else f"created {item.folder}/")
    return 0


def _replace(item, **changes):
    import dataclasses

    return dataclasses.replace(item, **changes)


def _init(args: list[str], cwd: str) -> int:
    parsed = init_arguments(args, cwd)
    if isinstance(parsed, str):
        print(f"[wingbox] ERROR: {parsed}", file=sys.stderr)
        return 2
    root, force, sexp = parsed.root, parsed.force, parsed.sexp
    builder = load_script("build_code_graph")
    load_script("architecture")
    from architecture.config import CONFIG_FILE, render_config
    from architecture.init import init_facts, render_text
    from architecture.library import load_library
    from architecture.plan import detect_plan
    from architecture.repository import list_files, text_reader
    from architecture.sexp import q
    from architecture.words import DEFAULT_SETTINGS, SYNONYM_SETTINGS

    skip = frozenset(builder.SKIP_DIRS)
    ignored = builder.load_git_ignored_paths(root, skip)
    if isinstance(ignored, builder.GitIgnoreUnavailable):
        print(f"[wingbox] {builder._ignore_note(ignored.reason)}", file=sys.stderr)
        ignored_paths: frozenset[str] = frozenset()
    else:
        ignored_paths = ignored.paths
    library = load_library()
    settings = SYNONYM_SETTINGS if parsed.synonyms else DEFAULT_SETTINGS
    paths = list_files(root, skip, ignored_paths)
    from architecture.detect import is_source

    if not any(is_source(path) for path in paths):
        return _init_new_project(parsed, library)
    plan = detect_plan(paths, text_reader(root), library, settings)
    config_path = os.path.join(root, CONFIG_FILE)
    if os.path.exists(config_path) and not force:
        config_line = f"(arch-config-kept {q(CONFIG_FILE)} :reason {q('the file exists; wingbox init --force rewrites it')})"
    else:
        try:
            _prepare_wingbox(os.path.dirname(os.path.dirname(config_path)))
            with open(config_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(render_config(plan, library, date.today().isoformat(), paths))
        except OSError as error:
            print(f"[wingbox] ERROR: cannot write {config_path}: {error}", file=sys.stderr)
            return 1
        config_line = f"(arch-config-written {q(CONFIG_FILE)})"
    from architecture.decisions import read_store, visible

    store = read_store(_store(root)[1])
    facts = init_facts(plan, library, config_line, paths, _graph_advice(root), lambda items: visible(store, items),
                       details=parsed.details)
    print("\n".join(facts) if sexp else render_text(facts, library.index.min_source_files), end="\n" if sexp else "")
    return 0


def _build(arguments: list[str]) -> int:
    from wingbox import freshness

    builder = load_script("build_code_graph")
    options = builder.parse_builder_options(arguments)
    if not isinstance(options, builder.Ok):
        print(f"[wingbox] build: {options.error}\n{BUILD_USAGE}", file=sys.stderr)
        return 2
    root = options.value.root
    # The build clears the marks of the hooks it includes.
    generation = freshness.read_state(root).generation
    status = int(builder.main(arguments))
    if status == 0:
        excluded = tuple(sorted(options.value.skip_directories - frozenset(builder.SKIP_DIRS)))
        freshness.record_build(root, generation, excluded)
    return _build_outputs(builder, status, options.value)


def _build_outputs(builder: ModuleType, status: int, options) -> int:
    if status != 0:
        return status
    outputs = write_outputs(options.root, options.output_path)
    if isinstance(outputs, OutputsFailed):
        print(f"[wingbox] ERROR: report and map not written: {outputs.error}", file=sys.stderr)
        return 1
    relative = os.path.relpath(outputs.page, options.root).replace(os.sep, "/")
    print(f"[wingbox] Report  {os.path.relpath(outputs.report, options.root).replace(os.sep, '/')}")
    print(f"[wingbox] Map     {relative}  (wingbox open shows it in a browser)")
    return 0


def _open_command(rest: list[str], cwd: str) -> int:
    """`wingbox open [PATH]`: .wingbox/map.html of the project at or above PATH, in the browser."""
    import webbrowser

    from wingbox import freshness

    if any(argument in ("-h", "--help") for argument in rest):
        print("usage: wingbox open [PATH]\n\nOpen .wingbox/map.html of the project at or above PATH (default: the "
              "current folder)\nin the browser; print its path when there is no browser.")
        return 0
    start = os.path.normpath(os.path.join(cwd, rest[0])) if rest else cwd
    root = freshness.find_project(start)
    if root is None:
        print(f"[wingbox] {freshness.no_project_message(start)}", file=sys.stderr)
        return 1
    page = freshness.map_path(root)
    if not os.path.isfile(page):
        print(f"[wingbox] no map in {os.path.dirname(page)}: run wingbox build {root}", file=sys.stderr)
        return 1
    try:
        opened = webbrowser.open("file://" + page)
    except webbrowser.Error:
        opened = False
    print(page if not opened else f"[wingbox] Opened {page}")
    return 0


def _print_facts(facts: list, sexp: bool) -> None:
    from wingbox.facts import render_sexp, render_text

    sys.stdout.write(render_sexp(facts) if sexp else render_text(facts, str(Path.home())))


def _machine(cwd: str):
    from wingbox import install

    return install.Machine(
        home=str(Path.home()),
        cwd=cwd,
        version=_version(),
        skill_files=read_skill_files(skill_directory(_PACKAGE_DIR)),
        hook_command=global_hook_command(sys.argv[0], sys.executable),
    )


def _install_command(command: str, rest: list[str], cwd: str) -> int:
    from wingbox import install

    if any(argument in ("-h", "--help") for argument in rest):
        print(install.__doc__)
        return 0
    options = install.parse_arguments("install" if command == "install" else "uninstall", rest)
    if isinstance(options, install.UsageError):
        print(f"[wingbox] {command}: {options.message}", file=sys.stderr)
        return 2
    machine = _machine(cwd)
    facts = install.install(options, machine) if options.command == "install" else install.uninstall(options, machine)
    _print_facts(facts, options.sexp)
    return install.exit_status(facts)


def _package_origin():
    from importlib.metadata import PackageNotFoundError, distribution

    from wingbox import update

    in_docker = os.path.exists("/.dockerenv")
    try:
        direct_url = distribution("wingbox").read_text("direct_url.json")
    except PackageNotFoundError:
        # Running from a source checkout: the package is not installed.
        return update.FromLocal(os.path.normpath(os.path.join(_PACKAGE_DIR, os.pardir, os.pardir)), True)
    return update.package_origin(direct_url, in_docker)


def _update_command(rest: list[str]) -> int:
    from wingbox import install, update

    if any(argument in ("-h", "--help") for argument in rest):
        print(update.__doc__)
        return 0
    options = update.parse_arguments(rest)
    if isinstance(options, update.UsageError):
        print(f"[wingbox] update: {options.message}", file=sys.stderr)
        return 2
    home = str(Path.home())
    record = install.record_path(home)
    try:
        with open(record, "r", encoding="utf-8") as handle:
            installed = install.read_record(handle.read())
    except FileNotFoundError:
        installed = []
    python = sys.executable
    package_parent = os.path.dirname(_PACKAGE_DIR)
    environment = update.Environment(
        origin=_package_origin(),
        python=python,
        prefix=sys.prefix,
        installed_version=_version(),
        installed=installed,
        run=update.run_installer,
        new_version=lambda: update.read_new_version(python),
        refresh=lambda entry: update.refresh_installed(python, package_parent, entry),
    )
    facts = update.update(options, environment)
    warnings = update.old_layout_warnings(os.getcwd(), installed)
    if options.sexp:
        _print_facts(facts + warnings, True)
    else:
        _print_facts(facts, False)
        # On Windows the console must accept ANSI sequences too (virtual terminal processing).
        color = update.use_color(sys.stdout.isatty(), os.environ) and (os.name != "nt" or load_script("portable").ansi_colours(sys.stdout))
        for warning in warnings:
            print(update.warning_text(warning, color))
    return update.exit_status(facts)


def main(argv: Optional[list[str]] = None) -> int:
    """Run a command; a reader that closes the pipe (`| head`) ends it quietly."""
    # A Windows console with a legacy code page must never stop a command on a character it cannot show.
    load_script("portable").utf8_streams()
    try:
        return _run(argv)
    except BrokenPipeError:
        # Point stdout at the null device so the interpreter does not report the closed pipe at exit.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 0


def _run(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if args[0] in ("-V", "--version", "version"):
        print(f"wingbox {_version()}")
        return 0

    cwd = os.getcwd()
    command, rest = args[0], args[1:]
    if command == "hook":
        from wingbox import hook

        return hook.main(rest)
    if command in ("install", "uninstall"):
        return _install_command(command, rest, cwd)
    if command == "update":
        return _update_command(rest)
    if command == "build":
        if any(argument in ("-h", "--help") for argument in rest):
            print(BUILD_USAGE)
            return 0
        return _build(build_arguments(rest, cwd))
    if command == "open":
        return _open_command(rest, cwd)
    if command == "mcp":
        from wingbox import mcp_server

        return mcp_server.main(rest, cwd)
    if command == "init":
        return _init(rest, cwd)
    if command in _ARCHITECTURE_COMMANDS:
        # They read the graph like the query commands: bring it up to date first, or say it is stale.
        _refresh_before_query([command, *rest], cwd)
        load_script("architecture")
        return _ARCHITECTURE_COMMANDS[command](rest, cwd)
    if command == "infra":
        sys.argv = ["wingbox infra"] + absolutize_path_options(rest, cwd)
        load_script("build_infra_topology").main()
        return 0
    query = absolutize_path_options(args, cwd)
    _refresh_before_query(query, cwd)
    return int(load_script("code_graph_search").main(query))


_ARCHITECTURE_COMMANDS = {
    "advice": lambda rest, cwd: _advice_command(rest, cwd),
    "align": lambda rest, cwd: _align_command(rest, cwd),
    "compare": lambda rest, cwd: _compare_command(rest, cwd),
    "learn": lambda rest, cwd: _learn_command(rest, cwd),
    "place": lambda rest, cwd: _place_command(rest, cwd),
}


def _refresh_before_query(args: list[str], cwd: str) -> None:
    """Bring the graph a query reads up to date (WINGBOX_AUTO_REFRESH=0: only say it is stale)."""
    if "--map" in args or args[0] in ("-h", "--help", "help"):
        return
    from wingbox import freshness

    start = args[args.index("--root") + 1] if "--root" in args[:-1] else cwd
    root = freshness.find_project(start)
    if root is None:
        return  # the query says there is no .wingbox/ and where it looked
    line = freshness.stale_line(freshness.ensure_graph_fresh(root, freshness.refresh_wait()), root)
    if line:
        print(f"[wingbox] WARNING: {line}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
