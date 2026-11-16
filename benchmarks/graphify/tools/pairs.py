"""File pairs of one tool, in the shape score_edges.py reads: {"pairs": [[from, to], ...]}.

Usage: python3 -I pairs.py wingbox  <edges.json from wb_edges.py> <out.json>
       python3 -I pairs.py graphify <graph.json from gf_edges.py> <out.json>
Wingbox: every dependency edge between two Python files (imports, calls-api, data-flow).
Graphify: every file pair gf_edges.py emitted for the chosen mode.
"""
from __future__ import annotations

import json
import sys

tool, source, out_path = sys.argv[1:4]
data = json.load(open(source))
if tool == "wingbox":
    is_python = lambda pair: pair[0].endswith(".py") and pair[1].endswith(".py")
    pairs = sorted({tuple(pair) for kind in ("imports", "calls-api", "data-flow")
                    for pair in data["edges"].get(kind, []) if is_python(pair)})
elif tool == "graphify":
    pairs = sorted({(edge["from"], edge["to"]) for edge in data["edges"]})
else:
    raise SystemExit("first argument: wingbox or graphify")
json.dump({"pairs": pairs}, open(out_path, "w"))
print(tool, "pairs", len(pairs))
