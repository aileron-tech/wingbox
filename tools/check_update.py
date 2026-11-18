#!/usr/bin/env python3
"""Run `wingbox update` for real, through the installed `wingbox` program, with each installer.

`wingbox update` upgrades with the installer that installed Wingbox. This script installs the
checkout the four ways a person does and updates each one by running its own `wingbox` program:

    uv pip   a virtual environment made by `uv venv` (no pip inside)
    uv       `uv tool install`
    pip      a virtual environment made by `python -m venv`
    pipx     `pipx install`

On Windows the `wingbox` program is wingbox.exe, a launcher that keeps running until Python ends,
and Windows lets nobody overwrite or delete a running program: the installer has to put the new
launcher in place while the old one is still in use.

The checkout is built twice, as version 98.0.0 and as version 99.0.0, into a folder the installers
read as an extra package source (UV_FIND_LINKS, PIP_FIND_LINKS); the dependencies come from the
package index. Everything else lives in a temporary folder: the home folder, the uv tool folders,
the pipx folders and the caches.

For each installer the script checks that
- `wingbox update --sexp` exits 0, names the installer and reports `(update-to 99.0.0)`;
- the skill `wingbox install` had put in the home folder, overwritten with old text before the
  update, is rewritten by the new version;
- the same `wingbox` program then answers `wingbox --version` with 99.0.0;
- a second `wingbox update`, with nothing to upgrade, exits 0 and leaves the program working;
- nothing but the `wingbox` program is left beside it.

    python tools/check_update.py            every installer found on PATH (uv is required)
    python tools/check_update.py uv pipx    only the named ones

Needs uv on PATH, pipx for the pipx case, and the network (build backend and dependencies).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
OLD, NEW = "98.0.0", "99.0.0"
PROGRAM = "wingbox.exe" if WINDOWS else "wingbox"
OUTDATED = "outdated skill text\n"
TIMEOUT_SECONDS = 900
LEFTOVER_WAIT_SECONDS = 60
VERSION_LINE = re.compile(r'^version = "[^"]+"$', re.M)
SKIPPED_FOLDERS = shutil.ignore_patterns(".git", "__pycache__", ".wingbox", "dist", "*.egg-info", ".venv", "node_modules")


@dataclass(frozen=True)
class Case:
    name: str        # the method `wingbox update` must report
    program: Path    # the wingbox program a person would start


class Failed(Exception):
    pass


def run(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT_SECONDS, check=False)


def must(command: list[str], env: dict[str, str], cwd: Path) -> str:
    done = run(command, env, cwd)
    if done.returncode != 0:
        raise Failed(f"{' '.join(command)} exited with {done.returncode}:\n{done.stdout}{done.stderr}")
    return done.stdout


def build(version: str, work: Path, links: Path, env: dict[str, str]) -> None:
    """Build the checkout as the given version into links."""
    source = work / f"source-{version}"
    shutil.copytree(REPO, source, ignore=SKIPPED_FOLDERS)
    pyproject = source / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    if len(VERSION_LINE.findall(text)) != 1:
        raise Failed("pyproject.toml: expected exactly one version line")
    pyproject.write_text(VERSION_LINE.sub(f'version = "{version}"', text), encoding="utf-8")
    must(["uv", "build", "--wheel", "--out-dir", str(links), str(source)], env, work)


def bin_folder(environment: Path) -> Path:
    return environment / ("Scripts" if WINDOWS else "bin")


def install_cases(names: list[str], work: Path, env: dict[str, str]) -> list[Case]:
    """Install version 98.0.0 each way; the package source holds no other version of the checkout yet."""
    python = sys.executable
    cases: list[Case] = []
    if "uv pip" in names:
        venv = work / "uv-venv"
        must(["uv", "venv", "--quiet", "--python", python, str(venv)], env, work)
        must(["uv", "pip", "install", "--python", str(bin_folder(venv) / ("python.exe" if WINDOWS else "python")), "wingbox"], env, work)
        cases.append(Case("uv pip", bin_folder(venv) / PROGRAM))
    if "uv" in names:
        must(["uv", "tool", "install", "--python", python, "wingbox"], env, work)
        cases.append(Case("uv", Path(env["UV_TOOL_BIN_DIR"]) / PROGRAM))
    if "pip" in names:
        venv = work / "pip-venv"
        must([python, "-m", "venv", str(venv)], env, work)
        must([str(bin_folder(venv) / ("python.exe" if WINDOWS else "python")), "-m", "pip", "install", "wingbox"], env, work)
        cases.append(Case("pip", bin_folder(venv) / PROGRAM))
    if "pipx" in names:
        must(["pipx", "install", "--python", python, "wingbox"], env, work)
        cases.append(Case("pipx", Path(env["PIPX_BIN_DIR"]) / PROGRAM))
    return cases


def version_of(case: Case, env: dict[str, str], cwd: Path) -> str:
    return must([str(case.program), "--version"], env, cwd).strip()


def leftovers(case: Case) -> list[str]:
    """Files beside the program whose name starts with its name, the program itself aside; waits for a
    file that is removed once the old program has ended."""
    deadline = time.monotonic() + LEFTOVER_WAIT_SECONDS
    while True:
        names = sorted(path.name for path in case.program.parent.iterdir()
                       if path.name.lower().startswith(PROGRAM.lower() + "."))
        if not names or time.monotonic() > deadline:
            return names
        time.sleep(1)


def check(case: Case, skill: Path, env: dict[str, str], cwd: Path) -> None:
    before = version_of(case, env, cwd)
    if OLD not in before:
        raise Failed(f"installed {before!r}, expected {OLD}")
    skill.write_text(OUTDATED, encoding="utf-8")
    first = run([str(case.program), "update", "--sexp"], env, cwd)
    if first.returncode != 0:
        raise Failed(f"wingbox update exited with {first.returncode}:\n{first.stdout}{first.stderr}")
    method = f'(update-method "{case.name}")' if " " in case.name else f"(update-method {case.name})"
    for expected in (method, f"(update-to {NEW})", "(update-refreshed "):
        if expected not in first.stdout:
            raise Failed(f"wingbox update did not print {expected}:\n{first.stdout}{first.stderr}")
    if skill.read_text(encoding="utf-8") == OUTDATED:
        raise Failed("the installed skill was not refreshed")
    after = version_of(case, env, cwd)
    if NEW not in after:
        raise Failed(f"after the update the program answers {after!r}, expected {NEW}")
    second = run([str(case.program), "update", "--sexp"], env, cwd)
    if second.returncode != 0:
        raise Failed(f"a second wingbox update exited with {second.returncode}:\n{second.stdout}{second.stderr}")
    again = version_of(case, env, cwd)
    if NEW not in again:
        raise Failed(f"after a second update the program answers {again!r}, expected {NEW}")
    left = leftovers(case)
    if left:
        raise Failed(f"left beside the program: {', '.join(left)}")


def main(argv: list[str]) -> int:
    if shutil.which("uv") is None:
        print("check_update: uv is not on PATH", file=sys.stderr)
        return 2
    available = ["uv pip", "uv", "pip"] + (["pipx"] if shutil.which("pipx") else [])
    names = [name for name in available if not argv or name in argv or (name == "uv pip" and "uv-pip" in argv)]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = Path(tmp).resolve()
        home, links, cwd = work / "home", work / "links", work / "elsewhere"
        for folder in (home / ".claude", links, cwd):
            folder.mkdir(parents=True)
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env.update(
            HOME=str(home), USERPROFILE=str(home),
            UV_FIND_LINKS=str(links), PIP_FIND_LINKS=str(links),
            UV_CACHE_DIR=str(work / "uv-cache"), PIP_CACHE_DIR=str(work / "pip-cache"),
            UV_TOOL_DIR=str(work / "uv-tools"), UV_TOOL_BIN_DIR=str(work / "uv-bin"),
            PIPX_HOME=str(work / "pipx"), PIPX_BIN_DIR=str(work / "pipx-bin"), PIPX_MAN_DIR=str(work / "pipx-man"),
            PIP_DISABLE_PIP_VERSION_CHECK="1",
        )
        try:
            build(OLD, work, links, env)
            cases = install_cases(names, work, env)
            must([str(cases[0].program), "install", "--yes"], env, cwd)
            skill = home / ".claude" / "skills" / "wingbox" / "SKILL.md"
            if not skill.is_file():
                raise Failed(f"wingbox install wrote no skill at {skill}")
            build(NEW, work, links, env)
        except Failed as failure:
            print(f"check_update: setup failed: {failure}", file=sys.stderr)
            return 1
        failures = 0
        for case in cases:
            try:
                check(case, skill, env, cwd)
                print(f"ok      {case.name}: {case.program.name} updated itself {OLD} -> {NEW}, skill refreshed, nothing left behind")
            except Failed as failure:
                failures += 1
                print(f"FAILED  {case.name}: {failure}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
