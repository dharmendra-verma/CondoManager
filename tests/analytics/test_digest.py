"""Digest composition tests (CM-36 AC5)."""

from __future__ import annotations

from agents.analytics.digest import build_digest


def test_digest_composes_all_sections(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="plumbing", days_ago=d) for d in (1, 5, 10, 20)]
    report = build_digest(tickets, now=now)
    assert report.ticket_count == 4
    assert len(report.recurring) == 1
    assert "Weekly Building Health Digest" in report.body
    assert "Recurring issues (1)" in report.body


def test_digest_notes_insufficient_data(make_ticket, now) -> None:  # noqa: ANN001
    # No vendor_id and no tone on any ticket -> both gaps noted.
    report = build_digest([make_ticket()], now=now)
    joined = " ".join(report.notes)
    assert "Contractor performance: insufficient data" in joined
    assert "Sentiment trend: insufficient data" in joined


def test_empty_digest_is_clean(now) -> None:  # noqa: ANN001
    report = build_digest([], now=now)
    assert report.ticket_count == 0
    assert "Recurring issues (0)" in report.body
    assert "none" in report.body


def test_digest_with_full_data_has_no_gap_notes(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [
        make_ticket(vendor_id="v1", status="Resolved", resolved_hours_after=3, tone="neutral"),
    ]
    report = build_digest(tickets, now=now)
    assert report.notes == []
