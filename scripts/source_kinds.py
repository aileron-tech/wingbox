"""Which language a file name belongs to: one Wingbox parses, one it has no parser for, or none.

The builder counts the files of the second kind, and a query about a file that is not in the graph
says which kind it is. The table of parsed extensions repeats the language registry (a test compares
them), so that the query script names a language without loading the parsers.

Standard library only.
"""
from __future__ import annotations

import os
from typing import Optional

# Extension -> the display name of the language that parses it (scripts/lang/<language>/__init__.py).
PARSED_EXTENSIONS: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "TypeScript", ".jsx": "TypeScript",
    ".mjs": "TypeScript", ".cjs": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".rs": "Rust",
    ".cs": "C#",
    ".java": "Java",
    ".go": "Go",
    ".rb": "Ruby", ".rake": "Ruby",
    ".php": "PHP and Laravel",
    ".pas": "Pascal", ".pp": "Pascal", ".lpr": "Pascal", ".inc": "Pascal",
    ".dart": "Dart",
    ".c": "C and C++", ".cc": "C and C++", ".cpp": "C and C++", ".cxx": "C and C++", ".h": "C and C++",
    ".hh": "C and C++", ".hpp": "C and C++", ".hxx": "C and C++", ".inl": "C and C++", ".ipp": "C and C++",
}

# Display name -> the name of the language in the registry, as the record of skipped languages writes it.
LANGUAGE_NAMES: dict[str, str] = {
    "Python": "python", "TypeScript": "typescript", "Rust": "rust", "C#": "csharp", "Java": "java", "Go": "go",
    "Ruby": "ruby", "PHP and Laravel": "php", "Pascal": "pascal", "Dart": "dart", "C and C++": "cpp",
}

# Extension -> a language Wingbox has no parser for. Only programming, build and shader languages: a
# document, a data file or an image is not a language. An extension two languages share names both.
NOT_PARSED_EXTENSIONS: dict[str, str] = {
    ".cmake": "CMake",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".bat": "Batch", ".cmd": "Batch",
    ".swift": "Swift",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".m": "Objective-C or MATLAB", ".mm": "Objective-C",
    ".lua": "Lua",
    ".pl": "Perl", ".pm": "Perl",
    ".r": "R",
    ".jl": "Julia",
    ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".fs": "F#", ".fsx": "F#",
    ".clj": "Clojure", ".cljs": "Clojure",
    ".zig": "Zig",
    ".nim": "Nim",
    ".f90": "Fortran", ".f95": "Fortran", ".f03": "Fortran",
    ".asm": "Assembly", ".s": "Assembly",
    ".sv": "SystemVerilog", ".vhd": "VHDL", ".vhdl": "VHDL",
    ".glsl": "GLSL", ".vert": "GLSL", ".frag": "GLSL", ".hlsl": "HLSL",
    ".sql": "SQL",
    ".groovy": "Groovy", ".gradle": "Groovy",
    ".vue": "Vue", ".svelte": "Svelte",
    ".sol": "Solidity",
    ".proto": "Protocol Buffers",
    ".tf": "Terraform",
    ".mk": "Make",
    ".vb": "Visual Basic",
    ".cu": "CUDA", ".cuh": "CUDA",
}
# Whole file names that are a language of their own.
NOT_PARSED_NAMES: dict[str, str] = {
    "CMakeLists.txt": "CMake",
    "Makefile": "Make", "makefile": "Make", "GNUmakefile": "Make",
    "Dockerfile": "Dockerfile",
}


def _extension(file_name: str) -> str:
    return os.path.splitext(file_name)[1].casefold()


def parsed_language(file_name: str) -> Optional[str]:
    """The display name of the language that parses the file, by its extension; None when none does."""
    lowered = file_name.casefold()
    if lowered.endswith(".blade.php"):
        return PARSED_EXTENSIONS[".php"]
    return PARSED_EXTENSIONS.get(_extension(file_name))


def not_parsed_language(file_name: str) -> Optional[str]:
    """The language of a file Wingbox has no parser for; None for a parsed file and for a file that is no
    source of a language (a document, a data file)."""
    name = os.path.basename(file_name)
    if name in NOT_PARSED_NAMES:
        return NOT_PARSED_NAMES[name]
    if parsed_language(name) is not None:
        return None
    return NOT_PARSED_EXTENSIONS.get(_extension(name))


def not_parsed_facts(counts: dict[str, int]) -> str:
    """The record of the files a build has no parser for: one fact per language, most files first."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "".join(f'(not-parsed-language "{language}" :files {files})\n' for language, files in ordered)


def read_not_parsed(text: str) -> tuple[tuple[str, int], ...]:
    """(language, files) of every fact of the record, in its order."""
    found = []
    for line in text.splitlines():
        if not line.startswith('(not-parsed-language "') or " :files " not in line:
            continue
        language, _, rest = line[len('(not-parsed-language "'):].partition('" :files ')
        count = rest.rstrip(")").strip()
        if language and count.isdigit():
            found.append((language, int(count)))
    return tuple(found)
