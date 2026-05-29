"""Stop-Rule guardrail tests + CM-26 alert-loop dual-side regression."""

from __future__ import annotations

from agents.orchestrator import (
    COST_CAP_USD,
    EVENT_COST_CAP,
    EVENT_LOOP_CAP,
    LOOP_CAP,
    AgentState,
    check,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _state(cost: float = 0.0, search: int = 0) -> AgentState:
    return AgentState(
        tenant_id="t-1",
        request_id="r-1",
        cost_so_far=cost,
        search_count=search,
    )


def test_no_trip_when_under_caps() -> None:
    r = check(_state(cost=1.0, search=1))
    assert r.tripped is False
    assert r.reason is None


def test_cost_cap_is_strict_greater_than() -> None:
    """Boundary: exactly == COST_CAP_USD does NOT trip; > does."""
    r = check(_state(cost=COST_CAP_USD))
    assert r.tripped is False

    r = check(_state(cost=COST_CAP_USD + 0.01))
    assert r.tripped is True
    assert r.reason is not None and "cost" in r.reason


def test_loop_cap_is_strict_greater_than() -> None:
    r = check(_state(search=LOOP_CAP))
    assert r.tripped is False

    r = check(_state(search=LOOP_CAP + 1))
    assert r.tripped is True
    assert r.reason is not None and "search_count" in r.reason


def test_cost_cap_emits_exact_cm26_event_name(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """**Dual-side CM-26 regression.** This event name MUST match the
    literal string in ``infra/bicep/modules/alert-rules.bicep``. Break
    it here without updating Bicep and the alert silently never fires.
    """
    check(_state(cost=COST_CAP_USD + 1.0))
    spans = in_memory_spans.get_finished_spans()
    names = {s.name for s in spans}
    assert EVENT_COST_CAP == "guardrail.cost_cap"  # exported constant
    assert "guardrail.cost_cap" in names


def test_loop_cap_emits_exact_cm26_event_name(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """**Dual-side CM-26 regression.** Mirror of the cost-cap test."""
    check(_state(search=LOOP_CAP + 5))
    spans = in_memory_spans.get_finished_spans()
    names = {s.name for s in spans}
    assert EVENT_LOOP_CAP == "guardrail.loop_cap"
    assert "guardrail.loop_cap" in names


def test_trip_span_carries_context_attributes(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Alert payload attributes — operators rely on these for context."""
    check(_state(cost=10.0))
    trip_span = next(
        s for s in in_memory_spans.get_finished_spans()
        if s.name == "guardrail.cost_cap"
    )
    attrs = trip_span.attributes or {}
    assert attrs["request_id"] == "r-1"
    assert attrs["tenant_id"] == "t-1"
    assert attrs["guardrail.cost_so_far_usd"] == 10.0
    assert attrs["guardrail.search_count"] == 0


def test_cost_cap_checked_before_loop_cap() -> None:
    """When BOTH trip, cost cap wins (first check; deterministic)."""
    r = check(_state(cost=10.0, search=100))
    assert r.tripped is True
    assert r.reason is not None and "cost" in r.reason
