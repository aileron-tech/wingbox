"""Find usages: "I will rename or change exported symbol S of file X. Where is S used?"

Usage: python3 -I usages.py <work dir> candidates          shuffle the exported symbols of every repository
       python3 -I usages.py <work dir> questions           keep the drawn questions, write the reference file lists
       python3 -I usages.py <work dir> sites               the reference use sites of every question, from SCIP
       python3 -I usages.py <work dir> wingbox             ask `wingbox usages` every question
       python3 -I usages.py <work dir> codegraph           ask `codegraph callers` every question
       python3 -I usages.py <work dir> graphify <depth>    ask `graphify affected` every question
       python3 -I usages.py <work dir> safety              check that nothing of the repositories was built
       python3 -I usages.py <work dir> versions            record the tool versions
       python3 -I usages.py <work dir> results <dir>       score every tool, write the result files into <dir>

<work dir> is prepared by run.sh: the repositories at pinned commits (bench/plain with installed dependencies
for the language tooling, and one clean copy per tool under test: bench/wb, bench/cg, bench/gf), the tools
pinned, the symbol lists in out/<repo>.symbols.json, the SCIP indexes in scip/<repo>.scip.

Sampling, per repository: the exported top-level symbols of the language's own parser (tools/*_symbols.*) are
shuffled with a fixed seed; the reference tool ("find usages" of the language tooling) is asked for each in that
order, and the first QUESTIONS_PER_REPO symbols with a reference in another file are the questions.

Two references per question:
  files  every file other than X with at least one reference to S, by the language tooling's find usages
         (TypeScript language service, pyright, rust-analyzer);
  sites  every occurrence of S in a file other than X that is not a definition, by the SCIP index of the same
         engines (scip-typescript, scip-python, rust-analyzer scip). For a TypeScript class, `new S(...)` is an
         occurrence of the class's constructor symbol in SCIP; those occurrences are sites of S too.
A site is scored by (file, line): Graphify prints no column, and one line rarely holds two uses of one symbol.
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))
import scip_refs  # noqa: E402

SEED = 20261116
QUESTIONS_PER_REPO = 20
# (name, language, path of the analysed tree under bench/plain, path under bench/wb, bench/cg and bench/gf)
REPOS = [
    ("chrome-devtools-mcp", "typescript", "chrome-devtools-mcp", "chrome-devtools-mcp"),
    ("immich-server", "typescript", "immich/server", "immich-server"),
    ("agent-browser", "rust", "agent-browser", "agent-browser"),
    ("claudes-c-compiler", "rust", "claudes-c-compiler", "claudes-c-compiler"),
    ("fastapi", "python", "fastapi", "fastapi"),
]
TOOLS = ("wingbox", "codegraph", "graphify_depth1", "graphify")
# A limit no answer reaches, so that every tool prints its whole list (CodeGraph prints 20 per definition by default).
NO_LIMIT = "1000000"
# Every CodeGraph run as in benchmarks/graphify/run.sh: a private HOME (added per call), no daemon, no file watcher.
CODEGRAPH_ENV = {"DO_NOT_TRACK": "1", "CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_NO_WATCH": "1",
                 "CODEGRAPH_NO_DOWNLOAD": "1", "NO_COLOR": "1"}
WB_USAGE = re.compile(r'^\(usage (\S+) (\d+) (\d+) :kind (\S+) :file ("[^"]*"|\S+)\)$')
WB_USAGE_FILE = re.compile(r'^\(usage-file (\S+) :kind (\S+) :file ("[^"]*"|\S+)\)$')
WB_SUMMARY = re.compile(r"^\(usages-summary \S+ :uses (\d+) :files (\d+) :unresolved (\d+)\)$")
GF_HIT = re.compile(r"^- (.*) \[([^\]]+)\] (.+?)(?::L(\d+)\S*)?$")


def run(command: list[str], cwd: str, env: dict | None = None) -> tuple[str, int]:
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=1800,
                          env=dict(os.environ, **(env or {})))
    return done.stdout, done.returncode


def load(path: str):
    return json.load(open(path))


def save(data, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w"), indent=1)


def tree(work: str, setup: str, repo: str) -> str:
    entry = next(e for e in REPOS if e[0] == repo)
    return os.path.join(work, "bench", setup, entry[2] if setup == "plain" else entry[3])


def questions_of(work: str) -> list[dict]:
    return load(os.path.join(work, "bench", "questions.json"))["questions"]


# ---------------------------------------------------------------- sampling
def candidates(work: str) -> None:
    for repo, _language, _plain, _tree in REPOS:
        symbols = load(os.path.join(work, "out", f"{repo}.symbols.json"))["symbols"]
        ordered = sorted(symbols, key=lambda s: (s["file"], s["line"], s["col"], s["name"]))
        random.Random(f"{SEED}:{repo}").shuffle(ordered)
        save(ordered, os.path.join(work, "out", f"{repo}.candidates.json"))
        print(f"{repo}: {len(ordered)} exported symbols shuffled")


def questions(work: str) -> None:
    gold_dir = os.path.join(work, "bench", "gold")
    shutil.rmtree(gold_dir, ignore_errors=True)
    meta = {"seed": SEED, "per_repo": QUESTIONS_PER_REPO, "repos": [], "questions": []}
    for repo, language, _plain, _tree in REPOS:
        reference = load(os.path.join(work, "out", f"{repo}.reference.json"))
        symbols = load(os.path.join(work, "out", f"{repo}.symbols.json"))
        used = [t for t in reference["tried"] if t["files"]][:QUESTIONS_PER_REPO]
        mine = []
        for number, item in enumerate(sorted(used, key=lambda t: (t["file"], t["line"], t["name"])), start=1):
            qid = f"{repo}-{number:02d}"
            question = {"id": qid, "repo": repo, "language": language, "file": item["file"], "name": item["name"],
                        "kind": item["kind"], "line": item["line"], "col": item["col"]}
            mine.append(question)
            save({"question": question, "files": item["files"], "references": item["references"]},
                 os.path.join(gold_dir, qid + ".json"))
        meta["questions"] += mine
        save(mine, os.path.join(work, "bench", f"questions.{repo}.json"))
        meta["repos"].append({"repo": repo, "language": language, "reference_tool": reference["tool"],
                              "source_files": symbols["files"], "exported_symbols": len(symbols["symbols"]),
                              "tried": len(reference["tried"]), "used": len(used)})
        print(f"{repo}: {len(reference['tried'])} symbols tried, {len(used)} used by another file")
    save(meta, os.path.join(work, "bench", "questions.json"))


# ---------------------------------------------------------------- reference sites
def sites(work: str) -> None:
    """Every non-definition occurrence of S in another file, from the SCIP index; 1-based line and column."""
    result = {}
    for repo, _language, _plain, _tree in REPOS:
        documents = scip_refs.read_index(os.path.join(work, "scip", repo + ".scip"))
        by_symbol = collections.defaultdict(set)
        for rel, occurrences in documents.items():
            for line, col, symbol, roles in occurrences:
                if not roles & scip_refs.DEFINITION:
                    by_symbol[symbol].add((rel, line + 1, col + 1))
        for question in (q for q in questions_of(work) if q["repo"] == repo):
            on_line = [(col, symbol) for line, col, symbol, roles in documents.get(question["file"], [])
                       if line == question["line"] - 1 and roles & scip_refs.DEFINITION]
            chosen = [symbol for col, symbol in on_line if col == question["col"]] or \
                [symbol for _col, symbol in on_line if scip_refs.ends_with_name(symbol, question["name"])]
            found = set()
            if chosen:
                for symbol in (chosen[0], chosen[0] + "`<constructor>`()."):
                    found |= {site for site in by_symbol.get(symbol, ()) if site[0] != question["file"]}
            result[question["id"]] = {"found": bool(chosen), "symbol": chosen[0] if chosen else None,
                                      "sites": sorted(found)}
        print(f"{repo}: {sum(len(result[q['id']]['sites']) for q in questions_of(work) if q['repo'] == repo)} sites")
    save(result, os.path.join(work, "out", "sites.json"))


# ---------------------------------------------------------------- tools under test
def wingbox(work: str) -> None:
    """`wingbox usages <file>::<S> --all`: the text an agent reads, and the S-expressions of the same answer."""
    binary = os.path.join(work, "venv_wb", "bin", "wingbox")
    rows = {}
    for question in questions_of(work):
        root = tree(work, "wb", question["repo"])
        target = f"{question['file']}::{question['name']}"
        text, _code = run([binary, "usages", target, "--all"], root)
        sexp, _code = run([binary, "usages", target, "--sexp", "--all"], root)
        named, unresolved, resolved = set(), 0, False
        for line in sexp.splitlines():
            if line.startswith("(usages-target "):
                resolved = True
            match = WB_USAGE.match(line)
            if match:
                if match.group(4) == "unresolved-call":
                    unresolved += 1
                else:
                    named.add((match.group(5).strip('"'), int(match.group(2)), int(match.group(3))))
                continue
            match = WB_USAGE_FILE.match(line)
            if match:
                named.add((match.group(3).strip('"'), None, None))
        rows[question["id"]] = {"resolved": resolved, "sites": sorted(named, key=_site_key), "unresolved": unresolved,
                                "chars": len(text), "command": f"wingbox usages {target} --all"}
        print(question["id"], question["name"], "wingbox", len(named), flush=True)
    save(rows, os.path.join(work, "out", "wingbox.json"))


def codegraph(work: str) -> None:
    """`codegraph callers <S> --file <X>`: its symbol-level "who uses this" command (calls, references,
    imports, instantiations), with a limit no answer reaches. Files and sites from --json, tokens from the text."""
    binary = os.path.join(work, "cg", "node_modules", ".bin", "codegraph")
    env = dict(CODEGRAPH_ENV, HOME=os.path.join(work, "cg_home"))
    rows = {}
    for question in questions_of(work):
        root = tree(work, "cg", question["repo"])
        command = [binary, "callers", question["name"], "--file", question["file"], "--limit", NO_LIMIT]
        text, _code = run(command, root, env)
        raw, _code = run([*command, "--json"], root, env)
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        named = set()
        for definition in data.get("definitions", []):
            files = {node["id"]: node["filePath"] for node in definition.get("callers", [])}
            for edge in definition.get("edges", []):
                file = files.get(edge["source"])
                if file is None:
                    continue
                line = edge.get("line")
                column = edge.get("column")  # 0-based in CodeGraph
                named.add((file, line, None if column is None else column + 1) if line else (file, None, None))
        resolved = bool(data.get("definitions")) and not data.get("filteredOut")
        rows[question["id"]] = {"resolved": resolved, "definitions": len(data.get("definitions", [])),
                                "sites": sorted(named, key=_site_key), "chars": len(text),
                                "command": " ".join(["codegraph", *command[1:]])}
        print(question["id"], question["name"], "codegraph", len(named), flush=True)
    save(rows, os.path.join(work, "out", "codegraph.json"))


def graphify(work: str, depth: int) -> None:
    """`graphify affected "<X>::<S>" --depth N`: its reverse traversal from one node, the command its help gives
    for "nodes impacted by X". Each hit names the file and line of the edge it came through."""
    binary = os.path.join(work, "venv_gf", "bin", "graphify")
    rows = {}
    for question in questions_of(work):
        root = tree(work, "gf", question["repo"])
        target = f"{question['file']}::{question['name']}"
        command = [binary, "affected", target, "--depth", str(depth)]
        text, code = run(command, root)
        named = set()
        for line in text.splitlines():
            match = GF_HIT.match(line)
            if match and match.group(3) != "-":
                named.add((match.group(3), int(match.group(4)) if match.group(4) else None, None))
        rows[question["id"]] = {"resolved": code == 0 and text.startswith("Affected nodes for "), "exit": code,
                                "sites": sorted(named, key=_site_key), "chars": len(text),
                                "command": " ".join(["graphify", *command[1:]])}
        print(question["id"], question["name"], f"graphify depth {depth}", len(named), flush=True)
    save(rows, os.path.join(work, "out", "graphify_depth1.json" if depth == 1 else "graphify.json"))


def _site_key(site: tuple) -> tuple:
    return (site[0], site[1] or 0, site[2] or 0)


# ---------------------------------------------------------------- safety, versions
def safety(work: str) -> None:
    """Evidence that nothing of the repositories was built or imported: no build output, no bytecode cache."""
    found = {"build_dirs": [], "bytecode_dirs": []}
    for setup in ("plain", "wb", "cg", "gf"):
        base = os.path.join(work, "bench", setup)
        for directory, folders, _files in os.walk(base):
            folders[:] = [f for f in folders if f not in ("node_modules", ".git")]
            rel = os.path.relpath(directory, base)
            for folder in folders:
                path = os.path.join(rel, folder).replace(os.sep, "/")
                if folder == "__pycache__":
                    found["bytecode_dirs"].append(f"{setup}/{path}")
                elif folder == "target" and os.path.isfile(os.path.join(directory, "Cargo.toml")):
                    found["build_dirs"].append(f"{setup}/{path}")
                elif folder in ("dist", "out") and os.path.isfile(os.path.join(directory, "package.json")):
                    found["build_dirs"].append(f"{setup}/{path}")
    found["build_dirs"].sort()
    found["bytecode_dirs"].sort()
    save(found, os.path.join(work, "out", "safety.json"))
    print("build output:", len(found["build_dirs"]), "bytecode caches:", len(found["bytecode_dirs"]))


def versions(work: str) -> None:
    def package(name: str) -> str:
        return load(os.path.join(work, "tools_node", "node_modules", name, "package.json"))["version"]

    def first_line(command: list[str], env: dict | None = None) -> str:
        return run(command, work, env)[0].strip().splitlines()[0]

    toolchain = os.listdir(os.path.join(work, "rust", "rustup", "toolchains"))[0]
    data = {
        "wingbox": first_line([os.path.join(work, "venv_wb", "bin", "wingbox"), "--version"]).split()[-1],
        "codegraph": first_line([os.path.join(work, "cg", "node_modules", ".bin", "codegraph"), "--version"],
                                dict(CODEGRAPH_ENV, HOME=os.path.join(work, "cg_home"))).split()[-1],
        "graphify": first_line([os.path.join(work, "venv_gf", "bin", "graphify"), "--version"]).split()[-1],
        "typescript": package("typescript"),
        "scip-typescript": package("@sourcegraph/scip-typescript"),
        "scip-python": package("@sourcegraph/scip-python"),
        "pyright": package("pyright"),
        "rust-analyzer": first_line([os.path.join(work, "ra", "rust-analyzer"), "--version"]),
        "rust toolchain": first_line([os.path.join(work, "rust", "rustup", "toolchains", toolchain, "bin", "rustc"), "--version"]),
        "date": time.strftime("%Y-%m-%d"),
    }
    save(data, os.path.join(work, "out", "versions.json"))
    print(data)


# ---------------------------------------------------------------- scoring
def score(named: set, required: set) -> dict:
    right = len(named & required)
    precision = right / len(named) if named else (1.0 if not required else 0.0)
    recall = right / len(required) if required else 1.0
    return {"named": len(named), "right": right, "outside": len(named - required), "missing": len(required - named),
            "precision": round(precision, 4), "recall": round(recall, 4), "exact": named == required,
            "complete": required <= named}


def score_sites(named: set[tuple], reference: set[tuple]) -> dict:
    """Use sites scored by (file, line); a site the tool names without a line counts for nothing."""
    lines = {(file, line) for file, line, _column in named if line}
    return score(lines, {(file, line) for file, line, _column in reference})


def score_columns(named: set[tuple], reference: set[tuple]) -> dict:
    """Of the reference sites whose line the tool names, how many it names with the same column."""
    by_line = collections.defaultdict(set)
    for file, line, column in named:
        if line and column:
            by_line[(file, line)].add(column)
    on_named_lines = [(file, line, column) for file, line, column in reference if (file, line) in by_line]
    same = sum(1 for file, line, column in on_named_lines if column in by_line[(file, line)])
    return {"reference_sites_on_named_lines": len(on_named_lines), "same_column": same}


def results(work: str, target: str) -> None:
    meta = load(os.path.join(work, "bench", "questions.json"))
    reference_sites = load(os.path.join(work, "out", "sites.json"))
    answers = {tool: load(os.path.join(work, "out", tool + ".json")) for tool in TOOLS}
    shutil.rmtree(os.path.join(target, "gold"), ignore_errors=True)
    os.makedirs(os.path.join(target, "gold"))
    rows = []
    for question in meta["questions"]:
        qid = question["id"]
        gold = load(os.path.join(work, "bench", "gold", qid + ".json"))
        gold["scip"] = reference_sites[qid]
        save(gold, os.path.join(target, "gold", qid + ".json"))
        required = set(gold["files"])
        expected = {tuple(site) for site in reference_sites[qid]["sites"]}
        row = {**question, "reference_files": len(required), "reference_sites": len(expected),
               "reference_lines": len({(f, l) for f, l, _c in expected})}
        for tool in TOOLS:
            answer = answers[tool][qid]
            named = {tuple(site) for site in answer["sites"] if site[0] != question["file"]}
            files = {site[0] for site in named}
            row[tool] = {"resolved": answer["resolved"], "tokens": answer["chars"] // 4,
                         "files": score(files, required), "sites": score_sites(named, expected),
                         "files_outside": sorted(files - required), "files_missing": sorted(required - files),
                         "command": answer["command"]}
            if tool in ("wingbox", "codegraph"):
                row[tool]["columns"] = score_columns(named, expected)
            if tool == "wingbox":
                row[tool]["unresolved_calls"] = answer["unresolved"]
        rows.append(row)
    save(meta, os.path.join(target, "questions.json"))
    save(rows, os.path.join(target, "rows.json"))
    for name in ("versions.json", "commits.txt", "safety.json"):
        source = os.path.join(work, "out", name)
        if os.path.exists(source):
            shutil.copy(source, os.path.join(target, name))
    print("written", target)


if __name__ == "__main__":
    work_dir, action = os.path.abspath(sys.argv[1]), sys.argv[2]
    if action == "candidates":
        candidates(work_dir)
    elif action == "questions":
        questions(work_dir)
    elif action == "sites":
        sites(work_dir)
    elif action == "wingbox":
        wingbox(work_dir)
    elif action == "codegraph":
        codegraph(work_dir)
    elif action == "graphify":
        graphify(work_dir, int(sys.argv[3]))
    elif action == "safety":
        safety(work_dir)
    elif action == "versions":
        versions(work_dir)
    elif action == "results":
        results(work_dir, os.path.abspath(sys.argv[3]))
    else:
        raise SystemExit("action: candidates, questions, sites, wingbox, codegraph, graphify, safety, versions or results")
