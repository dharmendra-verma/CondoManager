"""PRD success-metric emission (CM-39).

Jira: CM-39  | Epic: CM-Epic 14  | Phase 2

One helper, :func:`emit_metric`, that lands a single ``customEvents`` row per
PRD signal — mirroring the CM-26 guardrail-trip pattern
(:func:`agents.orchestrator.guardrails._emit_trip`) so the same App Insights
table + KQL the CM-25 workbook queries also carries the PRD metrics. Each event
name is ``metric.<area>.<name>`` and carries ``value`` + the correlation
attributes (``request_id``, ``tenant_id``) so dashboards can group per tenant.

Offline-safe: when no real ``TracerProvider`` is configured the OTel API hands
back a no-op span, so agents emit metrics with zero overhead and the
no-credentials test/demo contract (CM-28) is preserved.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from .correlation import get_request_id, get_tenant_id

#: Tracer scope — matches the CM-21 span helper so backends can filter on it.
_TRACER_NAME = "agents.observability"

# --- Metric event names (the dashboard/alert contract) -----------------------
METRIC_ACK_LATENCY = "metric.ack_latency_ms"
METRIC_TRIAGE_ROUTE = "metric.triage.route"
METRIC_KNOWLEDGE_ANSWERED = "metric.knowledge.answered"
METRIC_KNOWLEDGE_REFUSED = "metric.knowledge.refused"
METRIC_MAINTENANCE_DEDUP = "metric.maintenance.dedup"
METRIC_VENDOR_AUTO_DISPATCH = "metric.vendor.auto_dispatch"
METRIC_VENDOR_HITL = "metric.vendor.hitl"
METRIC_ESCALATION_LEGAL_FLAG = "metric.escalation.legal_flag"
METRIC_HITL_RATING = "metric.hitl.rating"
# CM-46: outcome-metric lifecycle signals (need resolved-ticket data).
METRIC_TTM_RESOLUTION_MS = "metric.ttm_resolution_ms"
METRIC_FOLLOWUP = "metric.followup"


def emit_metric(name: str, value: float = 1.0, **attrs: Any) -> None:
    """Emit one PRD-metric ``customEvents`` span.

    Args:
        name: Event name — use a ``METRIC_*`` constant from this module so the
            dashboards/alerts and the emitter stay in sync.
        value: The measured value (count=1.0 by default; latency ms, rate, …).
        **attrs: Extra attributes (e.g. ``intent=...``, ``vendor_id=...``).
            ``None`` values are dropped so callers can pass optionals freely.

    ``request_id`` + ``tenant_id`` are always attached (from the CM-21
    correlation ContextVars) so the event joins to logs/spans/Langfuse.
    """
    span = trace.get_tracer(_TRACER_NAME).start_span(name)
    try:
        span.set_attribute("metric.value", value)
        span.set_attribute("request_id", get_request_id())
        tenant = get_tenant_id()
        if tenant is not None:
            span.set_attribute("tenant_id", tenant)
        for key, attr_value in attrs.items():
            if attr_value is not None:
                span.set_attribute(key, attr_value)
    finally:
        # Always end — a leaked span would drop the customEvents row.
        span.end()
