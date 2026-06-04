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

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.channels.base import NormalizationError

from .flag import is_webchat_enabled
from .service import UnknownTenantError, handle_message, resolve_tenant

app = FastAPI(title="condomanager-webchat-test", docs_url=None, redoc_url=None)

# Local dev only: the SPA dev server calls these endpoints cross-origin. The
# whole app is dev/test-gated, and the origins are restricted to localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
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
