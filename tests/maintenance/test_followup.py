"""CM-46: the follow-up predicate ``dedup.find_resolved_recurrence``.

A follow-up is a fresh report that recurs against a previously-RESOLVED issue —
distinct from an open duplicate (which short-circuits ticket creation).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.maintenance import dedup
from agents.maintenance.schema import Priority, Ticket, TicketStatus

NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


def _ticket(
    *,
    status: TicketStatus,
    unit: str = "4b",
    issue: str = "kitchen sink leaking",
    category: str = "plumbing",
    age_days: float = 1.0,
) -> Ticket:
    created = NOW - timedelta(days=age_days)
    return Ticket(
        id="TKT-X",
        tenant_id="t-1",
        unit=unit,
        issue_text=issue,
        category=category,
        priority=Priority.P3,
        status=status,
        created_at=created,
        updated_at=created,
        resolved_at=created if status is TicketStatus.RESOLVED else None,
    )


def _find(existing: list[Ticket]) -> Ticket | None:
    return dedup.find_resolved_recurrence(
        unit="4b", issue_text="the sink is leaking again", existing=existing, now=NOW
    )


def test_recurs_against_resolved_in_window() -> None:
    assert _find([_ticket(status=TicketStatus.RESOLVED)]) is not None


def test_open_duplicate_is_not_a_followup() -> None:
    assert _find([_ticket(status=TicketStatus.NEW)]) is None
    assert _find([_ticket(status=TicketStatus.IN_PROGRESS)]) is None


def test_resolved_out_of_window_is_not_a_followup() -> None:
    assert _find([_ticket(status=TicketStatus.RESOLVED, age_days=30)]) is None


def test_different_category_is_not_a_followup() -> None:
    # An electrical resolved ticket is not a follow-up for a plumbing recurrence.
    assert _find([_ticket(status=TicketStatus.RESOLVED, issue="the lights are out")]) is None


def test_unknown_unit_never_matches() -> None:
    result = dedup.find_resolved_recurrence(
        unit=dedup.UNKNOWN_UNIT,
        issue_text="leaking again",
        existing=[_ticket(status=TicketStatus.RESOLVED)],
        now=NOW,
    )
    assert result is None


def test_returns_most_recent_resolved_match() -> None:
    older = _ticket(status=TicketStatus.RESOLVED, age_days=5)
    older = older.model_copy(update={"id": "TKT-OLD"})
    newer = _ticket(status=TicketStatus.RESOLVED, age_days=1)
    newer = newer.model_copy(update={"id": "TKT-NEW"})
    match = _find([older, newer])
    assert match is not None and match.id == "TKT-NEW"
