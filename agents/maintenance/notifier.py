"""Manager-notification seam (CM-31 AC5).

CM-31 *composes* the manager alert (see :func:`messages.manager_notification`)
and dispatches it through this seam. The real Slack/email transport is CM-32's
shared deliverable ("Manager alert composed and posted to Slack/email"); wiring
it here would duplicate throwaway plumbing, so the default is a structured log
line. The seam means CM-32 swaps in a real transport with zero agent changes.

Selector mirrors :func:`agents.maintenance.repository.get_ticket_repository`.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from agents.observability import mask_pii

_log = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Dispatches a composed manager notification."""

    def notify_manager(self, notification: dict[str, str]) -> None:
        """Send a manager alert payload."""
        ...


class LoggingNotifier:
    """Default notifier — emits a structured, PII-masked log line.

    The free-text ``summary`` is run through CM-27 ``mask_pii`` before
    logging so a tenant's phone/email never lands in plaintext logs.
    """

    def notify_manager(self, notification: dict[str, str]) -> None:
        safe = dict(notification)
        if "summary" in safe:
            safe["summary"] = mask_pii(safe["summary"])
        _log.info("manager_notification %s", safe)


_cached: Notifier | None = None


def get_notifier() -> Notifier:
    """Return the configured notifier (cached). Default: :class:`LoggingNotifier`."""
    global _cached
    if _cached is None:
        _cached = LoggingNotifier()
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached notifier so each test starts clean."""
    global _cached
    _cached = None
