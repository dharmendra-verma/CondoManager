"""Shared fixtures for ``tests/orchestrator/``.

Each test gets a fresh OTel TracerProvider with an ``InMemorySpanExporter``
so guardrail and node span emissions can be asserted without network I/O.
The pattern mirrors ``tests/observability/conftest.py`` (CM-21) — same
``_reset_otel_globals`` machinery, same ``in_memory_spans`` fixture
shape so tests across the two suites read identically.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from agents.observability import instrumentation, sdk
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _reset_otel_globals() -> None:
    """Hard-reset the OTel set-once tracer-provider guard (CM-21 pattern)."""
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


@pytest.fixture(autouse=True)
def _force_offline_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the deterministic heuristic Triage classifier (CM-30).

    Graph + hello-world tests assert topology, routing, and the trace
    contract — not LLM output quality. ``get_triage_classifier()`` returns the
    real ``LLMTriageClassifier`` whenever ``OPENAI_API_KEY`` is set, so a
    developer running the suite with a live key in their shell would hit the
    network and could flake (e.g. the model classifying ``"ping"`` somewhere
    other than ``knowledge``). CI has no key, so this just makes local runs
    match CI. Tests that specifically want the LLM path set the key themselves
    via ``monkeypatch.setenv`` (which runs after this autouse fixture).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def in_memory_spans() -> Generator[InMemorySpanExporter, None, None]:
    """Install an in-memory exporter on a fresh TracerProvider."""
    exporter = InMemorySpanExporter()
    resource = Resource.create(
        {"service.name": "orchestrator-test", "deployment.environment": "test"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    sdk._initialized = True
    yield exporter
    exporter.clear()


@pytest.fixture
def memory_checkpointer() -> MemorySaver:
    """Fresh ``MemorySaver`` so checkpoint state doesn't leak across tests."""
    return MemorySaver()
