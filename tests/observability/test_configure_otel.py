"""Tests for the `setup_tracer_provider` half of `configure_otel`."""

from __future__ import annotations

import pytest
from agents.observability import sdk
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def test_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call returns the same provider as the first."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    first = sdk.setup_tracer_provider(service_name="svc", environment="dev", sync=True)
    second = sdk.setup_tracer_provider(service_name="svc-DIFFERENT", environment="prod")
    assert first is second
    # The second call's args are ignored — proves the guard short-circuits.
    assert isinstance(first, TracerProvider)


def test_resource_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "1.2.3")
    provider = sdk.setup_tracer_provider(
        service_name="condomanager-test",
        environment="dev",
        sync=True,
    )
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "condomanager-test"
    assert attrs["deployment.environment"] == "dev"
    assert attrs["service.version"] == "1.2.3"


def test_global_tracer_provider_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    provider = sdk.setup_tracer_provider(service_name="svc", environment="dev", sync=True)
    assert trace.get_tracer_provider() is provider


def test_otlp_exporter_picked_when_endpoint_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_TRACES_EXPORTER=otlp` + endpoint -> OTLPSpanExporter."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    exporter = sdk._select_exporter()
    # We don't import OTLPSpanExporter at the top of this test to keep the
    # import surface symmetric with the prod code (lazy via select_exporter).
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    assert isinstance(exporter, OTLPSpanExporter)


def test_console_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    exporter = sdk._select_exporter()
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    assert isinstance(exporter, ConsoleSpanExporter)


def test_otlp_falls_back_to_console_when_endpoint_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_TRACES_EXPORTER=otlp` without an endpoint -> fall back to console.

    Better than crashing at startup or silently dropping spans.
    """
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    exporter = sdk._select_exporter()
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    assert isinstance(exporter, ConsoleSpanExporter)


def test_otlp_chosen_when_no_app_insights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for CM-22 precedence ordering.

    Without ``APPLICATIONINSIGHTS_CONNECTION_STRING``, ``OTEL_TRACES_EXPORTER=otlp``
    + endpoint must still pick the OTLP path. (Precedence: App Insights > OTLP > console;
    this test guards the OTLP -> console boundary, the test_azure_monitor suite
    guards the App Insights -> OTLP boundary.)
    """
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    assert isinstance(sdk._select_exporter(), OTLPSpanExporter)
