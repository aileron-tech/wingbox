"""The terms of a baseline entry: who accepted a rule break, why, and until when.

An entry of `.wingbox/baseline.sexp` may carry three optional keywords, filled
by the person who accepts the break:

  (baseline <part> <source> <target> :rule "<from -> to>" :from align
            [:owner "<who>"] [:reason "<why>"] [:expires YYYY-MM-DD])
  (baseline-capability <part> <module> <capability> :line <n> :from align
            [:owner "<who>"] [:reason "<why>"] [:expires YYYY-MM-DD])

`:expires` is the last day the entry hides its finding. From the next day on
the finding is a violation again, and so is one whose date cannot be read or
names no day of the calendar (2026-99-99): an entry never hides a finding by a
date nobody can check. Two entries for one finding keep the end date that
comes first: an end date is changed in its entry, not undone by another one.

The rule files a build reads (`rule_file_digests`): the graph records a digest
of each, so that a query can tell that the rules changed after the build.
Pure functions; the day and the reader of the files are passed in.
"""
from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from .sexp import keyword, parse, q

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TERM_KEYWORDS = ("owner", "reason", "expires")


def is_date(text: str) -> bool:
    """True for YYYY-MM-DD that names a day of the calendar."""
    if not _DATE.match(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Acceptance:
    """The terms of one entry; a field is "" when the entry does not state it."""

    owner: str = ""
    reason: str = ""
    expires: str = ""

    def stated(self) -> bool:
        return bool(self.owner or self.reason or self.expires)

    def expired(self, today: str) -> bool:
        """True from the day after `expires` (ISO dates compare as text), and for a date that cannot be read."""
        return bool(self.expires) and (not is_date(self.expires) or self.expires < today)


NO_TERMS = Acceptance()


def terms_of(tokens: Sequence[str]) -> Acceptance:
    """The terms among the tokens of one fact (`:owner`, `:reason`, `:expires`)."""
    items = list(tokens)
    return Acceptance(keyword(items, "owner", "") or "", keyword(items, "reason", "") or "",
                      keyword(items, "expires", "") or "")


def terms_sexp(terms: Acceptance) -> str:
    """` :owner "<who>" :reason "<why>" :expires <date>` with the stated fields only; "" without terms."""
    text = ""
    if terms.owner:
        text += f" :owner {q(terms.owner)}"
    if terms.reason:
        text += f" :reason {q(terms.reason)}"
    if terms.expires:
        text += f" :expires {terms.expires if _DATE.match(terms.expires) else q(terms.expires)}"
    return text


def expired_sexp(terms: Acceptance) -> str:
    """` :expired <date> [:owner "<who>"]` for a finding whose acceptance has ended."""
    date = terms.expires if _DATE.match(terms.expires) else q(terms.expires)
    return f" :expired {date}" + (f" :owner {q(terms.owner)}" if terms.owner else "")


BaselineKey = tuple[str, str, str, str]   # (baseline | baseline-capability, part, source or module, target or capability)


def _first_end(first: str, second: str) -> str:
    """Of two end dates the one that ends the acceptance first: a date that cannot be read (the entry is
    expired), else the earlier day; "" when neither entry states one."""
    stated = [date for date in (first, second) if date]
    return min(stated, key=lambda date: (is_date(date), date)) if stated else ""


def read_baseline_terms(text: str) -> Mapping[BaselineKey, Acceptance]:
    """The stated terms of each entry of a baseline file; entries without terms are left out.

    Several entries for one finding are read as one: the owner and the reason stated last, and the end date
    that comes first. An entry without an end date, or with a later one, does not undo an end date.
    """
    found: dict[BaselineKey, Acceptance] = {}
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] in ("baseline", "baseline-capability"):
            terms = terms_of(tokens[4:])
            if terms.stated():
                key = (tokens[0], tokens[1], tokens[2], tokens[3])
                before = found.get(key, NO_TERMS)
                found[key] = Acceptance(terms.owner or before.owner, terms.reason or before.reason,
                                        _first_end(before.expires, terms.expires))
    return found


# ---------------------------------------------------------------------------
# The rule files a build reads
# ---------------------------------------------------------------------------

CONFIG_FILE = ".wingbox/config.yaml"
BASELINE_FILE = ".wingbox/baseline.sexp"
DECISIONS_FILE = ".wingbox/decisions.sexp"
# The name of each digest in the graph, and the file a change of it is reported as.
RULE_FILES = (("config", CONFIG_FILE), ("baseline", BASELINE_FILE), ("learned", DECISIONS_FILE))
_LEARNED_LINES = ("(learned-layer ", "(learned-removed ")
NO_DIGEST = "-"


def _digest(text: Optional[str]) -> str:
    return NO_DIGEST if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def rule_file_digests(read_text: Callable[[str], Optional[str]]) -> tuple[tuple[str, str], ...]:
    """(name, digest) of what a build reads of each rule file; "-" for a file that is missing.

    Of the decisions log only the learned layers are rules (a build appends its own run and advice lines to
    that file), and only in a project without a config: with one, the config states the layers. A log
    without such a line counts as missing.
    """
    config = read_text(CONFIG_FILE)
    learned = [] if config is not None else [
        line.strip() for line in (read_text(DECISIONS_FILE) or "").splitlines() if line.strip().startswith(_LEARNED_LINES)
    ]
    return (("config", _digest(config)), ("baseline", _digest(read_text(BASELINE_FILE))),
            ("learned", _digest("\n".join(learned) if learned else None)))


def changed_rule_files(recorded: Mapping[str, str], now: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """The rule files whose digest differs from the one the graph recorded; none for a graph without a record."""
    if not recorded:
        return ()
    files = dict(RULE_FILES)
    return tuple(files[name] for name, digest in now if recorded.get(name, NO_DIGEST) != digest)
