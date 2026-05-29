"""Digest delivery seam (CM-36 AC5).

Composes are in :mod:`agents.analytics.digest`; this dispatches the rendered
report. Default is a structured, PII-masked log line. Real email + tenant-portal
delivery (the portal lands in CM-37) is deferred — the seam means it drops in
with no engine change.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from agents.observability import mask_pii

from .models import DigestReport

_log = logging.getLogger(__name__)


@runtime_checkable
class DigestDelivery(Protocol):
    """Delivers a composed weekly digest."""

    def deliver(self, report: DigestReport) -> None:
        """Send / publish the digest."""
        ...


class LoggingDigestDelivery:
    """Default delivery — logs a PII-masked summary + the rendered body."""

    def deliver(self, report: DigestReport) -> None:
        _log.info(
            "weekly_digest generated_at=%s tickets=%d recurring=%d predictions=%d",
            report.generated_at.isoformat(),
            report.ticket_count,
            len(report.recurring),
            len(report.predictions),
        )
        _log.info("weekly_digest body:\n%s", mask_pii(report.body))


_cached: DigestDelivery | None = None


def get_digest_delivery() -> DigestDelivery:
    """Return the cached delivery (default :class:`LoggingDigestDelivery`)."""
    global _cached
    if _cached is None:
        _cached = LoggingDigestDelivery()
    return _cached


def _reset_for_tests() -> None:
    global _cached
    _cached = None
