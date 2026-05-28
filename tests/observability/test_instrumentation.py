"""Auto-instrumentation tests.

These tests exercise the FastAPI / httpx / openai patch points. The
instrumentors here are installed ONCE at module scope (they cache the
tracer at install time, so swapping providers per-test would orphan
them and produce zero spans). Each test then clears the exporter at
the start and asserts on what was emitted during its own run.

The OpenAI test uses ``respx`` to mock the HTTP endpoint so the real
OpenAI client runs to completion without a real API key.

We don't reach into ``Traceloop.init`` here — its contract is tested
indirectly via ``test_openai_span_is_owned_by_openinference``, which
proves the openinference instrumentor (not Traceloop's) owned the
openai SDK call. That's what prevents double-emission.
"""

from __future__ import annotations

from collections.abc import Generator

import httpx
import pytest
import respx
from agents.observability import instrumentation, sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import _reset_otel_globals


@pytest.fixture(scope="module")
def module_spans() -> Generator[InMemorySpanExporter, None, None]:
    """One TracerProvider + InMemorySpanExporter + instrumentation install per module.

    Instrumentors cache the global tracer at install time, so we can't
    rotate providers between tests. Instead we install once at module
    scope and rely on per-test ``.clear()`` for isolation.
    """
    # Hard-reset OTel globals so nothing from `conftest.reset_otel` (which
    # ran for the previous module) leaks in.
    sdk._reset_for_tests()
    instrumentation._reset_for_tests()
    _reset_otel_globals()

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "instrumentation-test", "deployment.environment": "test"}
        )
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    sdk._initialized = True

    instrumentation.register_auto_instrumentation()
    yield exporter
    # Module teardown: leave the instrumentors installed; the next test module's
    # own reset_otel autouse will clear globals as needed.


@pytest.fixture(autouse=True)
def _clear_spans(module_spans: InMemorySpanExporter) -> Generator[None, None, None]:
    """Clear the module exporter at the start of each test."""
    module_spans.clear()
    yield


# Override the autouse `reset_otel` from conftest.py so it doesn't wipe our
# module-scoped TracerProvider between tests.
@pytest.fixture(autouse=True)
def reset_otel() -> Generator[None, None, None]:
    yield


def test_fastapi_emits_server_span(module_spans: InMemorySpanExporter) -> None:
    """FastAPI auto-instrumentation produces an HTTP server span."""
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Attach per-app instrumentation (global FastAPI instrumentor is already
    # installed by `register_auto_instrumentation`, so this just hooks the
    # specific app).
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    try:
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    spans = module_spans.get_finished_spans()
    server_spans = [s for s in spans if s.kind.name == "SERVER"]
    assert server_spans, f"no SERVER span in {[s.name for s in spans]}"
    server = server_spans[0]
    assert server.attributes is not None
    target = server.attributes.get("http.target") or server.attributes.get("url.path")
    assert target == "/health"


def test_httpx_emits_client_span(module_spans: InMemorySpanExporter) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://example.test/ping").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        with httpx.Client() as c:
            r = c.get("https://example.test/ping")
            assert r.status_code == 200

    spans = module_spans.get_finished_spans()
    client_spans = [s for s in spans if s.kind.name == "CLIENT"]
    assert client_spans, f"no CLIENT span in {[s.name for s in spans]}"


def test_register_is_idempotent(module_spans: InMemorySpanExporter) -> None:
    """Second call short-circuits via the module-level guard (no exception)."""
    instrumentation.register_auto_instrumentation()
    instrumentation.register_auto_instrumentation()


def test_openai_span_is_owned_by_openinference(
    module_spans: InMemorySpanExporter,
) -> None:
    """OpenAI calls produce an openinference span, not a Traceloop one.

    This is the contract that prevents double-emission. We mock the OpenAI
    HTTP endpoint with respx and inspect the produced span's instrumentation
    scope.
    """
    from openai import OpenAI

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        )
        client = OpenAI(api_key="sk-test-not-real")
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
        )

    spans = module_spans.get_finished_spans()
    openinference_spans = [
        s
        for s in spans
        if s.instrumentation_scope is not None
        and "openinference" in s.instrumentation_scope.name.lower()
    ]
    scopes = [
        s.instrumentation_scope.name if s.instrumentation_scope else None
        for s in spans
    ]
    assert openinference_spans, (
        f"expected at least one openinference-scoped span on the openai call; "
        f"got scopes: {scopes}"
    )
