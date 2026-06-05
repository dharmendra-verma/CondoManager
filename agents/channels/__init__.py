"""``agents.channels`` — channel-adapter framework.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Public surface:

* :class:`NormalizedMessage` — the single shape every downstream agent
  consumes regardless of inbound channel.
* :class:`Channel` — StrEnum of supported channels (moved here from
  ``agents.orchestrator.state`` in CM-29 — channels package owns its enum).
* :class:`ChannelAdapter` — Protocol every adapter implements. The
  one method is ``async def normalize(raw) -> NormalizedMessage``.
* :class:`NormalizationError` — single catchable for the HTTP / queue
  handler that called the adapter.
* :class:`WebAdapter` — reference ``ChannelAdapter`` for the public HTTP
  webhook. Worked example to copy when adding WhatsApp (CM-31),
  Telegram (CM-32), Email (CM-33).
* :class:`AudioTranscriber`, :class:`ImageOcr` — Protocols for the
  voice→text and image→text preprocessors. Real Azure AI Speech /
  Vision wiring lands in CM-34 / CM-35.
* :class:`NoopAudioTranscriber`, :class:`NoopImageOcr` — default stubs
  used until those stories ship.

See ``agents/channels/README.md`` for the recipe to add a new adapter.
"""

from __future__ import annotations

from .base import ChannelAdapter, NormalizationError
from .outbound import (
    LogOutbound,
    OutboundChannel,
    SmtpOutbound,
    TelegramOutbound,
    TwilioOutbound,
    get_outbound_channel,
)
from .preprocessors import (
    AudioTranscriber,
    ImageOcr,
    NoopAudioTranscriber,
    NoopImageOcr,
)
from .schema import (
    Attachment,
    AudioAttachment,
    Channel,
    FileAttachment,
    ImageAttachment,
    NormalizedMessage,
    TextAttachment,
)
from .web import WebAdapter

__all__ = [
    "Attachment",
    "AudioAttachment",
    "AudioTranscriber",
    "Channel",
    "ChannelAdapter",
    "FileAttachment",
    "ImageAttachment",
    "ImageOcr",
    "LogOutbound",
    "NoopAudioTranscriber",
    "NoopImageOcr",
    "NormalizationError",
    "NormalizedMessage",
    "OutboundChannel",
    "SmtpOutbound",
    "TelegramOutbound",
    "TextAttachment",
    "TwilioOutbound",
    "WebAdapter",
    "get_outbound_channel",
]
