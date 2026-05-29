"""Contractor performance tests (CM-36 AC2)."""

from __future__ import annotations

from agents.analytics.performance import score_contractors


def test_resolution_rate_and_response_time(make_ticket) -> None:  # noqa: ANN001
    tickets = [
        make_ticket(vendor_id="v1", status="Resolved", resolved_hours_after=2),
        make_ticket(vendor_id="v1", status="Resolved", resolved_hours_after=4),
        make_ticket(vendor_id="v1", status="New"),  # unresolved
    ]
    scores = score_contractors(tickets)
    assert len(scores) == 1
    s = scores[0]
    assert s.vendor_id == "v1"
    assert s.jobs == 3
    assert s.resolved == 2
    assert s.resolution_rate == round(2 / 3, 3)
    assert s.avg_response_hours == 3.0  # (2 + 4) / 2
    assert s.status == "ok"


def test_insufficient_data_without_resolved_at(make_ticket) -> None:  # noqa: ANN001
    tickets = [make_ticket(vendor_id="v2", status="Resolved")]  # no resolved_at
    scores = score_contractors(tickets)
    assert scores[0].status == "insufficient_data"
    assert scores[0].avg_response_hours is None


def test_tickets_without_vendor_are_ignored(make_ticket) -> None:  # noqa: ANN001
    assert score_contractors([make_ticket(vendor_id=None)]) == []


def test_sorted_by_resolution_rate(make_ticket) -> None:  # noqa: ANN001
    tickets = [
        make_ticket(vendor_id="low", status="New", resolved_hours_after=None),
        make_ticket(vendor_id="high", status="Resolved", resolved_hours_after=1),
    ]
    scores = score_contractors(tickets)
    assert [s.vendor_id for s in scores] == ["high", "low"]
