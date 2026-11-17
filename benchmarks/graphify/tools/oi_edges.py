"""OntoIndex's file-to-file edges of one graph, in the shape score_edges.py reads: {"pairs": [[from, to], ...]}.

Usage: python3 -I oi_edges.py <graph dir> <out prefix> [ontoindex executable]
Writes <out prefix>.imports.pairs.json: every IMPORTS edge between two File nodes (how OntoIndex records
an import statement), and <out prefix>.all.pairs.json: those plus every edge of the relation types its
impact command follows by default (CALLS, IMPORTS, EXTENDS, IMPLEMENTS, METHOD_OVERRIDES, OVERRIDES,
METHOD_IMPLEMENTS) between symbols of two different files, lifted to the pair of files.
Only pairs between two Python files are kept, as pairs.py does for Wingbox.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile

graph_dir, prefix = sys.argv[1:3]
ONTOINDEX = sys.argv[3] if len(sys.argv) > 3 else "ontoindex"
PAGE = 5000  # `ontoindex cypher` refuses a query without LIMIT <= 5000
IMPACT_RELATIONS = ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS", "METHOD_OVERRIDES", "OVERRIDES", "METHOD_IMPLEMENTS"]


def capture(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a command with its standard output going to a file: on a pipe, `ontoindex` 3.1.9 stops its
    output at 65,536 bytes (Node exits before the pipe is drained), so a pipe would cut long answers."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        done = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.PIPE, text=True, timeout=900)
        out.seek(0)
        return subprocess.CompletedProcess(command, done.returncode, out.read(), done.stderr)


def cypher_rows(text: str) -> list[dict]:
    """Rows of `ontoindex cypher`: it prints JSON whose "markdown" field is a Markdown table."""
    data = json.loads(text[text.index("{"):])
    lines = [line for line in data["markdown"].split("\n") if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    return [dict(zip(header, (cell.strip() for cell in line.strip("|").split("|")))) for line in lines[2:]]


def rows(match: str) -> list[dict]:
    out: list[dict] = []
    skip = 0
    while True:
        query = f"{match} RETURN DISTINCT a.filePath AS source, b.filePath AS target ORDER BY source, target SKIP {skip} LIMIT {PAGE}"
        done = capture([ONTOINDEX, "cypher", query], graph_dir)
        if done.returncode:
            raise SystemExit(f"cypher failed: {done.stderr[-800:]}")
        page = cypher_rows(done.stdout)
        out += page
        if len(page) < PAGE:
            return out
        skip += PAGE


def python_pairs(found: list[dict]) -> list[list[str]]:
    pairs = {(str(r["source"]), str(r["target"])) for r in found if r["source"] and r["target"]}
    return sorted([a, b] for a, b in pairs if a != b and a.endswith(".py") and b.endswith(".py"))


imports = python_pairs(rows("MATCH (a:File)-[r:CodeRelation]->(b:File) WHERE r.type = 'IMPORTS'"))
relations = "[" + ", ".join(f"'{name}'" for name in IMPACT_RELATIONS) + "]"
lifted = python_pairs(rows(f"MATCH (a)-[r:CodeRelation]->(b) WHERE r.type IN {relations} AND a.filePath <> b.filePath"))
all_pairs = sorted({tuple(p) for p in imports} | {tuple(p) for p in lifted})
json.dump({"pairs": imports}, open(prefix + ".imports.pairs.json", "w"))
json.dump({"pairs": [list(p) for p in all_pairs]}, open(prefix + ".all.pairs.json", "w"))
print("ontoindex pairs: imports", len(imports), "all", len(all_pairs))
