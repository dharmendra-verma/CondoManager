"""CM-55 — service pipeline: WebAdapter raw contract, mask-before-normalize, reply."""

from __future__ import annotations

import datetime as dt

import pytest
from agents.channels.schema import Channel, NormalizedMessage
from agents.webchat.service import (
    UnknownTenantError,
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
