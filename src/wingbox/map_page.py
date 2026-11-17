"""map.html: the interactive map of a code graph, one file that works offline.

render_map() is pure. read_assets() is the only function that touches the disk:
it loads the page template and the bundled d3 build shipped inside the package.
"""
from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass

from wingbox.overview import BLAST_RADIUS_DEPTH, LAYER_ORDER, Overview, ViolationInfo

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_MARKERS = ("__WINGBOX_TITLE__", "__WINGBOX_DATA__", "__WINGBOX_D3_LICENSE__", "__WINGBOX_D3__")


@dataclass(frozen=True)
class MapAssets:
    template: str     # assets/map_template.html
    d3: str           # assets/d3.min.js (d3 v7, ISC license)
    d3_license: str   # assets/D3-LICENSE.txt


def read_assets() -> MapAssets:
    """Load the page template and d3 from the package."""
    def read(name: str) -> str:
        with open(os.path.join(_ASSETS_DIR, name), "r", encoding="utf-8") as handle:
            return handle.read()

    return MapAssets(
        template=read("map_template.html"),
        d3=read("d3.min.js"),
        d3_license=read("D3-LICENSE.txt"),
    )


def _label_text(style: str, rule: str) -> str:
    """What set the layer of a module: "layered: folder routes/ -> api"."""
    return f"{style}: {rule}" if style not in ("-", "") else rule


def _violation_text(violation: ViolationInfo) -> str:
    known = f"; listed in {violation.known}" if violation.known else ""
    return f"{violation.style}: {violation.rule}, {violation.reason}{known}"


READABLE_RADIUS = 0.05


def start_module(view: Overview) -> int:
    """The file the map opens on: the most depended-on file whose 2-hop radius stays under 5 % of the
    modules (a radius that paints half the graph says little); else the file with the widest radius."""
    total = len(view.modules)
    candidates = [(i, m) for i, m in enumerate(view.modules) if m.within_two_hops > 0 and m.layer != "test"]
    readable = [(i, m) for i, m in candidates if m.within_two_hops < READABLE_RADIUS * total]
    if readable:
        return min(readable, key=lambda item: (-item[1].fan_in, -item[1].within_two_hops, item[0]))[0]
    if candidates:
        return min(candidates, key=lambda item: (-item[1].within_two_hops, -item[1].fan_in, item[0]))[0]
    return -1


def map_data(view: Overview, generated: str, tool_version: str) -> dict[str, object]:
    """The graph as the page reads it: modules by position, edges as index pairs."""
    position = {module.id: index for index, module in enumerate(view.modules)}
    layers = list(LAYER_ORDER) + sorted({module.layer for module in view.modules} - set(LAYER_ORDER))
    reasons = sorted({_violation_text(violation) for violation in view.violations})
    label_rules = sorted({_label_text(module.style, module.rule) for module in view.modules})
    return {
        "project": view.project,
        "generated": generated,
        "version": tool_version,
        "depth": BLAST_RADIUS_DEPTH,
        "start": start_module(view),
        "layers": layers,
        "layerCounts": [[layer, count] for layer, count in view.layers],
        "rules": [list(rule) for rule in view.boundary_rules],
        "candidates": view.violation_candidates,
        "typeOnly": len({(item.source, item.target) for item in view.type_only_crossings}),
        "placed": [view.placed_by_path, view.placed_by_role, view.unplaced],
        "ruleReasons": reasons,
        "labelRules": label_rules,
        "parts": [[part.path, part.style, part.state, part.origin, part.level] for part in view.parts],
        "advice": [[item.kind, item.target, item.reason, item.style, item.rule, item.evidence] for item in view.advice],
        "modules": [
            {
                "file": module.file,
                "lang": module.lang,
                "layer": layers.index(module.layer),
                "in": module.fan_in,
                "out": module.fan_out,
                "reach": module.within_two_hops,
                "rule": label_rules.index(_label_text(module.style, module.rule)),
            }
            for module in view.modules
        ],
        "edges": [[position[edge.source], position[edge.target]] for edge in view.edges],
        "violations": [
            [position[violation.source], position[violation.target], reasons.index(_violation_text(violation))]
            for violation in view.violations
        ],
    }


def _json_for_script(value: object) -> str:
    """JSON that cannot close its <script> element or open a comment."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )


def _fill(template: str, values: dict[str, str]) -> str:
    """Replace each marker once, without rescanning the inserted text."""
    spans = sorted((template.index(marker), marker) for marker in _MARKERS)
    parts: list[str] = []
    cursor = 0
    for start, marker in spans:
        parts.append(template[cursor:start])
        parts.append(values[marker])
        cursor = start + len(marker)
    parts.append(template[cursor:])
    return "".join(parts)


def render_map(view: Overview, generated: str, tool_version: str, assets: MapAssets) -> str:
    """The whole page: styles, data, d3 and the map code in one HTML file."""
    return _fill(
        assets.template,
        {
            "__WINGBOX_TITLE__": html.escape(view.project),
            "__WINGBOX_DATA__": _json_for_script(map_data(view, generated, tool_version)),
            "__WINGBOX_D3_LICENSE__": "d3 (https://d3js.org) is embedded below under the ISC license:\n\n"
            + assets.d3_license.strip().replace("--", "- -"),
            "__WINGBOX_D3__": assets.d3.replace("</script", "<\\/script"),
        },
    )
