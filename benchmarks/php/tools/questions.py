"""Draw the PHP benchmark questions and write the result files.

Usage: python3 -I questions.py <work dir> sample [--synthetic]
       python3 -I questions.py <work dir> results <dir>

<work dir> is prepared by `run.sh <work dir>`: one reference file per corpus in
`out/<corpus>.php_refs.json` (written by php_reference.php), the graph edges in
`out/<corpus>.graph.json` (written by graph_edges.py), and the commands'
answers scored by `tools/verify.py` into `out/command.json` and `out/edges.json`.

The question, for a file X: every file that depends on X directly or through one
other file, where "A depends on B" means that A names B (PHP: a class reference
or an import that resolves to B's file; Blade: a view, component or Livewire
reference that resolves to B). It is the same rule as benchmarks/impact.

Sampling: per corpus, PER_REPO files drawn without replacement with a fixed
seed, from the source files that at least one other file depends on. Nothing
else is filtered. The reference lists never come from Wingbox: they come from
`tools/php_reference.php`, which shares no code with it.
"""
from __future__ import annotations

import collections
import json
import os
import random
import shutil
import sys

SEED = 20261116
PER_REPO = 20
HERE = os.path.dirname(os.path.abspath(__file__))


def reference_edges(work: str, corpus: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(required edges, accepted edges) of one corpus.

    Accepted and not required: a reference that is ambiguous between several
    files (the reference cannot name one of them, so an edge to any of the
    candidates is not wrong), and the Livewire template of a component class
    (a running application renders it, and the graph records the edge in
    addition to the class edge).
    """
    path = os.path.join(work, "out", corpus + ".php_refs.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    required = {tuple(pair) for pair in data.get("edges", [])}
    accepted: set[tuple[str, str]] = {tuple(pair) for pair in data.get("accepted_edges", [])}
    for source, result in data.get("files", {}).items():
        for reference in result.get("references", []):
            if reference.get("status") != "ambiguous":
                continue
            for candidate in reference.get("candidates", []):
                if candidate != source:
                    accepted.add((source, candidate))
    return required, required | accepted


def two_hops(edges: set[tuple[str, str]], target: str) -> tuple[set[str], set[str]]:
    """The direct dependents of target and the files that depend on them."""
    dependents: dict[str, set[str]] = collections.defaultdict(set)
    for source, destination in edges:
        if source != destination:
            dependents[destination].add(source)
    first = set(dependents.get(target, ()))
    second: set[str] = set()
    for dependent in first:
        second |= dependents.get(dependent, set())
    second -= first
    second.discard(target)
    return first, second


def sample(work: str, synthetic: bool) -> None:
    out = os.path.join(work, "out")
    corpora = sorted(
        name[: -len(".php_refs.json")]
        for name in os.listdir(out)
        if name.endswith(".php_refs.json")
    )
    if not corpora:
        raise SystemExit(f"no <corpus>.php_refs.json in {out}; run php_reference.php first")
    rng = random.Random(SEED)
    questions: list[dict] = []
    summary: list[dict] = []
    for corpus in corpora:
        with open(os.path.join(out, corpus + ".php_refs.json"), encoding="utf-8") as handle:
            reference = json.load(handle)
        required = {tuple(pair) for pair in reference.get("edges", [])}
        imported = sorted({destination for source, destination in required if source != destination})
        if not imported:
            raise SystemExit(f"{corpus}: no file is depended on by another one")
        chosen = sorted(rng.sample(imported, min(PER_REPO, len(imported))))
        sizes = []
        for number, path in enumerate(chosen, start=1):
            first, second = two_hops(required, path)
            sizes.append(len(first | second))
            questions.append({
                "id": f"{corpus}-{number:02d}",
                "corpus": corpus,
                "language": "php",
                "file": path,
                "reference_files": len(first | second),
                "direct": sorted(first),
                "through_one": sorted(second),
                "gold": sorted(first | second),
            })
        summary.append({"name": corpus, "source_files": len(reference.get("files", {})),
                        "imported_files": len(imported), "questions": len(chosen),
                        "smallest": min(sizes), "median": sorted(sizes)[len(sizes) // 2], "largest": max(sizes)})
        print(f"{corpus}: {len(imported)} depended-on files, {len(chosen)} drawn, reference sizes "
              f"{min(sizes)}..{max(sizes)} (median {sorted(sizes)[len(sizes) // 2]})")
    meta = {
        "seed": SEED,
        "per_repo": PER_REPO,
        "synthetic": synthetic,
        "corpora": summary,
        "questions": questions,
    }
    with open(os.path.join(out, "questions.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=1)
        handle.write("\n")
    print(f"written {os.path.join(out, 'questions.json')}: {len(questions)} questions in {len(corpora)} corpora")


def results(work: str, target: str) -> None:
    out = os.path.join(work, "out")
    os.makedirs(target, exist_ok=True)
    for name in ("questions.json", "command.json", "edges.json", "versions.json"):
        source = os.path.join(out, name)
        if not os.path.exists(source):
            raise SystemExit(f"missing {source}")
        shutil.copy(source, os.path.join(target, name))
    commits = os.path.join(out, "commits.txt")
    if os.path.exists(commits):
        shutil.copy(commits, os.path.join(target, "commits.txt"))
    print("written", target)


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("usage: questions.py <work dir> sample [--synthetic] | questions.py <work dir> results <dir>", file=sys.stderr)
        return 2
    work, action = os.path.abspath(arguments[0]), arguments[1]
    if action == "sample":
        sample(work, "--synthetic" in arguments[2:])
    elif action == "results":
        if len(arguments) < 3:
            print("usage: questions.py <work dir> results <dir>", file=sys.stderr)
            return 2
        results(work, os.path.abspath(arguments[2]))
    else:
        print(f"unknown action {action!r}; use sample or results", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))