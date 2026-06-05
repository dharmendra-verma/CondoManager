"""Outbound delivery transports — tenant-facing replies over the originating channel.

Jira: CM-50  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 1

The mirror of the inbound ``ChannelAdapter`` seam: where adapters *normalize*
inbound messages, an :class:`OutboundChannel` *sends* a reply back over the
tenant's originating channel. Same env-driven-selector + never-raise posture as
``agents.orchestrator.notify.get_manager_notifier`` and
``agents.orchestrator.checkpointer.get_checkpointer``.

Transports (a real one only when its creds are configured; else ``LogOutbound``):

* :class:`TwilioOutbound`   — WhatsApp via the Twilio REST Messages API (``httpx``).
* :class:`TelegramOutbound` — Telegram Bot ``sendMessage`` (``httpx``).
* :class:`SmtpOutbound`     — email via ``smtplib`` (STARTTLS).
* :class:`LogOutbound`      — PII-masked structured log; offline / no-creds fallback.

:func:`get_outbound_channel` maps a :class:`~agents.channels.schema.Channel` to its
transport: ``WHATSAPP`` -> Twilio, ``TELEGRAM`` -> Telegram, ``EMAIL`` -> SMTP,
``WEB`` / ``UNKNOWN`` -> ``LogOutbound`` (web-chat replies return synchronously via
the HTTP response — see ``agents/webchat/service.py`` — so there is nothing to push).

Contract: ``send(recipient, message, attachments=None) -> bool`` MUST NOT raise.
A flaky carrier returns ``False`` (logged, PII-masked) and never breaks the graph —
the same guarantee ``SlackWebhookNotifier`` gives. Credentials come from the
environment (CM-18 Key Vault secrets in prod); the ``REPLACE-ME`` placeholder and
unset both mean "not configured", so dev / CI / offline never attempt a real send.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from agents.observability import mask_pii

from .schema import Channel

_log = logging.getLogger(__name__)

#: CM-18 placeholder; treated as if-unset.
SECRET_PLACEHOLDER = "REPLACE-ME"

#: Send timeout (seconds). Short so a hung carrier can't stall the graph step;
#: failure falls through to a logged miss.
_TIMEOUT_S = 5.0

#: Default SMTP submission port (STARTTLS).
_SMTP_PORT = 587


def _env(name: str) -> str:
    """Return a configured env var, or ``""`` when unset / blank / placeholder."""
    val = os.environ.get(name, "").strip()
    return "" if val == SECRET_PLACEHOLDER else val


def _bare_whatsapp(recipient: str) -> str:
    """Strip an existing ``whatsapp:`` prefix so we never double-prefix."""
    return recipient.split(":", 1)[1] if recipient.startswith("whatsapp:") else recipient


@runtime_checkable
class OutboundChannel(Protocol):
    """Sends a tenant-facing reply.

    Returns ``True`` when delivered, ``False`` on a handled failure.
    Implementations MUST NOT raise — delivery is best-effort and the caller
    (a graph node) treats a miss as non-fatal.
    """

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool: ...


class LogOutbound:
    """Logs the reply (PII-masked). Offline / no-creds fallback (always succeeds)."""

    def __init__(self, channel: Channel = Channel.UNKNOWN) -> None:
        self._channel = channel

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool:
        if not message.strip():
            return False
        _log.info(
            "OUTBOUND (no transport configured) channel=%s to=%s msg=%s",
            self._channel.value,
            mask_pii(recipient),
            mask_pii(message),
        )
        return True


class TwilioOutbound:
    """WhatsApp via the Twilio REST Messages API."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool:
        if not message.strip():
            return False
        import httpx  # noqa: PLC0415  (lazy; LogOutbound path skips the import)

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {
            "From": f"whatsapp:{self._from}",
            "To": f"whatsapp:{_bare_whatsapp(recipient)}",
            "Body": message,
        }
        try:
            resp = httpx.post(
                url, data=data, auth=(self._sid, self._token), timeout=_TIMEOUT_S
            )
        except Exception as e:  # noqa: BLE001  (carrier failure must not break the graph)
            _log.warning("Twilio send failed to %s: %s", mask_pii(recipient), e)
            return False
        if resp.status_code >= 400:
            _log.warning(
                "Twilio send returned %s to %s", resp.status_code, mask_pii(recipient)
            )
            return False
        return True


class TelegramOutbound:
    """Telegram Bot API ``sendMessage``."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool:
        if not message.strip():
            return False
        import httpx  # noqa: PLC0415

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = httpx.post(
                url, json={"chat_id": recipient, "text": message}, timeout=_TIMEOUT_S
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("Telegram send failed to %s: %s", mask_pii(recipient), e)
            return False
        if resp.status_code >= 400:
            _log.warning("Telegram send returned %s", resp.status_code)
            return False
        return True


class SmtpOutbound:
    """Email via ``smtplib`` (STARTTLS). ``recipient`` is the email address."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        port: int = _SMTP_PORT,
        sender: str | None = None,
        subject: str = "Update on your maintenance request",
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._port = port
        self._sender = sender or user or "no-reply@condomanager.local"
        self._subject = subject

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool:
        if not message.strip():
            return False
        import smtplib  # noqa: PLC0415
        from email.message import EmailMessage  # noqa: PLC0415

        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = recipient
        email["Subject"] = self._subject
        email.set_content(message)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=_TIMEOUT_S) as smtp:
                smtp.starttls()
                if self._user and self._password:
                    smtp.login(self._user, self._password)
                smtp.send_message(email)
        except Exception as e:  # noqa: BLE001
            _log.warning("SMTP send failed to %s: %s", mask_pii(recipient), e)
            return False
        return True


def get_outbound_channel(channel: Channel) -> OutboundChannel:
    """Return the transport for ``channel`` when its creds are set, else ``LogOutbound``.

    Same env-gating posture as the other CM selectors: a credential that is unset,
    blank, or the CM-18 ``REPLACE-ME`` placeholder counts as "not configured", so
    dev / CI / offline fall back to logging and never attempt a real send.
    ``WEB`` / ``UNKNOWN`` always log — web-chat answers via the HTTP response.
    """
    if channel == Channel.WHATSAPP:
        sid, token, frm = (
            _env("TWILIO_ACCOUNT_SID"),
            _env("TWILIO_AUTH_TOKEN"),
            _env("TWILIO_WHATSAPP_NUMBER"),
        )
        if sid and token and frm:
            return TwilioOutbound(sid, token, frm)
    elif channel == Channel.TELEGRAM:
        token = _env("TELEGRAM_BOT_TOKEN")
        if token:
            return TelegramOutbound(token)
    elif channel == Channel.EMAIL:
        host = _env("SMTP_HOST")
        if host:
            return SmtpOutbound(host, _env("SMTP_USER"), _env("SMTP_PASS"))
    return LogOutbound(channel)
