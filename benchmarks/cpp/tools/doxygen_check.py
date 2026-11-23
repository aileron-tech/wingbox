"""The include edges of a C or C++ tree as Doxygen resolves them, against another edge list.

Usage: python3 -I doxygen_check.py <root> <edges.json> <out.json> <work dir> <include root> [...]

Doxygen (GENERATE_XML) reads every file of the tree with its own preprocessor and writes, per file, the
includes it resolved to a file of the tree. Those edges are compared with the edges of <edges.json>
(the reference of cpp_reference.py, or the graph edges written by wb_edges.py): an edge Doxygen has and
the list lacks is counted as missing in the list. Doxygen evaluates `#if`, so it may have fewer edges
than a reader of every branch; an edge only the list has is counted, not judged.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

EXTENSIONS = ("c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx", "inl", "ipp")


def main() -> None:
    root, edges_path, out_path, work = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3], os.path.abspath(sys.argv[4])
    roots = sys.argv[5:]
    os.makedirs(work, exist_ok=True)
    config = "\n".join([
        f'INPUT = "{root}"', "RECURSIVE = YES", "FILE_PATTERNS = " + " ".join(f"*.{e}" for e in EXTENSIONS),
        "EXTENSION_MAPPING = inl=C++ ipp=C++", "EXTRACT_ALL = YES", "EXTRACT_STATIC = YES", "EXTRACT_PRIVATE = YES",
        "GENERATE_HTML = NO", "GENERATE_LATEX = NO", "GENERATE_XML = YES", f'XML_OUTPUT = "{work}/xml"', "XML_PROGRAMLISTING = NO",
        "ENABLE_PREPROCESSING = YES", "SEARCH_INCLUDES = YES", "MACRO_EXPANSION = NO",
        "INCLUDE_PATH = " + " ".join(f'"{os.path.join(root, r)}"' for r in roots),
        "STRIP_FROM_PATH = ", "FULL_PATH_NAMES = YES", "QUIET = YES", "WARNINGS = NO", "WARN_IF_UNDOCUMENTED = NO",
        "HAVE_DOT = NO", "REFERENCED_BY_RELATION = YES", "REFERENCES_RELATION = YES", "CASE_SENSE_NAMES = YES", "EXCLUDE_PATTERNS = */.git/* */.wingbox/*", "",
    ])
    with open(os.path.join(work, "Doxyfile"), "w", encoding="utf-8") as handle:
        handle.write(config)
    subprocess.run(["doxygen", os.path.join(work, "Doxyfile")], cwd=work, check=True, capture_output=True)
    version = subprocess.run(["doxygen", "--version"], capture_output=True, text=True).stdout.strip()
    files: dict[str, str] = {}      # refid -> path relative to the root
    includes: list[tuple[str, str]] = []   # (refid of the including file, refid of the included one)
    index = ET.parse(os.path.join(work, "xml", "index.xml")).getroot()
    for compound in index.findall("compound"):
        if compound.get("kind") != "file":
            continue
        tree = ET.parse(os.path.join(work, "xml", compound.get("refid") + ".xml")).getroot()
        definition = tree.find("compounddef")
        location = definition.find("location")
        if location is None:
            continue
        path = os.path.relpath(os.path.realpath(location.get("file")), os.path.realpath(root)).replace(os.sep, "/")
        files[definition.get("id")] = path
        for item in definition.findall("includes"):
            if item.get("refid"):
                includes.append((definition.get("id"), item.get("refid")))
    found = {(files[a], files[b]) for a, b in includes if a in files and b in files and files[a] != files[b]}
    listed_raw = json.load(open(edges_path, encoding="utf-8"))["edges"]
    listed_raw = listed_raw.get("imports", []) if isinstance(listed_raw, dict) else listed_raw
    known = set(files.values())
    listed = {tuple(edge) for edge in listed_raw if edge[0] in known and edge[1] in known}
    # An edge only Doxygen has: is the include that names the file written in angle brackets? Doxygen takes
    # the one file of the tree with that name for it (`<errno.h>` for `include/uv/errno.h`); a compiler
    # looks in the include folders and finds the system header.
    by_form = {"angle": 0, "quoted": 0, "other": 0}
    not_angle: list[tuple[str, str]] = []
    for source, target in sorted(found - listed):
        with open(os.path.join(root, source), encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        name = re.escape(os.path.basename(target))
        quoted = re.search(r'#[ \t]*include[ \t]*"[^"\n]*' + name + '"', text)
        angle = re.search(r'#[ \t]*include[ \t]*<[^>\n]*' + name + '>', text)
        form = "quoted" if quoted else "angle" if angle else "other"
        by_form[form] += 1
        if form != "angle":
            not_angle.append((source, target))
    result = {"doxygen": version, "files": len(files), "edges_of_doxygen": len(found), "edges_of_the_list": len(listed),
              "in_both": len(found & listed), "only_doxygen_count": len(found - listed), "only_doxygen_by_form": by_form,
              "only_doxygen_not_written_in_angle_brackets": not_angle, "only_the_list_count": len(listed - found), "only_the_list": sorted(listed - found)[:30]}
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1)
    print({key: value for key, value in result.items() if not isinstance(value, list)})


if __name__ == "__main__":
    main()
