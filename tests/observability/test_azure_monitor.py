"""Tests for the CM-22 Azure Monitor branch of ``setup_tracer_provider``.

We mock ``azure.monitor.opentelemetry.configure_azure_monitor`` so the test
stays offline (no Azure SDK auth / network). The assertions cover:

* When ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, the Azure Monitor
  distro is called once with the right kwargs (conn string, Live Metrics,
  Parent-based always-on sampler at the default ratio).
* ``OTEL_SAMPLER_RATIO`` plumbs through as ``ParentBased(TraceIdRatioBased(r))``.
* The CM-18 placeholder ``REPLACE-ME`` is treated as if-unset and falls back
  to the CM-21 console/OTLP path — Container Apps boots cleanly before the
  post-deploy seed step.
* Precedence: Azure Monitor wins over ``OTEL_TRACES_EXPORTER=otlp``.
* Sampler clamping + bad-input handling don't crash app startup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agents.observability import sdk
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)

FAKE_CONN = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://eastus2.in.applicationinsights.azure.com/;"
)


def _sampler_root(sampler: object) -> object:
    """Pull the inner sampler out of a ParentBased so we can introspect it."""
    assert isinstance(sampler, ParentBased)
    # ParentBased stores its root delegate on `_root` in the OTel SDK.
    return sampler._root  # type: ignore[attr-defined]


def test_app_insights_branch_calls_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", FAKE_CONN)
    monkeypatch.delenv("OTEL_SAMPLER_RATIO", raising=False)

    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock:
        sdk.setup_tracer_provider(service_name="svc-22", environment="dev")
        assert mock.call_count == 1
        kwargs = mock.call_args.kwargs

    assert kwargs["connection_string"] == FAKE_CONN
    assert kwargs["enable_live_metrics"] is True
    assert kwargs["resource_attributes"]["service.name"] == "svc-22"
    assert kwargs["resource_attributes"]["deployment.environment"] == "dev"
    assert _sampler_root(kwargs["sampler"]) is ALWAYS_ON


def test_app_insights_branch_picks_sampler_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", FAKE_CONN)
    monkeypatch.setenv("OTEL_SAMPLER_RATIO", "0.5")

    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock:
        sdk.setup_tracer_provider(service_name="svc", environment="prod")

    sampler = mock.call_args.kwargs["sampler"]
    root = _sampler_root(sampler)
    assert isinstance(root, TraceIdRatioBased)
    # TraceIdRatioBased stores the ratio on `_rate` in the OTel SDK.
    assert root._rate == 0.5  # type: ignore[attr-defined]


def test_placeholder_falls_back_to_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPLACE-ME is treated as if-unset — CM-18 placeholder shouldn't crash."""
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "REPLACE-ME")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock:
        sdk.setup_tracer_provider(
            service_name="svc", environment="dev", sync=True
        )
    mock.assert_not_called()
    # Console exporter selected by the fallback.
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    assert isinstance(sdk._select_exporter(), ConsoleSpanExporter)


def test_empty_string_falls_back_to_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty/whitespace conn string is also treated as if-unset."""
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "   ")
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock:
        sdk.setup_tracer_provider(
            service_name="svc", environment="dev", sync=True
        )
    mock.assert_not_called()


def test_app_insights_wins_over_otlp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Azure Monitor precedence: even with OTLP env vars set, AppI wins."""
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", FAKE_CONN)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock:
        sdk.setup_tracer_provider(service_name="svc", environment="dev")
    mock.assert_called_once()


def test_sampler_ratio_one_returns_always_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SAMPLER_RATIO", "1.0")
    sampler = sdk._sampler_from_env()
    assert _sampler_root(sampler) is ALWAYS_ON


def test_sampler_ratio_clamps_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SAMPLER_RATIO", "1.5")
    sampler = sdk._sampler_from_env()
    # Clamps to 1.0 -> ALWAYS_ON.
    assert _sampler_root(sampler) is ALWAYS_ON

    monkeypatch.setenv("OTEL_SAMPLER_RATIO", "-0.2")
    sampler = sdk._sampler_from_env()
    root = _sampler_root(sampler)
    assert isinstance(root, TraceIdRatioBased)
    assert root._rate == 0.0  # type: ignore[attr-defined]


def test_sampler_bad_input_defaults_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage in OTEL_SAMPLER_RATIO -> default 1.0, no crash."""
    monkeypatch.setenv("OTEL_SAMPLER_RATIO", "not-a-number")
    sampler = sdk._sampler_from_env()
    assert _sampler_root(sampler) is ALWAYS_ON
