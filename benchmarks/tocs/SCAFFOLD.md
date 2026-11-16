# Plan: static analysis plus a model on ToCS (scaffold-augmented evaluation)

Not run yet: it needs paid model calls. `scaffold_eval.py` is written and its plumbing is checked with
`--model scripted` (no model, no network); every number in this folder so far comes from Wingbox alone.

## Question

Does a model that starts from the Wingbox map, and spends its 20 actions on what Wingbox cannot see, build
a better map than the same model exploring alone (paper: Dep F1 0.676 for GPT-5.3-Codex, 0.664 for Claude
Sonnet 4.6 on the medium codebases) and better than Wingbox alone (0.953)?

## What each side contributes

- Wingbox, read from every file: IMPORTS, REGISTRY_WIRES, DATA_FLOWS_TO and the CALLS_API that go through
  the registry, with no false edge on the medium codebases (README.md of this folder).
- Left to the model: CALLS_API between files that also import each other (20 of the 39 on the medium
  codebases). Whether a call is a call of the target's API or of a helper is a judgement of role, which the
  ground truth makes and a syntax rule does not (README.md, "What Wingbox still gets wrong"). Invariants and
  design rationales (Inv F1) are not in the Wingbox map at all.

## Design

The ToCS harness runs unchanged: `Environment` (partial observability, budget 20), the model adapter's loop
and probes every 3 actions, `score_map` and `score_exploration`. One thing is added, through the harness's own
`initial_message` argument of `run_exploration`: the first user message lists the Wingbox edges, typed, and
says what Wingbox cannot see. The codebase tree the agent explores holds no Wingbox output.

Scored per run (`scaffold_eval.py` writes one JSON per model, codebase and budget):

- `wingbox_only`: the Wingbox map alone (budget 0);
- `model_final`: the model's last probe;
- `union_final`: that probe joined with the Wingbox map, so that an edge the model drops is not lost;
- `exploration`: ToCS's action AUC, with the Wingbox map as the map at step 0.

Conditions: budgets 20 (as in the paper), 10 and 5; models `openai/gpt-5.3-codex` and
`anthropic/claude-sonnet-4-6` (the paper's two best); the three medium codebases of the paper, then the three
large ones. One run per cell, as in the paper; a second run on the medium codebases if the differences are
smaller than the paper's half-ranges.

Expected outcome to test, not assumed: the model raises CALLS_API recall by opening the orchestrators and
adapters, at some cost in precision; Inv F1 should be compared with the paper's (0.739 and 0.778).

## Commands

```sh
# 1. Wingbox maps and the ToCS checkout (no model):
benchmarks/tocs/run.sh /tmp/wingbox-tocs
W=/tmp/wingbox-tocs
# 2. The harness dependencies of ToCS (litellm and the model SDKs):
uv pip install --python $W/venv_tocs/bin/python -e $W/tocs
# 3. Plumbing check, no model:
cd $W/tocs && $W/venv_tocs/bin/python -I <wingbox>/benchmarks/tocs/scaffold_eval.py --tocs . \
  --codebase ../codebases/codebase_42 --truth ../truth/codebase_42.json \
  --wingbox-map ../out/results/maps/after/codebase_42.json --model scripted --out ../scaffold
# 4. The runs (paid):
for model in openai/gpt-5.3-codex anthropic/claude-sonnet-4-6; do
  for cb in codebase_42 codebase_123 codebase_999; do
    for budget in 20 10 5; do
      $W/venv_tocs/bin/python -I <wingbox>/benchmarks/tocs/scaffold_eval.py --tocs . \
        --codebase ../codebases/$cb --truth ../truth/$cb.json \
        --wingbox-map ../out/results/maps/after/$cb.json \
        --model $model --budget $budget --probe-interval 3 --out ../scaffold
    done
  done
done
```

`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are read by LiteLLM from the environment. The paper's runs without
the scaffold are the comparison; to rerun them on the same day and models:
`python -m evaluation.run_eval evaluate --model <model> --codebase <codebase with ground_truth.json> --mode active --budget 20 --probe-interval 3 --output ../baseline`.
