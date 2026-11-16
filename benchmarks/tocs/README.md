# Wingbox on Theory of Code Space (ToCS)

[Theory of Code Space](https://arxiv.org/abs/2603.00601) (ToCS) generates Python codebases with a known architecture and scores a map of their module dependencies against the ground truth, by edge type. This file compares Wingbox 0.2.4 (release) with Wingbox 0.2.4 (unreleased changes) on ToCS commit `e617928`. It is written by `make_report.py` from the files in `results/`; `run.sh` reproduces everything from nothing.

## What is compared

ToCS agents explore a codebase with a budget of 20 actions (open a file, list a folder, search, inspect a symbol) and write down what they believe; Wingbox reads every file, with no model and no budget. Its condition is closest to the paper's passive-full condition, where a model is given every file at once, not to the active condition of the headline table. The numbers below place Wingbox next to the paper's numbers; they do not rank it against the models.

Scoring is ToCS's own: `edge_prf` of `metrics/map_accuracy.py`, precision, recall and F1 over (source, target, type) triples of the final map. Wingbox facts become ToCS types by meaning:

| Wingbox fact | ToCS type | Meaning |
|---|---|---|
| `imports` | IMPORTS | an import statement, or a literal module name given to `importlib` |
| `registry-wires` | REGISTRY_WIRES | a module loaded by a name read from a JSON, YAML or TOML file beside the loader |
| `calls` | CALLS_API | a function or method of the target called by the source |
| `data-flow :pipeline` | DATA_FLOWS_TO | the target step of a configured pipeline consumes the output of the source step |
| `calls-api`, `data-flow :validated`, `refs` | none | implements a base class; passes data to a validation module; uses a name |

## Summary

Dep F1, mean over the codebases of a group (+/- half the range), and the edges of each type found (true of gold, pooled):

| Codebases | Wingbox 0.2.4 (release) | Wingbox 0.2.4 (unreleased changes) | P | R | IMPORTS | CALLS_API | REGISTRY_WIRES | DATA_FLOWS_TO |
|---|---|---|---|---|---|---|---|---|
| Shipped medium (the paper's 3) | 0.802 (+/-0.002) | 0.953 (+/-0.003) | 1.000 (+/-0.000) | 0.911 (+/-0.005) | 150 of 150 | 19 of 39 | 19 of 19 | 16 of 16 |
| Shipped large | 0.766 (+/-0.003) | 0.869 (+/-0.002) | 0.938 (+/-0.001) | 0.809 (+/-0.003) | 291 of 291 | 24 of 109 | 24 of 24 | 21 of 21 |
| Generated (8) | 0.790 (+/-0.019) | 0.921 (+/-0.045) | 0.978 (+/-0.031) | 0.872 (+/-0.056) | 592 of 592 | 60 of 187 | 60 of 60 | 52 of 52 |

The same three medium codebases in the paper (budget 20, active exploration), with Wingbox's row for comparison:

| Method | Condition | Dep F1 | P | R | Recall IMPORTS | CALLS_API | REGISTRY_WIRES | DATA_FLOWS_TO |
|---|---|---|---|---|---|---|---|---|
| Wingbox 0.2.4 (unreleased changes) | reads every file, no model | 0.953 | 1.000 | 0.911 | 1.00 | 0.49 | 1.00 | 1.00 |
| GPT-5.3-Codex | 20 actions | 0.676 | 0.782 | 0.597 | 0.69 | 0.15 | 1.00 | 0.31 |
| Claude Sonnet 4.6 | 20 actions | 0.664 | 0.983 | 0.502 | 0.56 | 0.15 | 1.00 | 0.00 |
| Config-Aware (rule baseline) | 20 actions | 0.577 | 0.736 | 0.475 | 0.58 | 0.00 | 1.00 | 0.00 |

Paper, every file given at once (passive-full): GPT-5.3-Codex 0.457 on medium; Gemini 2.5 Flash 0.696 on medium, 0.616 on large.

## Every codebase

| Codebase | Tier | Modules | Dep F1 0.2.4 (release) | Dep F1 0.2.4 (unreleased changes) | P | R |
|---|---|---|---|---|---|---|
| codebase_42 | medium | 27 | 0.803 | 0.955 | 1.000 | 0.914 |
| codebase_123 | medium | 30 | 0.800 | 0.950 | 1.000 | 0.905 |
| codebase_999 | medium | 27 | 0.803 | 0.955 | 1.000 | 0.914 |
| large_42 | large | 46 | 0.763 | 0.868 | 0.936 | 0.808 |
| large_123 | large | 47 | 0.766 | 0.867 | 0.938 | 0.807 |
| large_999 | large | 47 | 0.769 | 0.871 | 0.938 | 0.812 |
| gen_large_1 | large | 50 | 0.777 | 0.872 | 0.942 | 0.811 |
| gen_large_2 | large | 48 | 0.772 | 0.872 | 0.939 | 0.815 |
| gen_large_3 | large | 49 | 0.771 | 0.869 | 0.940 | 0.808 |
| gen_medium_1 | medium | 30 | 0.792 | 0.958 | 1.000 | 0.919 |
| gen_medium_2 | medium | 29 | 0.809 | 0.946 | 1.000 | 0.897 |
| gen_medium_3 | medium | 31 | 0.797 | 0.955 | 1.000 | 0.913 |
| gen_medium_4 | medium | 29 | 0.797 | 0.954 | 1.000 | 0.912 |
| gen_medium_5 | medium | 31 | 0.803 | 0.946 | 1.000 | 0.898 |

Per edge type, pooled over all codebases (true / predicted / gold):

| Type | 0.2.4 (release) | 0.2.4 (unreleased changes) |
|---|---|---|
| IMPORTS | 1033 / 1081 / 1033 | 1033 / 1081 / 1033 |
| CALLS_API | 0 / 0 / 335 | 103 / 103 / 335 |
| REGISTRY_WIRES | 0 / 0 / 103 | 103 / 103 / 103 |
| DATA_FLOWS_TO | 0 / 0 / 89 | 89 / 89 / 89 |

## What Wingbox still gets wrong

**IMPORTS**: 0 missed, 48 not in the ground truth. Examples from `large_42`:

- not in the ground truth: `text_processor/stages/mod_a.py` -> `text_processor/validators/mod_o.py`
- not in the ground truth: `text_processor/stages/mod_b.py` -> `text_processor/validators/mod_o.py`
- not in the ground truth: `text_processor/stages/mod_c.py` -> `text_processor/validators/mod_p.py`
- not in the ground truth: `text_processor/stages/mod_d.py` -> `text_processor/validators/mod_p.py`

**CALLS_API**: 232 missed, 0 not in the ground truth. Examples from `large_42`:

- missed: `text_processor/adapters/mod_i.py` -> `text_processor/stages/mod_a.py`
- missed: `text_processor/adapters/mod_j.py` -> `text_processor/stages/mod_c.py`
- missed: `text_processor/adapters/mod_k.py` -> `text_processor/stages/mod_f.py`
- missed: `text_processor/cli.py` -> `text_processor/pipeline_factory.py`

Checked against the code with Python's `ast`, independently of Wingbox (`code_facts` in `tocs_score.py`), over all codebases:

- The 48 IMPORTS outside the ground truth are import statements of the code: the code holds 48 import pairs that the ground truth does not list, all in large codebases, where a stage imports the validator it calls and the ground truth records only CALLS_API for the pair.
- The CALLS_API that Wingbox finds are the calls of the runner into the stages, made through the registry, which no import shows. Of the 232 it misses, 232 join files where the source imports the target; 134 of them call an imported name inside a function, the others import a hook or a middleware that they never call or only place in a list. The ground truth does not count every call as CALLS_API: of the 407 pairs where a function calls an imported name, 273 are not CALLS_API (calls to helpers, model constructors, exceptions, configuration). Mapping every direct call to CALLS_API would lower precision more than it raises recall, so Wingbox does not guess this type; telling an API call from a helper call is left to a model.

Each case is listed in `results/scores.after.json` (`missed`, `extra`); the maps sent to the metric are in `results/maps/`.

## Reproduce

```sh
benchmarks/tocs/run.sh /tmp/wingbox-tocs
```

Needs git and uv; no model and no key. It installs both Wingbox versions, fetches ToCS at the pinned commit, copies the six shipped codebases, generates the others with ToCS's generator (`python -m generator --pattern pipeline --complexity <tier> --seed <n>`), moves every `ground_truth.json` out of the tree Wingbox reads, builds the graphs, scores the maps and compares the result files with the committed ones.
