"""Repository + selector tests (CM-31)."""

from __future__ import annotations

import os
from datetime import timedelta

from agents.maintenance.repository import (
    InMemoryTicketRepository,
    get_ticket_repository,
)


def test_inmemory_add_and_recent_filters_by_unit_and_window(make_ticket, now) -> None:  # noqa: ANN001
    repo = InMemoryTicketRepository()
    in_window = make_ticket(ticket_id="TKT-IN", unit="4b", created_at=now - timedelta(days=2))
    other_unit = make_ticket(ticket_id="TKT-OTH", unit="9c", created_at=now - timedelta(days=1))
    too_old = make_ticket(ticket_id="TKT-OLD", unit="4b", created_at=now - timedelta(days=20))
    for t in (in_window, other_unit, too_old):
        repo.add(t)

    found = repo.recent_for_unit("t-1", "4b", since=now - timedelta(days=7))
    ids = {t.id for t in found}
    assert ids == {"TKT-IN"}


def test_selector_falls_back_to_inmemory_when_unset(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    repo = get_ticket_repository()
    assert isinstance(repo, InMemoryTicketRepository)


def test_selector_treats_placeholder_as_unset(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("COSMOS_ENDPOINT", "REPLACE-ME")
    repo = get_ticket_repository()
    assert isinstance(repo, InMemoryTicketRepository)


def test_selector_is_cached(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert get_ticket_repository() is get_ticket_repository()


def test_env_isolation_sanity() -> None:
    # Guards against a stray real endpoint in the test environment.
    assert os.environ.get("COSMOS_ENDPOINT", "").strip() in ("", "REPLACE-ME")
