"""Shared fixtures for ``tests/maintenance/``.

Resets the cached repository + notifier singletons around every test so the
in-memory store never leaks state across tests, and provides a small
``make_ticket`` factory with sensible defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime

import pytest
from agents.maintenance import notifier as notifier_mod
from agents.maintenance import repository as repo_mod
from agents.maintenance.schema import Priority, Ticket, TicketStatus


@pytest.fixture(autouse=True)
def reset_seams() -> Generator[None, None, None]:
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()
    yield
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()


@pytest.fixture
def now() -> datetime:
    """A fixed 'now' so age calculations are deterministic."""
    return datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def make_ticket() -> Callable[..., Ticket]:
    def _make(
        *,
        ticket_id: str = "TKT-AAAA0001",
        tenant_id: str = "t-1",
        unit: str = "4b",
        issue_text: str = "kitchen sink leaking",
        category: str = "plumbing",
        priority: Priority = Priority.P3,
        status: TicketStatus = TicketStatus.NEW,
        created_at: datetime | None = None,
        eta: str = "within 2 business days",
    ) -> Ticket:
        ts = created_at or datetime(2026, 5, 29, 11, 0, 0, tzinfo=UTC)
        return Ticket(
            id=ticket_id,
            tenant_id=tenant_id,
            unit=unit,
            issue_text=issue_text,
            category=category,
            priority=priority,
            status=status,
            created_at=ts,
            updated_at=ts,
            eta=eta,
        )

    return _make
