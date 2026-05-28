"""langgraph_node_span helper tests using InMemorySpanExporter."""

from __future__ import annotations

from agents.observability import (
    REQUEST_ID_KEY,
    UNKNOWN_REQUEST_ID,
    langgraph_node_span,
    with_request_id,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_span_name_prefixed(in_memory_spans: InMemorySpanExporter) -> None:
    with langgraph_node_span("triage"):
        pass
    spans = in_memory_spans.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "langgraph.node.triage"


def test_span_carries_request_id_attribute(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    with with_request_id("req_xyz"), langgraph_node_span("knowledge"):
        pass
    spans = in_memory_spans.get_finished_spans()
    assert spans[0].attributes is not None
    assert spans[0].attributes[REQUEST_ID_KEY] == "req_xyz"


def test_extra_attrs_become_span_attributes(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    with langgraph_node_span(
        "triage",
        tenant_id="t-123",
        model="gpt-4o-mini",
        intent="maintenance",
    ):
        pass
    attrs = in_memory_spans.get_finished_spans()[0].attributes
    assert attrs is not None
    assert attrs["tenant_id"] == "t-123"
    assert attrs["model"] == "gpt-4o-mini"
    assert attrs["intent"] == "maintenance"


def test_none_valued_attrs_are_dropped(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Passing ``foo=None`` shouldn't crash (OTel rejects None attrs)."""
    with langgraph_node_span("triage", tenant_id="t-1", optional_attr=None):
        pass
    attrs = in_memory_spans.get_finished_spans()[0].attributes
    assert attrs is not None
    assert attrs["tenant_id"] == "t-1"
    assert "optional_attr" not in attrs


def test_missing_request_id_uses_sentinel(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """No `with_request_id` active -> attribute is "unknown", not a crash."""
    with langgraph_node_span("triage"):
        pass
    attrs = in_memory_spans.get_finished_spans()[0].attributes
    assert attrs is not None
    assert attrs[REQUEST_ID_KEY] == UNKNOWN_REQUEST_ID


def test_span_scope_is_agents_observability(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Span scope = the tracer name we registered.

    Backends use this to disambiguate "who emitted this span" — important
    when openinference + Traceloop both touch the same code path.
    """
    with langgraph_node_span("triage"):
        pass
    span = in_memory_spans.get_finished_spans()[0]
    assert span.instrumentation_scope is not None
    assert span.instrumentation_scope.name == "agents.observability"
