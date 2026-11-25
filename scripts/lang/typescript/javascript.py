"""Which JavaScript files are source: written code, not minified or bundled output."""

from __future__ import annotations

import os
import sys

JAVASCRIPT_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
# Written code has no line this long; minified code and inlined bundles do.
_MINIFIED_LINE_LENGTH = 5000
# The files this process found minified by their content since the last notice (minified_notice).
_LEFT_OUT: dict[str, None] = {}
_NAMED_IN_NOTICE = 20


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
            minified = any(len(line) > _MINIFIED_LINE_LENGTH for line in source_file)
    except OSError as error:
        print(
            f"[code-graph] WARNING: could not inspect {path}: {error}",
            file=sys.stderr,
        )
        return False
    if minified:
        _LEFT_OUT[path] = None
    return minified


def minified_notice(root: str) -> str:
    """What a build says about the JavaScript files left out as minified by their content since the last
    call: how many, the rule, and their paths below root (the first twenty by path); "" when there is
    none. A written file with one very long line (a data URI) is left out too, and nothing in the graph
    says so: the notice is where its name appears. The list starts again with every call."""
    paths = sorted(os.path.relpath(path, root).replace(os.sep, "/") for path in _LEFT_OUT)
    _LEFT_OUT.clear()
    if not paths:
        return ""
    named = ", ".join(paths[:_NAMED_IN_NOTICE]) + (f" and {len(paths) - _NAMED_IN_NOTICE} more" if len(paths) > _NAMED_IN_NOTICE else "")
    files = "1 JavaScript file" if len(paths) == 1 else f"{len(paths)} JavaScript files"
    return (f"Left out as minified ({files} with a line over {_MINIFIED_LINE_LENGTH:,} characters; "
            f"what they import and who imports them is not in the graph): {named}")
