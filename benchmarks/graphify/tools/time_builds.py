"""Time the graph build of Wingbox and Graphify on the benchmark corpus.

Usage: python3 -I time_builds.py <work dir> [rounds]
<work dir> is the directory prepared by run.sh (RUN_ONLY=corpus is enough): it holds the two
environments and the corpus at the pinned commits. Every run starts from a fresh export of the
checkout, so neither tool finds an earlier graph or a cache. The tools alternate inside a round.
Graphify runs with GRAPHIFY_BACKEND=none (code only, no model). Recorded per step: wall seconds
and CPU seconds (user plus system time of the child processes).

Wingbox: `wingbox build .` writes the graph, the report and the map.
Graphify: `graphify extract . --code-only` writes the graph; `graphify cluster-only . --no-label
--no-viz` is the step the benchmark runs before `graphify affected`. Both are timed separately.

After each build the size of the graph is read from the file the tool wrote (Wingbox: modules
and import edges of .wingbox/index.sexp; Graphify: nodes and links of graphify-out/graph.json), so a
run that built less than the reference run cannot pass as a fast one.

Writes <work dir>/out/build_times.json and prints a Markdown table.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time

CORPORA = {
    "karpathy": ["karpathy/nanoGPT", "karpathy/minGPT", "karpathy/micrograd"],
    "chrome-devtools-mcp": ["chrome-devtools-mcp"],
    "agent-browser": ["agent-browser"],
    "claudes-c-compiler": ["claudes-c-compiler"],
    "fastapi": ["fastapi"],
}
STEPS = {
    "wingbox": [("build", ["build", "."])],
    "graphify": [("extract", ["extract", ".", "--code-only"]), ("cluster", ["cluster-only", ".", "--no-label", "--no-viz"])],
}
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs")


def graph_size(tool: str, target: str) -> dict:
    """What the build wrote: the size of the graph, read from the graph file itself."""
    if tool == "wingbox":
        counts = {"modules": 0, "import_edges": 0}
        with open(os.path.join(target, ".wingbox", "index.sexp"), encoding="utf-8") as handle:
            for line in handle:
                text = line.lstrip()
                if text.startswith("(node "):
                    counts["modules"] += 1
                elif text.startswith("(imports "):
                    counts["import_edges"] += 1
        return counts
    graph = json.load(open(os.path.join(target, "graphify-out", "graph.json"), encoding="utf-8"))
    return {"nodes": len(graph["nodes"]), "links": len(graph["links"])}


def summarise(runs: list[dict]) -> list[dict]:
    """One row per corpus: the median wall and CPU seconds of each step over the rounds."""
    rows = []
    for corpus in dict.fromkeys(run["corpus"] for run in runs):
        row: dict = {"corpus": corpus}
        for run in runs:
            if run["corpus"] == corpus:
                row.setdefault("source_files", run["source_files"])
                if "graph" in run:
                    sizes = row.setdefault(f"{run['tool']}_graph", run["graph"])
                    if sizes != run["graph"]:
                        raise SystemExit(f"{run['tool']} built a different graph of {corpus} in round {run['round']}")
        for tool, steps in STEPS.items():
            for step, _ in steps:
                mine = [run for run in runs if run["corpus"] == corpus and run["tool"] == tool and run["step"] == step]
                if mine:
                    row[f"{tool}_{step}_wall_s"] = round(statistics.median(run["wall_s"] for run in mine), 2)
                    row[f"{tool}_{step}_cpu_s"] = round(statistics.median(run["cpu_s"] for run in mine), 2)
        if "wingbox_build_wall_s" in row and "graphify_extract_wall_s" in row:
            row["extract_to_build_wall_ratio"] = round(row["graphify_extract_wall_s"] / row["wingbox_build_wall_s"], 1)
        rows.append(row)
    return rows


def table(rows: list[dict]) -> str:
    lines = [
        "| Corpus | Source files | Wingbox build, s | Graphify extract, s | Graphify cluster, s | Extract / build |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['corpus']} | {row['source_files']:,} | {row['wingbox_build_wall_s']:.1f} | "
            f"{row['graphify_extract_wall_s']:.1f} | {row['graphify_cluster_wall_s']:.1f} | {row['extract_to_build_wall_ratio']:.1f}x |"
        )
    return "\n".join(lines)


def machine() -> dict:
    model = platform.processor() or platform.machine()
    if os.path.exists("/proc/cpuinfo"):
        for line in open("/proc/cpuinfo", encoding="utf-8", errors="replace"):
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return {"system": platform.system(), "release": platform.release(), "cpu": model, "logical_cpus": os.cpu_count()}


def export(work: str, parts: list[str], target: str) -> int:
    """A clean copy of the corpus: the tracked files of each checkout, nothing else."""
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    for part in parts:
        prefix = f"{os.path.basename(part)}/" if len(parts) > 1 else ""
        archive = subprocess.run(
            ["git", "-C", os.path.join(work, "corpus", part), "archive", "--format=tar", *([f"--prefix={prefix}"] if prefix else []), "HEAD"],
            check=True, capture_output=True,
        ).stdout
        subprocess.run(["tar", "-x", "-C", target], input=archive, check=True)
    return sum(name.endswith(SOURCE_SUFFIXES) for _, _, names in os.walk(target) for name in names)


def timed(command: list[str], cwd: str, env: dict) -> tuple[float, float, int]:
    import resource  # POSIX only: CPU time of the child processes; imported where it is measured
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    code = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    wall = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return wall, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime), code


def main() -> None:
    work = os.path.abspath(sys.argv[1])
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    tools = {"wingbox": os.path.join(work, "venv_wb", "bin", "wingbox"), "graphify": os.path.join(work, "venv_gf", "bin", "graphify")}
    env = dict(os.environ, GRAPHIFY_BACKEND="none")
    runs: list[dict] = []
    for round_no in range(1, rounds + 1):
        for corpus, parts in CORPORA.items():
            for tool, steps in STEPS.items():
                target = os.path.join(work, "timing", corpus)
                files = export(work, parts, target)
                for step, arguments in steps:
                    wall, cpu, code = timed([tools[tool], *arguments], target, env)
                    if code:
                        raise SystemExit(f"{tool} {step} failed on {corpus} with exit code {code}")
                    run = {"round": round_no, "corpus": corpus, "source_files": files, "tool": tool, "step": step,
                           "wall_s": round(wall, 2), "cpu_s": round(cpu, 2)}
                    if step in ("build", "extract"):
                        run["graph"] = graph_size(tool, target)
                    runs.append(run)
                    print(f"round {round_no} {corpus} {tool} {step}: {wall:.1f} s wall, {cpu:.1f} s cpu", file=sys.stderr, flush=True)
    shutil.rmtree(os.path.join(work, "timing"), ignore_errors=True)
    rows = summarise(runs)
    versions = {tool: subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip() for tool, exe in tools.items()}
    result = {"machine": machine(), "versions": versions, "rounds": rounds, "summary": rows, "runs": runs}
    os.makedirs(os.path.join(work, "out"), exist_ok=True)
    json.dump(result, open(os.path.join(work, "out", "build_times.json"), "w"), indent=1)
    info = result["machine"]
    print(f"Build time, median of {rounds} runs, fresh checkout each run. {info['system']} {info['release']}, "
          f"{info['cpu']}, {info['logical_cpus']} logical CPUs. {versions['wingbox']}; {versions['graphify']}.\n")
    print(table(rows))


if __name__ == "__main__":
    main()
