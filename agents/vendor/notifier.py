"""Vendor-contact seam (CM-35 AC5).

Composes are in :mod:`agents.vendor.messages`; this dispatches the vendor
notice. Default is a structured, PII-masked log line. Real email/SMS (Twilio
creds live in Key Vault per CM-18) is a deferred wiring step — the seam means
it drops in with no agent change. The *manager* approval alert reuses CM-31's
``agents.maintenance.get_notifier`` rather than duplicating a second seam.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from agents.channels.outbound import SECRET_PLACEHOLDER, get_outbound_channel
from agents.channels.schema import Channel
from agents.observability import mask_pii

_log = logging.getLogger(__name__)


@runtime_checkable
class VendorNotifier(Protocol):
    """Dispatches a composed vendor notice (email/SMS)."""

    def notify_vendor(self, notice: dict[str, str]) -> None:
        """Send a vendor dispatch notice."""
        ...


class LoggingVendorNotifier:
    """Default notifier — emits a structured, PII-masked log line."""

    def notify_vendor(self, notice: dict[str, str]) -> None:
        safe = dict(notice)
        for key in ("to_email", "to_sms", "body"):
            if key in safe:
                safe[key] = mask_pii(safe[key])
        _log.info("vendor_dispatch %s", safe)


class SendingVendorNotifier:
    """Dispatches the vendor notice by email via the CM-50 outbound seam.

    Emails ``notice["body"]`` to ``notice["to_email"]`` through
    ``get_outbound_channel(Channel.EMAIL)`` (a real SMTP transport when
    ``SMTP_*`` is configured, else ``LogOutbound``). Anything it can't send —
    no ``to_email``, a blank body, a send miss, or the ``to_sms`` part (vendor
    SMS is not wired in CM-50) — falls back to the masked log line so a dispatch
    is never silently dropped. Never raises (the transport swallows failures).
    """

    def notify_vendor(self, notice: dict[str, str]) -> None:
        to_email = notice.get("to_email", "").strip()
        body = notice.get("body", "")
        sent = False
        if to_email and body.strip():
            sent = get_outbound_channel(Channel.EMAIL).send(to_email, body)
        if not sent:
            LoggingVendorNotifier().notify_vendor(notice)


_cached: VendorNotifier | None = None


def get_vendor_notifier() -> VendorNotifier:
    """Cached vendor notifier.

    Sends by email when SMTP is configured (``SMTP_HOST`` is a real value), else
    logs (CM-50). The CM-18 ``REPLACE-ME`` placeholder and unset both count as
    unconfigured, so dev / CI keep logging.
    """
    global _cached
    if _cached is None:
        host = os.environ.get("SMTP_HOST", "").strip()
        if host and host != SECRET_PLACEHOLDER:
            _cached = SendingVendorNotifier()
        else:
            _cached = LoggingVendorNotifier()
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached notifier so each test starts clean."""
    global _cached
    _cached = None
