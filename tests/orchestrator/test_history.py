"""Ticket-history seam tests (CM-30 AC #5)."""

from __future__ import annotations

from agents.orchestrator.history import (
    NoopTicketHistory,
    TicketHistoryProvider,
    get_history_provider,
)


def test_noop_is_a_provider() -> None:
    assert isinstance(NoopTicketHistory(), TicketHistoryProvider)


def test_noop_returns_empty_for_any_tenant() -> None:
    provider = NoopTicketHistory()
    assert provider.recent_tickets("t-1") == []
    assert provider.recent_tickets("unknown-tenant", limit=10) == []


def test_selector_returns_noop_by_default() -> None:
    assert isinstance(get_history_provider(), NoopTicketHistory)


def test_selector_result_is_a_provider() -> None:
    assert isinstance(get_history_provider(), TicketHistoryProvider)
