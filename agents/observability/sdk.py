"""OpenTelemetry TracerProvider setup and exporter selection.

Jira: CM-21 (initial) | CM-22 (Application Insights backend)
Epic: Observability  | Phase 0

Three modes, picked at runtime from environment variables — precedence
top to bottom:

  1. **Azure Monitor (CM-22).** ``APPLICATIONINSIGHTS_CONNECTION_STRING`` set to
     a real value -> delegate to ``azure-monitor-opentelemetry``'s
     ``configure_azure_monitor``, which installs the provider, the Azure
     Monitor exporter, and Live Metrics in one supported call. The Python
     sampler is read from ``OTEL_SAMPLER_RATIO`` (default 1.0); the App
     Insights resource applies a second, server-side, adaptive sampler on
     top (``SamplingPercentage`` on the Bicep resource).
  2. **OTLP-HTTP (CM-21).** ``OTEL_TRACES_EXPORTER=otlp`` AND
     ``OTEL_EXPORTER_OTLP_ENDPOINT`` set -> ``OTLPSpanExporter``. The path
     for any OTLP-compatible backend other than App Insights.
  3. **Console (default).** ``OTEL_TRACES_EXPORTER=console`` or unset ->
     ``ConsoleSpanExporter``. Local-dev + tests work with zero env vars.

The conn string sentinel ``"REPLACE-ME"`` (the CM-18 KV placeholder) is
treated as if-unset so the Container App boots cleanly before
``seed-app-insights-secret.sh`` populates the real value.

``setup_tracer_provider()`` is idempotent — the second call returns the
provider installed by the first. Tests, hot-reloads, and serverless
cold/warm starts all rely on this.

The ``sync`` flag picks ``SimpleSpanProcessor`` (test-friendly, blocking)
over the default ``BatchSpanProcessor`` (production-friendly, async batched).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)

_log = logging.getLogger(__name__)

#: Sentinel that the CM-18 seed script writes for unpopulated secrets.
#: Treated as if-unset so Container Apps boots cleanly before the post-deploy
#: seed step replaces the value.
SECRET_PLACEHOLDER: str = "REPLACE-ME"

# Module-level flag — protects against double-initialization. Tests reset
# this via the `reset_otel` fixture in tests/observability/conftest.py.
_initialized: bool = False


def _app_insights_conn_string() -> str | None:
    """Return the App Insights connection string, or None if unset/placeholder."""
    val = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not val or val == SECRET_PLACEHOLDER:
        return None
    return val


def _sampler_from_env() -> Sampler:
    """Build the Parent-based sampler from ``OTEL_SAMPLER_RATIO``.

    The OTel spec recommends Parent-based samplers so that downstream services
    inherit upstream sampling decisions cleanly. Ratio defaults to 1.0
    (sample everything). Out-of-range values clamp into [0.0, 1.0] with a
    warning rather than raising — better than crashing app startup.
    """
    raw = os.environ.get("OTEL_SAMPLER_RATIO", "1.0")
    try:
        ratio = float(raw)
    except ValueError:
        _log.warning("OTEL_SAMPLER_RATIO=%r is not a float; using 1.0", raw)
        ratio = 1.0
    if ratio < 0.0 or ratio > 1.0:
        _log.warning("OTEL_SAMPLER_RATIO=%s out of [0,1]; clamping", ratio)
        ratio = max(0.0, min(1.0, ratio))
    if ratio >= 1.0:
        return ParentBased(root=ALWAYS_ON)
    return ParentBased(root=TraceIdRatioBased(ratio))


def _pii_masking_processor() -> SpanProcessor:
    """Build the CM-38 trace-layer PII-masking span processor.

    Imported lazily from ``agents.security`` so the observability package has no
    import-time dependency on the security package (keeps the layering one-way:
    security depends on observability, not the reverse).
    """
    from agents.security.masking import PiiMaskingSpanProcessor  # noqa: PLC0415

    return PiiMaskingSpanProcessor()


def _select_exporter() -> SpanExporter:
    """Pick the exporter for the non-Azure-Monitor branches."""
    name = os.environ.get("OTEL_TRACES_EXPORTER", "console").lower()
    if name == "otlp" and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # OTLPSpanExporter reads the endpoint + headers from env at construction.
        return OTLPSpanExporter()
    # Console is the safe default — produces output in local dev, tests can
    # capture stdout, no network required.
    return ConsoleSpanExporter()


def _setup_azure_monitor(*, service_name: str, environment: str) -> TracerProvider:
    """Delegate to ``azure-monitor-opentelemetry`` for App Insights.

    The distro is what installs the provider, the Azure Monitor exporter, and
    Live Metrics. We pass the sampler explicitly so prod can dial the
    client-side sampling ratio via env var without touching code.

    Imports the distro lazily so processes that don't use App Insights don't
    pay for it on startup.
    """
    from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415

    conn = _app_insights_conn_string()
    assert conn is not None, "setup_azure_monitor called without a connection string"

    configure_azure_monitor(
        connection_string=conn,
        resource_attributes={
            "service.name": service_name,
            "deployment.environment": environment,
            "service.version": os.environ.get("OTEL_SERVICE_VERSION", "0.1.0"),
        },
        # AC #4 — Live Metrics is a SDK-side feature; this kwarg is the only
        # way to wire its ingestion channel. Live Metrics are unsampled, so the
        # operator gets a real-time view even when traces are heavily sampled.
        enable_live_metrics=True,
        # AC #3 — client-side sampler. App Insights applies its own server-side
        # adaptive sampler on top (SamplingPercentage on the Bicep resource).
        sampler=_sampler_from_env(),
        # logs + metrics: keep off for now — CM-25 (workbooks) reads spans
        # directly from the LAW, CM-27 (structured logs) ships its own logging.
        # Leaving them on would double-export and inflate ingestion.
        logger_name=None,
    )
    # configure_azure_monitor sets the global provider; just hand it back.
    return trace.get_tracer_provider()  # type: ignore[return-value]


def setup_tracer_provider(
    *,
    service_name: str,
    environment: str,
    sync: bool = False,
) -> TracerProvider:
    """Install (once) the global TracerProvider. Idempotent.

    Args:
        service_name: Goes into the ``service.name`` resource attribute. Every
            CondoManager workload sets this to its own short name (e.g.
            ``"orchestrator"``, ``"triage-eval"``).
        environment: ``"dev"`` or ``"prod"`` — drives the
            ``deployment.environment`` resource attribute.
        sync: When True, use ``SimpleSpanProcessor`` (synchronous). Tests
            pass ``sync=True`` so spans are visible immediately on
            ``InMemorySpanExporter.get_finished_spans()``. Production code
            leaves this False to get batched, async export. **Ignored on the
            Azure Monitor branch** — the distro manages its own processors.

    Returns:
        The TracerProvider — already installed as the global one.
    """
    global _initialized
    if _initialized:
        # Cast through Any: get_tracer_provider() returns TracerProvider
        # (abstract); we know it's our SDK subclass because we installed it.
        return trace.get_tracer_provider()  # type: ignore[return-value]

    if _app_insights_conn_string() is not None:
        # CM-22: Azure Monitor branch. Takes precedence over OTLP and console
        # because if you've explicitly wired App Insights you want spans there.
        provider = _setup_azure_monitor(
            service_name=service_name, environment=environment
        )
        # CM-38 AC2: attach the PII masker to the distro-managed provider too,
        # so prod App Insights spans are masked. The distro already added its
        # BatchSpanProcessor; masking runs synchronously in on_end (export is
        # deferred to the batch worker), so attributes are masked before they
        # serialize. See docs/SECURITY.md for the batch-ordering note.
        # Guard: configure_azure_monitor may leave a ProxyTracerProvider (e.g.
        # when mocked in tests, or if the distro no-ops), which has no
        # add_span_processor — only attach when a real SDK provider is in place.
        if isinstance(provider, TracerProvider):
            provider.add_span_processor(_pii_masking_processor())
        _initialized = True
        return provider

    # CM-21: console / OTLP branch.
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
            "service.version": os.environ.get("OTEL_SERVICE_VERSION", "0.1.0"),
        }
    )
    provider = TracerProvider(resource=resource, sampler=_sampler_from_env())
    # CM-38 AC2: mask string span attributes BEFORE export. Registered first so
    # its synchronous on_end runs ahead of the exporting processor's on_end.
    provider.add_span_processor(_pii_masking_processor())
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
