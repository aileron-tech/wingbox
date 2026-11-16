"""Graphify's own token-reduction test (graphify/benchmark.py), applied to Graphify and to Wingbox.

Usage: <graphify venv>/bin/python -I formula_test.py <graphify corpus copy> <wingbox corpus copy> <wingbox binary> <out.json> [--ext .py]

Formula (Graphify's, unchanged):
  corpus tokens = words of the corpus * 100 // 75        (every file in the context, "naive")
  answer tokens = len(answer text) // 4
  reduction     = corpus tokens / mean answer tokens over the questions that got an answer
Graphify answers come from graphify.benchmark.run_benchmark (its own code).
Wingbox answers are the text its commands print; the absolute root of the checkout is printed
as /repo, the path a repository has in the documented Docker run.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from graphify.benchmark import _SAMPLE_QUESTIONS, run_benchmark
from graphify.detect import count_words
from graphify.serve import _query_terms

gf_root, wb_root, wingbox, out_path = sys.argv[1:5]
exts = tuple((sys.argv[sys.argv.index("--ext") + 1] if "--ext" in sys.argv else ".py").split(","))
PUBLISHED = [
    "what connects micrograd to nanoGPT",
    "how does FlashAttention improve memory efficiency",
    "what are the core abstractions",
    "how are errors handled",
    "how does the attention mechanism work",
]

code_files = sorted(str(p.relative_to(gf_root)) for p in Path(gf_root).rglob("*")
                    if p.is_file() and p.suffix in exts and "graphify-out" not in p.parts and ".git" not in p.parts)
words = sum(count_words(Path(gf_root) / f) for f in code_files)
corpus_tokens = words * 100 // 75
tokens = lambda text: max(1, len(text) // 4)


def wb(*args: str) -> str:
    done = subprocess.run([wingbox, *args], cwd=wb_root, capture_output=True, text=True, timeout=300)
    return done.stdout.replace(os.path.realpath(wb_root), "/repo").replace(os.path.abspath(wb_root), "/repo")


def stats(values: list[int]) -> dict:
    return {"queries": len(values), "mean_tokens": round(statistics.mean(values)), "median_tokens": round(statistics.median(values)),
            "max_tokens": max(values), "reduction_by_mean": round(corpus_tokens / statistics.mean(values), 1),
            "reduction_by_max": round(corpus_tokens / max(values), 1)}


result: dict = {"corpus": {"code_files": len(code_files), "words": words, "tokens": corpus_tokens}}
graph = os.path.join(gf_root, "graphify-out", "graph.json")
result["graphify"] = {
    "default_questions": run_benchmark(graph, corpus_words=words, questions=list(_SAMPLE_QUESTIONS)),
    "published_questions": run_benchmark(graph, corpus_words=words, questions=PUBLISHED),
}

modules = [os.path.splitext(f)[0] for f in code_files]
with ThreadPoolExecutor(max_workers=12) as pool:
    analyze = dict(zip(modules, pool.map(lambda m: wb("analyze", m), modules)))
    impact = dict(zip(code_files, pool.map(lambda f: wb("impact", f, "2", "--sexp"), code_files)))
empty = [m for m, t in analyze.items() if "(analyze " not in t]
result["wingbox"] = {
    "analyze_every_module": stats([tokens(t) for t in analyze.values()]),
    "impact_every_file": stats([tokens(t) for t in impact.values()]),
    "whole_graph_file_tokens": tokens(open(os.path.join(wb_root, ".wingbox", "index.sexp"), encoding="utf-8").read()),
    "modules_without_analyze_answer": empty,
}
for name, questions in (("default_questions", list(_SAMPLE_QUESTIONS)), ("published_questions", PUBLISHED)):
    rows = []
    for question in questions:
        parts = []
        for term in _query_terms(question):
            text = wb("quick", term)
            if text.strip() and not text.lstrip().startswith(("No ", "Error", "(no-match")) and "(analyze " in text:
                parts.append({"term": term, "tokens": tokens(text)})
        if parts:
            rows.append({"question": question, "terms": parts, "answer_tokens": sum(p["tokens"] for p in parts)})
    block = {"answered": len(rows), "of": len(questions), "per_question": rows}
    if rows:
        mean = statistics.mean(r["answer_tokens"] for r in rows)
        block.update({"mean_tokens": round(mean), "reduction": round(corpus_tokens / mean, 1)})
    result["wingbox"][name] = block
json.dump(result, open(out_path, "w"), indent=1)
json.dump({"analyze": {m: tokens(t) for m, t in analyze.items()}, "impact": {f: tokens(t) for f, t in impact.items()}},
          open(out_path.replace(".json", ".per_query.json"), "w"), indent=0)
print(json.dumps(result, indent=1))
