"""Test D for OntoIndex: the agent of agent_test.py, with the `ontoindex` command, on the same five questions.

Usage: python3 -I oi_agent_test.py <work dir> run [workers]     run the trials (paid model sessions)
       python3 -I oi_agent_test.py <work dir> report            write <work dir>/out/agent_summary.ontoindex.json
Same prompt, model alias, flags, budget, repeats, answer parsing and scoring as agent_test.py (its TASK,
parse_answer and score are used as they are); the setup adds one sentence that names OntoIndex's impact
command, in the shape of the Graphify and Wingbox sentences, and allows `ontoindex` and the same text tools.
`ontoindex` runs in the container of oi_run.sh (`--network none`) through a small wrapper put first on PATH;
the graphs are those oi_run.sh built in <work dir>/bench/oi/<repo>. Trials are kept in <work dir>/bench/trials/
as Q<n>_oi_<repeat>.json and are not run again; agent_test.py's own trials and report are not touched.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.abspath(sys.argv[1])
BENCH = os.path.join(P, "bench")
TRIALS = os.path.join(BENCH, "trials")
BIN = os.path.join(P, "oi_agent_bin")
IMAGE = "wingbox-bench-ontoindex:3.1.9"
NOTE = (
    "\n\nAn OntoIndex code graph of this repository is already built. The command "
    "`ontoindex impact File:<file> --include-tests --depth 2` prints what depends on a file, "
    "at depth 1 (directly) and at depth 2 (through one other node)."
)
TEXT_TOOLS = ("awk", "sed", "sort", "uniq", "cut", "tr", "wc", "head", "tail", "xargs", "cd", "jq", "grep", "ls", "cat")


def agent_test() -> dict:
    """TASK, QUESTIONS, REPEATS, CLAUDE, parse_answer and score of agent_test.py, without running its main()."""
    source = open(os.path.join(HERE, "agent_test.py"), encoding="utf-8").read().rsplit("\nmain()", 1)[0]
    names = {"__file__": os.path.join(HERE, "agent_test.py"), "__name__": "agent_test"}
    saved, sys.argv = sys.argv, [sys.argv[0], P]
    exec(compile(source, names["__file__"], "exec"), names)
    sys.argv = saved
    return names


AT = agent_test()


def wrapper() -> None:
    os.makedirs(BIN, exist_ok=True)
    path = os.path.join(BIN, "ontoindex")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n# OntoIndex in its container, no network, in the current directory.\n"
                     f'exec docker run --rm --network none -v "{P}:{P}" -e HOME=/benchhome -v "{P}/oi_home:/benchhome" -w "$PWD" {IMAGE} ontoindex "$@"\n')
    os.chmod(path, 0o755)


def run_trial(question: dict, repeat: int) -> dict:
    name = f"{question['id']}_oi_{repeat}"
    out_path = os.path.join(TRIALS, name + ".json")
    if os.path.exists(out_path):
        return json.load(open(out_path))
    command = [
        AT["CLAUDE"], "-p", AT["TASK"].format(file=question["file"]) + NOTE, "--model", "sonnet", "--output-format", "json",
        "--max-budget-usd", "2", "--setting-sources", "project", "--disable-slash-commands", "--strict-mcp-config",
        "--no-session-persistence", "--tools", "Read,Grep,Glob,Bash",
        "--allowedTools", "Bash(ontoindex:*)", *[f"Bash({tool}:*)" for tool in TEXT_TOOLS],
    ]
    env = dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"])
    started = time.time()
    try:
        done = subprocess.run(command, cwd=os.path.join(BENCH, "oi", question["repo"]), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1200)
        raw, err = done.stdout, done.stderr
    except subprocess.TimeoutExpired as error:
        raw, err = error.stdout or "", "timeout"
    wall = round(time.time() - started, 1)
    try:
        events = json.loads(raw)
    except json.JSONDecodeError:
        events = []
    events = events if isinstance(events, list) else [events]
    result = next((event for event in events if isinstance(event, dict) and event.get("type") == "result"), {})
    usage = result.get("usage", {})
    gold = json.load(open(os.path.join(BENCH, "gold", question["id"] + ".json")))["gold"]
    answer = AT["parse_answer"](result.get("result", ""))
    trial = {
        "name": name, "question": question["id"], "setup": "oi", "repeat": repeat,
        "tokens": sum(usage.get(key, 0) or 0 for key in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
        "output_tokens": usage.get("output_tokens", 0), "turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"), "seconds": wall, "api_ms": result.get("duration_api_ms"),
        "terminal": result.get("terminal_reason") or result.get("subtype"), "is_error": result.get("is_error"),
        "denials": len(result.get("permission_denials", []) or []),
        "models": sorted((result.get("modelUsage") or {}).keys()),
        "answer": answer, **AT["score"](answer, gold),
    }
    json.dump({"trial": trial, "result_text": result.get("result", ""), "permission_denials": result.get("permission_denials", []),
               "raw_tail": raw[-1500:] if not result else "", "stderr_tail": (err or "")[-800:]}, open(out_path, "w"), indent=1)
    return {"trial": trial}


def main() -> None:
    action = sys.argv[2]
    os.makedirs(TRIALS, exist_ok=True)
    if action == "run":
        wrapper()
        workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
        jobs = [(q, r) for r in range(1, AT["REPEATS"] + 1) for q in AT["QUESTIONS"]]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done in pool.map(lambda job: run_trial(*job), jobs):
                t = done["trial"]
                print(t["name"], "F1", t["f1"], "tokens", t["tokens"], "turns", t["turns"], "cost", t["cost_usd"],
                      "seconds", t["seconds"], t["terminal"], "denials", t["denials"], flush=True)
        return
    if action != "report":
        raise SystemExit("action: run or report")
    trials = [json.load(open(os.path.join(TRIALS, name)))["trial"] for name in sorted(os.listdir(TRIALS)) if "_oi_" in name]
    mean = lambda values: round(statistics.mean(values), 3) if values else None
    summary = {}
    for question in ["all"] + [q["id"] for q in AT["QUESTIONS"]]:
        items = [t for t in trials if question in ("all", t["question"])]
        summary[f"{question}_oi"] = {"trials": len(items), "f1": mean([t["f1"] for t in items]), "f1_min": min(t["f1"] for t in items),
                                     "tokens": round(mean([t["tokens"] for t in items])), "turns": mean([t["turns"] or 0 for t in items]),
                                     "cost_usd": mean([t["cost_usd"] or 0 for t in items]), "seconds": mean([t["seconds"] for t in items]),
                                     "denials": sum(t["denials"] for t in items), "total_cost_usd": round(sum(t["cost_usd"] or 0 for t in items), 2)}
        print(f"{question:4s} oi", summary[f"{question}_oi"])
    json.dump({"summary": summary, "trials": trials}, open(os.path.join(P, "out", "agent_summary.ontoindex.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
