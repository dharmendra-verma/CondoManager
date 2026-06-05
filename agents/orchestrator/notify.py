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

#: Default SMTP submission port (STARTTLS).
_SMTP_PORT = 587


def _env(name: str) -> str:
    """Configured env var, or ``""`` when unset / blank / the CM-18 placeholder."""
    val = os.environ.get(name, "").strip()
    return "" if val == SECRET_PLACEHOLDER else val


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


class SmtpManagerNotifier:
    """Emails the manager alert via ``smtplib`` (STARTTLS) — CM-50.

    Same best-effort, never-raise contract as :class:`SlackWebhookNotifier`:
    a flaky mail server is logged and reported via the return value.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        recipient: str,
        *,
        port: int = _SMTP_PORT,
        sender: str | None = None,
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._recipient = recipient
        self._port = port
        self._sender = sender or user or "no-reply@condomanager.local"

    def notify(self, record: EscalationRecord) -> bool:
        import smtplib  # noqa: PLC0415  (lazy; LogNotifier path skips the import)
        from email.message import EmailMessage  # noqa: PLC0415

        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = self._recipient
        msg["Subject"] = f"[CondoManager] Escalation {record.record_id}"
        msg.set_content(record.manager_alert)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=_TIMEOUT_S) as smtp:
                smtp.starttls()
                if self._user and self._password:
                    smtp.login(self._user, self._password)
                smtp.send_message(msg)
        except Exception as e:  # noqa: BLE001  (mail failure must not break the graph)
            _log.warning(
                "SMTP manager-alert send failed for %s: %s", record.record_id, e
            )
            return False
        return True


class _MultiNotifier:
    """Fans the alert out to several notifiers. Delivered if ANY succeed.

    Attempts every notifier (a Slack outage must not suppress the email);
    never raises — each delegate already swallows its own failures.
    """

    def __init__(self, notifiers: list[ManagerNotifier]) -> None:
        self._notifiers = notifiers

    def notify(self, record: EscalationRecord) -> bool:
        results = [n.notify(record) for n in self._notifiers]
        return any(results)


def get_manager_notifier() -> ManagerNotifier:
    """Slack and/or Email when configured, else ``LogNotifier``.

    * Slack — enabled by ``SLACK_WEBHOOK_URL``.
    * Email — enabled by ``SMTP_HOST`` + ``MANAGER_ALERT_EMAIL`` (optional
      ``SMTP_USER`` / ``SMTP_PASS``).

    When both are set the alert fans out to both (CM-50). Same env-gating posture
    as the other CM selectors — the CM-18 ``REPLACE-ME`` placeholder and unset
    both count as off, so dev / CI never deliver.
    """
    notifiers: list[ManagerNotifier] = []
    url = _env("SLACK_WEBHOOK_URL")
    if url:
        notifiers.append(SlackWebhookNotifier(url))
    smtp_host = _env("SMTP_HOST")
    recipient = _env("MANAGER_ALERT_EMAIL")
    if smtp_host and recipient:
        notifiers.append(
            SmtpManagerNotifier(
                smtp_host, _env("SMTP_USER"), _env("SMTP_PASS"), recipient
            )
        )
    if not notifiers:
        return LogNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return _MultiNotifier(notifiers)
