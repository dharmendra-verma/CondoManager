"""``NormalizedMessage`` Pydantic schema for inbound channel events.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Every channel-specific adapter (WhatsApp / Telegram / Email / Web) maps
its vendor payload into a ``NormalizedMessage`` so downstream agents
(Triage in CM-30, Maintenance/Knowledge/etc. later) work against a
single shape regardless of channel.

Design choices:

* **Pydantic v2, frozen + extra=forbid**. Schema drift fails loudly at
  adapter time, not silently at agent time. Downstream agents must not
  mutate the message — they construct a new ``AgentState`` with
  computed fields, which is the LangGraph idiom anyway.
* **`AwareDatetime`**. Every timestamp is tz-aware. Avoids the "what
  timezone is this?" question every channel would otherwise hit.
* **Discriminated-union attachments**. ``match attachment.kind:`` reads
  cleanly downstream; Pydantic v2 handles the dispatch via the ``kind``
  literal field.
* **`Channel` is owned by this module (CM-29)**. CM-28 had a local copy
  in ``agents/orchestrator/state.py``; CM-29 moves the source of truth
  here. ``state.py`` now imports from us. The ``UNKNOWN`` value
  represents the pre-route state inside an ``AgentState`` (typically
  set by the channel adapter before it's identified the inbound source).
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Channel(StrEnum):
    """Inbound channel — drives adapter selection + per-channel ops.

    Includes ``UNKNOWN`` for the pre-route state inside an ``AgentState``
    (matches what CM-28 had locally before CM-29 took ownership).
    """

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEB = "web"
    UNKNOWN = "unknown"


class _AttachmentBase(BaseModel):
    """Common fields for every attachment kind."""

    model_config = ConfigDict(extra="forbid")

    media_id: str  # opaque vendor id — for re-fetching the blob later
    filename: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class TextAttachment(_AttachmentBase):
    """A textual sub-payload — e.g. a Twilio MessagingMedia textified
    early, or an email body part the adapter has already extracted."""

    kind: Literal["text"] = "text"
    content: str


class AudioAttachment(_AttachmentBase):
    """Audio attachment. ``transcript`` is filled by an
    ``AudioTranscriber`` (CM-34 will wire Azure AI Speech). ``None`` is
    valid for the no-op stub case — Triage handles the absent transcript
    gracefully."""

    kind: Literal["audio"] = "audio"
    transcript: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ImageAttachment(_AttachmentBase):
    """Image attachment. ``ocr_text`` is filled by an ``ImageOcr`` (CM-35
    will wire Azure AI Vision)."""

    kind: Literal["image"] = "image"
    ocr_text: str | None = None
    mime_type: str | None = None


class FileAttachment(_AttachmentBase):
    """Catch-all for binary attachments we don't preprocess (PDFs,
    spreadsheets, archive files). Downstream agents may opt to surface
    these to a human reviewer rather than try to interpret them."""

    kind: Literal["file"] = "file"
    mime_type: str | None = None


Attachment = Annotated[
    TextAttachment | AudioAttachment | ImageAttachment | FileAttachment,
    Field(discriminator="kind"),
]


class NormalizedMessage(BaseModel):
    """One inbound message after channel-specific normalization.

    Every downstream consumer accepts this shape and only this shape, so
    swapping channels is a zero-touch change for them. Adapters MUST
    construct via ``ChannelAdapter.normalize(raw)``; the Pydantic
    validation catches schema drift the moment it lands.

    Fields:
      channel: Which channel produced the message.
      tenant_id: Which condo association this message belongs to.
      sender_id: Vendor-specific identifier (E.164 phone / Telegram
          username / email address).
      content: Already-PII-masked text content. Adapters call CM-27's
          ``mask_pii`` before constructing the message.
      attachments: Polymorphic list — see the discriminated union above.
      received_at: When the channel saw the message (vendor timestamp).
      received_by_us_at: When our adapter ran. Difference bounds the
          channel→us latency budget.
      upstream_message_id: Vendor-opaque correlation (Twilio MessageSid,
          Telegram update_id, RFC-822 Message-ID). NOT ``request_id`` —
          that's our internal CM-21 ContextVar.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: Channel
    tenant_id: str
    sender_id: str
    content: str
    attachments: list[Attachment] = Field(default_factory=list)
    received_at: AwareDatetime
    received_by_us_at: AwareDatetime
    upstream_message_id: str

    @property
    def latency_ms(self) -> float:
        """Channel → us latency in milliseconds. Negative if the vendor
        clock is ahead of ours (clock skew); callers should ``abs()`` if
        they want a magnitude."""
        delta: dt.timedelta = self.received_by_us_at - self.received_at
        return delta.total_seconds() * 1000.0
