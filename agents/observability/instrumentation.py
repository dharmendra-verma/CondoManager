"""Auto-instrumentation registration: FastAPI, httpx, openai (+ Traceloop for LangChain).

Jira: CM-21  | Epic: Observability  | Phase 0

The settled split:

* FastAPI + httpx -> OTel community instrumentors. Standard, well-tested.
* openai -> ``openinference`` (richer LLM-specific attributes than Traceloop's
  built-in openai instrumentor; used downstream by Phoenix / Arize and
  matches the attribute shape App Insights + LangSmith expect).
* LangChain / LangGraph -> Traceloop SDK, with its ``openai`` instrumentor
  carved out via the ``instruments`` allowlist so we don't double-emit
  spans for the same OpenAI call.

Idempotency: every instrumentor here is a no-op if already installed,
which means ``configure_otel()`` can be called more than once (tests,
serverless cold/warm starts, hot reload) without a "double instrument"
exception. We still guard with a module-level flag so the second call
short-circuits before importing Traceloop again.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

_log = logging.getLogger(__name__)

# Module-level guard so the second call is fast. Tests reset via the
# `reset_otel` fixture which calls `_reset_for_tests()`.
_instrumented: bool = False


def register_auto_instrumentation(*, app: Any | None = None) -> None:
    """Install auto-instrumentation for FastAPI, httpx, openai, and LangChain.

    Args:
        app: If supplied, attach FastAPI instrumentation directly to this
            app instance (``FastAPIInstrumentor.instrument_app(app)``). If
            ``None``, install the global instrumentor so any FastAPI app
            constructed after this call is auto-instrumented. The per-app
            form is preferred when you have the app handle, because it
            avoids surprising other FastAPI apps in the same process.
    """
    global _instrumented
    if _instrumented:
        _log.debug("auto-instrumentation already registered; skipping")
        return

    # FastAPI — per-app if we have it, global otherwise. The per-app branch
    # delegates to ``instrument_fastapi_app`` so there is one code path that
    # attaches the server-span middleware; when the app was already instrumented
    # at import time (the correct place — see that function's docstring) this is
    # an idempotent no-op.
    if app is not None:
        instrument_fastapi_app(app)
    else:
        FastAPIInstrumentor().instrument()

    # httpx — global; instruments all sync + async clients.
    HTTPXClientInstrumentor().instrument()

    # OpenAI via openinference. Imported lazily so projects that don't use
    # openai never pay for the import.
    _register_openai_instrumentation()

    # Traceloop for LangChain / LangGraph — disjoint with openinference.
    _register_traceloop_for_langchain()

    _instrumented = True


def instrument_fastapi_app(app: Any) -> None:
    """Attach FastAPI server-span instrumentation to ``app`` — call at IMPORT time.

    This MUST run right after the FastAPI app is constructed, BEFORE the ASGI
    server (uvicorn) builds the middleware stack and starts serving.
    ``FastAPIInstrumentor.instrument_app`` adds an ASGI middleware, and Starlette
    freezes its middleware stack on startup; calling it later (e.g. from the
    FastAPI lifespan, where ``configure_otel(app=app)`` runs) is too late and
    silently yields ZERO server-request spans. That is exactly why App Insights
    showed traces/dependencies/metrics but an empty ``requests`` table — so the
    latency / Performance panels never lit up (CM-80).

    Idempotent: the instrumentor sets ``_is_instrumented_by_opentelemetry`` on the
    app, so a later ``register_auto_instrumentation(app=app)`` call no-ops it. The
    middleware resolves the global TracerProvider lazily per request, so attaching
    it before ``setup_tracer_provider`` runs in the lifespan is safe — the spans
    still land on whatever provider is global when the request is handled.
    """
    FastAPIInstrumentor.instrument_app(app)


def _register_openai_instrumentation() -> None:
    """OpenAI -> openinference (richer LLM attributes than Traceloop's openai)."""
    # Import-time side effects: instrument() monkey-patches the openai SDK
    # client classes. Idempotent — second call is a no-op.
    from openinference.instrumentation.openai import OpenAIInstrumentor  # noqa: PLC0415

    OpenAIInstrumentor().instrument()


def _register_traceloop_for_langchain() -> None:
    """Traceloop SDK for LangChain/LangGraph, with `openai` carved out.

    The ``instruments`` kwarg on ``Traceloop.init`` is the supported way
    to install only a subset of Traceloop's auto-instrumentors. We pick
    LangChain + LlamaIndex (the two LLM-orchestration frameworks we expect
    to use) and explicitly exclude openai (covered by openinference above).

    If a future Traceloop release drops ``instruments``, the fallback is
    documented in ``docs/OBSERVABILITY.md``: call ``Traceloop.init()`` and
    then re-call ``OpenAIInstrumentor().instrument()`` so openinference's
    wrapper is the last writer (instrumentors stack as a LIFO).
    """
    try:
        from traceloop.sdk import Traceloop  # noqa: PLC0415
        from traceloop.sdk.instruments import Instruments  # noqa: PLC0415
    except ImportError:
        _log.warning("traceloop-sdk is not installed; LangChain/LangGraph spans will not emit")
        return

    Traceloop.init(
        # Don't have Traceloop construct its own BatchSpanProcessor — the
        # TracerProvider we set up in `sdk.py` already has one and Traceloop
        # piggybacks on the global provider.
        disable_batch=False,
        # Disjoint scoping: LangChain + LlamaIndex only. openai is owned by
        # openinference; bedrock/anthropic/etc. we don't use today.
        instruments={Instruments.LANGCHAIN, Instruments.LLAMA_INDEX},
        # Cost / token metrics enrichment is Langfuse + App Insights workbook
        # territory (CM-24, CM-25), not Traceloop's. Keep it off.
        should_enrich_metrics=False,
    )


def _reset_for_tests() -> None:
    """Test-only — clears the module-level guard. Do not call from app code."""
    global _instrumented
    _instrumented = False
