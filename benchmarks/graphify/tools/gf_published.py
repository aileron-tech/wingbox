"""Graphify's token-reduction test on a graph file Graphify publishes, with Graphify's own code.

Usage: <graphify venv>/bin/python -I gf_published.py <graph.json> <corpus words> <out.json> <question> [<question> ...]
"""
import json
import sys

from graphify.benchmark import run_benchmark

graph_path, words, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
questions = sys.argv[4:]
graph = json.load(open(graph_path))
result = run_benchmark(graph_path, corpus_words=words, questions=questions)
result["graph_file"] = {"nodes": len(graph["nodes"]), "links": len(graph.get("links") or graph.get("edges") or [])}
result["questions_asked"] = len(questions)
json.dump(result, open(out_path, "w"), indent=1)
print(json.dumps(result, indent=1))
