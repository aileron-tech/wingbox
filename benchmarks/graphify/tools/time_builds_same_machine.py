"""Time the graph build of Wingbox, Graphify and CodeGraph on one machine: cold and after a one-file edit.

Usage: python3 -I time_builds_same_machine.py <work dir> [rounds]
<work dir> is the directory prepared by run.sh: the three tools and the corpus at the pinned commits.

Cold: every run starts from a fresh export of the checkout, with no earlier output and no cache.
  Wingbox    `wingbox build .`
  Graphify   `graphify extract . --code-only`, then `graphify cluster-only . --no-label --no-viz`
  CodeGraph  `codegraph init --yes` (creates .codegraph/ and indexes the project)
Edit: one line is appended to one source file of the corpus, then each tool's own command for a
changed project runs on the graph it has just built:
  Wingbox    `wingbox build .` (it reuses its cache)
  Graphify   `graphify update .`
  CodeGraph  `codegraph sync`
The tools take turns inside a round, and the order of the tools rotates from round to round. The
load average of the machine is read before every step. After every step the size of the graph is read
from what the tool wrote, so a run that built less cannot pass as a fast one: cold builds of one tool
must agree across rounds, and so must its builds after the edit.

Writes <work dir>/out/build_times.same_machine.json and prints a Markdown table.
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
EDITED = {
    "karpathy": "nanoGPT/model.py",
    "chrome-devtools-mcp": "src/collectors/PageCollector.ts",
    "agent-browser": "cli/src/validation.rs",
    "claudes-c-compiler": "src/backend/elf/constants.rs",
    "fastapi": "fastapi/sse.py",
}
TOOLS = ("wingbox", "graphify", "codegraph")
COLD = {
    "wingbox": [("build", ["build", "."])],
    "graphify": [("extract", ["extract", ".", "--code-only"]), ("cluster", ["cluster-only", ".", "--no-label", "--no-viz"])],
    "codegraph": [("init", ["init", "--yes"])],
}
EDIT = {"wingbox": ["build", "."], "graphify": ["update", "."], "codegraph": ["sync"]}
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs")


def graph_size(tool: str, target: str, env: dict, exe: str) -> dict:
    """What the step wrote: the size of the graph, read from the graph the tool keeps."""
    if tool == "wingbox":
        counts = {"modules": 0, "import_edges": 0}
        path = next(p for p in (os.path.join(target, ".wingbox", "index.sexp"), os.path.join(target, ".code_graph.sexp")) if os.path.exists(p))
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                text = line.lstrip()
                if text.startswith("(node "):
                    counts["modules"] += 1
                elif text.startswith("(imports "):
                    counts["import_edges"] += 1
        return counts
    if tool == "graphify":
        graph = json.load(open(os.path.join(target, "graphify-out", "graph.json"), encoding="utf-8"))
        return {"nodes": len(graph["nodes"]), "links": len(graph["links"])}
    status = json.loads(subprocess.run([exe, "status", "--json"], cwd=target, env=env, capture_output=True, text=True, check=True).stdout)
    return {"files": status["fileCount"], "nodes": status["nodeCount"], "edges": status["edgeCount"]}


def summarise(runs: list[dict]) -> list[dict]:
    """One row per corpus: per tool and step the median wall and CPU seconds, and the size of the graph
    after the cold build (<tool>_graph) and after the edit (<tool>_edit_graph)."""
    rows = []
    for corpus in dict.fromkeys(run["corpus"] for run in runs):
        row: dict = {"corpus": corpus}
        mine = [run for run in runs if run["corpus"] == corpus]
        row["source_files"] = mine[0]["source_files"]
        for run in mine:
            if "graph" in run:
                key = f"{run['tool']}_edit_graph" if run["step"] == "edit" else f"{run['tool']}_graph"
                sizes = row.setdefault(key, run["graph"])
                if sizes != run["graph"]:
                    raise SystemExit(f"{run['tool']} {run['step']} wrote a different graph of {corpus} in round {run['round']}: {run['graph']} against {sizes}")
        for tool in TOOLS:
            for step in [name for name, _ in COLD[tool]] + ["edit"]:
                timed_runs = [run for run in mine if run["tool"] == tool and run["step"] == step]
                if timed_runs:
                    row[f"{tool}_{step}_wall_s"] = round(statistics.median(run["wall_s"] for run in timed_runs), 2)
                    row[f"{tool}_{step}_cpu_s"] = round(statistics.median(run["cpu_s"] for run in timed_runs), 2)
        rows.append(row)
    return rows


def table(rows: list[dict]) -> str:
    lines = [
        "| Corpus | Source files | Wingbox cold, s | Graphify cold (extract + cluster), s | CodeGraph cold, s | Wingbox after edit, s | Graphify after edit, s | CodeGraph after edit, s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        graphify_cold = row["graphify_extract_wall_s"] + row["graphify_cluster_wall_s"]
        lines.append(
            f"| {row['corpus']} | {row['source_files']:,} | {row['wingbox_build_wall_s']:.2f} | {graphify_cold:.2f} | {row['codegraph_init_wall_s']:.2f} | "
            f"{row['wingbox_edit_wall_s']:.2f} | {row['graphify_edit_wall_s']:.2f} | {row['codegraph_edit_wall_s']:.2f} |"
        )
    return "\n".join(lines)


def machine() -> dict:
    model = platform.processor() or platform.machine()
    memory = 0
    if os.path.exists("/proc/cpuinfo"):
        for line in open("/proc/cpuinfo", encoding="utf-8", errors="replace"):
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    elif platform.system() == "Darwin":
        model = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip() or model
        memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip() or 0)
    if not memory and hasattr(os, "sysconf"):
        try:
            memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            memory = 0
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "cpu": model,
            "logical_cpus": os.cpu_count(), "memory_gb": round(memory / 2**30, 1)}


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


def edit(target: str, path: str) -> None:
    comment = "#" if path.endswith(".py") else "//"
    with open(os.path.join(target, path), "a", encoding="utf-8") as handle:
        handle.write(f"\n{comment} one changed line for the build-time benchmark\n")


def main() -> None:
    work = os.path.abspath(sys.argv[1])
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    exes = {"wingbox": os.path.join(work, "venv_wb", "bin", "wingbox"), "graphify": os.path.join(work, "venv_gf", "bin", "graphify"),
            "codegraph": os.path.join(work, "cg", "node_modules", ".bin", "codegraph")}
    base = dict(os.environ, GRAPHIFY_BACKEND="none")
    # CodeGraph: a private HOME, no daemon, no file watcher, no background network calls.
    envs = {"wingbox": base, "graphify": base,
            "codegraph": dict(base, HOME=os.path.join(work, "cg_home"), DO_NOT_TRACK="1", CODEGRAPH_NO_DAEMON="1",
                              CODEGRAPH_NO_WATCH="1", CODEGRAPH_NO_DOWNLOAD="1", NO_COLOR="1")}
    runs: list[dict] = []
    for round_no in range(1, rounds + 1):
        order = TOOLS[round_no % len(TOOLS):] + TOOLS[:round_no % len(TOOLS)]
        for corpus, parts in CORPORA.items():
            for tool in order:
                target = os.path.join(work, "timing", corpus)
                files = export(work, parts, target)
                steps = [(name, arguments) for name, arguments in COLD[tool]] + [("edit", EDIT[tool])]
                for step, arguments in steps:
                    if step == "edit":
                        edit(target, EDITED[corpus])
                    load = os.getloadavg()[0]
                    wall, cpu, code = timed([exes[tool], *arguments], target, envs[tool])
                    if code:
                        raise SystemExit(f"{tool} {step} failed on {corpus} with exit code {code}")
                    run = {"round": round_no, "corpus": corpus, "source_files": files, "tool": tool, "step": step,
                           "wall_s": round(wall, 2), "cpu_s": round(cpu, 2), "load_1min_before": round(load, 2)}
                    if step != "cluster":
                        run["graph"] = graph_size(tool, target, envs[tool], exes[tool])
                    runs.append(run)
                    print(f"round {round_no} {corpus} {tool} {step}: {wall:.2f} s wall, {cpu:.1f} s cpu, load {load:.2f}", file=sys.stderr, flush=True)
    shutil.rmtree(os.path.join(work, "timing"), ignore_errors=True)
    os.makedirs(os.path.join(work, "out"), exist_ok=True)
    json.dump({"runs": runs}, open(os.path.join(work, "out", "build_times.same_machine.runs.json"), "w"), indent=1)
    rows = summarise(runs)
    versions = {tool: subprocess.run([exe, "--version"], env=envs[tool], capture_output=True, text=True).stdout.strip() for tool, exe in exes.items()}
    loads = [run["load_1min_before"] for run in runs]
    result = {"machine": machine(), "versions": versions, "rounds": rounds, "edited_files": EDITED,
              "load_1min": {"min": min(loads), "median": round(statistics.median(loads), 2), "max": max(loads)},
              "summary": rows, "runs": runs}
    json.dump(result, open(os.path.join(work, "out", "build_times.same_machine.json"), "w"), indent=1)
    info = result["machine"]
    print(f"Median of {rounds} rounds. {info['system']} {info['machine']}, {info['cpu']}, {info['logical_cpus']} logical CPUs, "
          f"{info['memory_gb']} GB. Load average (1 min) {result['load_1min']}. {versions}.\n")
    print(table(rows))


if __name__ == "__main__":
    main()
