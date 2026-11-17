"""Time the graph build of several tools on one machine: cold, and after a one-file edit.

Usage: python3 -I time_builds_tools.py <work dir> <out.json> [rounds] --tool <name>=<executable> ...
Tools known here: wingbox, graphify, ontoindex (one --tool per tool; the executable is a path).
<work dir> holds the corpus at the pinned commits (run.sh with RUN_ONLY=corpus is enough).

Cold: every run starts from a fresh export of the checkout, with no earlier output and no cache.
  wingbox    `wingbox build .`
  graphify   `graphify extract . --code-only`, then `graphify cluster-only . --no-label --no-viz`
  ontoindex  `ontoindex analyze . --skip-agents-md`; OntoIndex needs a git repository to track
             commits and changes, so its export gets `git init` and one commit before the clock starts.
Edit: one line is appended to one source file (the target file of the question of that corpus), then
each tool's own command for a changed project runs on the graph it has just built:
  wingbox    `wingbox build .` (it reuses its cache)
  graphify   `graphify update .`
  ontoindex  `ontoindex analyze . --skip-agents-md` (the tree is now dirty), and separately
             `ontoindex analyze . --dirty-overlay --skip-agents-md`, what `ontoindex watch` runs on a change.
The tools take turns inside a round and their order rotates from round to round. The 1-minute load
average is read before every step. After every cold build and edit the size of the graph is read from
what the tool wrote, so a run that built less cannot pass as a fast one.

Writes <out.json> (machine, versions, every run, medians) and prints a Markdown table.
"""
from __future__ import annotations

import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
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
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs")
COLD = {
    "wingbox": [("build", ["build", "."])],
    "graphify": [("extract", ["extract", ".", "--code-only"]), ("cluster", ["cluster-only", ".", "--no-label", "--no-viz"])],
    "ontoindex": [("analyze", ["analyze", ".", "--skip-agents-md"])],
}
EDIT = {
    "wingbox": [("edit", ["build", "."])],
    "graphify": [("edit", ["update", "."])],
    "ontoindex": [("edit", ["analyze", ".", "--skip-agents-md"]), ("edit_overlay", ["analyze", ".", "--dirty-overlay", "--skip-agents-md"])],
}
NEEDS_GIT = {"ontoindex"}
OI_SIZE_QUERY = ("MATCH (f:File) WITH count(f) AS files MATCH (a:File)-[r:CodeRelation]->(b:File) WHERE r.type = 'IMPORTS' "
                 "RETURN files, count(r) AS import_edges LIMIT 1")


def cypher_rows(text: str) -> list[dict]:
    """Rows of `ontoindex cypher`: it prints JSON whose "markdown" field is a Markdown table."""
    data = json.loads(text[text.index("{"):])
    lines = [line for line in data["markdown"].split("\n") if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    return [dict(zip(header, (cell.strip() for cell in line.strip("|").split("|")))) for line in lines[2:]]


def capture(command: list[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    """Run a command with its standard output going to a file: on a pipe, `ontoindex` 3.1.9 stops its
    output at 65,536 bytes (Node exits before the pipe is drained), so a pipe would cut long answers."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        done = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.PIPE, text=True, env=env, timeout=900)
        out.seek(0)
        return subprocess.CompletedProcess(command, done.returncode, out.read(), done.stderr)


def graph_size(tool: str, target: str, env: dict, exe: str) -> dict:
    """What the step wrote: the size of the graph, read from the graph the tool keeps."""
    if tool == "wingbox":
        counts = {"modules": 0, "import_edges": 0}
        with open(os.path.join(target, ".wingbox", "index.sexp") if os.path.exists(os.path.join(target, ".wingbox", "index.sexp"))
                  else os.path.join(target, ".code_graph.sexp"), encoding="utf-8") as handle:
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
    text = capture([exe, "cypher", OI_SIZE_QUERY], target, env).stdout
    rows = cypher_rows(text)
    return {"files": int(rows[0]["files"]), "import_edges": int(rows[0]["import_edges"])}


def machine() -> dict:
    model = platform.processor() or platform.machine()
    if os.path.exists("/proc/cpuinfo"):
        for line in open("/proc/cpuinfo", encoding="utf-8", errors="replace"):
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "cpu": model,
            "logical_cpus": os.cpu_count(), "memory_gb": round(memory / 2**30, 1)}


def export(work: str, parts: list[str], target: str, git: bool) -> int:
    """A clean copy of the corpus: the tracked files of each checkout, nothing else (plus one commit when asked)."""
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    for part in parts:
        prefix = f"{os.path.basename(part)}/" if len(parts) > 1 else ""
        archive = subprocess.run(
            ["git", "-C", os.path.join(work, "corpus", part), "archive", "--format=tar", *([f"--prefix={prefix}"] if prefix else []), "HEAD"],
            check=True, capture_output=True,
        ).stdout
        subprocess.run(["tar", "-x", "-C", target], input=archive, check=True)
    files = sum(name.endswith(SOURCE_SUFFIXES) for _, _, names in os.walk(target) for name in names)
    if git:
        for command in (["init", "-q"], ["add", "-A"], ["-c", "user.name=bench", "-c", "user.email=bench@localhost", "commit", "-qm", "corpus"]):
            subprocess.run(["git", "-C", target, *command], check=True, capture_output=True)
    return files


def timed(command: list[str], cwd: str, env: dict) -> tuple[float, float, int]:
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


def summarise(runs: list[dict], tools: list[str]) -> list[dict]:
    rows = []
    for corpus in dict.fromkeys(run["corpus"] for run in runs):
        mine = [run for run in runs if run["corpus"] == corpus]
        row: dict = {"corpus": corpus, "source_files": mine[0]["source_files"],
                     "load_1min_median": round(statistics.median(run["load_1min_before"] for run in mine), 2)}
        for run in mine:
            if "graph" in run:
                key = f"{run['tool']}_{run['step']}_graph"
                sizes = row.setdefault(key, run["graph"])
                if sizes != run["graph"]:
                    raise SystemExit(f"{run['tool']} {run['step']} wrote a different graph of {corpus} in round {run['round']}: {run['graph']} against {sizes}")
        for tool in tools:
            for step, _ in COLD[tool] + EDIT[tool]:
                timed_runs = [run for run in mine if run["tool"] == tool and run["step"] == step]
                if timed_runs:
                    row[f"{tool}_{step}_wall_s"] = round(statistics.median(run["wall_s"] for run in timed_runs), 2)
                    row[f"{tool}_{step}_cpu_s"] = round(statistics.median(run["cpu_s"] for run in timed_runs), 2)
        rows.append(row)
    return rows


def table(rows: list[dict], tools: list[str]) -> str:
    columns = [(tool, step) for tool in tools for step, _ in COLD[tool] + EDIT[tool]]
    lines = ["| Corpus | Source files | Load | " + " | ".join(f"{tool} {step}, s" for tool, step in columns) + " |",
             "|---|---|---|" + "---|" * len(columns)]
    for row in rows:
        cells = [f"{row.get(f'{tool}_{step}_wall_s', float('nan')):.2f}" for tool, step in columns]
        lines.append(f"| {row['corpus']} | {row['source_files']:,} | {row['load_1min_median']:.1f} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    arguments = sys.argv[1:]
    exes = {}
    while "--tool" in arguments:
        at = arguments.index("--tool")
        name, exe = arguments[at + 1].split("=", 1)
        if name not in COLD:
            raise SystemExit(f"unknown tool {name}; known: {', '.join(COLD)}")
        exes[name] = exe
        del arguments[at:at + 2]
    work, out_path = os.path.abspath(arguments[0]), arguments[1]
    rounds = int(arguments[2]) if len(arguments) > 2 else 3
    tools = list(exes)
    env = dict(os.environ, GRAPHIFY_BACKEND="none")
    runs: list[dict] = []
    for round_no in range(1, rounds + 1):
        order = tools[round_no % len(tools):] + tools[:round_no % len(tools)]
        for corpus, parts in CORPORA.items():
            for tool in order:
                target = os.path.join(work, "timing", corpus)
                files = export(work, parts, target, tool in NEEDS_GIT)
                steps = COLD[tool] + EDIT[tool]
                for step, step_arguments in steps:
                    if step == "edit":
                        edit(target, EDITED[corpus])
                    load = os.getloadavg()[0]
                    wall, cpu, code = timed([exes[tool], *step_arguments], target, env)
                    if code:
                        raise SystemExit(f"{tool} {step} failed on {corpus} with exit code {code}")
                    run = {"round": round_no, "corpus": corpus, "source_files": files, "tool": tool, "step": step,
                           "wall_s": round(wall, 2), "cpu_s": round(cpu, 2), "load_1min_before": round(load, 2)}
                    if step not in ("cluster", "edit_overlay"):
                        run["graph"] = graph_size(tool, target, env, exes[tool])
                    runs.append(run)
                    print(f"round {round_no} {corpus} {tool} {step}: {wall:.2f} s wall, {cpu:.1f} s cpu, load {load:.2f}", file=sys.stderr, flush=True)
    shutil.rmtree(os.path.join(work, "timing"), ignore_errors=True)
    rows = summarise(runs, tools)
    versions = {tool: subprocess.run([exe, "--version"], env=env, capture_output=True, text=True).stdout.strip() for tool, exe in exes.items()}
    result = {"machine": machine(), "versions": versions, "rounds": rounds, "summary": rows, "runs": runs}
    json.dump(result, open(out_path, "w"), indent=1)
    info = result["machine"]
    print(f"Build time, median of {rounds} runs, fresh checkout each run. {info['system']} {info['release']}, {info['cpu']}, "
          f"{info['logical_cpus']} logical CPUs, {info['memory_gb']} GB. {versions}.\n")
    print(table(rows, tools))


if __name__ == "__main__":
    main()
