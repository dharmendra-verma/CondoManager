"""Outbound transport + selector tests (CM-50)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from agents.channels.outbound import (
    LogOutbound,
    OutboundChannel,
    SmtpOutbound,
    TelegramOutbound,
    TwilioOutbound,
    get_outbound_channel,
)
from agents.channels.schema import Channel

_TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
_TELEGRAM_URL = "https://api.telegram.org/botBOT:tok/sendMessage"


class _FakeSMTP:
    """Minimal context-manager stub for ``smtplib.SMTP``."""

    captured: dict[str, Any] = {}

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        _FakeSMTP.captured = {"host": host, "port": port}

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self) -> None:
        _FakeSMTP.captured["starttls"] = True

    def login(self, user: str, password: str) -> None:
        _FakeSMTP.captured["login"] = (user, password)

    def send_message(self, msg: Any) -> None:  # noqa: ANN401
        _FakeSMTP.captured["to"] = msg["To"]
        _FakeSMTP.captured["body"] = msg.get_content()


# ---- LogOutbound -----------------------------------------------------------


def test_log_outbound_is_an_outbound_channel_and_succeeds() -> None:
    t = LogOutbound(Channel.WEB)
    assert isinstance(t, OutboundChannel)
    assert t.send("+15551234567", "hi") is True


def test_log_outbound_skips_blank_message() -> None:
    assert LogOutbound().send("+15551234567", "   ") is False


# ---- Twilio (WhatsApp) -----------------------------------------------------


@respx.mock
def test_twilio_posts_whatsapp_message() -> None:
    route = respx.post(_TWILIO_URL).mock(return_value=httpx.Response(201))
    ok = TwilioOutbound("AC123", "tok", "+1999").send("+15551234567", "Ticket TKT-1 logged")
    assert ok is True
    assert route.called
    form = parse_qs(route.calls.last.request.content.decode())
    assert form["From"] == ["whatsapp:+1999"]
    assert form["To"] == ["whatsapp:+15551234567"]
    assert form["Body"] == ["Ticket TKT-1 logged"]


@respx.mock
def test_twilio_does_not_double_prefix_whatsapp() -> None:
    route = respx.post(_TWILIO_URL).mock(return_value=httpx.Response(201))
    TwilioOutbound("AC123", "tok", "+1999").send("whatsapp:+15551234567", "hi")
    form = parse_qs(route.calls.last.request.content.decode())
    assert form["To"] == ["whatsapp:+15551234567"]


@respx.mock
def test_twilio_returns_false_on_http_error() -> None:
    respx.post(_TWILIO_URL).mock(return_value=httpx.Response(401))
    assert TwilioOutbound("AC123", "tok", "+1999").send("+15551234567", "hi") is False


@respx.mock
def test_twilio_swallows_network_error() -> None:
    respx.post(_TWILIO_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert TwilioOutbound("AC123", "tok", "+1999").send("+15551234567", "hi") is False


def test_twilio_skips_blank_message() -> None:
    assert TwilioOutbound("AC123", "tok", "+1999").send("+15551234567", "") is False


# ---- Telegram --------------------------------------------------------------


@respx.mock
def test_telegram_sends_message() -> None:
    route = respx.post(_TELEGRAM_URL).mock(return_value=httpx.Response(200))
    ok = TelegramOutbound("BOT:tok").send("9988", "hello")
    assert ok is True
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"chat_id": "9988", "text": "hello"}


@respx.mock
def test_telegram_returns_false_on_error() -> None:
    respx.post(_TELEGRAM_URL).mock(return_value=httpx.Response(400))
    assert TelegramOutbound("BOT:tok").send("9988", "hello") is False


# ---- SMTP ------------------------------------------------------------------


def test_smtp_sends_via_smtplib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    ok = SmtpOutbound("smtp.test", "user@test", "pw").send("vendor@x.com", "body text")
    assert ok is True
    assert _FakeSMTP.captured["starttls"] is True
    assert _FakeSMTP.captured["login"] == ("user@test", "pw")
    assert _FakeSMTP.captured["to"] == "vendor@x.com"


def test_smtp_returns_false_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", _boom)
    assert SmtpOutbound("smtp.test", "u", "p").send("v@x.com", "hi") is False


# ---- selector --------------------------------------------------------------


def _clear_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_WHATSAPP_NUMBER",
        "TELEGRAM_BOT_TOKEN",
        "SMTP_HOST",
    ):
        monkeypatch.delenv(k, raising=False)


def test_selector_whatsapp_logs_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_creds(monkeypatch)
    assert isinstance(get_outbound_channel(Channel.WHATSAPP), LogOutbound)


def test_selector_whatsapp_twilio_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_WHATSAPP_NUMBER", "+1999")
    assert isinstance(get_outbound_channel(Channel.WHATSAPP), TwilioOutbound)


def test_selector_placeholder_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_creds(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "REPLACE-ME")
    assert isinstance(get_outbound_channel(Channel.TELEGRAM), LogOutbound)


def test_selector_telegram_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT:tok")
    assert isinstance(get_outbound_channel(Channel.TELEGRAM), TelegramOutbound)


def test_selector_email_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    assert isinstance(get_outbound_channel(Channel.EMAIL), SmtpOutbound)


def test_selector_web_always_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    assert isinstance(get_outbound_channel(Channel.WEB), LogOutbound)
