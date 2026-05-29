"""Recurring-issue detection (CM-36 AC1).

A ``(tenant_id, unit, category)`` group is *recurring* when it has **more than**
``min_occurrences`` (default 3, i.e. ≥ 4) tickets created within ``window_days``
(default 30) of ``now``. Deterministic and pure.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .models import AnalyticsTicket, RecurringIssue

DEFAULT_WINDOW_DAYS = 30
DEFAULT_MIN_OCCURRENCES = 3


def detect_recurring(
    tickets: list[AnalyticsTicket],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
) -> list[RecurringIssue]:
    """Return recurring ``(unit, category)`` issues per building."""
    cutoff = now - timedelta(days=window_days)
    groups: dict[tuple[str, str, str], list[AnalyticsTicket]] = defaultdict(list)
    for t in tickets:
        if t.created_at >= cutoff:
            groups[(t.tenant_id, t.unit, t.category)].append(t)

    issues: list[RecurringIssue] = []
    for (tenant_id, unit, category), items in groups.items():
        if len(items) > min_occurrences:
            times = sorted(t.created_at for t in items)
            issues.append(
                RecurringIssue(
                    tenant_id=tenant_id,
                    unit=unit,
                    category=category,
                    count=len(items),
                    first_seen=times[0],
                    last_seen=times[-1],
                )
            )
    # Most-frequent first, then stable by location for deterministic output.
    issues.sort(key=lambda i: (-i.count, i.tenant_id, i.unit, i.category))
    return issues
