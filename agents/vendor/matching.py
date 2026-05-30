"""Vendor matching (CM-35 AC2): by category + availability + performance.

Deterministic and pure so the matching-quality eval is reproducible. A vendor
matches a ticket when it serves the ticket's category and is available on the
target day; matches are ranked by performance (descending), tie-broken by the
cheaper cost tier, then by id for stable ordering.

Pre-approval is NOT a matching filter — it feeds the dispatch decision (AC3),
so the best contractor is surfaced even when it would require manager approval.
"""

from __future__ import annotations

from datetime import datetime

from .schema import CostTier, Vendor, VendorMatch

#: Monday=0 … Sunday=6 -> abbreviation used in ``Vendor.available_days``.
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_COST_RANK: dict[CostTier, int] = {
    CostTier.LOW: 0,
    CostTier.MEDIUM: 1,
    CostTier.HIGH: 2,
}


def weekday_name(when: datetime) -> str:
    """Return the lowercase weekday abbreviation for ``when``."""
    return _WEEKDAYS[when.weekday()]


def match_vendors(category: str, when: datetime, vendors: list[Vendor]) -> list[VendorMatch]:
    """Return category- and availability-matched vendors, best first."""
    day = weekday_name(when)
    candidates = [v for v in vendors if category in v.categories and day in v.available_days]
    matches = [VendorMatch(vendor=v, score=v.performance_score) for v in candidates]
    matches.sort(key=lambda m: (-m.score, _COST_RANK[m.vendor.cost_tier], m.vendor.id))
    return matches
