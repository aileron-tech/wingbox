"""The terms of a baseline entry: who accepted a rule break, why, and until when.

An entry of `.wingbox/baseline.sexp` may carry three optional keywords, filled
by the person who accepts the break:

  (baseline <part> <source> <target> :rule "<from -> to>" :from align
            [:owner "<who>"] [:reason "<why>"] [:expires YYYY-MM-DD])
  (baseline-capability <part> <module> <capability> :line <n> :from align
            [:owner "<who>"] [:reason "<why>"] [:expires YYYY-MM-DD])

`:expires` is the last day the entry hides its finding. From the next day on
the finding is a violation again, and so is one whose date cannot be read: an
entry never hides a finding by a date nobody can check. Pure functions; the
day is passed in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .sexp import keyword, parse, q

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TERM_KEYWORDS = ("owner", "reason", "expires")


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
        return bool(self.expires) and (not _DATE.match(self.expires) or self.expires < today)


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


def read_baseline_terms(text: str) -> Mapping[BaselineKey, Acceptance]:
    """The stated terms of each entry of a baseline file; entries without terms are left out."""
    found: dict[BaselineKey, Acceptance] = {}
    for line in text.splitlines():
        tokens = parse(line)
        if len(tokens) >= 4 and tokens[0] in ("baseline", "baseline-capability"):
            terms = terms_of(tokens[4:])
            if terms.stated():
                found[(tokens[0], tokens[1], tokens[2], tokens[3])] = terms
    return found
