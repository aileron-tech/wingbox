"""Agent test: plain search, Wingbox and Graphify on "what depends on this file" questions.

Usage: python3 -I agent_test.py <work dir> run [filter] [workers]   run the trials; filter = regular expression on the trial name
       python3 -I agent_test.py <work dir> report                  print the summary and save <work dir>/out/agent_summary.json
Agent: Claude Code, headless (`claude -p`), model alias `sonnet`; no user settings, skills or connectors.
The `claude` command is taken from PATH, or from the environment variable CLAUDE_BIN. Each trial is one paid
model session; a finished trial is kept in <work dir>/bench/trials/ and is not run again.
Reference lists: <work dir>/bench/gold/, written by tools/gold.py.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

P = os.path.abspath(sys.argv[1])
BENCH = os.path.join(P, "bench")
TRIALS = os.path.join(BENCH, "trials")
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
QUESTIONS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "questions.json")))
SETUPS = ("plain", "wb", "gf")
REPEATS = 3
TASK = (
    "Before changing `{file}`, I need a complete review list: every file of this repository, test files included, that depends on "
    "`{file}` directly, or through exactly one other file. File A is on the list if A depends on `{file}`, or if A "
    "depends on some file B that depends on `{file}`. \"A depends on B\" means that the source of A imports or names "
    "the module of B (TypeScript: import or export ... from, type-only imports included; Rust: a `mod` declaration of "
    "it, a `use` of it, or a path that names it; Python: an `import` or `from ... import` statement that names its module). "
    "Do not list `{file}` itself. Answer with only a JSON array of repository-relative file paths."
)
GRAPHIFY_NOTE = (
    "\n\nA Graphify knowledge graph of this repository is already built in `graphify-out/`. The command "
    "`graphify affected \"<file>\" --depth 2` does a reverse traversal of it and prints what is impacted by a file, "
    "at depth 1 (directly) and at depth 2 (through one other node)."
)
WINGBOX_NOTE = (
    "\n\nA code graph of this repository is already built. The command `wingbox impact <file> 2` prints the files that "
    "depend on a file at depth 1 (directly) and at depth 2 (through one other file)."
)


def parse_answer(text: str) -> list[str]:
    match = re.search(r"\[[^\[\]]*\]", text or "", re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return sorted({str(item).strip().lstrip("./") for item in items if isinstance(item, str)})


def score(answer: list[str], gold: list[str]) -> dict:
    got, want = set(answer), set(gold)
    hit = len(got & want)
    precision = hit / len(got) if got else 0.0
    recall = hit / len(want) if want else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3), "listed": len(got)}


def run_trial(question: dict, setup: str, repeat: int) -> dict:
    name = f"{question['id']}_{setup}_{repeat}"
    out_path = os.path.join(TRIALS, name + ".json")
    if os.path.exists(out_path):
        return json.load(open(out_path))
    prompt = TASK.format(file=question["file"]) + {"wb": WINGBOX_NOTE, "gf": GRAPHIFY_NOTE}.get(setup, "")
    command = [
        CLAUDE, "-p", prompt, "--model", "sonnet", "--output-format", "json", "--max-budget-usd", "2",
        "--setting-sources", "project", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence",
        "--tools", "Read,Grep,Glob" if setup == "plain" else "Read,Grep,Glob,Bash",
    ]
    env = dict(os.environ)
    text_tools = [f"Bash({tool}:*)" for tool in
                  ("awk", "sed", "sort", "uniq", "cut", "tr", "wc", "head", "tail", "xargs", "cd", "jq", "grep", "ls", "cat")]
    if setup == "wb":
        command += ["--allowedTools", "Bash(wingbox:*)", *text_tools]
        env["PATH"] = os.path.join(P, "venv_wb", "bin") + os.pathsep + env["PATH"]
    if setup == "gf":
        command += ["--allowedTools", "Bash(graphify:*)", *text_tools]
        env["PATH"] = os.path.join(P, "venv_gf", "bin") + os.pathsep + env["PATH"]
        env["GRAPHIFY_BACKEND"] = "none"
    started = time.time()
    try:
        done = subprocess.run(command, cwd=os.path.join(BENCH, setup, question["repo"]), env=env,
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
    answer = parse_answer(result.get("result", ""))
    trial = {
        "name": name, "question": question["id"], "setup": setup, "repeat": repeat,
        "tokens": sum(usage.get(key, 0) or 0 for key in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
        "output_tokens": usage.get("output_tokens", 0), "turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"), "seconds": wall, "api_ms": result.get("duration_api_ms"),
        "terminal": result.get("terminal_reason") or result.get("subtype"), "is_error": result.get("is_error"),
        "denials": len(result.get("permission_denials", []) or []),
        "models": sorted((result.get("modelUsage") or {}).keys()),
        "answer": answer, **score(answer, gold),
    }
    json.dump({"trial": trial, "result_text": result.get("result", ""), "permission_denials": result.get("permission_denials", []),
               "raw_tail": raw[-1500:] if not result else "", "stderr_tail": (err or "")[-800:]}, open(out_path, "w"), indent=1)
    return {"trial": trial}


def main() -> None:
    action = sys.argv[2]
    os.makedirs(TRIALS, exist_ok=True)
    if action == "run":
        wanted = sys.argv[3] if len(sys.argv) > 3 else ""
        workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
        jobs = [(q, s, r) for r in range(1, REPEATS + 1) for q in QUESTIONS for s in SETUPS
                if re.search(wanted, f"{q['id']}_{s}_{r}")]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done in pool.map(lambda job: run_trial(*job), jobs):
                t = done["trial"]
                print(t["name"], "F1", t["f1"], "tokens", t["tokens"], "turns", t["turns"], "cost", t["cost_usd"],
                      "seconds", t["seconds"], t["terminal"], "denials", t["denials"], flush=True)
        return
    if action != "report":
        raise SystemExit("action: run or report")
    trials = []
    for name in sorted(os.listdir(TRIALS)):
        if re.match(r"Q\d_(plain|wb|gf)_\d\.json$", name):
            trials.append(json.load(open(os.path.join(TRIALS, name)))["trial"])
    rows = collections.defaultdict(list)
    for t in trials:
        rows[(t["question"], t["setup"])].append(t)
        rows[("all", t["setup"])].append(t)
    mean = lambda values: round(sum(values) / len(values), 3) if values else None
    summary = {}
    for (question, setup), items in sorted(rows.items()):
        summary[f"{question}_{setup}"] = {
            "trials": len(items), "f1": mean([t["f1"] for t in items]), "f1_min": min(t["f1"] for t in items),
            "tokens": round(mean([t["tokens"] for t in items]) or 0), "turns": mean([t["turns"] or 0 for t in items]),
            "cost_usd": mean([t["cost_usd"] or 0 for t in items]), "seconds": mean([t["seconds"] for t in items]),
            "denials": sum(t["denials"] for t in items),
        }
        print(f"{question:4s} {setup:6s}", summary[f"{question}_{setup}"])
    json.dump({"summary": summary, "trials": trials}, open(os.path.join(P, "out", "agent_summary.json"), "w"), indent=1)


main()
