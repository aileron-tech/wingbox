"""One hundred "which files depend on this file" questions, with confidence intervals.

Usage: python3 -I impact100.py <work dir> sample             draw the questions and write the reference lists
       python3 -I impact100.py <work dir> command            run `wingbox impact` on every question
       python3 -I impact100.py <work dir> agent [workers]    run the agent on the agent subset (paid model sessions)
       python3 -I impact100.py <work dir> results <dir>      write the result files into <dir>

<work dir> is prepared by `run.sh <work dir>`: Wingbox in its own environment, the repositories at
pinned commits, graphs built, reference edges computed.

The question, for a file X: every file that depends on X directly or through one other file,
where "A depends on B" means that A imports B. The reference edges never come from Wingbox:
Python's `ast` (equal to grimp on FastAPI), an own TypeScript resolver, an own Rust resolver,
all in tools/.

Sampling: per repository, SAMPLE_PER_REPO files drawn without replacement, with a fixed seed,
from the source files that at least one other file imports. Nothing else is filtered.
"""
from __future__ import annotations

import collections
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20261116
SAMPLE_PER_REPO = 20
AGENT_PER_REPO = 12
AGENT_MAX_FILES = 120
REPOS = [
    ("chrome-devtools-mcp", "typescript"),
    ("immich-server", "typescript"),
    ("agent-browser", "rust"),
    ("claudes-c-compiler", "rust"),
    ("fastapi", "python"),
]
TASK = (
    "Before changing `{file}`, I need a complete review list: every file of this repository, test files included, that depends on "
    "`{file}` directly, or through exactly one other file. File A is on the list if A depends on `{file}`, or if A "
    "depends on some file B that depends on `{file}`. \"A depends on B\" means that the source of A imports or names "
    "the module of B (TypeScript: import or export ... from, type-only imports included; Rust: a `mod` declaration of "
    "it, a `use` of it, or a path that names it; Python: an `import` or `from ... import` statement that names its module). "
    "Do not list `{file}` itself. Answer with only a JSON array of repository-relative file paths."
)
NOTES = {
    "plain": "",
    "wb": "\n\nA code graph of this repository is already built. The command `wingbox impact <file> 2` prints the files that "
          "depend on a file at depth 1 (directly) and at depth 2 (through one other file).",
}
SETUPS = ("plain", "wb")


def sh(command: list[str], cwd: str | None = None, env: dict | None = None, check: bool = True) -> str:
    return subprocess.run(command, cwd=cwd, env=env, check=check, capture_output=True, text=True, timeout=1800).stdout


# ---------------------------------------------------------------- reference
def compiled_output_edges(root: str, files: dict) -> set[tuple[str, str]]:
    """Imports of the compiled form of a source file, mapped back through outDir and rootDir of tsconfig.json.

    A script that imports `../out/gen/src/a.js` depends on `src/a.ts` when tsconfig compiles `src/a.ts` to that
    path. The TypeScript resolver leaves such an import unresolved, because the compiled file is not in the checkout.
    """
    config_path = os.path.join(root, "tsconfig.json")
    if not os.path.isfile(config_path):
        return set()
    text = re.sub(r"^\s*//.*$", "", open(config_path, encoding="utf-8").read(), flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        options = json.loads(text).get("compilerOptions", {})
    except json.JSONDecodeError:
        return set()
    if "outDir" not in options:
        return set()
    out_dir = os.path.normpath(options["outDir"])
    root_dir = os.path.normpath(options.get("rootDir", "."))
    edges = set()
    for path, items in files.items():
        for item in items:
            if item.get("target") or item.get("how") != "relative":
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), item["spec"]))
            if not resolved.startswith(out_dir + os.sep):
                continue
            stem = os.path.splitext(os.path.normpath(os.path.join(root_dir, resolved[len(out_dir) + 1:])))[0]
            for extension in (".ts", ".tsx", ".mts", ".js", ".mjs"):
                if stem + extension in files:
                    edges.add((path, stem + extension))
                    break
    return edges


def ancestor_module_edges(data: dict) -> set[tuple[str, str]]:
    """A Rust path `crate::a::b::c` reaches module c through the modules a and a::b.

    The files of those ancestor modules declare the next step of the path (`pub mod b;`), so a change there can
    break the file that wrote the path. Like a Python package __init__.py, they are accepted, not required.
    """
    paths_of: dict[str, list[str]] = collections.defaultdict(list)
    for module_path, file in data["modules"].items():
        paths_of[file].append(module_path)
    edges = set()
    for ref in data["refs"]:
        for module_path in paths_of.get(ref["to"], ()):
            parts = module_path.split("::")
            for cut in range(len(parts) - 1, 0, -1):
                ancestor = "::".join(parts[:cut])
                file = data["modules"].get(ancestor) or data["modules"].get(ancestor + "::")
                if file and file not in (ref["frm"], ref["to"]):
                    edges.add((ref["frm"], file))
    return edges


def reference(work: str, repo: str, language: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(required edges, accepted edges).

    Accepted and not required: in Python the package __init__.py files executed on the way to the imported module;
    in TypeScript the imports of the compiled output of a file (see compiled_output_edges); in Rust the ancestor
    modules of a path (see ancestor_module_edges).
    """
    out = os.path.join(work, "out")
    if language == "typescript":
        files = json.load(open(os.path.join(out, repo + ".ts_refs.json")))["files"]
        edges = {(path, item["target"]) for path, items in files.items() for item in items if item.get("target")}
        return edges, edges | compiled_output_edges(os.path.join(work, "bench", "plain", repo), files)
    if language == "rust":
        data = json.load(open(os.path.join(out, repo + ".rust_refs.json")))
        edges = {(ref["frm"], ref["to"]) for ref in data["refs"]}
        return edges, edges | ancestor_module_edges(data)
    data = json.load(open(os.path.join(out, repo + ".ref.json")))
    required = {tuple(pair) for pair in data["pairs"]}
    return required, required | {tuple(pair) for pair in data["implied_pairs"]}


def two_hops(edges: set[tuple[str, str]], target: str) -> tuple[set[str], set[str]]:
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


def sample(work: str) -> None:
    rng = random.Random(SEED)
    questions = []
    gold_dir = os.path.join(work, "bench", "gold100")
    shutil.rmtree(gold_dir, ignore_errors=True)
    os.makedirs(gold_dir)
    for repo, language in REPOS:
        required, accepted = reference(work, repo, language)
        root = os.path.join(work, "bench", "plain", repo)
        imported = sorted({destination for source, destination in required
                           if source != destination and os.path.isfile(os.path.join(root, destination))})
        chosen = sorted(rng.sample(imported, SAMPLE_PER_REPO))
        rows = []
        for path in chosen:
            first, second = two_hops(required, path)
            a_first, a_second = two_hops(accepted, path)
            rows.append((path, first, second, (a_first | a_second) - first - second))
        fits = [row[0] for row in rows if 3 <= len(row[1] | row[2]) <= AGENT_MAX_FILES]
        agent = set(rng.sample(fits, min(AGENT_PER_REPO, len(fits))))
        for number, (path, first, second, extra) in enumerate(rows, start=1):
            qid = f"{repo}-{number:02d}"
            questions.append({"id": qid, "repo": repo, "language": language, "file": path, "agent": path in agent})
            json.dump({"question": {"id": qid, "repo": repo, "file": path}, "direct": sorted(first), "through_one": sorted(second),
                       "gold": sorted(first | second), "also_accepted": sorted(extra)},
                      open(os.path.join(gold_dir, qid + ".json"), "w"), indent=1)
        sizes = sorted(len(row[1] | row[2]) for row in rows)
        print(f"{repo}: {len(imported)} imported files, {len(chosen)} drawn, reference sizes {sizes[0]}..{sizes[-1]} "
              f"(median {sizes[len(sizes) // 2]}), {len(agent)} in the agent subset")
    json.dump({"seed": SEED, "per_repo": SAMPLE_PER_REPO, "agent_per_repo": AGENT_PER_REPO, "agent_max_files": AGENT_MAX_FILES, "questions": questions}, open(os.path.join(work, "bench", "questions100.json"), "w"), indent=1)


# ---------------------------------------------------------------- scoring
def score(named: set[str], gold: dict, target: str) -> dict:
    """Named files against the reference: on the list, not on it, missing. Accepted files are neither required nor wrong."""
    required, accepted = set(gold["gold"]), set(gold["gold"]) | set(gold["also_accepted"])
    named = named - {target}
    right = len(named & required)
    off = len(named - accepted)
    counted = len(named & required) + off
    precision = right / counted if counted else (1.0 if not required else 0.0)
    recall = right / len(required) if required else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"named": len(named), "right": right, "not_on_list": off, "missing": len(required - named),
            "accepted_extra": len((named & accepted) - required),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "exact": off == 0 and right == len(required),
            "exact_strict": named == required}


def wingbox_files(text: str) -> set[str]:
    files, section = set(), ""
    for line in text.split("\n"):
        if line.startswith("Depth "):
            section = "depth"
        elif line and not line.startswith(" "):
            section = ""
        elif section and line.strip() and not line.strip().startswith("..."):
            files.add(line.split()[0])
    return files


def command(work: str) -> None:
    questions = json.load(open(os.path.join(work, "bench", "questions100.json")))["questions"]
    rows = []
    for question in questions:
        gold = json.load(open(os.path.join(work, "bench", "gold100", question["id"] + ".json")))
        repo, target = question["repo"], question["file"]
        text = sh([os.path.join(work, "venv_wb", "bin", "wingbox"), "impact", target, "2", "--all"],
                  cwd=os.path.join(work, "bench", "wb", repo), check=False)
        row = {"id": question["id"], "repo": repo, "language": question["language"], "file": target, "reference_files": len(gold["gold"]),
               "wingbox": {"tokens": len(text) // 4, **score(wingbox_files(text), gold, target)}}
        rows.append(row)
        w = row["wingbox"]
        print(row["id"], target, "reference", row["reference_files"], f"| wingbox {w['right']}/{w['not_on_list']}/{w['missing']}", flush=True)
    json.dump(rows, open(os.path.join(work, "out", "impact100.command.json"), "w"), indent=1)


# ---------------------------------------------------------------- agent
def parse_answer(text: str) -> set[str]:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return set()
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return set()
    return {str(item).strip().lstrip("./") for item in items if isinstance(item, str)}


def trial(work: str, question: dict, setup: str) -> dict:
    trials = os.path.join(work, "bench", "trials100")
    path = os.path.join(trials, f"{question['id']}_{setup}.json")
    if os.path.exists(path):
        return json.load(open(path))["trial"]
    claude = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
    command_line = [
        claude, "-p", TASK.format(file=question["file"]) + NOTES[setup], "--model", "sonnet", "--output-format", "json",
        "--max-budget-usd", "2", "--setting-sources", "project", "--disable-slash-commands", "--strict-mcp-config",
        "--no-session-persistence", "--tools", "Read,Grep,Glob" if setup == "plain" else "Read,Grep,Glob,Bash",
    ]
    env = dict(os.environ)
    text_tools = [f"Bash({tool}:*)" for tool in
                  ("awk", "sed", "sort", "uniq", "cut", "tr", "wc", "head", "tail", "xargs", "cd", "jq", "grep", "ls", "cat")]
    if setup == "wb":
        command_line += ["--allowedTools", "Bash(wingbox:*)", *text_tools]
        env["PATH"] = os.path.join(work, "venv_wb", "bin") + os.pathsep + env["PATH"]
    started = time.time()
    try:
        done = subprocess.run(command_line, cwd=os.path.join(work, "bench", setup, question["repo"]), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1500)
        raw = done.stdout
    except subprocess.TimeoutExpired as error:
        raw = error.stdout or ""
    try:
        events = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        events = []
    events = events if isinstance(events, list) else [events]
    result = next((event for event in events if isinstance(event, dict) and event.get("type") == "result"), {})
    usage = result.get("usage", {})
    gold = json.load(open(os.path.join(work, "bench", "gold100", question["id"] + ".json")))
    answer = parse_answer(result.get("result", "") or "")
    record = {
        "id": question["id"], "repo": question["repo"], "setup": setup,
        "tokens": sum(usage.get(key, 0) or 0 for key in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
        "turns": result.get("num_turns"), "cost_usd": result.get("total_cost_usd"), "seconds": round(time.time() - started, 1),
        "terminal": result.get("terminal_reason") or result.get("subtype"), "is_error": result.get("is_error"),
        "models": sorted((result.get("modelUsage") or {}).keys()),
        **score(answer, gold, question["file"]),
    }
    json.dump({"trial": record, "answer": sorted(answer), "result_text": (result.get("result", "") or "")[:20000]}, open(path, "w"), indent=1)
    return record


def agent(work: str, workers: int) -> None:
    os.makedirs(os.path.join(work, "bench", "trials100"), exist_ok=True)
    questions = [q for q in json.load(open(os.path.join(work, "bench", "questions100.json")))["questions"] if q["agent"]]
    jobs = [(question, setup) for question in questions for setup in SETUPS]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(lambda job: trial(work, *job), jobs):
            print(record["id"], record["setup"], "F1", record["f1"], "tokens", record["tokens"], "turns", record["turns"],
                  "cost", record["cost_usd"], record["terminal"], flush=True)


def results(work: str, target: str) -> None:
    os.makedirs(os.path.join(target, "gold"), exist_ok=True)
    shutil.copy(os.path.join(work, "bench", "questions100.json"), os.path.join(target, "questions.json"))
    for name in sorted(os.listdir(os.path.join(work, "bench", "gold100"))):
        shutil.copy(os.path.join(work, "bench", "gold100", name), os.path.join(target, "gold", name))
    shutil.copy(os.path.join(work, "out", "impact100.command.json"), os.path.join(target, "command.json"))
    trials_dir = os.path.join(work, "bench", "trials100")
    if os.path.isdir(trials_dir):
        # Scores are computed again from the stored answers, so they always follow the reference lists written by `sample`.
        files = {q["id"]: q["file"] for q in json.load(open(os.path.join(work, "bench", "questions100.json")))["questions"]}
        trials = []
        for name in sorted(os.listdir(trials_dir)):
            stored = json.load(open(os.path.join(trials_dir, name)))
            record = stored["trial"]
            gold = json.load(open(os.path.join(work, "bench", "gold100", record["id"] + ".json")))
            record.update(score(set(stored["answer"]), gold, files[record["id"]]))
            trials.append(record)
        json.dump(trials, open(os.path.join(target, "agent.json"), "w"), indent=1)
    versions = {"wingbox": sh([os.path.join(work, "venv_wb", "bin", "wingbox"), "--version"]).strip(),
                "date": time.strftime("%Y-%m-%d")}
    json.dump(versions, open(os.path.join(target, "versions.json"), "w"), indent=1)
    print("written", target)


if __name__ == "__main__":
    work_dir, action = os.path.abspath(sys.argv[1]), sys.argv[2]
    if action == "sample":
        sample(work_dir)
    elif action == "command":
        command(work_dir)
    elif action == "agent":
        agent(work_dir, int(sys.argv[3]) if len(sys.argv) > 3 else 4)
    elif action == "results":
        results(work_dir, os.path.abspath(sys.argv[3]))
    else:
        raise SystemExit("action: sample, command, agent or results")
