"""Shared fixtures for ``tests/channels/``.

Provides the same in-memory OTel span exporter shape as
``tests/observability/conftest.py`` so the CM-46 ack-latency emission can be
asserted without network I/O. Autouse ``reset_otel`` keeps the set-once
tracer-provider guard clean across tests (and is harmless for the channel tests
that don't touch OTel).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from agents.observability import instrumentation, sdk
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _reset_otel_globals() -> None:
    """Hard-reset OTel's set-once tracer-provider guard (CM-21 pattern)."""
    for once_attr in ("_TRACER_PROVIDER_SET_ONCE", "_TRACER_PROVIDER_SET_ONCE_DONE"):
        once = getattr(trace, once_attr, None)
        if once is not None and hasattr(once, "_done"):
            once._done = False
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def reset_otel() -> Generator[None, None, None]:
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()
    yield
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()


@pytest.fixture
def in_memory_spans() -> Generator[InMemorySpanExporter, None, None]:
    """Install an in-memory exporter on a fresh TracerProvider."""
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "channels-test", "deployment.environment": "test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    sdk._initialized = True
    yield exporter
    exporter.clear()
