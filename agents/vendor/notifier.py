"""Vendor-contact seam (CM-35 AC5).

Composes are in :mod:`agents.vendor.messages`; this dispatches the vendor
notice. Default is a structured, PII-masked log line. Real email/SMS (Twilio
creds live in Key Vault per CM-18) is a deferred wiring step — the seam means
it drops in with no agent change. The *manager* approval alert reuses CM-31's
``agents.maintenance.get_notifier`` rather than duplicating a second seam.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

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


_cached: VendorNotifier | None = None


def get_vendor_notifier() -> VendorNotifier:
    """Return the cached vendor notifier (default :class:`LoggingVendorNotifier`)."""
    global _cached
    if _cached is None:
        _cached = LoggingVendorNotifier()
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached notifier so each test starts clean."""
    global _cached
    _cached = None
