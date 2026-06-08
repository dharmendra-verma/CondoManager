"""FastAPI app for the CM-55 TEST-ONLY web chat channel.

Jira: CM-55  | Epic: CM-4  | Phase 1

Two endpoints — ``POST /web/login`` and ``POST /web/message`` — both of which
return **404 unless ``WEBCHAT_TEST_ENABLED`` is set**, so the channel is
indistinguishable from "not deployed" in staging/prod. The channel logic lives
in :mod:`agents.webchat.service`; this module is thin HTTP glue.

Run locally::

    WEBCHAT_TEST_ENABLED=1 uvicorn agents.webchat.app:app --port 8000

The vite dev server (portal, :5173) calls these endpoints cross-origin during
``npm run dev`` — see the CORS allow-list below and the ``/web`` dev proxy in
``portal/vite.config.ts``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.channels.base import NormalizationError
from agents.observability import (
    configure_logging,
    configure_otel,
    instrument_fastapi_app,
)
from agents.observability.langfuse_export import flush_langfuse, init_langfuse

from .flag import is_webchat_enabled
from .service import UnknownTenantError, handle_message, resolve_tenant

# deployment.environment resource attribute on emitted spans. This is the prod
# inbound entry point (CM-59); override DEPLOY_ENV locally if desired.
_DEPLOY_ENV = os.environ.get("DEPLOY_ENV", "prod")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize observability once, when the server starts (CM-63).

    Deliberately at startup (not import time) so unit tests that merely
    construct ``TestClient(app)`` without entering its context manager stay
    side-effect-free. Every call here no-ops gracefully when its backend is
    unconfigured: ``configure_otel`` falls back to the console exporter with no
    OTLP endpoint, and ``init_langfuse`` returns ``None`` when the Langfuse keys
    are unset/placeholder. This is what actually activates the CM-24 Langfuse
    path + CM-22 App Insights spans on the live web-chat pipeline; without it the
    ``@observe_node`` decorators and ``langgraph_node_span`` calls have no
    initialized backend to emit to.
    """
    # CM-77: initialize structured JSON stdout logging FIRST so every subsequent
    # app log — startup, init_langfuse, and the per-request triage/knowledge
    # decision lines — emits at INFO and shows up in the Container Apps log stream
    # in real time. Without this the app's loggers fall back to Python's default
    # (root=WARNING, no JSON handler) and all INFO app logs are silently dropped
    # (only uvicorn's own access logs appear) — which is why the decision lines
    # were invisible until now.
    configure_logging(service_name="condomanager-webchat", environment=_DEPLOY_ENV)
    configure_otel(service_name="condomanager-webchat", environment=_DEPLOY_ENV, app=app)
    init_langfuse()
    try:
        yield
    finally:
        # Belt-and-suspenders alongside the per-request flush: drain any buffered
        # Langfuse observations before the worker stops, for scale-to-zero
        # shutdowns where the SDK's background consumer wouldn't run (CM-69).
        flush_langfuse()


app = FastAPI(
    title="condomanager-webchat-test", docs_url=None, redoc_url=None, lifespan=_lifespan
)

# CM-80: attach FastAPI server-request instrumentation HERE, at import time —
# before uvicorn builds the middleware stack and starts serving. Doing it in
# _lifespan (where configure_otel(app=app) runs) is too late: Starlette freezes
# the middleware stack at startup, so instrument_app() silently fails to add its
# span middleware and NO server-request spans are emitted. That left the App
# Insights `requests` table — and the latency panel — empty in prod even though
# traces, dependencies, and metrics all flowed. The exporter/provider/Langfuse
# setup stays in the lifespan; only this middleware attachment must move earlier.
instrument_fastapi_app(app)

# The SPA calls these endpoints cross-origin. In local dev that's the vite dev
# server (localhost:5173); in prod the portal is served from the Static Web App,
# a different origin than this Container App. CM-60: WEBCHAT_CORS_ORIGINS (set by
# container-app.bicep to the SWA origin) is appended to the allow-list — comma-
# separated; empty/unset (local dev, tests) leaves just the localhost origins.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_EXTRA_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("WEBCHAT_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS + _EXTRA_ORIGINS,
    allow_methods=["POST"],
    allow_headers=["content-type"],
)


class LoginRequest(BaseModel):
    mobile: str = Field(min_length=1)


class MessageRequest(BaseModel):
    mobile: str = Field(min_length=1)
    content: str = Field(min_length=1)


def _require_enabled() -> None:
    # 404 (not 403) so the channel looks "not deployed" when the flag is off.
    if not is_webchat_enabled():
        raise HTTPException(status_code=404, detail="not_found")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — ALWAYS 200, independent of WEBCHAT_TEST_ENABLED (CM-59).

    The Container App ingress probe and the prod smoke test hit this to confirm
    the runtime is up. It deliberately does NOT reveal whether the chat channel
    is enabled (``/web/*`` still 404s when the flag is off), so the gate posture
    is unchanged — this only reports that the process is serving HTTP.
    """
    return {"status": "ok", "channel_enabled": is_webchat_enabled()}


@app.post("/web/login")
def login(req: LoginRequest) -> dict[str, str]:
    """Validate a mobile number against the hardcoded test tenants."""
    _require_enabled()
    try:
        tenant = resolve_tenant(req.mobile)
    except UnknownTenantError:
        raise HTTPException(status_code=404, detail="unknown_number") from None
    return {"tenant_id": tenant.tenant_id, "name": tenant.name, "unit": tenant.unit}


@app.post("/web/message")
async def message(req: MessageRequest) -> dict[str, Any]:
    """Run a message through the WebAdapter -> triage pipeline; return the reply."""
    _require_enabled()
    try:
        return await handle_message(req.mobile, req.content)
    except UnknownTenantError:
        raise HTTPException(status_code=404, detail="unknown_number") from None
    except NormalizationError as exc:
        raise HTTPException(status_code=400, detail="normalize_failed") from exc
    finally:
        # Force-send this message's trace before the response returns, so a
        # scale-to-zero teardown right after the request can't drop it (CM-69).
        flush_langfuse()
