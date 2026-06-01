"""CM-46: outcome-metric baselines — ``ttm_baseline`` + ``followup_rate``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.analytics import followup_rate, ttm_baseline
from agents.maintenance.schema import Priority, Ticket, TicketStatus

NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


def _ticket(
    *,
    ticket_id: str,
    status: TicketStatus = TicketStatus.RESOLVED,
    unit: str = "4b",
    category: str = "plumbing",
    created_days_ago: float = 5.0,
    resolved_hours_after: float | None = 2.0,
) -> Ticket:
    created = NOW - timedelta(days=created_days_ago)
    resolved = (
        created + timedelta(hours=resolved_hours_after)
        if resolved_hours_after is not None
        else None
    )
    return Ticket(
        id=ticket_id,
        tenant_id="t-1",
        unit=unit,
        issue_text="kitchen sink leaking",
        category=category,
        priority=Priority.P3,
        status=status,
        created_at=created,
        updated_at=resolved or created,
        resolved_at=resolved,
    )


def test_ttm_baseline_pending_when_no_resolved() -> None:
    unresolved = _ticket(ticket_id="T1", status=TicketStatus.NEW, resolved_hours_after=None)
    base = ttm_baseline([unresolved], now=NOW)
    assert base.n_resolved == 0
    assert base.median_hours is None
    assert base.p90_hours is None


def test_ttm_baseline_median_and_p90() -> None:
    tickets = [
        _ticket(ticket_id="T1", resolved_hours_after=1.0),
        _ticket(ticket_id="T2", resolved_hours_after=2.0),
        _ticket(ticket_id="T3", resolved_hours_after=3.0),
    ]
    base = ttm_baseline(tickets, now=NOW)
    assert base.n_resolved == 3
    assert base.median_hours == 2.0
    assert base.p90_hours == 2.8  # linear interp between 2.0 and 3.0 at q=0.9


def test_ttm_baseline_ignores_clock_skew_and_out_of_window() -> None:
    tickets = [
        _ticket(ticket_id="SKEW", resolved_hours_after=-1.0),  # resolved before created
        _ticket(ticket_id="OLD", created_days_ago=120, resolved_hours_after=2.0),  # out of window
        _ticket(ticket_id="GOOD", resolved_hours_after=4.0),
    ]
    base = ttm_baseline(tickets, now=NOW)
    assert base.n_resolved == 1
    assert base.median_hours == 4.0


def test_followup_rate_pending_when_no_resolved() -> None:
    rate = followup_rate([], now=NOW)
    assert rate.n_resolved == 0
    assert rate.rate is None


def test_followup_rate_counts_recurrence_after_resolution() -> None:
    # A ticket resolved 5 days ago, then a same-unit/category recurrence 4 days ago.
    resolved = _ticket(ticket_id="R1", created_days_ago=6, resolved_hours_after=24.0)
    recurrence = _ticket(
        ticket_id="R2", status=TicketStatus.NEW, created_days_ago=4, resolved_hours_after=None
    )
    # A second resolved ticket with no recurrence.
    lonely = _ticket(ticket_id="R3", unit="9c", created_days_ago=3, resolved_hours_after=1.0)

    rate = followup_rate([resolved, recurrence, lonely], now=NOW)
    assert rate.n_resolved == 2  # R1 + R3
    assert rate.n_followups == 1  # only R1 saw a recurrence
    assert rate.rate == 0.5
