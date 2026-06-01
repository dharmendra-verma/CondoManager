"""``WebAdapter`` — reference ``ChannelAdapter`` for the public HTTP webhook.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Worked example for CM-31/32/33 — copy this file's shape when adding
WhatsApp / Telegram / Email adapters. The only adapter-specific concern
the Web one has is timestamp handling (the caller provides
``received_at``; our ``received_by_us_at`` is now). The vendor adapters
will also need to async-refetch media URLs inside ``normalize``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import ValidationError

from agents.channels.base import NormalizationError
from agents.channels.schema import Channel, NormalizedMessage
from agents.observability import METRIC_ACK_LATENCY, emit_metric


class WebAdapter:
    """Normalize a public web HTTP webhook payload.

    Expected raw shape::

        {
          "tenant_id": "...",
          "sender_id": "user_42",
          "content": "the message body",
          "received_at": "2026-05-29T10:00:00+00:00",
          "upstream_message_id": "web_abc123",
          "attachments": [
            {"kind": "text", "media_id": "...", "content": "..."},
            ...
          ]
        }

    The caller (HTTP handler) MUST PII-mask the ``content`` field before
    handing the dict to ``normalize`` — CM-27 ``mask_pii`` is the canonical
    function. The adapter doesn't mask again (single source of truth for
    masking decisions).
    """

    async def normalize(self, raw: Any) -> NormalizedMessage:
        if not isinstance(raw, dict):
            raise NormalizationError(
                f"WebAdapter.normalize: expected dict, got {type(raw).__name__}",
            )
        # Inject the channel + our timestamp; the caller doesn't know
        # which adapter is processing them.
        payload = {
            **raw,
            "channel": Channel.WEB,
            "received_by_us_at": dt.datetime.now(dt.UTC),
        }
        try:
            message = NormalizedMessage.model_validate(payload)
        except ValidationError as exc:
            # Single catchable for the HTTP layer; leaks no Pydantic
            # internals to the client beyond the short error message.
            raise NormalizationError(str(exc)) from exc

        # CM-46: ack-latency PRD signal (<2s SLO). This is the entry layer, so
        # the honest measure is the channel→us acknowledgement budget already
        # computed by ``NormalizedMessage.latency_ms``. ``tenant_id`` is passed
        # explicitly because the CM-21 correlation ContextVar may not be set
        # this early. No-op when OTel is unconfigured. Future channel adapters
        # copy this one emit (web.py is the reference adapter).
        emit_metric(
            METRIC_ACK_LATENCY,
            value=message.latency_ms,
            channel=message.channel.value,
            tenant_id=message.tenant_id,
        )
        return message
