"""CM-55 web chat pipeline: resolve tenant -> mask -> normalize -> triage.

The HTTP layer (``app.py``) is intentionally thin; the channel logic lives here
so it is unit-testable without a running server. This is the **first real
consumer** of the CM-29 :class:`~agents.channels.web.WebAdapter` reference
adapter and reuses ``Channel.WEB`` (no new enum value).

Flow (``handle_message``):

1. Resolve the mobile number to a hardcoded :class:`~agents.webchat.tenants.TestTenant`
   (raises :class:`UnknownTenantError` on a miss).
2. Shape the documented ``WebAdapter`` raw payload and **PII-mask the content
   via CM-27 ``mask_pii`` BEFORE** ``normalize`` (the adapter does not re-mask).
3. ``await WebAdapter().normalize(raw)`` -> a validated ``NormalizedMessage``
   with ``channel = web``.
4. Build an :class:`~agents.orchestrator.state.AgentState` and run the CM-30
   triage/agent spine. The reply is the agent's output when the live loop is
   wired, else a clearly-labelled stub acknowledgement carrying the triage
   classification (offline / no-credentials envs).
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

from agents.channels import WebAdapter
from agents.channels.schema import Channel, NormalizedMessage
from agents.observability import mask_pii
from agents.orchestrator.graph import build_graph
from agents.orchestrator.state import AgentState

from .tenants import TestTenant, lookup_tenant


class UnknownTenantError(Exception):
    """Raised when a mobile number is not in the TEST_TENANTS map."""


def resolve_tenant(mobile: str) -> TestTenant:
    """Resolve ``mobile`` to a test tenant or raise :class:`UnknownTenantError`."""
    tenant = lookup_tenant(mobile)
    if tenant is None:
        raise UnknownTenantError(mobile)
    return tenant


def build_raw(
    tenant: TestTenant,
    content: str,
    *,
    received_at: dt.datetime | None = None,
    upstream_message_id: str | None = None,
) -> dict[str, Any]:
    """Shape the ``WebAdapter`` raw payload.

    ``content`` is PII-masked here — **before** ``normalize`` — per the adapter
    contract (the adapter is the single masking-decision boundary and does not
    re-mask). The returned dict carries exactly the six documented keys; the
    adapter injects ``channel`` + ``received_by_us_at`` itself.
    """
    ts = received_at or dt.datetime.now(dt.UTC)
    return {
        "tenant_id": tenant.tenant_id,
        "sender_id": tenant.mobile,
        "content": mask_pii(content),
        "received_at": ts.isoformat(),
        "upstream_message_id": upstream_message_id or f"web_{uuid4().hex}",
        "attachments": [],
    }


async def normalize_message(tenant: TestTenant, content: str) -> NormalizedMessage:
    """Mask + normalize one message into a ``NormalizedMessage`` (channel=web)."""
    raw = build_raw(tenant, content)
    return await WebAdapter().normalize(raw)


def _as_dict(state: Any) -> dict[str, Any]:
    """Normalize LangGraph's invoke result to a plain dict.

    ``invoke`` returns the merged state, which is a dict-like ``AddableValuesDict``
    on current LangGraph but could be an ``AgentState`` — handle both.
    """
    if isinstance(state, dict):
        return state
    if hasattr(state, "model_dump"):
        return state.model_dump()  # type: ignore[no-any-return]
    return dict(state)


def _enum_value(value: Any) -> Any:
    """Return ``.value`` for an enum, else the value unchanged (or None)."""
    return getattr(value, "value", value)


def _stub_reply(intent: Any, urgency: Any) -> str:
    """A clearly-labelled stub acknowledgement carrying the triage classification."""
    intent_s = _enum_value(intent) or "unknown"
    urgency_s = _enum_value(urgency) or "unknown"
    return (
        f"[stub acknowledgement] Thanks — we received your message and triaged it "
        f"as {intent_s} (urgency: {urgency_s}). A condo manager will follow up. "
        f"(The live agent reply isn't wired in this environment.)"
    )


def _render_reply(final: dict[str, Any]) -> tuple[str, bool]:
    """Turn the graph's final state into a (reply_text, is_stub) pair.

    Prefers a tenant-facing string from the agent ``output`` (maintenance
    ``confirmation``, knowledge ``answer``, or a guardrail ``reason``); falls
    back to a labelled stub built from the triage classification.
    """
    output = final.get("output") or {}
    if isinstance(output, dict):
        for key in ("confirmation", "answer", "reason"):
            text = output.get(key)
            if isinstance(text, str) and text.strip():
                return text, False
    return _stub_reply(final.get("intent"), final.get("urgency")), True


def _run_pipeline(state: AgentState, request_id: str) -> dict[str, Any]:
    """Run the agent spine, degrading gracefully so a reply is always produced.

    Preferred path is the full LangGraph spine (``build_graph().invoke``). If a
    downstream dependency is unavailable in this env (e.g. no Cosmos), we fall
    back to the triage node alone for a classification, and finally to the bare
    state — every path yields enough for ``_render_reply`` to respond.
    """
    try:
        final = build_graph().invoke(
            state, config={"configurable": {"thread_id": request_id}}
        )
        return _as_dict(final)
    except Exception:
        try:
            from agents.orchestrator import nodes  # noqa: PLC0415 -- lazy seam

            update = nodes.triage(state)
            return {**state.model_dump(), **update}
        except Exception:
            return state.model_dump()


async def handle_message(mobile: str, content: str) -> dict[str, Any]:
    """End-to-end: resolve -> mask -> normalize -> triage; return a reply dict.

    Returns ``{reply, stub, channel, intent, masked_content}``. Raises
    :class:`UnknownTenantError` on an unknown number and
    :class:`~agents.channels.base.NormalizationError` if the payload can't be
    normalized — the HTTP layer maps those to 404 / 400.
    """
    tenant = resolve_tenant(mobile)
    message = await normalize_message(tenant, content)
    request_id = message.upstream_message_id

    state = AgentState(
        tenant_id=tenant.tenant_id,
        request_id=request_id,
        channel=Channel.WEB,
        normalized=message,
    )
    final = _run_pipeline(state, request_id)
    reply, stub = _render_reply(final)

    return {
        "reply": reply,
        "stub": stub,
        "channel": Channel.WEB.value,
        "intent": _enum_value(final.get("intent")),
        "masked_content": message.content,
    }
