"""Tests for ``agents.channels.web.WebAdapter``.

Covers AC #2 for the reference adapter: a valid raw payload normalizes,
missing required fields raise ``NormalizationError`` (NOT a raw Pydantic
``ValidationError`` — adapter wraps the underlying exception).
"""

from __future__ import annotations

import datetime as dt

import pytest
from agents.channels.base import NormalizationError
from agents.channels.schema import Channel
from agents.channels.web import WebAdapter


def _raw(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_id": "t-1",
        "sender_id": "user_42",
        "content": "hello",
        "received_at": "2026-05-29T10:00:00+00:00",
        "upstream_message_id": "web_abc",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_happy_path() -> None:
    msg = await WebAdapter().normalize(_raw())
    assert msg.channel == Channel.WEB
    assert msg.tenant_id == "t-1"
    assert msg.sender_id == "user_42"
    assert msg.content == "hello"
    assert msg.upstream_message_id == "web_abc"
    assert msg.received_at.tzinfo is not None
    # received_by_us_at is injected by the adapter — must be aware + recent.
    assert msg.received_by_us_at.tzinfo is not None


@pytest.mark.asyncio
async def test_missing_required_field_raises_normalization_error() -> None:
    raw = _raw()
    del raw["tenant_id"]
    with pytest.raises(NormalizationError):
        await WebAdapter().normalize(raw)


@pytest.mark.asyncio
async def test_extra_unknown_field_raises_normalization_error() -> None:
    """Adapter rejects vendor-sneak-in attempts (extra=forbid on the schema)."""
    raw = _raw(secret_admin_flag=True)
    with pytest.raises(NormalizationError):
        await WebAdapter().normalize(raw)


@pytest.mark.asyncio
async def test_non_dict_input_raises_normalization_error() -> None:
    with pytest.raises(NormalizationError):
        await WebAdapter().normalize("not a dict")  # type: ignore[arg-type]
    with pytest.raises(NormalizationError):
        await WebAdapter().normalize(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_attachments_pass_through() -> None:
    raw = _raw(
        attachments=[
            {"kind": "text", "media_id": "m1", "content": "the quoted bit"},
        ],
    )
    msg = await WebAdapter().normalize(raw)
    assert len(msg.attachments) == 1
    assert msg.attachments[0].kind == "text"


@pytest.mark.asyncio
async def test_adapter_does_not_mask_content() -> None:
    """The adapter trusts the caller to have PII-masked. It does not re-mask
    (single source of truth — see CM-27)."""
    raw = _raw(content="user@example.com signed in")
    msg = await WebAdapter().normalize(raw)
    # No masking happens in the adapter.
    assert msg.content == "user@example.com signed in"


@pytest.mark.asyncio
async def test_received_by_us_at_is_recent() -> None:
    """The adapter stamps its own clock — must be UTC-aware + ~now."""
    msg = await WebAdapter().normalize(_raw())
    now = dt.datetime.now(dt.UTC)
    age = abs((now - msg.received_by_us_at).total_seconds())
    assert age < 1.0  # within 1 second
