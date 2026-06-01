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
from agents.observability import instrumentation, sdk
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(autouse=True)
def reset_seams() -> Generator[None, None, None]:
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()
    yield
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()


def _reset_otel_globals() -> None:
    """Hard-reset OTel's set-once tracer-provider guard (CM-21 pattern)."""
    for once_attr in ("_TRACER_PROVIDER_SET_ONCE", "_TRACER_PROVIDER_SET_ONCE_DONE"):
        once = getattr(trace, once_attr, None)
        if once is not None and hasattr(once, "_done"):
            once._done = False
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def reset_otel() -> Generator[None, None, None]:
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()
    yield
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()


@pytest.fixture
def in_memory_spans() -> Generator[InMemorySpanExporter, None, None]:
    """Install an in-memory exporter on a fresh TracerProvider (CM-46 TTM emit)."""
    exporter = InMemorySpanExporter()
    resource = Resource.create(
        {"service.name": "maintenance-test", "deployment.environment": "test"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    sdk._initialized = True
    yield exporter
    exporter.clear()


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
