"""Contractor performance scoring (CM-36 AC2).

Per vendor: resolution rate (resolved / total) + average response time
(``resolved_at - created_at``). When the underlying fields aren't yet persisted
on tickets (``vendor_id`` from CM-35; ``resolved_at``), the score is flagged
``insufficient_data`` rather than reported as a real number — a board digest
must not invent contractor metrics.
"""

from __future__ import annotations

from collections import defaultdict

from .models import AnalyticsTicket, ContractorScore

_RESOLVED = "Resolved"


def score_contractors(tickets: list[AnalyticsTicket]) -> list[ContractorScore]:
    """Return per-vendor performance scores (vendor-attributed tickets only)."""
    by_vendor: dict[str, list[AnalyticsTicket]] = defaultdict(list)
    for t in tickets:
        if t.vendor_id:
            by_vendor[t.vendor_id].append(t)

    scores: list[ContractorScore] = []
    for vendor_id, items in by_vendor.items():
        jobs = len(items)
        resolved = sum(1 for t in items if t.status == _RESOLVED)
        response_hours = [
            (t.resolved_at - t.created_at).total_seconds() / 3600.0
            for t in items
            if t.resolved_at is not None
        ]
        avg_response = (
            round(sum(response_hours) / len(response_hours), 2) if response_hours else None
        )
        # If we can't see any resolution timestamps, the rate is the only signal
        # and the response time is unknown -> flag it.
        status = "ok" if response_hours else "insufficient_data"
        scores.append(
            ContractorScore(
                vendor_id=vendor_id,
                jobs=jobs,
                resolved=resolved,
                resolution_rate=round(resolved / jobs, 3) if jobs else 0.0,
                avg_response_hours=avg_response,
                status=status,
            )
        )
    scores.sort(key=lambda s: (-s.resolution_rate, s.vendor_id))
    return scores
