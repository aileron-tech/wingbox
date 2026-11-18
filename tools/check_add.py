#!/usr/bin/env python3
"""`wingbox add` against the real package index, in a clean environment (CI; needs the network).

    python tools/check_add.py                  csharp and go
    python tools/check_add.py ruby php         the named languages

The unit tests reach the command through a local wheel or a stand-in for the download. This check runs the
real thing once per system and Python version: the checkout is installed without any extra into a fresh
virtual environment (`uv venv`, `uv pip install .`), with a temporary home folder and per-user parser
folder, and for each language:

1. a build of a project with one file of the language and one Python file skips the language, records
   `(skipped-language ...)` and names `wingbox add <language>`;
2. `wingbox add <language>` downloads the pinned wheel, checks its sha256 and unpacks it;
3. the next query brings the graph up to date by itself: the module of the language is in it and the
   record of skipped languages is gone;
4. `wingbox add --list` says the parser is installed from the per-user folder, and a second
   `wingbox add <language>` says it is present.

Exit status 0 when every step held, 1 with the failing step otherwise.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
TIMEOUT_SECONDS = 900
SOURCES = {
    "csharp": ("Service.cs", "namespace Storefront { public class Service { } }\n"),
    "java": ("Service.java", "package storefront;\npublic class Service { }\n"),
    "go": ("service.go", "package storefront\n\nfunc Serve() {}\n"),
    "ruby": ("service.rb", "class Service\nend\n"),
    "php": ("service.php", "<?php\nclass Service {}\n"),
    "dart": ("service.dart", "class Service {}\n"),
}


class Failed(Exception):
    pass


def run(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT_SECONDS, check=False)


def must(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    done = run(command, env, cwd)
    if done.returncode != 0:
        raise Failed(f"{' '.join(command)} exited with {done.returncode}:\n{done.stdout}{done.stderr}")
    return done


def expect(condition: bool, step: str, shown: str) -> None:
    if not condition:
        raise Failed(f"{step}:\n{shown}")


def check(language: str, wingbox: Path, work: Path, env: dict[str, str]) -> None:
    name, text = SOURCES[language]
    project = work / f"project-{language}"
    (project / "storefront").mkdir(parents=True)
    (project / "storefront" / name).write_text(text, encoding="utf-8")
    (project / "tool.py").write_text("X = 1\n", encoding="utf-8")
    record = project / ".wingbox" / "graph-skipped.sexp"
    graph = project / ".wingbox" / "index.sexp"
    fact = f'(skipped-language {language} :files 1 :add "wingbox add {language}")'

    built = must([str(wingbox), "build", "."], env, project)
    expect(f"wingbox add {language}" in built.stderr, f"{language}: the build names the command", built.stderr)
    expect(record.is_file() and fact in record.read_text(encoding="utf-8"), f"{language}: the skipped record", built.stderr)
    expect("storefront" not in graph.read_text(encoding="utf-8"), f"{language}: the file is not in the graph yet", "")
    asked = must([str(wingbox), "impact", "tool.py"], env, project)
    expect(fact in asked.stderr, f"{language}: a query passes the fact on", asked.stderr)

    added = must([str(wingbox), "add", language, "--sexp"], env, project)
    expect(f"(parser-added {language} " in added.stdout, f"{language}: wingbox add", added.stdout + added.stderr)
    expect("(graph-marked " in added.stdout, f"{language}: the project is marked", added.stdout)
    folders = [path for path in Path(env["WINGBOX_PARSERS_DIR"]).glob("*/*") if path.is_dir()]
    expect(any(path.name.startswith("tree-sitter-") for path in folders), f"{language}: the per-user folder", str(folders))

    asked = must([str(wingbox), "impact", "tool.py"], env, project)
    expect("skipped-language" not in asked.stderr, f"{language}: no fact after the refresh", asked.stderr)
    expect("storefront" in graph.read_text(encoding="utf-8"), f"{language}: the file is in the graph", asked.stderr)
    expect(not record.exists(), f"{language}: the skipped record is removed", "")

    listed = must([str(wingbox), "add", "--list", "--sexp"], env, project)
    expect(f"(parser-installed {language} " in listed.stdout and "per-user-folder" in listed.stdout,
           f"{language}: --list", listed.stdout)
    again = must([str(wingbox), "add", language, "--sexp"], env, project)
    expect(f"(parser-present {language} " in again.stdout, f"{language}: a second add", again.stdout)
    print(f"ok  {language}: skipped, added from the package index, in the graph")


def main(argv: list[str]) -> int:
    languages = argv or ["csharp", "go"]
    unknown = [name for name in languages if name not in SOURCES]
    if unknown:
        print(f"unknown language: {', '.join(unknown)}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as directory:
        work = Path(os.path.realpath(directory))
        home = work / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), WINGBOX_PARSERS_DIR=str(work / "parsers"),
                   NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        venv = work / "venv"
        scripts = venv / ("Scripts" if WINDOWS else "bin")
        try:
            must(["uv", "venv", "--quiet", "--python", sys.executable, str(venv)], env, work)
            must(["uv", "pip", "install", "--quiet", "--python", str(scripts / ("python.exe" if WINDOWS else "python")), str(REPO)], env, work)
            for language in languages:
                check(language, scripts / ("wingbox.exe" if WINDOWS else "wingbox"), work, env)
        except Failed as failure:
            print(f"FAILED  {failure}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
