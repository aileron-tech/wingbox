"""Every module dependency of the Wingbox graph against the import statements of the code.

Usage: python3 -I edge_test.py <work dir> <out.json>
Reference per corpus: Python's `ast` through tools/py_edges.py (equal to grimp on FastAPI), the own
TypeScript resolver tools/ts_resolve.py, the own Rust resolver tools/rust_resolve.py. An edge is wrong
when the reference neither requires nor accepts it; a required edge the graph lacks is missing. What is
accepted and not required is defined in impact100.reference().
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from impact100 import reference  # noqa: E402

work, out_path = os.path.abspath(sys.argv[1]), sys.argv[2]
CORPORA = [
    ("immich-server", "immich, server", "typescript", ".ts,.tsx,.js,.mjs,.cjs,.jsx,.mts,.cts", ""),
    ("chrome-devtools-mcp", "chrome-devtools-mcp", "typescript", ".ts,.tsx,.js,.mjs,.cjs,.jsx,.mts,.cts", ""),
    ("agent-browser", "agent-browser, Rust sources", "rust", ".rs", ""),
    ("claudes-c-compiler", "claudes-c-compiler", "rust", ".rs", ""),
    ("fastapi", "FastAPI, whole tree", "python", ".py", ""),
    ("fastapi", "FastAPI, the fastapi/ package", "python", ".py", "fastapi/"),
    ("karpathy", "nanoGPT, minGPT, micrograd", "python", ".py", ""),
]
SOURCE = {"typescript": (".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".mts", ".cts"), "rust": (".rs",), "python": (".py",)}
rows = []
for repo, label, language, extensions, scope in CORPORA:
    edges_path = os.path.join(work, "out", f"{repo}.wb.edges.json")
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "tools", "wb_edges.py"), os.path.join(work, "bench", "wb", repo),
                    edges_path, "--ext", extensions], check=True, capture_output=True)
    graph = json.load(open(edges_path))["edges"]
    inside = lambda pair: all(path.startswith(scope) and path.endswith(SOURCE[language]) for path in pair)
    tool = {tuple(pair) for pair in graph.get("imports", []) if inside(tuple(pair))}
    required, accepted = reference(work, repo, language)
    required = {pair for pair in required if pair[0] != pair[1] and inside(pair)}
    accepted = {pair for pair in accepted if inside(pair)} | required
    row = {"corpus": label, "language": {"typescript": "TypeScript", "rust": "Rust", "python": "Python"}[language],
           "pairs": len(required), "edges": len(tool), "wrong": len(tool - accepted), "missing": len(required - tool),
           "accepted_extra": len((tool & accepted) - required),
           "wrong_edges": sorted(tool - accepted)[:20], "missing_edges": sorted(required - tool)[:20]}
    rows.append(row)
    print(f"{label}: {row['pairs']} dependencies in the code, graph {row['edges']}, wrong {row['wrong']}, missing {row['missing']}")
json.dump(rows, open(out_path, "w"), indent=1)
