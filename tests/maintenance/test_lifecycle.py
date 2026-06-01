"""CM-46: ticket resolution stamps resolved_at + emits the TTM outcome metric."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.maintenance import get_ticket_repository, resolve_ticket
from agents.maintenance.schema import Priority, Ticket, TicketStatus
from agents.observability import METRIC_TTM_RESOLUTION_MS
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _seed(created_at: datetime) -> None:
    get_ticket_repository().add(
        Ticket(
            id="TKT-RES",
            tenant_id="t-res",
            unit="4b",
            issue_text="kitchen sink leaking",
            category="plumbing",
            priority=Priority.P2,
            status=TicketStatus.NEW,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def test_resolve_ticket_stamps_and_emits_ttm(in_memory_spans: InMemorySpanExporter) -> None:
    created = datetime(2026, 5, 29, 10, 0, 0, tzinfo=UTC)
    resolved_now = created + timedelta(hours=3)
    _seed(created)

    ticket = resolve_ticket("t-res", "TKT-RES", now=resolved_now)

    assert ticket is not None
    assert ticket.status is TicketStatus.RESOLVED
    assert ticket.resolved_at == resolved_now
    assert ticket.updated_at == resolved_now

    spans = [s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_TTM_RESOLUTION_MS]
    assert len(spans) == 1
    # 3 hours == 10_800_000 ms.
    assert spans[0].attributes["metric.value"] == 3 * 3600 * 1000.0
    assert spans[0].attributes["category"] == "plumbing"
    assert spans[0].attributes["priority"] == "P2"


def test_resolve_persists_to_repository(in_memory_spans: InMemorySpanExporter) -> None:
    created = datetime(2026, 5, 29, 10, 0, 0, tzinfo=UTC)
    _seed(created)
    resolve_ticket("t-res", "TKT-RES", now=created + timedelta(hours=1), owner="vendor-7")

    stored = get_ticket_repository().get("t-res", "TKT-RES")
    assert stored is not None
    assert stored.status is TicketStatus.RESOLVED
    assert stored.owner == "vendor-7"
    assert stored.resolved_at is not None


def test_resolve_unknown_ticket_returns_none_and_no_emit(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    result = resolve_ticket("t-res", "TKT-MISSING", now=datetime.now(UTC))
    assert result is None
    assert not [
        s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_TTM_RESOLUTION_MS
    ]


def test_resolve_clamps_clock_skew_to_zero(in_memory_spans: InMemorySpanExporter) -> None:
    """A resolved_at earlier than created_at (skew) yields a non-negative TTM."""
    created = datetime(2026, 5, 29, 10, 0, 0, tzinfo=UTC)
    _seed(created)
    resolve_ticket("t-res", "TKT-RES", now=created - timedelta(hours=1))

    spans = [s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_TTM_RESOLUTION_MS]
    assert len(spans) == 1
    assert spans[0].attributes["metric.value"] == 0.0
