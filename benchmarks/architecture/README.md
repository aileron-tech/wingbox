# Wingbox benchmark: folder names to architecture layers, and style detection

Run of 2026-11-16 on the working tree of the `architecture-init` branch. The numbers
below are copied from `results/summary.txt`, which `run.sh` writes; `run.sh`
fails when a rerun gives other results than the committed ones. No network, no
model.

## Summary

- 476 folders of 19 public repositories labelled by hand with the layer they
  play in the style of their part (or "none"). All comparisons are on the
  held-out half (240 folders).
- Levenshtein changes nothing on this set: it holds almost no misspelt names.
- History: a third step, a built-in table of word vectors for the names the
  first two leave unmatched, was measured on the same held-out half; it did
  not beat the dictionaries (accuracy 75.4 % against 76.2 %) and was removed
  in 0.11.0, with the `--synonyms` option that turned it on.
- Style detection on 33 parts of the same repositories: 31 right with
  dictionaries (with or without Levenshtein).

## Data

- `labels.tsv`: repository, part, style of the part, folder name, role, half.
  The folders are the most frequent folder names of each judged part of 19
  repositories (the path lists in `trees/`, from the GitHub tree API), read by
  hand and given the layer of the part's style they play, or `none` when the
  name states no role of that style (a feature, a URL segment, a language
  folder such as `src` or `java`). For the backend of archestra the layer
  assignment of the project's own `layers.mjs` was the reference, mapped onto
  the layered style (its `schema` and `models` layers to domain, its `core`
  folders to infra or shared, its `routes-*` layers to api).
- The half of each row is fixed by the SHA-1 of "repository|part|folder"
  (even: tune, odd: test).
- `expected_styles.tsv`: the style each part is expected to have, from reading
  its tree (none for a library, a component library, a script collection or a
  package-by-feature application).

## Results on the test half (240 folders)

| Variant | Accuracy | Unmatched | Wrong match | Missed |
|---|---:|---:|---:|---:|
| dictionaries | 76.2 % | 77.5 % | 1.2 % | 22.5 % |
| + Levenshtein | 76.2 % | 77.5 % | 1.2 % | 22.5 % |

"Missed": the folder has a role but no match. "Unmatched" includes the folders
whose right answer is none.

By style (accuracy: dictionaries / + Levenshtein):

| Style | Folders | Accuracy |
|---|---:|---|
| feature-sliced | 7 | 100 % / 100 % |
| hexagonal | 7 | 100 % / 100 % |
| layered | 63 | 79 % / 79 % |
| mvc | 54 | 83 % / 83 % |
| nestjs | 8 | 62 % / 62 % |
| onion-clean | 65 | 62 % / 62 % |
| route-tree | 36 | 81 % / 81 % |

Of the 54 folders missed by both variants, many are technology names
(`rabbitmq`, `nats`, `grpcserver`, `k8s`), patterns of a style that its
dictionary does not list (`Commands`, `Events`, `Handlers`, `Specifications`,
`serializers`, `presenters` in MVC) and project words (`Behaviours`, `Outbox`,
`llm-providers`).

## Style detection (33 parts)

| Variant | Right |
|---|---:|
| dictionaries | 31 |
| + Levenshtein | 31 |

The two parts every variant misses stay undetermined: the Laravel example
(expected mvc) and the smaller Feature-Sliced example (13 source files). Per part: `results/detection.tsv`.

## Method

- Matching steps (scripts/architecture/words.py): exact normalised name, then
  Levenshtein distance 1 for names of at least 7 letters.

## Run

```bash
benchmarks/architecture/run.sh
```
