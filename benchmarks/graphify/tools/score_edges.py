"""Score a tool's file-to-file edges against the reference from py_edges.py.

Usage: python3 -I score_edges.py <label> <reference.json> <tool pairs json> <out.json> [--scope prefix/]
<tool pairs json> = {"pairs": [[from, to], ...]}.
strict: an edge is right when the code has an import from A that names module B, or when B is a
        package __init__.py that Python executes for such an import (implied; accepted, not required).
either: also right when B is the file that defines a name A imports (re-export followed).
Recall is always over the modules the import statements name.
"""
from __future__ import annotations

import json
import sys

label, ref_path, tool_path, out_path = sys.argv[1:5]
scope = sys.argv[sys.argv.index("--scope") + 1] if "--scope" in sys.argv else ""
ref = json.load(open(ref_path))
tool = {tuple(p) for p in json.load(open(tool_path))["pairs"]}
inside = lambda pair: pair[0].startswith(scope) and pair[1].startswith(scope)
strict = {tuple(p) for p in ref["pairs"] if inside(p)}
followed = {tuple(p) for p in ref["followed_pairs"] if inside(p)}
implied = {tuple(p) for p in ref.get("implied_pairs", []) if inside(p)}
tool = {p for p in tool if inside(p)}
by_pair: dict[tuple[str, str], set[tuple[str, str]]] = {}
for r in ref["refs"]:
    if r["frm"] != r["to"]:
        by_pair.setdefault((r["frm"], r["to"]), set()).add((r["frm"], r["to_followed"]))
covered = lambda pair: pair in tool or any(alt in tool for alt in by_pair.get(pair, ()) if alt[0] != alt[1])
hit = tool & strict
right_strict = tool & (strict | implied)
either_hit = tool & (strict | implied | followed)
result = {
    "label": label, "scope": scope or "(whole tree)", "reference_pairs": len(strict), "tool_edges": len(tool),
    "strict": {"right": len(right_strict), "of_them_implied_package_init": len(right_strict - strict),
               "precision": round(len(right_strict) / len(tool), 4) if tool else None,
               "recall": round(len(hit) / len(strict), 4) if strict else None},
    "either": {"right": len(either_hit), "precision": round(len(either_hit) / len(tool), 4) if tool else None,
               "found": sum(covered(p) for p in strict),
               "recall": round(sum(covered(p) for p in strict) / len(strict), 4) if strict else None},
    "tool_edges_not_in_code": sorted(tool - strict - implied - followed),
    "tool_edges_wrong_strict": sorted(tool - strict - implied),
    "code_pairs_missing_strict": sorted(strict - tool),
    "code_pairs_missing_either": sorted(p for p in strict if not covered(p)),
}
json.dump(result, open(out_path, "w"), indent=1)
print(f"{label:34s} ref {len(strict):5d} tool {len(tool):5d} | strict P {result['strict']['precision']} R {result['strict']['recall']}"
      f" | either P {result['either']['precision']} R {result['either']['recall']}"
      f" | wrong {len(result['tool_edges_not_in_code'])} missing {len(result['code_pairs_missing_either'])}")
