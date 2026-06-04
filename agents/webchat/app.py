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
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.channels.base import NormalizationError

from .flag import is_webchat_enabled
from .service import UnknownTenantError, handle_message, resolve_tenant

app = FastAPI(title="condomanager-webchat-test", docs_url=None, redoc_url=None)

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
