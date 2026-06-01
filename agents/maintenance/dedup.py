"""Duplicate detection + the text helpers it depends on (CM-31 AC2).

A candidate issue is a **duplicate** of an existing ticket when ALL hold:

* both map to the SAME resolved unit (an ``unknown`` unit never matches —
  we will not auto-merge two unidentified-unit reports, which protects
  precision, the metric the AC6 eval grades);
* both fall in the same coarse :func:`categorize` bucket;
* their normalized-token Jaccard :func:`similarity` is >= ``SIMILARITY_THRESHOLD``;
* the existing ticket was created within ``DEDUP_WINDOW_DAYS`` of the candidate.

The predicate is deterministic (no LLM, no embeddings) so the dedup-precision
eval (``tests/maintenance/test_dedup_eval.py``) is reproducible in CI. An
embedding-based scorer can later slot in behind :func:`similarity` without
touching callers.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .schema import Ticket, TicketStatus

#: Dedup look-back window (CM-31 AC2: "within 7 days").
DEDUP_WINDOW_DAYS = 7

#: Minimum token-Jaccard for two same-category issues to count as the same.
#: Tuned so paraphrases match while two different same-category issues
#: (e.g. "toilet clogged" vs "sink leaking") stay below it.
SIMILARITY_THRESHOLD = 0.3

#: Sentinel unit when none can be extracted from the message text.
UNKNOWN_UNIT = "unknown"

# unit 4B | apt 12 | apartment 3C | suite 200 | #7A | no. 9
# The "#" form can't carry a leading \b (start-of-string + "#" is not a word
# boundary), so it gets its own branch separate from the word keywords.
_UNIT_RE = re.compile(
    r"(?:\b(?:unit|apt|apartment|suite|ste|flat|no)\.?\s*#?\s*|#\s*)([0-9]{1,4}[a-z]?)\b",
    re.IGNORECASE,
)

# Coarse issue categories keyed by trigger words. Order matters only for
# overlap; the first bucket with a hit wins. "general" is the fallback.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "plumbing": (
        "leak",
        "leaking",
        "water",
        "pipe",
        "drain",
        "faucet",
        "sink",
        "toilet",
        "flush",
        "clog",
        "clogged",
        "sewage",
        "tap",
        "shower",
    ),
    "electrical": (
        "power",
        "outlet",
        "socket",
        "light",
        "lights",
        "wiring",
        "breaker",
        "electric",
        "electrical",
        "spark",
        "fuse",
        "switch",
    ),
    "hvac": (
        "heat",
        "heating",
        "heater",
        "ac",
        "a/c",
        "air",
        "furnace",
        "boiler",
        "thermostat",
        "cooling",
        "vent",
        "hvac",
    ),
    "appliance": (
        "fridge",
        "refrigerator",
        "oven",
        "stove",
        "dishwasher",
        "washer",
        "dryer",
        "microwave",
        "appliance",
    ),
    "structural": (
        "door",
        "window",
        "wall",
        "ceiling",
        "floor",
        "roof",
        "lock",
        "crack",
        "broken",
    ),
    "pest": ("pest", "rodent", "mouse", "mice", "rat", "roach", "ants", "bug", "infestation"),
}

# Tokens with no discriminating value for similarity.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "in",
        "on",
        "at",
        "of",
        "to",
        "and",
        "or",
        "my",
        "our",
        "it",
        "this",
        "that",
        "there",
        "please",
        "help",
        "with",
        "for",
        "i",
        "we",
        "has",
        "have",
        "been",
        "very",
        "really",
        "now",
        "still",
        "again",
        "some",
        "from",
        "by",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def extract_unit(text: str) -> str:
    """Return a normalized unit token (e.g. ``"4b"``) or :data:`UNKNOWN_UNIT`.

    Normalized to lowercase with the keyword stripped so ``"Unit 4B"`` and
    ``"apt 4b"`` collide on the same partition-local key.
    """
    m = _UNIT_RE.search(text or "")
    if not m:
        return UNKNOWN_UNIT
    return m.group(1).lower()


def categorize(text: str) -> str:
    """Bucket an issue into a coarse maintenance category (or ``"general"``).

    Matches on whole word tokens, not substrings — so ``"air"`` does not fire
    on ``"upstairs"`` and ``"ac"`` does not fire on every word containing it.
    """
    tokens = set(re.findall(r"[a-z0-9/]+", (text or "").lower()))
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if tokens.intersection(keywords):
            return category
    return "general"


def _tokens(text: str) -> set[str]:
    """Content tokens: lowercase alphanumerics minus stopwords."""
    return {t for t in _WORD_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity over content tokens. Range ``[0.0, 1.0]``."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def is_duplicate_pair(
    *,
    existing_unit: str,
    existing_issue: str,
    candidate_unit: str,
    candidate_issue: str,
    age_days: float,
) -> bool:
    """Core dedup predicate — the function the AC6 precision eval grades.

    ``age_days`` is how long ago the *existing* ticket was created relative
    to the candidate. All other inputs are the two issues' unit + free text.
    """
    if candidate_unit == UNKNOWN_UNIT or existing_unit == UNKNOWN_UNIT:
        return False
    if candidate_unit != existing_unit:
        return False
    if age_days < 0 or age_days > DEDUP_WINDOW_DAYS:
        return False
    if categorize(candidate_issue) != categorize(existing_issue):
        return False
    return similarity(candidate_issue, existing_issue) >= SIMILARITY_THRESHOLD


def find_open_duplicate(
    *,
    unit: str,
    issue_text: str,
    existing: list[Ticket],
    now: datetime,
) -> Ticket | None:
    """Return the most recent OPEN (non-resolved) duplicate, or ``None``.

    Resolved tickets are not treated as open duplicates — a recurrence of a
    previously-closed issue should open a fresh ticket (and bump priority via
    :func:`is_repeat`), not silently attach to a closed one.
    """
    matches = [
        t
        for t in existing
        if t.status is not TicketStatus.RESOLVED
        and is_duplicate_pair(
            existing_unit=t.unit,
            existing_issue=t.issue_text,
            candidate_unit=unit,
            candidate_issue=issue_text,
            age_days=_age_days(t.created_at, now),
        )
    ]
    if not matches:
        return None
    return max(matches, key=lambda t: t.created_at)


def find_resolved_recurrence(
    *,
    unit: str,
    issue_text: str,
    existing: list[Ticket],
    now: datetime,
) -> Ticket | None:
    """Most recent RESOLVED same-unit/category ticket in the window — a follow-up.

    A *fresh* ticket that recurs against a previously **RESOLVED** issue is a
    follow-up contact: the fix didn't hold, or the tenant came back. This is the
    quantity the follow-up-reduction PRD outcome metric measures (CM-46), and is
    deliberately distinct from :func:`find_open_duplicate` (which only matches
    OPEN tickets and would short-circuit ticket creation). The window is the same
    created-at ``DEDUP_WINDOW_DAYS`` the rest of the module uses; ``resolved_at``
    now exists on the ticket and can later tighten this to "resolved within N
    days" without touching callers.
    """
    cat = categorize(issue_text)
    if unit == UNKNOWN_UNIT:
        return None
    matches = [
        t
        for t in existing
        if t.status is TicketStatus.RESOLVED
        and t.unit == unit
        and categorize(t.issue_text) == cat
        and 0 <= _age_days(t.created_at, now) <= DEDUP_WINDOW_DAYS
    ]
    if not matches:
        return None
    return max(matches, key=lambda t: t.created_at)


def is_repeat(
    *,
    unit: str,
    issue_text: str,
    existing: list[Ticket],
    now: datetime,
) -> bool:
    """True if any same-unit, same-category issue occurred within the window.

    Broader than :func:`find_open_duplicate`: includes RESOLVED tickets, so a
    recurring-but-previously-closed problem is recognized as a repeat and
    bumps priority (CM-31 AC3 "repeat-status").
    """
    cat = categorize(issue_text)
    if unit == UNKNOWN_UNIT:
        return False
    return any(
        t.unit == unit
        and categorize(t.issue_text) == cat
        and 0 <= _age_days(t.created_at, now) <= DEDUP_WINDOW_DAYS
        for t in existing
    )


def _age_days(created_at: datetime, now: datetime) -> float:
    """Whole + fractional days between ``created_at`` and ``now``."""
    delta: timedelta = now - created_at
    return delta.total_seconds() / 86400.0
