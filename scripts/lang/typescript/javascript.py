"""Which JavaScript files are source: written code, not minified or bundled output."""

from __future__ import annotations

import sys

JAVASCRIPT_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
# Written code has no line this long; minified code and inlined bundles do.
_MINIFIED_LINE_LENGTH = 5000


def is_javascript_file(file_name: str) -> bool:
    return file_name.endswith(JAVASCRIPT_EXTENSIONS)


def is_bundled_javascript_name(file_name: str) -> bool:
    """Recognize minified files and bundles by name (x.min.js, x-bundle.js)."""
    stem = file_name.rsplit(".", 1)[0].lower()
    return (
        stem.endswith(".min")
        or stem == "bundle"
        or stem.endswith((".bundle", "-bundle", "_bundle"))
    )


def is_minified_javascript(path: str) -> bool:
    """Recognize minified JavaScript by content: it has a very long line."""
    if not is_javascript_file(path):
        return False
    try:
        with open(path, "rb") as source_file:
            return any(len(line) > _MINIFIED_LINE_LENGTH for line in source_file)
    except OSError as error:
        print(
            f"[code-graph] WARNING: could not inspect {path}: {error}",
            file=sys.stderr,
        )
        return False
