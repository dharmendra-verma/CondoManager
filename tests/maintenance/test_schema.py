"""Ticket schema tests (CM-31 AC1)."""

from __future__ import annotations

from agents.maintenance.schema import Priority, Ticket, TicketStatus


def test_status_values_match_ac_exactly() -> None:
    assert [s.value for s in TicketStatus] == [
        "New",
        "In Progress",
        "Waiting",
        "Resolved",
    ]


def test_priority_bands() -> None:
    assert [p.value for p in Priority] == ["P1", "P2", "P3", "P4"]


def test_ticket_has_ownership_and_defaults(make_ticket) -> None:  # noqa: ANN001
    t = make_ticket()
    # ownership field exists (AC1) and defaults to unassigned
    assert t.owner is None
    assert t.status is TicketStatus.NEW
    assert t.duplicate_of is None


def test_ticket_round_trips_through_json(make_ticket) -> None:  # noqa: ANN001
    t = make_ticket()
    restored = Ticket.model_validate(t.model_dump(mode="json"))
    assert restored == t
