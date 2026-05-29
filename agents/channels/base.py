"""``ChannelAdapter`` Protocol + ``NormalizationError`` exception.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Every channel adapter (WhatsApp / Telegram / Email / Web) implements a
single async method ``normalize(raw) -> NormalizedMessage``. The
Protocol is ``@runtime_checkable`` so adapters compose into registries
typed as ``Mapping[Channel, ChannelAdapter]`` and tests can assert
adherence via ``isinstance``.

Why async even for trivially-sync adapters (Web)? WhatsApp/Telegram
both need to async-fetch media URLs (Twilio MediaUrl, Telegram getFile)
inside ``normalize`` — committing to async now means the Protocol
doesn't change when those adapters land in CM-31/CM-32.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agents.channels.schema import NormalizedMessage


class NormalizationError(Exception):
    """Raised by a channel adapter when the raw payload can't be normalized.

    The HTTP / queue handler that called the adapter should log + return
    a 4xx so the upstream channel retries or drops the message; never
    propagate the underlying Pydantic ``ValidationError`` to the client
    (it leaks our schema internals).

    Adapters MUST wrap any underlying error in this exception type so
    downstream callers have a single catchable.
    """


@runtime_checkable
class ChannelAdapter(Protocol):
    """Every channel adapter (web, WhatsApp, Telegram, email) implements this.

    Implementations are typically pure (no I/O) for the in-process Web
    adapter, and async-with-I/O for the vendor adapters that re-fetch
    media. Stateless: instances are cheap, registries can hold one per
    channel and reuse it.
    """

    async def normalize(self, raw: Any) -> NormalizedMessage:
        """Normalize a vendor-specific payload into our schema.

        Args:
            raw: Vendor-shaped dict / object. The adapter knows the
                shape; documenting the expected shape is the adapter's
                docstring job.

        Returns:
            A validated ``NormalizedMessage``.

        Raises:
            NormalizationError: When the payload is structurally
                incompatible (missing required fields, wrong types,
                extra unknown fields per the schema's
                ``extra="forbid"``).
        """
        ...
