"""CM-55 — service pipeline: WebAdapter raw contract, mask-before-normalize, reply."""

from __future__ import annotations

import datetime as dt

import pytest
from agents.channels.schema import Channel, NormalizedMessage
from agents.webchat import service
from agents.webchat.service import (
    UnknownTenantError,
    _render_reply,
    _run_pipeline,
    build_raw,
    handle_message,
    normalize_message,
    resolve_tenant,
)

# The six documented WebAdapter raw keys (channel + received_by_us_at are
# injected by the adapter, not the caller).
WEBADAPTER_RAW_KEYS = {
    "tenant_id",
    "sender_id",
    "content",
    "received_at",
    "upstream_message_id",
    "attachments",
}


def test_build_raw_matches_webadapter_contract() -> None:
    tenant = resolve_tenant("+919876543210")
    raw = build_raw(tenant, "No hot water")
    assert set(raw) == WEBADAPTER_RAW_KEYS
    assert raw["tenant_id"] == tenant.tenant_id
    assert raw["sender_id"] == tenant.mobile
    assert raw["attachments"] == []
    # received_at must be a parseable aware timestamp.
    assert dt.datetime.fromisoformat(raw["received_at"]).tzinfo is not None


async def test_normalize_produces_web_channel_message() -> None:
    tenant = resolve_tenant("+919876543210")
    msg = await normalize_message(tenant, "No hot water in 4B")
    assert isinstance(msg, NormalizedMessage)
    assert msg.channel == Channel.WEB
    assert msg.tenant_id == tenant.tenant_id
    assert msg.sender_id == tenant.mobile
    assert msg.attachments == []


async def test_content_is_pii_masked_before_normalize() -> None:
    tenant = resolve_tenant("+919876543210")
    msg = await normalize_message(
        tenant, "reach me on +14155551234 or at jane@example.com",
    )
    # Raw PII must be gone, replaced by the CM-27 redaction tokens.
    assert "+14155551234" not in msg.content
    assert "jane@example.com" not in msg.content
    assert "+***" in msg.content
    assert "***@***.***" in msg.content


def test_resolve_unknown_raises() -> None:
    with pytest.raises(UnknownTenantError):
        resolve_tenant("+10000000000")


async def test_handle_message_returns_renderable_reply() -> None:
    out = await handle_message("+919876543210", "There is a leak under the sink")
    assert isinstance(out["reply"], str)
    assert out["reply"].strip() != ""
    assert out["channel"] == "web"
    assert isinstance(out["stub"], bool)
    # masked_content proves the message went through the normalize path.
    assert isinstance(out["masked_content"], str)


async def test_handle_message_masks_pii_in_pipeline() -> None:
    out = await handle_message("+919876543210", "call +14155551234 about the leak")
    assert "+14155551234" not in out["masked_content"]
    assert "+***" in out["masked_content"]


async def test_handle_message_unknown_number_raises() -> None:
    with pytest.raises(UnknownTenantError):
        await handle_message("+10000000000", "hello")


# --- CM-95: Coordinator reply rendering ------------------------------------


def test_render_reply_renders_coordinator_reply() -> None:
    """The Coordinator's combined answer lives under output['reply'] — render it."""
    final = {
        "intent": "maintenance",
        "output": {
            "status": "coordinated",
            "reply": "We've logged ticket TKT-ABC123. Policy: owners only.",
        },
    }
    text, stub = _render_reply(final)
    assert stub is False
    assert "TKT-ABC123" in text


def test_render_reply_prefers_specialist_keys_over_reply() -> None:
    """A single-specialist confirmation/answer still wins when present."""
    text, stub = _render_reply(
        {"output": {"confirmation": "logged TKT-2", "reply": "should-not-win"}}
    )
    assert stub is False
    assert text == "logged TKT-2"


def test_render_reply_stub_when_no_renderable_text() -> None:
    text, stub = _render_reply(
        {"intent": "maintenance", "urgency": "high", "output": {"status": "coordinated"}}
    )
    assert stub is True
    assert "stub acknowledgement" in text


async def test_handle_message_multi_intent_returns_coordinator_reply() -> None:
    """End-to-end: a compound message routes to the Coordinator and returns its
    combined reply (not a stub) — the CM-95 regression repro, offline."""
    out = await handle_message(
        "+919876543210",
        "the sink is leaking and can a tenant book the banquet hall?",
    )
    assert out["stub"] is False
    assert out["intent"] == "maintenance"
    # The synthesized reply carries the maintenance ticket confirmation.
    assert "TKT-" in out["reply"]


def test_run_pipeline_logs_when_graph_invoke_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A graph-invoke failure is logged (with traceback), not silently swallowed,
    and degrades to the triage-only fallback."""
    import logging

    from agents.channels.schema import Channel
    from agents.orchestrator.state import AgentState

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("graph boom")

    monkeypatch.setattr(service, "build_graph", _boom)
    state = AgentState(
        tenant_id="condo-tower-a",
        request_id="r1",
        channel=Channel.WEB,
        raw_message="the heater is broken",
    )
    with caplog.at_level(logging.ERROR, logger="agents.webchat.service"):
        result = _run_pipeline(state, "r1")
    assert "graph invoke failed" in caplog.text
    # Fell back to triage-only: a classification is still present.
    assert result.get("intent") is not None
