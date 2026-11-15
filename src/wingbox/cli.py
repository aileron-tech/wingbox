"""Command-line entry point for Wingbox.

    wingbox build [PATH] [build options]    Build the code graph of a project (default: current directory)
    wingbox <command> [arguments]           Query the code graph: impact, quick, analyze, search, ...
    wingbox infra [arguments]               Build the infrastructure topology (Kustomize, Helm, Terraform)
    wingbox --version

`wingbox build` runs scripts/build_code_graph.py and every query command runs
scripts/code_graph_search.py, so the options and the output are the ones
documented for those scripts. Relative values of --root, --map and --output are
resolved against the current directory.
"""
from __future__ import annotations

import os
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from types import ModuleType
from typing import Optional

_PATH_OPTIONS = ("--root", "--map", "--output")
_VALUE_OPTIONS = _PATH_OPTIONS + ("--lsp-timeout", "--exclude-dir")
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


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


def _load_script(name: str) -> ModuleType:
    directory = scripts_directory(_PACKAGE_DIR)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return import_module(name)


def _version() -> str:
    try:
        return version("wingbox")
    except PackageNotFoundError:
        return "0+unknown"


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if args[0] in ("-V", "--version", "version"):
        print(f"wingbox {_version()}")
        return 0

    cwd = os.getcwd()
    command, rest = args[0], args[1:]
    if command == "build":
        return int(_load_script("build_code_graph").main(build_arguments(rest, cwd)))
    if command == "infra":
        sys.argv = ["wingbox infra"] + absolutize_path_options(rest, cwd)
        _load_script("build_infra_topology").main()
        return 0
    return int(_load_script("code_graph_search").main(absolutize_path_options(args, cwd)))


if __name__ == "__main__":
    sys.exit(main())
