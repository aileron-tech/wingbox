"""The decisions store: advice given, what the project did with it, and what was learned.

`.wingbox/decisions.sexp` is an append-only S-expression log, meant to be
committed so that a team shares it:

  (advice-given <id> :part <part> :kind <kind> :target "<path>" :rule "<rule>" [:layer <layer> :destination "<folder>"] :at <date> :graph <hash>)
  (advice-outcome <id> :followed :at <date>)
  (advice-outcome <id> :done-differently :actual "<path or what changed>" :at <date>)
  (build-run :graph <hash> :at <date>)                     ; one line per build
  (advice-outcome <id> :ignored :runs <n> :at <date>)       ; logged once, when it is hidden
  (learned-removed <part> "<folder>" :by config :at <date>) ; the config no longer has a learned folder
  (advice-outcome <id> :accepted :by user :at <date>)
  (advice-outcome <id> :rejected :by user :reason "<why>" :at <date>)
  (learned-layer <part> "<folder>" <layer> :from <id> :at <date>)

Every build (and init with a graph) compares the open advice with the
current advice and files: advice that is gone is followed (the folder exists,
the file sits in the advised folder, the violation is gone with its source in
place) or done differently (the file moved elsewhere: that folder is learned
as the advised layer for the part); advice still open counts one ignored run
when the graph differs from the one of the run before (a rebuild of the same
code is not a run in which the project left the advice aside).
An item rejected, or ignored for IGNORED_RUNS runs, is no longer shown; the
advice of the same kind and rule in the same part is ranked lower, and ranked
higher after a followed item. Advice to fix a finding (NEVER_HIDDEN) is not
hidden by inaction: it stays until the finding is fixed, accepted in the
baseline, or rejected with a reason. Pure functions; the command line reads
and appends the file.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .advice import Advice
from .sexp import keyword, parse, q

STORE_FILE = ".wingbox/decisions.sexp"
IGNORED_RUNS = 3
# A finding that `boundaries` still reports keeps its advice: only layout suggestions are hidden by inaction.
NEVER_HIDDEN = ("fix-violation", "fix-capability", "fix-pattern")
_TERMINAL = ("followed", "done-differently", "rejected")


@dataclass(frozen=True)
class Given:
    id: str
    part: str
    kind: str
    target: str
    rule: str
    layer: str
    destination: str


@dataclass(frozen=True)
class Learned:
    part: str
    folder: str
    layer: str
    source: str     # the advice id it was learned from


@dataclass
class Store:
    given: dict[str, Given] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)       # id -> last outcome
    ignored_runs: dict[str, int] = field(default_factory=dict)
    learned: list[Learned] = field(default_factory=list)
    runs: int = 0                                                 # builds so far, a rebuild of the same graph counted once
    last_graph: str = ""                                          # the graph hash of the last (build-run ...) line
    given_at_run: dict[str, int] = field(default_factory=dict)    # id -> runs when it was given


def read_store(text: str) -> Store:
    store = Store()
    for line in text.splitlines():
        tokens = parse(line)
        if not tokens:
            continue
        head = tokens[0]
        if head == "build-run":
            graph = keyword(tokens, "graph", "") or ""
            if _counts(store, graph):
                store.runs += 1
            store.last_graph = graph
        elif head == "advice-given" and len(tokens) > 1:
            store.given_at_run[tokens[1]] = store.runs
            store.given[tokens[1]] = Given(
                tokens[1], keyword(tokens, "part", "") or "", keyword(tokens, "kind", "") or "",
                keyword(tokens, "target", "") or "", keyword(tokens, "rule", "") or "",
                keyword(tokens, "layer", "") or "", keyword(tokens, "destination", "") or "",
            )
        elif head == "advice-outcome" and len(tokens) > 2:
            outcome = tokens[2].lstrip(":")
            store.outcomes[tokens[1]] = outcome
            if outcome == "ignored":
                store.ignored_runs[tokens[1]] = int(keyword(tokens, "runs", "0") or 0)
        elif head == "learned-layer" and len(tokens) > 3:
            store.learned.append(Learned(tokens[1], tokens[2], tokens[3], keyword(tokens, "from", "") or ""))
        elif head == "learned-removed" and len(tokens) > 2:
            store.learned = [item for item in store.learned if (item.part, item.folder) != (tokens[1], tokens[2])]
    return store


def _layer_of(advice: Advice) -> str:
    return advice.rule.rsplit(" -> ", 1)[-1] if advice.kind == "move" else ""


def given_line(advice: Advice, today: str, graph_hash: str) -> str:
    extra = ""
    if advice.kind == "move":
        extra = f" :layer {_layer_of(advice)} :destination {q(advice.destination)}"
    return (
        f"(advice-given {advice.id} :part {q(advice.part)} :kind {advice.kind} :target {q(advice.target)} "
        f":rule {q(advice.rule)}{extra} :at {today} :graph {graph_hash})"
    )


def _counts(store: Store, graph_hash: str) -> bool:
    """True when a build of this graph is a new run: the first one, or a graph unlike the one before."""
    return store.runs == 0 or not graph_hash or graph_hash != store.last_graph


def needs_reason(store: Store, advice_id: str) -> bool:
    """True for advice that only a stated reason may reject: the advice to fix a finding."""
    given = store.given.get(advice_id)
    return given is not None and given.kind in NEVER_HIDDEN


def _is_open(store: Store, advice_id: str) -> bool:
    return store.outcomes.get(advice_id) not in _TERMINAL


def _under(path: str, folder: str) -> bool:
    return bool(folder) and (path == folder or path.startswith(folder.rstrip("/") + "/"))


def reconcile(store: Store, current: Iterable[Advice], paths: tuple[str, ...], today: str, graph_hash: str) -> list[str]:
    """The lines to append after a run: new advice, outcomes of the open advice, learned layers."""
    current = list(current)
    now = {item.id for item in current}
    files = set(paths)
    folders = {path.rsplit("/", 1)[0] for path in paths if "/" in path}
    # Only changes of state are logged: one run line, new advice, outcomes, and the run in which an
    # open item has been ignored IGNORED_RUNS times (it is hidden from then on).
    this_run = store.runs + (1 if _counts(store, graph_hash) else 0)
    lines: list[str] = [f"(build-run :graph {graph_hash} :at {today})"]
    for item in current:
        if item.id not in store.given:
            lines.append(given_line(item, today, graph_hash))
    for advice_id, given in sorted(store.given.items()):
        if not _is_open(store, advice_id):
            continue
        if advice_id in now:
            outcome = store.outcomes.get(advice_id)
            runs = this_run - store.given_at_run.get(advice_id, this_run)
            if outcome not in ("accepted", "ignored") and runs >= IGNORED_RUNS and given.kind not in NEVER_HIDDEN:
                lines.append(f"(advice-outcome {advice_id} :ignored :runs {runs} :at {today})")
            continue
        lines.extend(_gone(given, files, folders, today))
    return lines


def _gone(given: Given, files: set[str], folders: set[str], today: str) -> list[str]:
    """Outcome of an advice item that the current run no longer gives."""
    if given.kind == "missing-folder":
        if any(_under(folder, given.target) for folder in folders):
            return [f"(advice-outcome {given.id} :followed :at {today})"]
        return [f"(advice-outcome {given.id} :done-differently :actual {q('the style no longer proposes it')} :at {today})"]
    if given.kind == "move":
        if given.target in files:
            return [f"(advice-outcome {given.id} :done-differently :actual {q('its dependents changed')} :at {today})"]
        name = given.target.rsplit("/", 1)[-1]
        moved = sorted(path for path in files if path.rsplit("/", 1)[-1] == name)
        if any(_under(path, given.destination) for path in moved):
            return [f"(advice-outcome {given.id} :followed :at {today})"]
        if moved:
            folder = moved[0].rsplit("/", 1)[0]
            relative = folder if given.part == "." else folder[len(given.part) + 1:]
            last = relative.rsplit("/", 1)[-1]
            return [
                f"(advice-outcome {given.id} :done-differently :actual {q(moved[0])} :at {today})",
                f"(learned-layer {q(given.part)} {q(last)} {given.layer} :from {given.id} :at {today})",
            ]
        return [f"(advice-outcome {given.id} :done-differently :actual {q('the file was removed')} :at {today})"]
    source = given.target
    if source in files:
        return [f"(advice-outcome {given.id} :followed :at {today})"]
    return [f"(advice-outcome {given.id} :done-differently :actual {q('the importing file moved or was removed')} :at {today})"]


def user_outcome(store: Store, advice_id: str, accepted: bool, reason: str, today: str) -> str | None:
    """The line for `wingbox advice accept|reject <id>`, or None when the id was never given."""
    if advice_id not in store.given:
        return None
    if accepted:
        return f"(advice-outcome {advice_id} :accepted :by user :at {today})"
    return f"(advice-outcome {advice_id} :rejected :by user :reason {q(reason)} :at {today})"


def visible(store: Store, current: Iterable[Advice]) -> list[Advice]:
    """Advice still worth showing, best first: rejected and long-ignored items are dropped (advice to fix
    a finding is dropped only when rejected); the advice of a kind and rule that the project rejected or
    ignored in a part is ranked lower, followed higher."""
    weight: Counter[tuple[str, str, str]] = Counter()
    for advice_id, outcome in store.outcomes.items():
        given = store.given.get(advice_id)
        if given is None:
            continue
        key = (given.part, given.kind, _rule_kind(given.rule))
        if outcome == "followed" or outcome == "accepted":
            weight[key] += 1
        elif outcome == "rejected" or (outcome == "ignored" and given.kind not in NEVER_HIDDEN
                                       and store.ignored_runs.get(advice_id, 0) >= IGNORED_RUNS):
            weight[key] -= 1
    # A store written before fix advice stopped being hidden may hold `:ignored` for it: it is shown again.
    shown = [item for item in current if store.outcomes.get(item.id) != "rejected"
             and (store.outcomes.get(item.id) != "ignored" or item.kind in NEVER_HIDDEN)]
    order = {item.id: index for index, item in enumerate(shown)}
    return sorted(shown, key=lambda item: (-weight[(item.part, item.kind, _rule_kind(item.rule))], order[item.id]))


def _rule_kind(rule: str) -> str:
    """The rule without the target name: "target domain/value-objects" -> "target domain"."""
    return rule.split("/", 1)[0]


def learned_layers(store: Store) -> dict[str, list[Learned]]:
    by_part: dict[str, list[Learned]] = {}
    for item in store.learned:
        by_part.setdefault(item.part, []).append(item)
    return by_part
