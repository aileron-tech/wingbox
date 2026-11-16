"""File-to-file edges of a Graphify graph.json (NetworkX node-link format).

Usage: python3 -I gf_edges.py <graph.json> <repo root> <out.json> --mode modules|imports|all --ext .py

Pure data transformation; no repository code is executed.
  1. Every Graphify node carries `source_file` (path relative to the scan root); a node is
     lifted to that file. Nodes with an empty source_file are external stubs or concepts.
  2. Every link is directed source -> target (Graphify ARCHITECTURE.md, "Edge direction in
     graph.json"). A link flagged `reverse_import` also stands for the opposite direction,
     so the reverse pair is added as well.
  3. --mode modules keeps the module-level import relations: imports_from, re_exports,
     dynamic_import (imports_from + re_exports is what Graphify's own
     analyze.find_import_cycles uses for its file-level import graph).
     --mode imports adds the symbol-level relation `imports` (file -> imported symbol),
     lifted to the file that defines the symbol.
     --mode all keeps every relation whose two endpoints lie in different files.
  4. A file pair (A, B) is emitted when A != B and both files have one of the extensions of
     --ext and are not .d.ts. Dropped pairs are counted in `graphify_meta`.
"""
from __future__ import annotations

import argparse
import collections
import json
import os

IMPORT_FAMILY = ("imports_from", "imports", "re_exports", "dynamic_import")
MODULE_LEVEL = ("imports_from", "re_exports", "dynamic_import")


def bfs_rev(start, rev, depth=2):
    seen = {start}
    frontier = {start}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            for p in rev.get(n, ()):
                if p not in seen:
                    seen.add(p)
                    nxt.add(p)
        frontier = nxt
    seen.discard(start)
    return seen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("graph")
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--mode", choices=("modules", "imports", "all"), default="imports")
    ap.add_argument("--ext", required=True, help="comma-separated extensions of the language under test")
    a = ap.parse_args()
    exts = tuple(x for x in a.ext.split(",") if x)

    g = json.load(open(a.graph))
    nodes = {n["id"]: n for n in g["nodes"]}
    links = g.get("links") or g.get("edges") or []

    def in_lang(f: str) -> bool:
        return bool(f) and f.endswith(exts) and not f.endswith(".d.ts")

    files = sorted({n.get("source_file") for n in nodes.values() if n.get("source_file")})
    lang_files = [f for f in files if in_lang(f)]

    rel_total = collections.Counter(e.get("relation") for e in links)
    cross_by_rel = collections.Counter()      # links whose endpoints lie in two different files (any type)
    lang_by_rel = collections.Counter()       # ... both of the language under test
    dropped = collections.Counter()
    pairs: dict[tuple[str, str], dict] = {}
    other_targets = collections.Counter()     # import-family links to a non-language file (e.g. package.json)
    other_target_examples = collections.defaultdict(list)

    def add(sf, tf, e, s, t, reverse=False):
        rec = pairs.setdefault((sf, tf), {"relations": collections.Counter(), "evidence": []})
        rec["relations"][e.get("relation")] += 1
        if len(rec["evidence"]) < 4:
            rec["evidence"].append({
                "relation": e.get("relation"), "confidence": e.get("confidence"),
                "source_file": e.get("source_file"), "source_location": e.get("source_location"),
                "source_label": s.get("label"), "target_label": t.get("label"),
                "type_only": bool(e.get("type_only")), "deferred": bool(e.get("deferred")),
                "reverse_import_stamp": reverse,
            })

    for e in links:
        rel = e.get("relation")
        s, t = nodes.get(e["source"]), nodes.get(e["target"])
        if s is None or t is None:
            dropped["unknown-endpoint"] += 1
            continue
        sf, tf = s.get("source_file") or "", t.get("source_file") or ""
        wanted = rel in MODULE_LEVEL if a.mode == "modules" else (rel in IMPORT_FAMILY if a.mode == "imports" else True)
        if not sf or not tf:
            if wanted:
                dropped["endpoint-without-file (external stub or concept)"] += 1
            continue
        if sf == tf:
            if wanted:
                dropped["same-file"] += 1
            continue
        cross_by_rel[rel] += 1
        if not (in_lang(sf) and in_lang(tf)):
            if wanted:
                dropped["cross-file, endpoint not a source file of the language under test"] += 1
                if rel in IMPORT_FAMILY and in_lang(sf):
                    key = os.path.splitext(tf)[1] or os.path.basename(tf)
                    if tf.endswith(".d.ts"):
                        key = ".d.ts"
                    other_targets[key] += 1
                    if len(other_target_examples[key]) < 5:
                        other_target_examples[key].append({"from": sf, "to": tf, "relation": rel, "line": e.get("source_location"), "target_label": t.get("label")})
            continue
        lang_by_rel[rel] += 1
        if not wanted:
            continue
        add(sf, tf, e, s, t)
        if e.get("reverse_import"):
            add(tf, sf, e, t, s, reverse=True)

    def kind_of(rec) -> str:
        for k in IMPORT_FAMILY:
            if rec["relations"].get(k):
                return k
        return rec["relations"].most_common(1)[0][0]

    edges = [{"from": a_, "to": b_, "kind": kind_of(r), "relations": dict(r["relations"]), "evidence": r["evidence"]}
             for (a_, b_), r in sorted(pairs.items())]
    rev = collections.defaultdict(set)
    fwd = collections.defaultdict(set)
    for (a_, b_) in pairs:
        rev[b_].add(a_)
        fwd[a_].add(b_)
    modules = [{
        "id": os.path.splitext(f)[0], "file": f, "lang": os.path.splitext(f)[1].lstrip("."), "layer": "n/a",
        "direct_dependents": len(rev.get(f, ())), "depends_on": len(fwd.get(f, ())),
        "within_two_hops": len(bfs_rev(f, rev)),
    } for f in lang_files]

    out = {
        "project": os.path.basename(os.path.normpath(a.root)),
        "root": os.path.basename(os.path.normpath(a.root)),
        "modules": modules,
        "edges": edges,
        "violations": [],
        "graphify_meta": {
            "graphify_version": (g.get("graph") or {}).get("graphify_version"),
            "built_at_commit": g.get("built_at_commit"),
            "mode": a.mode, "extensions": list(exts),
            "nodes": len(nodes), "links": len(links),
            "files_with_nodes_all_types": len(files),
            "files_with_nodes_language_under_test": len(lang_files),
            "relations_total": dict(rel_total.most_common()),
            "links_crossing_files_by_relation": dict(cross_by_rel.most_common()),
            "links_crossing_language_files_by_relation": dict(lang_by_rel.most_common()),
            "dropped": dict(dropped),
            "import_links_to_other_file_types": dict(other_targets.most_common()),
            "import_links_to_other_file_types_examples": other_target_examples,
            "file_pairs_emitted": len(edges),
            "file_pairs_by_kind": dict(collections.Counter(e["kind"] for e in edges).most_common()),
        },
    }
    json.dump(out, open(a.out, "w"), indent=1)
    m = out["graphify_meta"]
    print(out["project"], a.mode, "modules", len(modules), "file pairs", len(edges), m["file_pairs_by_kind"])
    print(" dropped", m["dropped"])
    print(" import links to other file types", m["import_links_to_other_file_types"])


if __name__ == "__main__":
    main()
