# Wingbox benchmark: folder names to architecture layers, and style detection

Run of 2026-11-16 on the working tree of the `architecture-init` branch. The numbers
below are copied from `results/summary.txt`, which `run.sh` writes; `run.sh`
fails when a rerun gives other results than the committed ones. No network, no
model.

## Summary

- 476 folders of 19 public repositories labelled by hand with the layer they
  play in the style of their part (or "none"). The synonym threshold was chosen
  on one half (236 folders); all comparisons are on the other half (240).
- On the held-out half, word vectors do **not** beat dictionaries and
  Levenshtein: accuracy 75.4 % against 76.2 % (2 folders fewer right); they
  match 2.5 % more folders, but wrong matches rise from 1.2 % to 2.9 %.
  Levenshtein changes nothing on this set: it holds almost no misspelt names.
- The vector step is already the last one: it looks only at the names that
  the dictionaries and Levenshtein leave unmatched. Used that way it lowers
  the unmatched share (77.5 % to 75.0 %) but also the accuracy, so word
  vectors are **off by default**; `wingbox init --synonyms` and
  `wingbox build --synonyms` turn them on for the folder -> layer step, and
  every match they make is labelled `:match synonym`.
- Style detection on 33 parts of the same repositories: 31 right with
  dictionaries (with or without Levenshtein), 30 with vectors. Detection never
  uses vectors.

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
| + word vectors (threshold 0.74, margin 0.10) | 75.4 % | 75.0 % | 2.9 % | 21.7 % |

"Missed": the folder has a role but no match. "Unmatched" includes the folders
whose right answer is none.

By style (accuracy: dictionaries / + Levenshtein / + vectors):

| Style | Folders | Accuracy |
|---|---:|---|
| feature-sliced | 7 | 100 % / 100 % / 100 % |
| hexagonal | 7 | 100 % / 100 % / 100 % |
| layered | 63 | 79 % / 79 % / 75 % |
| mvc | 54 | 83 % / 83 % / 83 % |
| nestjs | 8 | 62 % / 62 % / 75 % |
| onion-clean | 65 | 62 % / 62 % / 60 % |
| route-tree | 36 | 81 % / 81 % / 83 % |

Every synonym match on the test half (`results/summary.txt`): `schema` ->
domain (right), `shared-components` -> ui (right), `Projects` ->
infrastructure, `app-templates` -> ui, `response_model` -> ui, `sql_databases`
-> infra (all four wrong). Of the 52 folders missed by every variant, many
are technology names (`rabbitmq`, `nats`, `grpcserver`, `k8s`), patterns of a
style that its dictionary does not list (`Commands`, `Events`, `Handlers`,
`Specifications`, `serializers`, `presenters` in MVC) and project words
(`Behaviours`, `Outbox`, `llm-providers`); a general English model does not
place them near the layer words.

## Style detection (33 parts)

| Variant | Right |
|---|---:|
| dictionaries | 31 |
| + Levenshtein | 31 |
| + word vectors | 30 |

The two parts every variant misses stay undetermined: the Laravel example
(expected mvc) and the smaller Feature-Sliced example (13 source files). With vectors the immich web frontend becomes undetermined: synonyms
lift the layered score to a tie with route-tree. Per part: `results/detection.tsv`.

## Method

- Matching steps (scripts/architecture/words.py): exact normalised name, then
  Levenshtein distance 1 for names of at least 7 letters, then the nearest
  dictionary word by cosine similarity of word vectors (mean vector of the
  words of a folder name) when it reaches the threshold and leads the best
  word of another layer by the margin.
- The threshold and margin were chosen by grid search (0.40 to 0.95 by 0.01;
  margin 0 to 0.10) on the tune half, best accuracy, ties to the stricter
  setting. They were not changed after the test half was read.
- Word vectors: GloVe 6B, 100 dimensions (Pennington, Socher and Manning,
  2014; Public Domain Dedication and License), the 30,000 most frequent
  alphabetic words plus the words of the style files, quantised to signed
  bytes: `scripts/architecture/vectors/` (3 MB). `build_vectors.py` in this
  folder builds the table; it is run by the maintainer and needs the network,
  the package does not.

## Run

```bash
benchmarks/architecture/run.sh
```
