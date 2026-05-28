"""OpenTelemetry TracerProvider setup and exporter selection.

Jira: CM-21  | Epic: Observability  | Phase 0

Three modes, picked at runtime from environment variables:

  1. `OTEL_TRACES_EXPORTER=otlp` *and* `OTEL_EXPORTER_OTLP_ENDPOINT` set
     -> OTLP-over-HTTP exporter (the path CM-22 wires App Insights into).
  2. `OTEL_TRACES_EXPORTER=console` (the default)
     -> ConsoleSpanExporter. Local-dev + tests work with zero env vars.
  3. Any other value (or `none`)
     -> No-op: TracerProvider is installed so `trace.get_tracer()` works,
        but no spans are exported.

`setup_tracer_provider()` is idempotent — the second call returns the
provider installed by the first call. Idempotency matters because tests,
hot-reloads, and serverless cold/warm starts all call it.

The `sync` flag picks `SimpleSpanProcessor` (test-friendly, blocking) over
the default `BatchSpanProcessor` (production-friendly, async batched).
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

# Module-level flag — protects against double-initialization. Tests reset
# this via the `reset_otel` fixture in tests/observability/conftest.py.
_initialized: bool = False


def _select_exporter() -> SpanExporter:
    """Pick the exporter for this process based on env vars."""
    name = os.environ.get("OTEL_TRACES_EXPORTER", "console").lower()
    if name == "otlp" and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # OTLPSpanExporter reads the endpoint + headers from env at construction.
        return OTLPSpanExporter()
    # Console is the safe default — produces output in local dev, tests can
    # capture stdout, no network required.
    return ConsoleSpanExporter()


def setup_tracer_provider(
    *,
    service_name: str,
    environment: str,
    sync: bool = False,
) -> TracerProvider:
    """Install (once) the global TracerProvider. Idempotent.

    Args:
        service_name: Goes into the `service.name` resource attribute. Every
            CondoManager workload sets this to its own short name (e.g.
            ``"orchestrator"``, ``"triage-eval"``).
        environment: ``"dev"`` or ``"prod"`` — drives the
            ``deployment.environment`` resource attribute.
        sync: When True, use ``SimpleSpanProcessor`` (synchronous). Tests
            pass ``sync=True`` so spans are visible immediately on
            ``InMemorySpanExporter.get_finished_spans()``. Production code
            leaves this False to get batched, async export.

    Returns:
        The TracerProvider — already installed as the global one.
    """
    global _initialized
    if _initialized:
        # Cast through Any: get_tracer_provider() returns TracerProvider
        # (abstract); we know it's our SDK subclass because we installed it.
        return trace.get_tracer_provider()  # type: ignore[return-value]

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
            "service.version": os.environ.get("OTEL_SERVICE_VERSION", "0.1.0"),
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = _select_exporter()
    processor_cls = SimpleSpanProcessor if sync else BatchSpanProcessor
    provider.add_span_processor(processor_cls(exporter))
    trace.set_tracer_provider(provider)
    _initialized = True
    return provider


def _reset_for_tests() -> None:
    """Test-only — clears the module-level guard. Do not call from app code."""
    global _initialized
    _initialized = False
