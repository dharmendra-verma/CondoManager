"""Shared fixtures for ``tests/analytics/``.

Resets the cached source + delivery seams around every test and provides a
fixed ``now`` plus an :class:`AnalyticsTicket` factory keyed by ``days_ago``.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta

import pytest
from agents.analytics import delivery as delivery_mod
from agents.analytics import source as source_mod
from agents.analytics.models import AnalyticsTicket

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_seams() -> Generator[None, None, None]:
    source_mod._reset_for_tests()
    delivery_mod._reset_for_tests()
    yield
    source_mod._reset_for_tests()
    delivery_mod._reset_for_tests()


@pytest.fixture
def now() -> datetime:
    return _NOW


@pytest.fixture
def make_ticket() -> Callable[..., AnalyticsTicket]:
    def _make(
        *,
        tenant_id: str = "t-1",
        unit: str = "4b",
        category: str = "plumbing",
        status: str = "New",
        priority: str = "P3",
        days_ago: float = 1.0,
        resolved_hours_after: float | None = None,
        vendor_id: str | None = None,
        tone: str | None = None,
    ) -> AnalyticsTicket:
        created = _NOW - timedelta(days=days_ago)
        resolved = (
            created + timedelta(hours=resolved_hours_after)
            if resolved_hours_after is not None
            else None
        )
        return AnalyticsTicket(
            tenant_id=tenant_id,
            unit=unit,
            category=category,
            status=status,
            priority=priority,
            created_at=created,
            updated_at=resolved or created,
            resolved_at=resolved,
            vendor_id=vendor_id,
            tone=tone,
        )

    return _make
