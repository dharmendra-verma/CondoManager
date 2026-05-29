"""Analyzer tests (CM-36) — recurring / contractor / sentiment / predictive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.analytics.analyze import (
    detect_recurring,
    predictive_flags,
    score_contractors,
    sentiment_trend,
)
from agents.analytics.models import EscalationEvent
from agents.maintenance.schema import Priority, Ticket, TicketStatus

# A Monday, so ISO-week bucketing is easy to reason about.
NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _ticket(
    unit: str,
    category: str,
    *,
    created: datetime,
    updated: datetime | None = None,
    owner: str | None = None,
    status: TicketStatus = TicketStatus.NEW,
) -> Ticket:
    return Ticket(
        id=f"TKT-{unit}-{category}-{created.isoformat()}",
        tenant_id="t-1",
        unit=unit,
        issue_text="x",
        category=category,
        priority=Priority.P3,
        status=status,
        owner=owner,
        created_at=created,
        updated_at=updated or created,
    )


# --- detect_recurring --------------------------------------------------------


def test_recurring_flags_more_than_three_in_window() -> None:
    tickets = [
        _ticket("1A", "plumbing", created=NOW - timedelta(days=d))
        for d in (1, 5, 10, 20)  # 4 occurrences in 30d
    ]
    issues = detect_recurring(tickets, now=NOW)
    assert len(issues) == 1
    assert issues[0].unit == "1A"
    assert issues[0].category == "plumbing"
    assert issues[0].count == 4


def test_recurring_strict_boundary_three_not_flagged() -> None:
    tickets = [
        _ticket("2B", "electrical", created=NOW - timedelta(days=d)) for d in (1, 2, 3)
    ]
    assert detect_recurring(tickets, now=NOW) == []


def test_recurring_excludes_outside_window_and_other_keys() -> None:
    tickets = [
        *[_ticket("1A", "plumbing", created=NOW - timedelta(days=d)) for d in (1, 2, 3)],
        _ticket("1A", "plumbing", created=NOW - timedelta(days=40)),  # too old
        _ticket("1A", "electrical", created=NOW - timedelta(days=4)),  # other category
    ]
    # Only 3 in-window plumbing for 1A -> not > 3.
    assert detect_recurring(tickets, now=NOW) == []


# --- score_contractors -------------------------------------------------------


def test_contractor_resolution_rate_and_response_time() -> None:
    tickets = [
        _ticket(
            "1A", "plumbing", created=NOW - timedelta(hours=10),
            updated=NOW - timedelta(hours=8), owner="alice", status=TicketStatus.RESOLVED,
        ),
        _ticket(
            "1B", "plumbing", created=NOW - timedelta(hours=6),
            updated=NOW - timedelta(hours=2), owner="alice", status=TicketStatus.RESOLVED,
        ),
        _ticket("1C", "plumbing", created=NOW, owner="alice", status=TicketStatus.NEW),
    ]
    scores = score_contractors(tickets)
    assert len(scores) == 1
    alice = scores[0]
    assert alice.owner == "alice"
    assert alice.assigned == 3
    assert alice.resolved == 2
    assert alice.resolution_rate == round(2 / 3, 3)
    # response hours = (2h + 4h) / 2 = 3.0
    assert alice.avg_response_hours == 3.0


def test_contractor_unassigned_bucket() -> None:
    scores = score_contractors([_ticket("1A", "plumbing", created=NOW)])
    assert scores[0].owner == "unassigned"
    assert scores[0].avg_response_hours is None  # nothing resolved


def test_contractor_empty() -> None:
    assert score_contractors([]) == []


# --- sentiment_trend ---------------------------------------------------------


def _esc(days_ago: int, *, severity: str = "high", legal: bool = False) -> EscalationEvent:
    return EscalationEvent(
        ts=NOW - timedelta(days=days_ago), severity=severity, legal_risk=legal
    )


def test_sentiment_trend_buckets_and_direction_rising() -> None:
    # NOW is Monday 2026-05-25; ISO weeks run Mon–Sun. days_ago=2 (Sat 05-23)
    # falls in the *previous* week (Mon 05-18). Two events this week, one last.
    events = [
        _esc(0, severity="critical", legal=True),  # this week (Mon 05-25)
        _esc(0),  # this week
        _esc(2),  # last week (Sat 05-23 -> week of Mon 05-18)
    ]
    trend = sentiment_trend(events, now=NOW, weeks=4)
    assert len(trend.points) == 4
    assert trend.points[-1].week_start == "2026-05-25"
    assert trend.points[-1].escalations == 2  # this week
    assert trend.points[-1].critical == 1
    assert trend.points[-1].legal == 1
    assert trend.points[-2].escalations == 1  # last week
    assert trend.direction == "rising"


def test_sentiment_trend_empty_is_flat() -> None:
    trend = sentiment_trend([], now=NOW, weeks=4)
    assert trend.direction == "flat"
    assert all(p.escalations == 0 for p in trend.points)


# --- predictive_flags --------------------------------------------------------


def test_predictive_flag_fires_on_threshold() -> None:
    tickets = [
        _ticket("3C", "boiler", created=NOW - timedelta(days=d)) for d in (1, 3, 6)
    ]
    flags = predictive_flags(tickets, now=NOW)
    assert len(flags) == 1
    assert flags[0].unit == "3C"
    assert flags[0].category == "boiler"
    assert flags[0].count_in_window == 3
    assert "boiler" in flags[0].message


def test_predictive_flag_respects_window() -> None:
    tickets = [
        _ticket("3C", "boiler", created=NOW - timedelta(days=d)) for d in (1, 3, 20)
    ]
    # Only 2 within the 14-day window -> below threshold.
    assert predictive_flags(tickets, now=NOW) == []
