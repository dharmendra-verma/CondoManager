"""Tests for Protocol compliance + preprocessor stub behavior.

Verifies that ``WebAdapter`` is recognized as a ``ChannelAdapter`` via
``isinstance`` (the ``@runtime_checkable`` Protocol works), and that the
no-op preprocessor stubs return the documented sentinel strings.
"""

from __future__ import annotations

import pytest

from agents.channels.base import ChannelAdapter
from agents.channels.preprocessors.audio import (
    NOOP_AUDIO_SENTINEL,
    AudioTranscriber,
    NoopAudioTranscriber,
)
from agents.channels.preprocessors.image import (
    NOOP_IMAGE_SENTINEL,
    ImageOcr,
    NoopImageOcr,
)
from agents.channels.web import WebAdapter


def test_web_adapter_is_channel_adapter() -> None:
    """``WebAdapter`` satisfies the runtime-checkable Protocol."""
    assert isinstance(WebAdapter(), ChannelAdapter)


def test_noop_audio_transcriber_is_protocol_compliant() -> None:
    assert isinstance(NoopAudioTranscriber(), AudioTranscriber)


def test_noop_image_ocr_is_protocol_compliant() -> None:
    assert isinstance(NoopImageOcr(), ImageOcr)


@pytest.mark.asyncio
async def test_noop_audio_returns_sentinel() -> None:
    """Triage (CM-30) recognizes this exact sentinel — do not change."""
    result = await NoopAudioTranscriber().transcribe(b"fake audio bytes")
    assert result == NOOP_AUDIO_SENTINEL
    assert result == "[audio: not transcribed]"  # explicit literal — tripwire


@pytest.mark.asyncio
async def test_noop_image_returns_sentinel() -> None:
    """Triage (CM-30) recognizes this exact sentinel — do not change."""
    result = await NoopImageOcr().extract_text(b"fake image bytes")
    assert result == NOOP_IMAGE_SENTINEL
    assert result == "[image: OCR not configured]"  # explicit literal — tripwire


@pytest.mark.asyncio
async def test_noop_audio_accepts_language_kwarg() -> None:
    """Language hint is part of the Protocol — stub must accept it."""
    result = await NoopAudioTranscriber().transcribe(b"audio", language="hi-IN")
    assert result == NOOP_AUDIO_SENTINEL


@pytest.mark.asyncio
async def test_noop_image_accepts_mime_type_kwarg() -> None:
    """MIME hint is part of the Protocol — stub must accept it."""
    result = await NoopImageOcr().extract_text(b"image", mime_type="image/jpeg")
    assert result == NOOP_IMAGE_SENTINEL
