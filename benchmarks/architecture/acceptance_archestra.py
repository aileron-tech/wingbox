"""Acceptance check of the layer labels on archestra (github.com/archestra-ai/archestra, commit 7765354).

    python3 -I benchmarks/architecture/acceptance_archestra.py <checkout built with wingbox build>

Wingbox 0.2.5 labelled these files wrongly; the check fails when any comes back:
the frontend has no declared rules, so its labels come from style detection
(route-tree); the backend declares its layers in platform/backend/layers.mjs,
whose own upward-import baseline (layers.known-upward-imports.json, 112 pairs)
must equal the backend's rule breaks found here, pair for pair. Prints the
numbers and exits 1 on a failed check.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "scripts"))

import code_graph_search as search  # noqa: E402

NOT_INFRA = (
    "platform/frontend/src/app/mcp/gateways/gateway-tools",
    "platform/frontend/src/app/mcp/gateways/gateway-tools.query",
    "platform/frontend/src/app/connection/clients",
)
BACKEND_HELPERS = (
    "platform/backend/src/clients/llm-client",
    "platform/backend/src/k8s/cluster-ip-families",
    "platform/backend/src/git-hosts/git-access",
    "platform/backend/src/observability/tracing/attributes",
    "platform/backend/src/agents/a2a-executor",
)


def main() -> int:
    root = os.path.realpath(sys.argv[1])
    graph = search.CodeGraph()
    loaded = graph.load(os.path.join(root, ".wingbox", "index.sexp"), root)
    if isinstance(loaded, search.MapLoadErr):
        print(loaded.error)
        return 1
    failures: list[str] = []

    def check(ok: bool, text: str) -> None:
        print(("ok    " if ok else "FAIL  ") + text)
        if not ok:
            failures.append(text)

    for module in NOT_INFRA:
        label = search.module_label(graph, module)
        check(label.style == "route-tree" and label.layer != "infra", f"{module}: {label.layer} ({label.style}: {label.rule})")
    shared = search.module_label(graph, "platform/shared")
    check(shared.layer == "other", f"platform/shared/index.ts: {shared.layer} ({shared.style}: {shared.rule})")
    openappa = sorted(m for m in graph.nodes if m.startswith("platform/backend/src/openappa/"))
    for module in BACKEND_HELPERS + tuple(openappa[:3]):
        label = search.module_label(graph, module)
        check(label.style == "declared" and label.layer != "api", f"{module}: {label.layer} ({label.rule})")
    check(all(search.module_label(graph, m).style == "declared" for m in openappa),
          f"all {len(openappa)} files of src/openappa take their layer from layers.mjs")

    with open(os.path.join(root, "platform/backend/layers.known-upward-imports.json"), encoding="utf-8") as handle:
        theirs = {tuple(re.match(r"(\S+) -> (\S+)", item).groups()) for item in json.load(handle)}
    strip = lambda path: re.sub(r"(/index)?\.(ts|tsx|js|mjs)$", "", path)  # noqa: E731
    theirs = {(strip(a), strip(b)) for a, b in theirs}
    ours = {
        (item.source[len("platform/backend/"):], item.target[len("platform/backend/"):])
        for item in search.boundary_violations(graph, set(graph.nodes))
        if item.part == "platform/backend" and item.severity != "candidate"
    }
    check(ours == theirs, f"backend upward imports: {len(ours)} found, {len(theirs)} in the project's baseline, "
                          f"{len(ours - theirs)} extra, {len(theirs - ours)} missing")
    placed = [m for m in graph.nodes if m.startswith("platform/backend/")
              and search.module_label(graph, m).style == "declared" and search.module_label(graph, m).layer not in ("other", "test")]
    print(f"info  backend modules in a declared layer: {len(placed)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
