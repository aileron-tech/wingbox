"""Scaffold-augmented ToCS run: a model explores with the Wingbox map as its starting belief.

Usage (from the ToCS checkout, with its dependencies installed; a real model needs its API key):
  python3 -I scaffold_eval.py --tocs <ToCS checkout> --codebase <codebase without ground_truth.json>
      --truth <ground_truth.json> --wingbox-map <CognitiveMap JSON from tocs_score.py>
      --model <LiteLLM model name | scripted> --budget 20 --probe-interval 3 --out <dir>

The ToCS harness is used unchanged: Environment (partial observability, the budget), the model adapter's
exploration loop and probes, and score_map / score_exploration of metrics/map_accuracy.py. The only addition
is the first user message (ToCS's own `initial_message` argument): the Wingbox edges, typed, and what Wingbox
cannot see. Two final maps are scored: the model's last probe, and its union with the Wingbox map (a model
that forgets an edge Wingbox gave it is not counted as having lost it). `--model scripted` runs a fixed
action script with no model and no network, to check the plumbing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
for name in ("--tocs", "--codebase", "--truth", "--wingbox-map", "--model", "--out"):
    parser.add_argument(name, required=True)
parser.add_argument("--budget", type=int, default=20)
parser.add_argument("--probe-interval", type=int, default=3)
args = parser.parse_args()
sys.path.insert(0, os.path.abspath(args.tocs))
from evaluation.model_adapters.base import BaseAdapter  # noqa: E402
from harness.environment import Environment  # noqa: E402
from metrics.map_accuracy import score_exploration, score_map  # noqa: E402
from models import CodebaseGroundTruth, CognitiveMap  # noqa: E402

STARTING_BELIEF = """A static analyzer (Wingbox) has read every file of this codebase, without a model. Its edges are
certain for what it reports, as source -> target (filepaths as in your map):

{edges}

What it cannot see, and where your actions are worth spending:
- CALLS_API between files that also IMPORT each other: Wingbox reports the import, not whether the source
  uses the target's public API (a call of a helper, a model constructor or an exception is not CALLS_API).
- Anything that is not in the code: the purpose of each module, invariants and design rationales.
Start your cognitive map from these edges and keep them unless a file you open contradicts one."""


def edge_lines(cmap: CognitiveMap) -> str:
    rows = sorted((edge.type.value, path, edge.target) for path, comp in cmap.components.items() for edge in comp.edges)
    return "\n".join(f"{edge_type} {source} -> {target}" for edge_type, source, target in rows)


def union(model_map: CognitiveMap, wingbox_map: CognitiveMap) -> CognitiveMap:
    merged = model_map.model_copy(deep=True)
    for path, comp in wingbox_map.components.items():
        target = merged.components.setdefault(path, comp.model_copy(deep=True))
        known = {(edge.target, edge.type) for edge in target.edges}
        target.edges += [edge for edge in comp.edges if (edge.target, edge.type) not in known]
    return merged


class ScriptedAdapter(BaseAdapter):
    """No model: opens the runner, then answers each probe with the Wingbox map. For plumbing checks only."""

    def __init__(self, wingbox_map: CognitiveMap) -> None:
        super().__init__(model="scripted")
        self._map_json = wingbox_map.model_dump_json()
        self._actions = iter(["LIST()", "OPEN(" + next(p for p in wingbox_map.components if p.endswith("runner.py")) + ")", "LIST()"])

    def _call_model(self, messages: list[dict], system: str) -> str:
        if "externalize your current architectural belief" in messages[-1]["content"]:
            return self._map_json
        return next(self._actions, "DONE()")


with open(args.truth, encoding="utf-8") as handle:
    gt = CodebaseGroundTruth.model_validate_json(handle.read())
with open(args.wingbox_map, encoding="utf-8") as handle:
    wingbox_map = CognitiveMap.model_validate_json(handle.read())
if args.model == "scripted":
    adapter: BaseAdapter = ScriptedAdapter(wingbox_map)
else:
    from evaluation.model_adapters.litellm_adapter import LiteLLMAdapter

    adapter = LiteLLMAdapter(model=args.model, temperature=1.0 if "gpt-5" in args.model else 0.0)
env = Environment(args.codebase, budget=args.budget)
result = adapter.run_exploration(
    env, probe_interval=args.probe_interval, initial_message=STARTING_BELIEF.format(edges=edge_lines(wingbox_map))
)
maps = [wingbox_map.model_copy(update={"step": 0})] + result.maps
final = maps[-1]
report = {
    "model": args.model, "codebase": gt.codebase_id, "budget": args.budget, "actions": env.actions_taken,
    "probes": len(result.maps), "opened": env.opened_files,
    "wingbox_only": score_map(wingbox_map, gt).model_dump(),
    "model_final": score_map(final, gt).model_dump(),
    "union_final": score_map(union(final, wingbox_map), gt).model_dump(),
    "exploration": score_exploration(maps, result.action_log, gt).model_dump(),
    "token_usage": result.token_usage,
}
os.makedirs(args.out, exist_ok=True)
stem = os.path.join(args.out, f"{args.model.replace('/', '_')}_{gt.codebase_id}_b{args.budget}")
with open(stem + ".json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=1)
with open(stem + "_conversation.json", "w", encoding="utf-8") as handle:
    json.dump(result.conversation, handle, indent=1)
for key in ("wingbox_only", "model_final", "union_final"):
    print(f"{key:13} Dep F1 {report[key]['dependency_f1']:.3f}  P {report[key]['dependency_precision']:.3f}  "
          f"R {report[key]['dependency_recall']:.3f}  Inv F1 {report[key]['invariant_f1_relaxed']:.3f}")
