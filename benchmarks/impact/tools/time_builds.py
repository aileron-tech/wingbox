"""Time the graph build of Wingbox on the benchmark corpus.

Usage: python3 -I time_builds.py <work dir> [rounds]
<work dir> is the directory prepared by run.sh (RUN_ONLY=corpus is enough): it holds the environment
and the corpus at the pinned commits. Every run starts from a fresh export of the checkout, so the
build finds no earlier graph and no cache. Recorded per run: wall seconds and CPU seconds (user plus
system time of the child processes).

`wingbox build .` writes the graph, the report and the map.

After each build the size of the graph is read from the file the build wrote (modules and import edges
of .wingbox/index.sexp), so a run that built less than the reference run cannot pass as a fast one.

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

# corpus -> (checkouts under <work dir>/corpus, tree-ish to export)
CORPORA = {
    "karpathy": (["karpathy/nanoGPT", "karpathy/minGPT", "karpathy/micrograd"], "HEAD"),
    "chrome-devtools-mcp": (["chrome-devtools-mcp"], "HEAD"),
    "agent-browser": (["agent-browser"], "HEAD"),
    "claudes-c-compiler": (["claudes-c-compiler"], "HEAD"),
    "immich-server": (["immich"], "HEAD:server"),
    "fastapi": (["fastapi"], "HEAD"),
}
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs")


def graph_size(target: str) -> dict:
    """What the build wrote: the size of the graph, read from the graph file itself."""
    counts = {"modules": 0, "import_edges": 0}
    with open(os.path.join(target, ".wingbox", "index.sexp"), encoding="utf-8") as handle:
        for line in handle:
            text = line.lstrip()
            if text.startswith("(node "):
                counts["modules"] += 1
            elif text.startswith("(imports "):
                counts["import_edges"] += 1
    return counts


def summarise(runs: list[dict]) -> list[dict]:
    """One row per corpus: the median wall and CPU seconds of the build over the rounds, and the graph it wrote."""
    rows = []
    for corpus in dict.fromkeys(run["corpus"] for run in runs):
        mine = [run for run in runs if run["corpus"] == corpus]
        graphs = [run["graph"] for run in mine]
        if any(graph != graphs[0] for graph in graphs):
            raise SystemExit(f"the builds of {corpus} wrote graphs of different size")
        rows.append({"corpus": corpus, "source_files": mine[0]["source_files"],
                     "build_wall_s": round(statistics.median(run["wall_s"] for run in mine), 2),
                     "build_cpu_s": round(statistics.median(run["cpu_s"] for run in mine), 2),
                     "modules": graphs[0]["modules"], "import_edges": graphs[0]["import_edges"]})
    return rows


def table(rows: list[dict]) -> str:
    lines = ["| Corpus | Source files | Build, s | Modules | Import edges |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['corpus']} | {row['source_files']:,} | {row['build_wall_s']:.2f} | {row['modules']:,} | {row['import_edges']:,} |")
    return "\n".join(lines)


def machine() -> dict:
    model = platform.processor() or platform.machine()
    if os.path.exists("/proc/cpuinfo"):
        for line in open("/proc/cpuinfo", encoding="utf-8", errors="replace"):
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return {"system": platform.system(), "release": platform.release(), "cpu": model, "logical_cpus": os.cpu_count()}


def export(work: str, parts: list[str], tree: str, target: str) -> int:
    """A clean copy of the corpus: the tracked files of each checkout, nothing else."""
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    for part in parts:
        prefix = f"{os.path.basename(part)}/" if len(parts) > 1 else ""
        archive = subprocess.run(
            ["git", "-C", os.path.join(work, "corpus", part), "archive", "--format=tar", *([f"--prefix={prefix}"] if prefix else []), tree],
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
    wingbox = os.path.join(work, "venv_wb", "bin", "wingbox")
    runs: list[dict] = []
    for round_no in range(1, rounds + 1):
        for corpus, (parts, tree) in CORPORA.items():
            target = os.path.join(work, "timing", corpus)
            files = export(work, parts, tree, target)
            wall, cpu, code = timed([wingbox, "build", "."], target, dict(os.environ))
            if code:
                raise SystemExit(f"wingbox build failed on {corpus} with exit code {code}")
            runs.append({"round": round_no, "corpus": corpus, "source_files": files, "wall_s": round(wall, 2), "cpu_s": round(cpu, 2),
                         "graph": graph_size(target)})
            print(f"round {round_no} {corpus}: {wall:.1f} s wall, {cpu:.1f} s cpu", file=sys.stderr, flush=True)
    shutil.rmtree(os.path.join(work, "timing"), ignore_errors=True)
    rows = summarise(runs)
    version = subprocess.run([wingbox, "--version"], capture_output=True, text=True).stdout.strip()
    result = {"machine": machine(), "version": version, "rounds": rounds, "summary": rows, "runs": runs}
    os.makedirs(os.path.join(work, "out"), exist_ok=True)
    json.dump(result, open(os.path.join(work, "out", "build_times.json"), "w"), indent=1)
    info = result["machine"]
    print(f"Build time, median of {rounds} runs, fresh checkout each run. {info['system']} {info['release']}, "
          f"{info['cpu']}, {info['logical_cpus']} logical CPUs. {version}.\n")
    print(table(rows))


if __name__ == "__main__":
    main()
