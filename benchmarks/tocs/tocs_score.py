"""Turn Wingbox graphs of ToCS codebases into ToCS cognitive maps and score them with ToCS's own metric.

Usage: python3 -I tocs_score.py <ToCS checkout> <bench dir> <label> <out dir>

<bench dir>/<codebase>/ holds a copy of one generated codebase without its ground_truth.json and the
graph that Wingbox built there (.wingbox/index.sexp, .code_graph.sexp before 0.5.0); the ground truth is read from <bench dir>/../truth/<codebase>.json.
Writes <out dir>/maps/<label>/<codebase>.json (a ToCS CognitiveMap) and <out dir>/scores.<label>.json.
The scores come from metrics/map_accuracy.py of the ToCS checkout (edge_prf); nothing of the metric is
recomputed here. Counts of true, predicted and gold edges and the lists of missed edges are only reported.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

tocs_dir, bench_dir, label, out_dir = sys.argv[1:5]
sys.path.insert(0, os.path.abspath(tocs_dir))
from metrics.map_accuracy import edge_prf  # noqa: E402
from models import BeliefEdge, CodebaseGroundTruth, CognitiveMap, ComponentBelief, ModuleStatus  # noqa: E402

TYPES = ("IMPORTS", "CALLS_API", "REGISTRY_WIRES", "DATA_FLOWS_TO")


def wingbox_type(kind: str, flags: list[str]) -> str | None:
    """The ToCS edge type that a Wingbox fact means, or None when no ToCS type means the same.

    imports         -> IMPORTS         an import statement (or a literal importlib name)
    registry-wires  -> REGISTRY_WIRES  a module loaded by a name read from a configuration file
    calls           -> CALLS_API       a function or method of the target called from the source
    data-flow :pipeline -> DATA_FLOWS_TO  the target step consumes the output of the source step
    Not mapped: calls-api (Wingbox: the source implements a base class of the target), data-flow
    without :pipeline (the source passes data to a validation module it imports), refs (a name is used).
    """
    if kind == "imports":
        return "IMPORTS"
    if kind == "registry-wires":
        return "REGISTRY_WIRES"
    if kind == "calls":
        return "CALLS_API"
    if kind == "data-flow" and ":pipeline" in flags:
        return "DATA_FLOWS_TO"
    return None


LEAF = re.compile(r"^\((\S+) (.+)\)$")
# The graph file of a build: .wingbox/index.sexp since 0.5.0, .code_graph.sexp for the earlier
# version that run.sh scores as "before".
GRAPH_FILES = (os.path.join(".wingbox", "index.sexp"), ".code_graph.sexp")


def graph_file(root: str) -> str | None:
    return next((os.path.join(root, name) for name in GRAPH_FILES if os.path.isfile(os.path.join(root, name))), None)


def read_graph(root: str) -> tuple[list[str], list[tuple[str, str, str, list[str]]]]:
    """Module ids and edge facts (kind, source module, target module, flags) of a .wingbox/index.sexp."""
    id_to_path: dict[str, str] = {}
    facts: list[tuple[str, str, str, list[str]]] = []
    section, stack, in_tree = "", [], False
    path = graph_file(root)
    assert path is not None, root
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            s = line.strip()
            if s.startswith("; ---"):
                section = s.strip("; -").strip()
                continue
            if section == "paths":
                declared = re.match(r'^\(def (\S+) "(.*)"\)$', s)
                if declared:
                    id_to_path[declared.group(1)] = declared.group(2)
                elif s == "(paths":
                    in_tree = True
                elif not in_tree or not s:
                    continue
                elif s == ")":
                    if stack:
                        stack.pop()
                    else:
                        in_tree = False
                elif LEAF.match(s):
                    sid, name = LEAF.match(s).groups()
                    name = name.strip('"')
                    id_to_path[sid] = "/".join(stack) if name == ":self" else "/".join(stack + [name])
                elif s.startswith("(") and not s.endswith(")"):
                    stack.append(s[1:].strip('"'))
                else:
                    raise SystemExit(f"unparsed paths line: {s!r}")
            elif section == "edges" and s and not s.startswith(";"):
                parts = s.strip("()").split()
                source, target = (id_to_path.get(p.split("::")[0], p.split("::")[0]) for p in parts[1:3])
                facts.append((parts[0], source, target, [p for p in parts[3:] if p.startswith(":")]))
    return sorted(id_to_path.values()), facts


def cognitive_map(root: str) -> CognitiveMap:
    modules, facts = read_graph(root)
    files = {module: module + ".py" for module in modules if os.path.isfile(os.path.join(root, module + ".py"))}
    edges: dict[str, dict[tuple[str, str], BeliefEdge]] = {path: {} for path in files.values()}
    for kind, source, target, flags in facts:
        edge_type = wingbox_type(kind, flags)
        if edge_type is None or source not in files or target not in files or source == target:
            continue
        edges[files[source]][(files[target], edge_type)] = BeliefEdge(target=files[target], type=edge_type, confidence=1.0)
    components = {
        path: ComponentBelief(filepath=path, status=ModuleStatus.OBSERVED, edges=list(by_key.values()), confidence=1.0)
        for path, by_key in sorted(edges.items())
    }
    return CognitiveMap(step=0, components=components)


def of_type(cmap: CognitiveMap, gt: CodebaseGroundTruth, edge_type: str) -> tuple[CognitiveMap, CodebaseGroundTruth]:
    """The map and the ground truth restricted to one edge type, for ToCS's own edge_prf."""
    typed_map = cmap.model_copy(deep=True)
    for component in typed_map.components.values():
        component.edges = [edge for edge in component.edges if edge.type.value == edge_type]
    typed_gt = gt.model_copy(update={"dependency_edges": [e for e in gt.dependency_edges if e["type"] == edge_type]})
    return typed_map, typed_gt


def code_facts(root: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Pairs of files (source, target) with an import statement, and with a call of an imported name in a function.

    Read with Python's ast, independently of Wingbox, to explain the differences with the ground truth.
    """
    imports: set[tuple[str, str]] = set()
    calls: set[tuple[str, str]] = set()
    for folder, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            source = os.path.relpath(path, root)
            tree = ast.parse(open(path, encoding="utf-8").read())
            bound: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    base = os.path.dirname(path)
                    for _ in range(node.level - 1):
                        base = os.path.dirname(base)
                    base = os.path.join(base, *(node.module or "").split("."))
                    for alias in node.names:
                        for candidate in (os.path.join(base, alias.name + ".py"), base + ".py", os.path.join(base, "__init__.py")):
                            if os.path.isfile(candidate):
                                target = os.path.relpath(candidate, root)
                                imports.add((source, target))
                                bound[alias.asname or alias.name] = target
                                break
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(function):
                        if isinstance(node, ast.Call):
                            head = node.func
                            while isinstance(head, ast.Attribute):
                                head = head.value
                            if isinstance(head, ast.Name) and head.id in bound:
                                calls.add((source, bound[head.id]))
    return {pair for pair in imports if pair[0] != pair[1]}, {pair for pair in calls if pair[0] != pair[1]}


def triples(cmap: CognitiveMap) -> set[tuple[str, str, str]]:
    return {(path, edge.target, edge.type.value) for path, comp in cmap.components.items() for edge in comp.edges}


rows = []
truth_dir = os.path.join(os.path.dirname(os.path.abspath(bench_dir)), "truth")
os.makedirs(os.path.join(out_dir, "maps", label), exist_ok=True)
for name in sorted(os.listdir(bench_dir)):
    root = os.path.join(bench_dir, name)
    if graph_file(root) is None:
        continue
    with open(os.path.join(truth_dir, name + ".json"), encoding="utf-8") as handle:
        gt = CodebaseGroundTruth.model_validate_json(handle.read())
    cmap = cognitive_map(root)
    with open(os.path.join(out_dir, "maps", label, name + ".json"), "w", encoding="utf-8") as handle:
        handle.write(cmap.model_dump_json(indent=1) + "\n")
    p, r, f1 = edge_prf(cmap, gt)
    predicted, gold = triples(cmap), {(e["source"], e["target"], e["type"]) for e in gt.dependency_edges}
    per_type = {}
    for edge_type in TYPES:
        tp_, tr_, tf_ = edge_prf(*of_type(cmap, gt, edge_type))
        pred_t = {t for t in predicted if t[2] == edge_type}
        gold_t = {t for t in gold if t[2] == edge_type}
        per_type[edge_type] = {
            "precision": round(tp_, 4), "recall": round(tr_, 4), "f1": round(tf_, 4),
            "true": len(pred_t & gold_t), "predicted": len(pred_t), "gold": len(gold_t),
            "missed": sorted(list(t[:2]) for t in gold_t - pred_t),
            "extra": sorted(list(t[:2]) for t in pred_t - gold_t),
        }
    code_imports, code_calls = code_facts(root)
    gold_imports = {(a, b) for a, b, t in gold if t == "IMPORTS"}
    gold_calls = {(a, b) for a, b, t in gold if t == "CALLS_API"}
    diagnostics = {
        "imports_in_code_not_in_gold": len(code_imports - gold_imports),
        "calls_api_gold_with_import_in_code": len(gold_calls & code_imports),
        "calls_api_gold_with_direct_call_in_code": len(gold_calls & code_calls),
        "direct_calls_in_code": len(code_calls),
        "direct_calls_in_code_not_calls_api_gold": len(code_calls - gold_calls),
    }
    rows.append({
        "codebase": name, "diagnostics": diagnostics, "complexity": gt.complexity.value, "modules": len(gt.modules),
        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4), "per_type": per_type,
    })
    print(f"{label:8} {name:16} P {p:.3f} R {r:.3f} F1 {f1:.3f}  "
          + "  ".join(f"{t[:4]} {v['true']}/{v['gold']}+{v['predicted'] - v['true']}" for t, v in per_type.items()))
with open(os.path.join(out_dir, f"scores.{label}.json"), "w", encoding="utf-8") as handle:
    json.dump(rows, handle, indent=1)
    handle.write("\n")
