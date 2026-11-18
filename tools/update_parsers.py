#!/usr/bin/env python3
"""Write the catalog `wingbox add` downloads from: src/wingbox/assets/parsers.json.

    python3 tools/update_parsers.py            write the catalog
    python3 tools/update_parsers.py --check    exit 1 when the file differs from what the index says

For every language whose parser is installed on demand (scripts/lang/ondemand.py), the catalog names the
package and the exact version its language adapter pins, and for each wheel of that version on the Python
Package Index its file name, URL, sha256 and size, read from the index's JSON API
(https://pypi.org/pypi/<package>/<version>/json). `wingbox add` downloads only a file listed here and
refuses it unless its sha256 matches, so run this after changing a pin, in the same change as the pin in
the adapter, requirements/<language>.txt and the extras of pyproject.toml (tests/test_parsers_on_demand.py
compares them). Yanked files are left out. This tool needs the network; no test runs it.
"""
from __future__ import annotations

import importlib
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "src" / "wingbox" / "assets" / "parsers.json"
INDEX = "https://pypi.org/pypi/{package}/{version}/json"
FILES = "https://files.pythonhosted.org/packages/"
ABOUT = (
    "The parsers `wingbox add` installs: package, pinned version, and every wheel of that version on the "
    "Python Package Index. Written by tools/update_parsers.py; not edited by hand."
)


def pinned() -> list[tuple[str, str, str]]:
    """(language, package, version) of every on-demand language, from the language adapters."""
    sys.path.insert(0, str(REPO / "scripts"))
    languages = importlib.import_module("lang").LANGUAGES
    ondemand = importlib.import_module("lang.ondemand")
    found = []
    for name in ondemand.ON_DEMAND_LANGUAGES:
        dependency = ondemand.grammar(next(language for language in languages if language.name == name))
        if dependency is None:
            raise SystemExit(f"{name}: the language adapter pins no grammar package")
        found.append((name, dependency.distribution, dependency.version))
    return found


def release(package: str, version: str) -> dict:
    with urllib.request.urlopen(INDEX.format(package=package, version=version), timeout=60) as response:
        return json.load(response)


def entry(package: str, version: str, files: list[dict]) -> dict:
    wheels = []
    for item in sorted(files, key=lambda file: file["filename"]):
        if item.get("yanked") or item["packagetype"] != "bdist_wheel":
            continue
        if not item["url"].startswith(FILES):
            raise SystemExit(f"{item['filename']}: unexpected URL {item['url']}")
        wheels.append({
            "filename": item["filename"], "url": item["url"], "sha256": item["digests"]["sha256"], "size": item["size"],
        })
    if not wheels:
        raise SystemExit(f"{package} {version}: the index lists no wheel")
    return {
        "package": package,
        "version": version,
        "module": package.replace("-", "_"),
        "sdist": any(item["packagetype"] == "sdist" and not item.get("yanked") for item in files),
        "wheels": wheels,
    }


def catalog_text() -> str:
    languages = {name: entry(package, version, release(package, version)["urls"]) for name, package, version in pinned()}
    return json.dumps({"about": ABOUT, "languages": languages}, indent=2) + "\n"


def main(argv: list[str]) -> int:
    text = catalog_text()
    if "--check" in argv:
        current = CATALOG.read_text(encoding="utf-8") if CATALOG.is_file() else ""
        if current != text:
            print(f"{CATALOG.relative_to(REPO)} differs from the package index: run python3 tools/update_parsers.py")
            return 1
        print(f"{CATALOG.relative_to(REPO)} agrees with the package index")
        return 0
    CATALOG.write_bytes(text.encode("utf-8"))
    print(f"wrote {CATALOG.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
