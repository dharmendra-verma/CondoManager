"""Manager-alert notifier (CM-32 AC #4).

Jira: CM-32  | Epic: CM-7 (Agent 4 — Escalation Manager Agent)  | Phase 1

Posts the composed manager alert when an escalation is raised. Slack is the
concrete channel for this story (incoming webhook); email/SMTP is a documented
follow-up.

* :class:`SlackWebhookNotifier` — POSTs ``{"text": record.manager_alert}`` to
  ``SLACK_WEBHOOK_URL`` via the already-present ``httpx``.
* :class:`LogNotifier` — logs the alert; the offline / no-webhook fallback.
* :func:`get_manager_notifier` — Slack when ``SLACK_WEBHOOK_URL`` is a real
  value, else ``LogNotifier`` (so dev/CI never attempt a real POST).

Delivery failure is logged and reported via the return value, never raised —
a flaky webhook must not break the graph or block the HITL gate. The
escalation is already persisted (AC #3) before the alert is attempted.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from .state import EscalationRecord

_log = logging.getLogger(__name__)

#: CM-18 placeholder; treated as if-unset.
SECRET_PLACEHOLDER = "REPLACE-ME"

#: Webhook POST timeout (seconds). Kept short so a hung webhook can't stall
#: the graph step; failure falls through to a logged miss.
_TIMEOUT_S = 5.0


@runtime_checkable
class ManagerNotifier(Protocol):
    """Posts a manager alert for an escalation record.

    Returns ``True`` when the alert was delivered, ``False`` on a handled
    failure. Implementations MUST NOT raise — the caller treats notification
    as best-effort and the escalation is already recorded.
    """

    def notify(self, record: EscalationRecord) -> bool: ...


class LogNotifier:
    """Logs the manager alert. Offline / no-webhook fallback (always succeeds)."""

    def notify(self, record: EscalationRecord) -> bool:
        _log.warning("MANAGER ALERT (no webhook configured)\n%s", record.manager_alert)
        return True


class SlackWebhookNotifier:
    """Posts the alert to a Slack incoming webhook via httpx."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def notify(self, record: EscalationRecord) -> bool:
        import httpx  # noqa: PLC0415  (lazy; LogNotifier path skips the import)

        try:
            resp = httpx.post(
                self._url, json={"text": record.manager_alert}, timeout=_TIMEOUT_S
            )
        except Exception as e:  # noqa: BLE001  (network failure must not break the graph)
            _log.warning(
                "Slack manager-alert POST failed for %s: %s", record.record_id, e
            )
            return False
        if resp.status_code >= 400:
            _log.warning(
                "Slack manager-alert POST returned %s for %s",
                resp.status_code,
                record.record_id,
            )
            return False
        return True


def get_manager_notifier() -> ManagerNotifier:
    """Slack when ``SLACK_WEBHOOK_URL`` is a real value, else ``LogNotifier``.

    Same env-gating posture as the other CM selectors — the CM-18 ``REPLACE-ME``
    placeholder and unset both fall back to logging, so dev/CI never POST.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if url and url != SECRET_PLACEHOLDER:
        return SlackWebhookNotifier(url)
    return LogNotifier()
