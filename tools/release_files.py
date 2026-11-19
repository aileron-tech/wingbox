#!/usr/bin/env python3
"""Write the checksum file and the SBOM of a release next to its built files.

    python3 tools/release_files.py DIST [--pyproject PATH]    (default PATH: pyproject.toml of the repository)

DIST is the folder the release was built into (`uv build --out-dir DIST`). Two files are written there:

- `<name>-<version>.cdx.json`: a CycloneDX 1.5 JSON SBOM. It names the package of pyproject.toml, the
  built files of that distribution found in DIST with their SHA-256 (`<name>-<version>*`: the source
  archive, the wheel, the Claude Desktop extension when it was built into DIST), and every dependency
  pyproject.toml pins: the name as written, the version, the purl `pkg:pypi/<name>@<version>`. A
  dependency that only an extra names is marked optional. Nothing is resolved from a package index:
  the list is what pyproject.toml declares.
- `SHA256SUMS`: one line per file of DIST, the SBOM included, in the form `shasum -a 256 -c` and
  `sha256sum -c` read. Files whose name starts with a dot are not release files and are left out.

The same files in DIST and the same pyproject.toml give the same bytes: no timestamp, and the serial
number of the SBOM is derived from its content. A dependency without an exact `==` version, or a DIST
without a built file of the package at its version, is an error: exit 1, nothing written.

Standard library only; the tool opens no network connection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKSUMS = "SHA256SUMS"
SBOM_SUFFIX = ".cdx.json"
# name, optional extras, an exact version, an optional environment marker (PEP 508)
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*"
    r"==\s*(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*(?:;\s*(?P<marker>.+?))?\s*$"
)


class ReleaseFilesError(Exception):
    """A cause that stops the tool before it writes anything."""


@dataclass(frozen=True)
class Dependency:
    """A distribution pyproject.toml pins."""

    name: str  # as written
    version: str
    marker: str  # the environment marker, or ""
    optional: bool  # named by an extra only


@dataclass(frozen=True)
class BuiltFile:
    """A file of the release folder and its SHA-256."""

    name: str
    sha256: str


def normalized(name: str) -> str:
    """The name of a distribution as a package index writes it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def purl(name: str, version: str) -> str:
    return f"pkg:pypi/{normalized(name)}@{version}"


def file_prefix(name: str, version: str) -> str:
    """How the name of every built file of a distribution starts (source archive and wheel)."""
    return f"{normalized(name).replace('-', '_')}-{version}"


def pinned(requirement: str, optional: bool) -> Dependency:
    """The dependency a requirement line pins; a line without an exact version is refused."""
    match = _REQUIREMENT.match(requirement)
    if match is None:
        raise ReleaseFilesError(f"not pinned to an exact version with ==: {requirement}")
    return Dependency(match["name"], match["version"], match["marker"] or "", optional)


def dependencies(project: dict) -> list[Dependency]:
    """Every dependency of [project], then those only an extra names, each distribution once, sorted by name."""
    found: dict[str, Dependency] = {}
    for requirement in project.get("dependencies", []):
        item = pinned(requirement, optional=False)
        found.setdefault(normalized(item.name), item)
    for extra in project.get("optional-dependencies", {}).values():
        for requirement in extra:
            item = pinned(requirement, optional=True)
            found.setdefault(normalized(item.name), item)
    return [found[key] for key in sorted(found)]


def _library(item: Dependency) -> dict:
    reference = purl(item.name, item.version)
    component: dict = {
        "type": "library",
        "bom-ref": reference,
        "name": item.name,
        "version": item.version,
        "scope": "optional" if item.optional else "required",
        "purl": reference,
    }
    if item.marker:
        component["properties"] = [{"name": "python:environment-marker", "value": item.marker}]
    return component


def sbom(project: dict, files: list[BuiltFile]) -> dict:
    """The CycloneDX 1.5 document of a package, its built files and its pinned dependencies."""
    name, version = project["name"], project["version"]
    reference = purl(name, version)
    package: dict = {"type": "application", "bom-ref": reference, "name": name, "version": version, "purl": reference}
    if isinstance(project.get("license"), str):
        package["licenses"] = [{"expression": project["license"]}]
    package["components"] = [
        {"type": "file", "name": item.name, "hashes": [{"alg": "SHA-256", "content": item.sha256}]}
        for item in sorted(files, key=lambda item: item.name)
    ]
    libraries = [_library(item) for item in dependencies(project)]
    uses = sorted(item["bom-ref"] for item in libraries)
    body: dict = {
        "metadata": {"component": package},
        "components": libraries,
        "dependencies": [{"ref": reference, "dependsOn": uses}] + [{"ref": ref} for ref in uses],
    }
    # The serial number names this content: the same release files give the same number.
    serial = uuid.uuid5(uuid.NAMESPACE_URL, reference + "#" + json.dumps(body, sort_keys=True, ensure_ascii=True))
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial}", "version": 1, **body}


def sbom_text(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def checksum_text(files: list[BuiltFile]) -> str:
    """One `<sha256>  <name>` line per file, sorted by name."""
    return "".join(f"{item.sha256}  {item.name}\n" for item in sorted(files, key=lambda item: item.name))


def read_files(dist: Path) -> list[BuiltFile]:
    """Every file of the folder with its SHA-256, without what this tool writes there and without
    hidden files (`uv build` puts a .gitignore into the folder)."""
    return [
        BuiltFile(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(dist.iterdir())
        if path.is_file() and not path.name.startswith(".") and path.name != CHECKSUMS and not path.name.endswith(SBOM_SUFFIX)
    ]


def write_release_files(dist: Path, pyproject: Path) -> tuple[Path, Path]:
    """Write the SBOM and the checksum file into dist; the two paths."""
    if not dist.is_dir():
        raise ReleaseFilesError(f"not a folder: {dist}")
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    files = read_files(dist)
    prefix = file_prefix(project["name"], project["version"])
    own = [item for item in files if item.name.startswith(prefix + ".") or item.name.startswith(prefix + "-")]
    if not own:
        raise ReleaseFilesError(f"no built file {prefix}* in {dist}: build the release into it first")
    text = sbom_text(sbom(project, own))
    sbom_path = dist / f"{project['name']}-{project['version']}{SBOM_SUFFIX}"
    sums_path = dist / CHECKSUMS
    sbom_path.write_bytes(text.encode("ascii"))
    listed = files + [BuiltFile(sbom_path.name, hashlib.sha256(text.encode("ascii")).hexdigest())]
    sums_path.write_bytes(checksum_text(listed).encode("ascii"))
    return sbom_path, sums_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write SHA256SUMS and the CycloneDX SBOM of a release folder.")
    parser.add_argument("dist", type=Path, help="the folder the release was built into")
    parser.add_argument("--pyproject", type=Path, default=REPO / "pyproject.toml", help="the pyproject.toml of the package")
    options = parser.parse_args(argv)
    try:
        written = write_release_files(options.dist, options.pyproject)
    except ReleaseFilesError as error:
        print(f"release_files: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
