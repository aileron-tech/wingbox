"""Evaluate how folder names are mapped to the layers of a style, and how styles are detected.

    python3 -I benchmarks/architecture/evaluate.py [--trees benchmarks/architecture/trees]

1. Role mapping on labels.tsv: three variants on the same rows -
   dictionaries (exact normalised names), + Levenshtein (spelling), + word vectors
   (synonyms). The synonym threshold and margin are chosen on the `tune` half
   only (grid search, best accuracy, ties to the stricter setting); every number
   reported for comparison is from the `test` half.
2. Style detection on the path lists in trees/ (one gzip file per repository,
   one path per line, from the GitHub tree API) against expected_styles.tsv,
   for the three variants.

Writes results/roles.tsv, results/summary.txt, results/detection.tsv and
prints the summary. Deterministic: no randomness, no network.
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
from collections import Counter, defaultdict
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "scripts"))

from architecture import words  # noqa: E402
from architecture.detect import judge, layer_dictionary, split_parts  # noqa: E402
from architecture.library import load_library  # noqa: E402

VARIANTS = {
    "dictionaries": words.MatchSettings(spelling=False, synonyms=False),
    "+levenshtein": words.MatchSettings(spelling=True, synonyms=False),
    "+vectors": words.MatchSettings(spelling=True, synonyms=True),
}


def read_labels(path: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] = []
    with open(path, encoding="ascii") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            values = line.rstrip("\n").split("\t")
            if not header:
                header = values
                continue
            rows.append(dict(zip(header, values)))
    return rows


def predict(row: dict[str, str], settings: words.MatchSettings) -> tuple[str, str]:
    library = load_library()
    style = library.style(row["style"])
    assert style is not None, row
    match = words.match_name(row["folder"], layer_dictionary(style, synonyms=settings.synonyms), settings)
    return (match.role, match.step) if match else ("none", "none")


def score(rows: list[dict[str, str]], settings: words.MatchSettings) -> dict[str, float]:
    correct = unmatched = wrong = missed = 0
    for row in rows:
        role, _ = predict(row, settings)
        if role == row["role"]:
            correct += 1
        elif role == "none":
            missed += 1
        else:
            wrong += 1
        unmatched += role == "none"
    total = len(rows) or 1
    return {
        "rows": len(rows), "accuracy": correct / total, "unmatched": unmatched / total,
        "wrong": wrong / total, "missed": missed / total,
    }


def tune(rows: list[dict[str, str]]) -> words.MatchSettings:
    """Threshold and margin with the best accuracy on the tune half; ties go to the stricter setting."""
    best: tuple[float, float, float] | None = None
    for threshold in [round(0.40 + 0.01 * i, 2) for i in range(56)]:
        for margin in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10):
            settings = words.MatchSettings(threshold=threshold, margin=margin)
            accuracy = score(rows, settings)["accuracy"]
            key = (accuracy, threshold, margin)
            if best is None or key > best:
                best = key
    assert best is not None
    return words.MatchSettings(threshold=best[1], margin=best[2])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trees", help="folder of repository path lists for the detection check")
    args = parser.parse_args()
    rows = read_labels(os.path.join(HERE, "labels.tsv"))
    tune_rows = [row for row in rows if row["half"] == "tune"]
    test_rows = [row for row in rows if row["half"] == "test"]
    chosen = tune(tune_rows)
    variants = dict(VARIANTS)
    variants["+vectors"] = chosen
    results = os.path.join(HERE, "results")
    os.makedirs(results, exist_ok=True)
    lines = [
        f"labelled folders: {len(rows)} ({len(tune_rows)} tune, {len(test_rows)} test)",
        f"chosen on the tune half: threshold {chosen.threshold:.2f}, margin {chosen.margin:.2f}",
        f"shipped in scripts/architecture/words.py: threshold {words.SYNONYM_THRESHOLD:.2f}, margin {words.SYNONYM_MARGIN:.2f}",
        "",
        "test half                accuracy  unmatched  wrong  missed",
    ]
    for name, settings in variants.items():
        result = score(test_rows, settings)
        lines.append(
            f"  {name:<22} {result['accuracy']:8.1%} {result['unmatched']:10.1%} {result['wrong']:6.1%} {result['missed']:7.1%}"
        )
    lines += ["", "test half by style (dictionaries / +levenshtein / +vectors accuracy)"]
    by_style: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in test_rows:
        by_style[row["style"]].append(row)
    for style, style_rows in sorted(by_style.items()):
        values = " / ".join(f"{score(style_rows, settings)['accuracy']:.0%}" for settings in variants.values())
        lines.append(f"  {style:<16} {len(style_rows):4} rows  {values}")
    with open(os.path.join(results, "roles.tsv"), "w", encoding="ascii") as handle:
        handle.write("repository\tpart\tstyle\tfolder\trole\thalf\t" + "\t".join(variants) + "\tstep\n")
        for row in rows:
            predictions = [predict(row, settings) for settings in variants.values()]
            handle.write(
                "\t".join(row[key] for key in ("repository", "part", "style", "folder", "role", "half"))
                + "\t" + "\t".join(role for role, _ in predictions) + f"\t{predictions[-1][1]}\n"
            )
    lines += ["", "wrong matches of +vectors on the test half (folder: predicted, expected, step)"]
    for row in test_rows:
        role, step = predict(row, chosen)
        if role != row["role"] and role != "none":
            lines.append(f"  {row['repository']} {row['folder']}: {role}, expected {row['role']} ({step})")
    lines += ["", "synonym matches of +vectors on the test half (folder -> role via word, right or wrong)"]
    for row in test_rows:
        role, step = predict(row, chosen)
        if step == "synonym":
            mark = "right" if role == row["role"] else "WRONG"
            lines.append(f"  {row['folder']} -> {role} ({mark}, expected {row['role']})")
    if args.trees:
        lines += ["", *detection(args.trees, variants)]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(results, "summary.txt"), "w", encoding="ascii") as handle:
        handle.write(text)
    print(text, end="")
    return 0


def detection(trees: str, variants: dict[str, words.MatchSettings]) -> list[str]:
    library = load_library()
    expected = read_labels(os.path.join(HERE, "expected_styles.tsv"))
    by_repository: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in expected:
        by_repository[row["repository"]].append(row)
    lines = ["style detection (expected style per part; undetermined counts as right when expected is none)"]
    totals: Counter[str] = Counter()
    rows_out: list[str] = []
    for repository, parts in sorted(by_repository.items()):
        path = os.path.join(trees, repository.replace("/", "__") + ".txt.gz")
        if not os.path.exists(path):
            lines.append(f"  {repository}: path list missing, skipped")
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            paths = tuple(handle.read().splitlines())
        found = {part.path: part for part in split_parts(paths, library)}
        for row in parts:
            part = found.get(row["part"])
            results = []
            for name, settings in variants.items():
                if part is None:
                    got = "missing"
                else:
                    judgement = judge(part, library, settings)
                    got = judgement.best.style if judgement.best else ("none" if judgement.state != "undetermined" else "undetermined")
                right = got == row["style"] or (row["style"] == "none" and got in ("none", "undetermined"))
                totals[name] += right
                results.append(got)
            rows_out.append("\t".join([repository, row["part"], row["style"], *results]))
    lines.append(f"  parts: {len(rows_out)}")
    for name in variants:
        lines.append(f"  {name:<22} {totals[name]} / {len(rows_out)} right")
    with open(os.path.join(HERE, "results", "detection.tsv"), "w", encoding="ascii") as handle:
        handle.write("repository\tpart\texpected\t" + "\t".join(variants) + "\n")
        handle.write("\n".join(rows_out) + "\n")
    lines.append("  per part: results/detection.tsv")
    return lines


if __name__ == "__main__":
    sys.exit(main())
