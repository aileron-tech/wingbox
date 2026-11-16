"""Command-line entry point for Wingbox.

    wingbox build [PATH] [build options]    Build the code graph of a project (default: current directory)
                                            and write wingbox-out/REPORT.md and wingbox-out/map.html
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
    wingbox --version

Assistants: claude-code, codex, gemini, cursor, copilot, agents (the shared
~/.agents/skills folder). Without --assistant, install uses those whose
configuration folder exists in the home folder.

`wingbox build` runs scripts/build_code_graph.py and every query command runs
scripts/code_graph_search.py, so the options and the output are the ones
documented for those scripts. Relative values of --root, --map and --output are
resolved against the current directory.

The code graph (.code_graph.sexp) is the file agents query. REPORT.md and
map.html say the same thing to a person: the report in text, the map as an
interactive page that opens in a browser without a network connection.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from types import ModuleType
from typing import Optional

_PATH_OPTIONS = ("--root", "--map", "--output")
_VALUE_OPTIONS = _PATH_OPTIONS + ("--lsp-timeout", "--exclude-dir")
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIRECTORY = "wingbox-out"


@dataclass(frozen=True)
class OutputsWritten:
    report: str
    page: str


@dataclass(frozen=True)
class OutputsFailed:
    error: str


OutputsResult = OutputsWritten | OutputsFailed


def absolutize_path_options(args: list[str], cwd: str) -> list[str]:
    """Resolve relative --root, --map and --output values against cwd."""
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


def write_outputs(root: str, graph_path: str) -> OutputsResult:
    """Write wingbox-out/REPORT.md and wingbox-out/map.html from a built code graph."""
    from wingbox import map_page, overview

    search = load_script("code_graph_search")
    graph = search.CodeGraph()
    loaded = graph.load(graph_path, root)
    if isinstance(loaded, search.MapLoadErr):
        return OutputsFailed(f"cannot read {graph_path}: {loaded.error}")
    view = overview.build_overview(graph, search, os.path.basename(os.path.normpath(root)) or root)
    generated = date.today().isoformat()
    directory = os.path.join(root, OUTPUT_DIRECTORY)
    report = os.path.join(directory, "REPORT.md")
    page = os.path.join(directory, "map.html")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(report, "w", encoding="utf-8") as handle:
            handle.write(overview.render_report(view, generated, _version()))
        with open(page, "w", encoding="utf-8") as handle:
            handle.write(map_page.render_map(view, generated, _version(), map_page.read_assets()))
    except OSError as error:
        return OutputsFailed(f"cannot write {directory}: {error}")
    return OutputsWritten(report=report, page=page)


def _build(arguments: list[str]) -> int:
    builder = load_script("build_code_graph")
    status = int(builder.main(arguments))
    options = builder.parse_builder_options(arguments)
    if status != 0 or not isinstance(options, builder.Ok):
        return status
    outputs = write_outputs(options.value.root, options.value.output_path)
    if isinstance(outputs, OutputsFailed):
        print(f"[wingbox] ERROR: report and map not written: {outputs.error}", file=sys.stderr)
        return 1
    relative = os.path.relpath(outputs.page, options.value.root).replace(os.sep, "/")
    print(f"[wingbox] Report  {os.path.relpath(outputs.report, options.value.root).replace(os.sep, '/')}")
    print(f"[wingbox] Map     {relative}  (open it in a browser)")
    return 0


def _print_facts(facts: list, sexp: bool) -> None:
    from wingbox.facts import render_sexp, render_text

    sys.stdout.write(render_sexp(facts) if sexp else render_text(facts, os.path.expanduser("~")))


def _machine(cwd: str):
    from wingbox import install

    return install.Machine(
        home=os.path.expanduser("~"),
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
    home = os.path.expanduser("~")
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
    _print_facts(facts, options.sexp)
    return update.exit_status(facts)


def main(argv: Optional[list[str]] = None) -> int:
    """Run a command; a reader that closes the pipe (`| head`) ends it quietly."""
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
            return int(load_script("build_code_graph").main(rest))
        return _build(build_arguments(rest, cwd))
    if command == "infra":
        sys.argv = ["wingbox infra"] + absolutize_path_options(rest, cwd)
        load_script("build_infra_topology").main()
        return 0
    return int(load_script("code_graph_search").main(absolutize_path_options(args, cwd)))


if __name__ == "__main__":
    sys.exit(main())
