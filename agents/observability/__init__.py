"""``agents.observability`` — OpenTelemetry SDK + auto-instrumentation + correlation.

Jira: CM-21  | Epic: Observability  | Phase 0

Public API (re-exported here, also importable from submodules):

* ``configure_otel(service_name, environment, app=None)`` -- one-call setup
  at app startup. Idempotent.
* ``with_request_id(request_id=None)`` -- inbound-boundary context manager
  that propagates the id through ContextVar, OTel baggage, and the current
  span. Auto-generates an id when omitted.
* ``langgraph_node_span(node_name, **attrs)`` -- manual span helper used
  by LangGraph node bodies (CM-28).
* ``get_request_id()``, ``set_request_id()``, ``new_request_id()``,
  ``REQUEST_ID_KEY`` -- correlation primitives.

CM-22 will set ``OTEL_EXPORTER_OTLP_ENDPOINT`` so spans flow into
Application Insights. Until then, leave it unset and the SDK falls back
to the ConsoleSpanExporter (visible on stdout).
"""

from __future__ import annotations

from typing import Any

from .correlation import (
    REQUEST_ID_KEY,
    UNKNOWN_REQUEST_ID,
    get_agent_name,
    get_request_id,
    get_tenant_id,
    new_request_id,
    set_agent_name,
    set_request_id,
    set_tenant_id,
    with_agent,
    with_request_id,
    with_tenant,
)
from .instrumentation import register_auto_instrumentation
from .logging import configure_logging
from .metrics import (
    METRIC_ACK_LATENCY,
    METRIC_ESCALATION_LEGAL_FLAG,
    METRIC_HITL_RATING,
    METRIC_KNOWLEDGE_ANSWERED,
    METRIC_KNOWLEDGE_REFUSED,
    METRIC_TRIAGE_ROUTE,
    METRIC_VENDOR_AUTO_DISPATCH,
    METRIC_VENDOR_HITL,
    emit_metric,
)
from .pii import mask_pii
from .sdk import setup_tracer_provider
from .spans import TRACER_NAME, langgraph_node_span

__all__ = [
    "METRIC_ACK_LATENCY",
    "METRIC_ESCALATION_LEGAL_FLAG",
    "METRIC_HITL_RATING",
    "METRIC_KNOWLEDGE_ANSWERED",
    "METRIC_KNOWLEDGE_REFUSED",
    "METRIC_TRIAGE_ROUTE",
    "METRIC_VENDOR_AUTO_DISPATCH",
    "METRIC_VENDOR_HITL",
    "REQUEST_ID_KEY",
    "TRACER_NAME",
    "UNKNOWN_REQUEST_ID",
    "configure_logging",
    "configure_otel",
    "emit_metric",
    "get_agent_name",
    "get_request_id",
    "get_tenant_id",
    "langgraph_node_span",
    "mask_pii",
    "new_request_id",
    "register_auto_instrumentation",
    "set_agent_name",
    "set_request_id",
    "set_tenant_id",
    "setup_tracer_provider",
    "with_agent",
    "with_request_id",
    "with_tenant",
]


def configure_otel(
    *,
    service_name: str,
    environment: str,
    app: Any | None = None,
    sync: bool = False,
) -> None:
    """One-call observability setup. Call once at app startup.

    Args:
        service_name: Short name for this workload — ends up as the
            ``service.name`` resource attribute on every emitted span.
        environment: ``"dev"`` or ``"prod"`` — ``deployment.environment``
            resource attribute.
        app: Optional FastAPI app. When supplied, the FastAPI instrumentor
            is attached per-app instead of globally.
        sync: When True, use a synchronous SpanProcessor (tests pass this
            so spans are visible immediately on the InMemorySpanExporter).
            Production code leaves this False for batched async export.
    """
    setup_tracer_provider(service_name=service_name, environment=environment, sync=sync)
    register_auto_instrumentation(app=app)
