"""How Wingbox starts a Python of its own, and what its own Python may import.

`python -m module` and `python -c code` put the folder they are started in first on the module search
path. Wingbox is started inside the project it reads, so a folder or a file of that project named like a
module Wingbox imports (`wingbox/`, `pip.py`, `yaml.py`) would be imported instead of the installed one:
code of the project would run. Wingbox reads code and never runs it, so every Python it starts gets `-P`
(Python 3.11 and newer), which leaves that folder out, and a command a person starts as `python -m wingbox`
takes the folder off its own path before it loads a parser, a script or any other module.
"""
from __future__ import annotations

import os


def module_command(python: str, module: str) -> tuple[str, ...]:
    """The start of a command that runs an installed module with this Python: (python, "-P", "-m", module)."""
    return (python, "-P", "-m", module)


def code_command(python: str, code: str) -> tuple[str, ...]:
    """A command that runs a line of Python with this interpreter: (python, "-P", "-c", code)."""
    return (python, "-P", "-c", code)


def without_starting_folder(path: list[str], folder: str, package_parent: str) -> list[str]:
    """The module search path without the folder the command was started in ("" stands for it too).

    The folder stays when the Wingbox package itself lies in it (a source checkout run from its own folder),
    and an entry that is not the starting folder is never touched."""
    def same(entry: str, other: str) -> bool:
        return os.path.normcase(os.path.abspath(entry or folder)) == os.path.normcase(os.path.abspath(other))

    if same(folder, package_parent):
        return list(path)
    return [entry for entry in path if not same(entry, folder)]
