"""``agents.webchat`` — TEST-ONLY web chat channel (CM-55).

Jira: CM-55  | Epic: CM-4 (Channel Adapters & Data Ingestion)  | Phase 1

A self-contained, zero-dependency channel so the team can exercise the full
``NormalizedMessage`` → Triage pipeline end-to-end without standing up Twilio /
Telegram / IMAP. A tenant "logs in" with a hardcoded mobile number and sends a
message, which flows through the CM-29 :class:`~agents.channels.web.WebAdapter`
(PII-masked first via CM-27 ``mask_pii``) into the CM-30 triage spine.

**TEST-ONLY.** The hardcoded credentials live in :mod:`agents.webchat.tenants`
and the whole channel is gated behind the ``WEBCHAT_TEST_ENABLED`` flag (see
:func:`agents.webchat.flag.is_webchat_enabled`) so it is disabled in
staging/prod. The FastAPI ``app`` is intentionally NOT imported here — import it
explicitly via ``agents.webchat.app:app`` so ``import agents.webchat`` (e.g. the
flag check) stays light.
"""

from __future__ import annotations

from .flag import is_webchat_enabled

__all__ = ["is_webchat_enabled"]
