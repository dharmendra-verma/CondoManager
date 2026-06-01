"""Deterministic analytics over tickets + escalations (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

Pure functions — no I/O, no LLM. Each maps to one AC and is exhaustively
unit-tested with synthetic fixtures. The Cosmos reader + Function App supply
the inputs; these just count.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from agents.maintenance.schema import Ticket, TicketStatus

from .models import (
    ContractorScore,
    EscalationEvent,
    FollowupRate,
    PredictiveFlag,
    RecurringIssue,
    SentimentPoint,
    SentimentTrend,
    TtmBaseline,
)

#: AC #1 — "same problem class + same location, >3 occurrences in 30 days".
RECURRING_WINDOW_DAYS = 30
RECURRING_MIN_OCCURRENCES = 3  # strict greater-than (4+ trips it)

#: AC #4 — forward-looking threshold rule over a shorter window.
PREDICTIVE_WINDOW_DAYS = 14
PREDICTIVE_THRESHOLD = 3  # >= this many in the window flags it

#: CM-46 — window for the TTM / follow-up outcome baselines.
OUTCOME_WINDOW_DAYS = 30

UNASSIGNED = "unassigned"


def detect_recurring(
    tickets: list[Ticket],
    *,
    now: datetime,
    window_days: int = RECURRING_WINDOW_DAYS,
    min_occurrences: int = RECURRING_MIN_OCCURRENCES,
) -> list[RecurringIssue]:
    """Group tickets by (unit, category); flag groups with **> min_occurrences**
    in the trailing ``window_days`` (AC #1). Newest/most-frequent first."""
    cutoff = now - timedelta(days=window_days)
    groups: dict[tuple[str, str], list[Ticket]] = defaultdict(list)
    for t in tickets:
        if t.created_at >= cutoff:
            groups[(t.unit, t.category)].append(t)

    issues = [
        RecurringIssue(
            unit=unit,
            category=category,
            count=len(group),
            first_seen=min(t.created_at for t in group),
            last_seen=max(t.created_at for t in group),
        )
        for (unit, category), group in groups.items()
        if len(group) > min_occurrences
    ]
    issues.sort(key=lambda i: (-i.count, i.unit, i.category))
    return issues


def score_contractors(tickets: list[Ticket]) -> list[ContractorScore]:
    """Resolution rate + best-effort response time per ``owner`` (AC #2).

    Response time is still derived from ``updated_at - created_at`` for backward
    compatibility; CM-46 added ``Ticket.resolved_at`` (the precise resolution
    stamp) which the TTM baseline uses — switching this proxy over is a small
    future tidy left out of CM-46's scope.
    """
    groups: dict[str, list[Ticket]] = defaultdict(list)
    for t in tickets:
        groups[t.owner or UNASSIGNED].append(t)

    scores: list[ContractorScore] = []
    for owner, group in groups.items():
        resolved = [t for t in group if t.status == TicketStatus.RESOLVED]
        response_hours = [
            (t.updated_at - t.created_at).total_seconds() / 3600.0 for t in resolved
        ]
        avg_response = (
            round(sum(response_hours) / len(response_hours), 2)
            if response_hours
            else None
        )
        scores.append(
            ContractorScore(
                owner=owner,
                assigned=len(group),
                resolved=len(resolved),
                resolution_rate=round(len(resolved) / len(group), 3),
                avg_response_hours=avg_response,
            )
        )
    scores.sort(key=lambda s: (-s.assigned, s.owner))
    return scores


def _week_start(dt: datetime) -> str:
    """ISO date (Monday) of the week containing ``dt``."""
    monday = (dt - timedelta(days=dt.weekday())).date()
    return monday.isoformat()


def sentiment_trend(
    events: list[EscalationEvent], *, now: datetime, weeks: int = 4
) -> SentimentTrend:
    """Week-over-week escalation signal — the available sentiment proxy (AC #3).

    Tickets don't persist tone, so negative sentiment is approximated by
    escalation volume + severity + legal-risk count, bucketed by ISO week.
    Empty weeks are included so the direction is well-defined.
    """
    # The last `weeks` Monday buckets, oldest first.
    this_monday = now - timedelta(days=now.weekday())
    buckets = [
        _week_start(this_monday - timedelta(weeks=offset))
        for offset in reversed(range(weeks))
    ]
    counts: dict[str, list[int]] = {wk: [0, 0, 0] for wk in buckets}  # [esc, crit, legal]
    for e in events:
        wk = _week_start(e.ts)
        if wk in counts:
            counts[wk][0] += 1
            if e.severity == "critical":
                counts[wk][1] += 1
            if e.legal_risk:
                counts[wk][2] += 1

    points = [
        SentimentPoint(week_start=wk, escalations=c[0], critical=c[1], legal=c[2])
        for wk, c in ((wk, counts[wk]) for wk in buckets)
    ]

    direction: str = "flat"
    if len(points) >= 2:
        last, prev = points[-1].escalations, points[-2].escalations
        if last > prev:
            direction = "rising"
        elif last < prev:
            direction = "falling"
    return SentimentTrend(points=points, direction=direction)  # type: ignore[arg-type]


def predictive_flags(
    tickets: list[Ticket],
    *,
    now: datetime,
    window_days: int = PREDICTIVE_WINDOW_DAYS,
    threshold: int = PREDICTIVE_THRESHOLD,
) -> list[PredictiveFlag]:
    """Threshold rule: (unit, category) with >= ``threshold`` tickets in the
    trailing ``window_days`` is flagged as likely to need proactive service
    (AC #4 — e.g. "boiler likely to need service if X tickets in N days")."""
    cutoff = now - timedelta(days=window_days)
    groups: dict[tuple[str, str], int] = defaultdict(int)
    for t in tickets:
        if t.created_at >= cutoff:
            groups[(t.unit, t.category)] += 1

    flags = [
        PredictiveFlag(
            unit=unit,
            category=category,
            count_in_window=count,
            window_days=window_days,
            message=(
                f"Unit {unit} had {count} '{category}' tickets in the last "
                f"{window_days} days — likely to need proactive service."
            ),
        )
        for (unit, category), count in groups.items()
        if count >= threshold
    ]
    flags.sort(key=lambda f: (-f.count_in_window, f.unit, f.category))
    return flags


# --- CM-46 outcome-metric baselines ------------------------------------------


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated ``q``-quantile of an already-sorted, non-empty list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def ttm_baseline(
    tickets: list[Ticket],
    *,
    now: datetime,
    window_days: int = OUTCOME_WINDOW_DAYS,
) -> TtmBaseline:
    """Median/p90 time-to-mitigate over tickets RESOLVED in the window (CM-46).

    The baseline for the PRD's 80%-TTM-reduction target. Considers only tickets
    with a ``resolved_at`` inside the trailing ``window_days`` and ignores
    clock-skewed rows (``resolved_at < created_at``). With no qualifying data it
    returns ``n_resolved=0`` and ``None`` medians — "pending data", not a faked
    number.
    """
    cutoff = now - timedelta(days=window_days)
    durations_h = sorted(
        (t.resolved_at - t.created_at).total_seconds() / 3600.0
        for t in tickets
        if t.status == TicketStatus.RESOLVED
        and t.resolved_at is not None
        and t.resolved_at >= cutoff
        and t.resolved_at >= t.created_at
    )
    if not durations_h:
        return TtmBaseline(n_resolved=0, window_days=window_days)
    return TtmBaseline(
        n_resolved=len(durations_h),
        median_hours=round(_percentile(durations_h, 0.5), 2),
        p90_hours=round(_percentile(durations_h, 0.9), 2),
        window_days=window_days,
    )


def followup_rate(
    tickets: list[Ticket],
    *,
    now: datetime,
    window_days: int = OUTCOME_WINDOW_DAYS,
) -> FollowupRate:
    """Share of window-resolved tickets that saw a same-issue recurrence (CM-46).

    For each ticket resolved within ``window_days``, counts it as a follow-up
    when another ticket for the same ``(unit, category)`` was created after its
    ``resolved_at`` and within ``window_days`` of it. The baseline for the PRD's
    50%-follow-up-reduction target. ``n_resolved=0`` ⇒ ``rate`` is ``None``.
    """
    cutoff = now - timedelta(days=window_days)
    resolved = [
        t
        for t in tickets
        if t.status == TicketStatus.RESOLVED
        and t.resolved_at is not None
        and t.resolved_at >= cutoff
    ]
    if not resolved:
        return FollowupRate(n_resolved=0, n_followups=0, window_days=window_days)

    followups = 0
    for r in resolved:
        assert r.resolved_at is not None  # narrowed by the filter above
        horizon = r.resolved_at + timedelta(days=window_days)
        if any(
            t is not r
            and t.unit == r.unit
            and t.category == r.category
            and r.resolved_at <= t.created_at <= horizon
            for t in tickets
        ):
            followups += 1
    return FollowupRate(
        n_resolved=len(resolved),
        n_followups=followups,
        rate=round(followups / len(resolved), 3),
        window_days=window_days,
    )
