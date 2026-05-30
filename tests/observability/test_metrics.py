"""Tests for ``agents.observability.metrics.emit_metric`` (CM-39)."""

from __future__ import annotations

from agents.observability import (
    METRIC_TRIAGE_ROUTE,
    emit_metric,
    with_request_id,
    with_tenant,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_emit_metric_emits_named_span_with_attrs(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    with with_request_id("req_test"), with_tenant("t-9"):
        emit_metric(METRIC_TRIAGE_ROUTE, value=1.0, intent="inquiry", route="knowledge")

    span = next(
        s for s in in_memory_spans.get_finished_spans() if s.name == METRIC_TRIAGE_ROUTE
    )
    attrs = span.attributes or {}
    assert attrs.get("metric.value") == 1.0
    assert attrs.get("request_id") == "req_test"
    assert attrs.get("tenant_id") == "t-9"
    assert attrs.get("intent") == "inquiry"
    assert attrs.get("route") == "knowledge"


def test_emit_metric_drops_none_attrs(in_memory_spans: InMemorySpanExporter) -> None:
    emit_metric("metric.test.x", value=2.0, foo=None, bar="b")
    span = next(
        s for s in in_memory_spans.get_finished_spans() if s.name == "metric.test.x"
    )
    attrs = span.attributes or {}
    assert "foo" not in attrs
    assert attrs.get("bar") == "b"
    assert attrs.get("metric.value") == 2.0


def test_emit_metric_is_no_op_safe_without_provider() -> None:
    # No `in_memory_spans` fixture → the reset_otel autouse leaves a no-op
    # tracer; emitting must not raise (preserves the no-credentials contract).
    emit_metric("metric.test.noop", value=1.0, tenant_id=None)
