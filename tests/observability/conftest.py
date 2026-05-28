"""Shared fixtures for ``tests/observability/``.

We use an isolated ``TracerProvider`` with an ``InMemorySpanExporter`` so
tests can assert what was emitted without any network calls. The
``reset_otel`` fixture is autouse so every test gets a clean SDK + a
clean instrumentation guard.
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
    """Hard-reset OTel's set-once tracer-provider guard.

    `trace.set_tracer_provider` is guarded by a `_Once` latch — once any
    code (including a previous test) calls it, subsequent calls are
    silently ignored with a warning. For tests we want a fresh provider
    every time, so we reset both the provider slot AND the latch.
    """
    # The attribute name has changed across OTel versions; try both.
    for once_attr in ("_TRACER_PROVIDER_SET_ONCE", "_TRACER_PROVIDER_SET_ONCE_DONE"):
        once = getattr(trace, once_attr, None)
        if once is not None and hasattr(once, "_done"):
            once._done = False
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def reset_otel() -> Generator[None, None, None]:
    """Clear module-level guards before AND after each test."""
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()
    yield
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()


@pytest.fixture
def in_memory_spans() -> Generator[InMemorySpanExporter, None, None]:
    """Install an in-memory exporter + return it for `.get_finished_spans()`.

    Usage::

        def test_thing(in_memory_spans):
            do_thing_that_emits_spans()
            spans = in_memory_spans.get_finished_spans()
            assert spans[0].name == "expected"
    """
    exporter = InMemorySpanExporter()
    resource = Resource.create(
        {"service.name": "test", "deployment.environment": "test"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # Mark the SDK guard as initialized so `setup_tracer_provider` won't
    # overwrite our test provider if the code under test calls configure_otel.
    sdk._initialized = True
    yield exporter
    exporter.clear()
