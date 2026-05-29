"""Tests for ``agents.channels.schema`` — ``NormalizedMessage`` + discriminated
``Attachment`` union.

Covers AC #1: the schema enforces channel, tenant_id, content, attachments,
and timestamps. Tz-naive timestamps raise; unknown attachment kinds raise;
frozen models reject assignment; roundtrips through JSON yield equal objects.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from agents.channels.schema import (
    AudioAttachment,
    Channel,
    FileAttachment,
    ImageAttachment,
    NormalizedMessage,
    TextAttachment,
)


# Helper: a minimal valid NormalizedMessage payload.
def _make(**overrides: object) -> NormalizedMessage:
    payload: dict[str, object] = {
        "channel": Channel.WEB,
        "tenant_id": "t-1",
        "sender_id": "user_42",
        "content": "hello",
        "received_at": dt.datetime(2026, 5, 29, 10, 0, tzinfo=dt.timezone.utc),
        "received_by_us_at": dt.datetime(2026, 5, 29, 10, 0, 1, tzinfo=dt.timezone.utc),
        "upstream_message_id": "web_abc",
    }
    payload.update(overrides)
    return NormalizedMessage.model_validate(payload)


def test_minimal_message_constructs() -> None:
    msg = _make()
    assert msg.channel == Channel.WEB
    assert msg.tenant_id == "t-1"
    assert msg.content == "hello"
    assert msg.attachments == []


def test_required_fields_raise_when_missing() -> None:
    for missing in (
        "channel",
        "tenant_id",
        "sender_id",
        "content",
        "received_at",
        "received_by_us_at",
        "upstream_message_id",
    ):
        payload: dict[str, object] = {
            "channel": Channel.WEB,
            "tenant_id": "t",
            "sender_id": "s",
            "content": "c",
            "received_at": dt.datetime.now(dt.timezone.utc),
            "received_by_us_at": dt.datetime.now(dt.timezone.utc),
            "upstream_message_id": "u",
        }
        payload.pop(missing)
        with pytest.raises(ValidationError):
            NormalizedMessage.model_validate(payload)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _make(unknown_field="oops")


def test_tz_naive_timestamps_rejected() -> None:
    """``AwareDatetime`` enforcement — naive timestamps are bugs upstream."""
    naive = dt.datetime(2026, 5, 29, 10, 0)  # no tzinfo
    with pytest.raises(ValidationError):
        _make(received_at=naive)
    with pytest.raises(ValidationError):
        _make(received_by_us_at=naive)


def test_frozen_model_rejects_assignment() -> None:
    msg = _make()
    with pytest.raises(ValidationError):
        msg.content = "tampered"  # type: ignore[misc]


def test_json_roundtrip() -> None:
    msg = _make(
        attachments=[
            TextAttachment(media_id="m1", content="quoted reply"),
            AudioAttachment(media_id="m2", duration_ms=4200),
        ],
    )
    blob = msg.model_dump_json()
    msg2 = NormalizedMessage.model_validate_json(blob)
    assert msg2 == msg


def test_attachment_discriminator_dispatches_per_kind() -> None:
    """Every attachment kind round-trips through the discriminated union."""
    cases = [
        {"kind": "text", "media_id": "m1", "content": "abc"},
        {"kind": "audio", "media_id": "m2", "transcript": "hi there"},
        {"kind": "image", "media_id": "m3", "ocr_text": "STOP", "mime_type": "image/jpeg"},
        {"kind": "file", "media_id": "m4", "mime_type": "application/pdf"},
    ]
    expected_types = (TextAttachment, AudioAttachment, ImageAttachment, FileAttachment)
    msg = _make(attachments=cases)
    for got, want_type in zip(msg.attachments, expected_types, strict=True):
        assert isinstance(got, want_type)


def test_unknown_attachment_kind_raises() -> None:
    with pytest.raises(ValidationError):
        _make(attachments=[{"kind": "telepathy", "media_id": "m"}])


def test_latency_ms_property() -> None:
    early = dt.datetime(2026, 5, 29, 10, 0, 0, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 5, 29, 10, 0, 0, 250000, tzinfo=dt.timezone.utc)  # +250ms
    msg = _make(received_at=early, received_by_us_at=later)
    assert msg.latency_ms == pytest.approx(250.0, abs=0.001)


def test_empty_attachments_default() -> None:
    msg = _make()
    assert msg.attachments == []
