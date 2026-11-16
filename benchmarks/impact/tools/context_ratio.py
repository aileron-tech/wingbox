"""How much smaller a Wingbox impact answer is than the source it covers.

The same kind of number as `graphify benchmark` ("Nx fewer tokens per query"):
estimated tokens of the repository's source divided by the average size of the
answer. Here the answers are the 100 impact answers of results/command.json,
each one checked against the import statements (results/gold), so the ratio is
reported next to how many answers were complete.

Corpus: source files in the languages of the benchmark (.ts .tsx .js .jsx .mjs
.cjs .py .rs), without node_modules, build output, third_party/vendor folders,
bundles and minified files (average line longer than 200 characters).
Tokens: characters / 4, the estimate Graphify uses for answer size.

usage: python3 context_ratio.py <folder holding the five checkouts> [graphify.json]
graphify.json (optional): {"<repo>": {"corpus_tokens": N, "avg_query_tokens": M}}
from `graphify benchmark` on the same checkouts, to compare on Graphify's own
corpus estimate.
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
ROOTS = {
    "chrome-devtools-mcp": "chrome-devtools-mcp",
    "agent-browser": "agent-browser",
    "claudes-c-compiler": "claudes-c-compiler",
    "fastapi": "fastapi",
    "immich-server": "immich/server",
}
EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs")
SKIP = {".git", "node_modules", "dist", "build", ".venv", "venv", "target",
        "__pycache__", "coverage", "third_party", "vendor"}


def corpus_tokens(base: str) -> tuple[int, int]:
    chars = files = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for name in filenames:
            if not name.endswith(EXT) or ".min." in name or "bundle" in name:
                continue
            with open(os.path.join(dirpath, name), errors="ignore") as fh:
                text = fh.read()
            if len(text) / (text.count("\n") + 1) > 200:
                continue
            chars += len(text)
            files += 1
    return round(chars / 4), files


def main() -> None:
    checkouts = sys.argv[1]
    graphify = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {}
    answers = json.load(open(os.path.join(RESULTS, "command.json")))
    rows = []
    for repo, root in ROOTS.items():
        mine = [a for a in answers if a["repo"] == repo]
        sizes = [a["wingbox"]["tokens"] for a in mine]
        corpus, files = corpus_tokens(os.path.join(checkouts, root))
        mean = statistics.mean(sizes)
        row = {
            "repo": repo,
            "source_files": files,
            "corpus_tokens": corpus,
            "questions": len(mine),
            "complete_answers": sum(a["wingbox"]["exact"] for a in mine),
            "avg_answer_tokens": round(mean),
            "ratio": round(corpus / mean),
        }
        g = graphify.get(repo)
        if g:
            row["graphify"] = {
                "corpus_tokens": g["corpus_tokens"],
                "avg_query_tokens": g["avg_query_tokens"],
                "ratio": round(g["corpus_tokens"] / g["avg_query_tokens"], 1),
                "wingbox_ratio_on_graphify_corpus": round(g["corpus_tokens"] / mean),
            }
        rows.append(row)
    out = {"method": __doc__.strip().split("\n\n")[1], "repos": rows}
    with open(os.path.join(RESULTS, "context_ratio.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    for r in rows:
        print(r["repo"], r["ratio"], "x;", r["complete_answers"], "of", r["questions"], "complete;", r.get("graphify", ""))


if __name__ == "__main__":
    main()
