"""The two impact commands themselves, without an agent: files they name against the reference lists.

Usage: python3 -I command_check.py <work dir> <out.json>
Wingbox:  `wingbox impact <file> 2`                          (files under "Depth 1" and "Depth 2")
Graphify: `graphify affected "<file>" --depth 2`              (the file at the end of every result line)
          and the same with --relation imports_from, imports, re_exports, dynamic_import only
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

P, out_path = sys.argv[1:3]
BENCH = os.path.join(P, "bench")
IMPORT_RELATIONS = ["imports_from", "imports", "re_exports", "dynamic_import"]


def run(command: list[str], cwd: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ, **(env_extra or {}))
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=600).stdout


def score(files: set[str], gold: set[str], target: str) -> dict:
    files = files - {target}
    hit = len(files & gold)
    precision = hit / len(files) if files else 0.0
    recall = hit / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"files_named": len(files), "right": hit, "wrong": len(files - gold), "missing": len(gold - files),
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "missing_files": sorted(gold - files)[:40], "wrong_files": sorted(files - gold)[:40]}


rows = []
for name in sorted(os.listdir(os.path.join(BENCH, "gold"))):
    gold_data = json.load(open(os.path.join(BENCH, "gold", name)))
    question, gold = gold_data["question"], set(gold_data["gold"])
    repo, target = question["repo"], question["file"]
    text = run([os.path.join(P, "venv_wb", "bin", "wingbox"), "impact", target, "2"], os.path.join(BENCH, "wb", repo))
    files, section = set(), ""
    for line in text.split("\n"):
        if line.startswith("Depth "):
            section = "depth"
        elif line and not line.startswith(" "):
            section = ""
        elif section and line.strip():
            files.add(line.split()[0])
    row = {"question": question["id"], "repo": repo, "file": target, "reference_files": len(gold),
           "wingbox": {"lines": text.count("\n"), "chars": len(text), "tokens_chars_div_4": len(text) // 4, **score(files, gold, target)}}
    for label, extra in (("graphify", []), ("graphify_import_relations", [a for r in IMPORT_RELATIONS for a in ("--relation", r)])):
        text = run([os.path.join(P, "venv_gf", "bin", "graphify"), "affected", target, "--depth", "2", *extra],
                   os.path.join(BENCH, "gf", repo), {"GRAPHIFY_BACKEND": "none"})
        files = set(re.findall(r" (\S+?):L\d+\s*$", text, re.M))
        row[label] = {"lines": text.count("\n"), "chars": len(text), "tokens_chars_div_4": len(text) // 4, **score(files, gold, target)}
    rows.append(row)
    print(row["question"], repo, target, "reference", len(gold))
    for label in ("wingbox", "graphify", "graphify_import_relations"):
        r = row[label]
        print(f"   {label:26s} lines {r['lines']:5d} tokens {r['tokens_chars_div_4']:6d} | files {r['files_named']:4d} right {r['right']:3d} wrong {r['wrong']:4d} missing {r['missing']:3d} | P {r['precision']} R {r['recall']} F1 {r['f1']}")
json.dump(rows, open(out_path, "w"), indent=1)
