"""Vendor notifier dispatch via the outbound seam (CM-50)."""

from __future__ import annotations

import pytest
from agents.vendor import notifier as notifier_mod
from agents.vendor.notifier import (
    LoggingVendorNotifier,
    SendingVendorNotifier,
    get_vendor_notifier,
)


class _FakeSMTP:
    captured: dict[str, object] = {}

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        _FakeSMTP.captured = {"host": host}

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self) -> None:
        _FakeSMTP.captured["tls"] = True

    def login(self, user: str, password: str) -> None:
        _FakeSMTP.captured["login"] = (user, password)

    def send_message(self, msg: object) -> None:
        _FakeSMTP.captured["to"] = msg["To"]  # type: ignore[index]
        _FakeSMTP.captured["body"] = msg.get_content()  # type: ignore[attr-defined]


def test_selector_logs_without_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    notifier_mod._reset_for_tests()
    assert isinstance(get_vendor_notifier(), LoggingVendorNotifier)


def test_selector_sends_with_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    notifier_mod._reset_for_tests()
    assert isinstance(get_vendor_notifier(), SendingVendorNotifier)


def test_selector_placeholder_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "REPLACE-ME")
    notifier_mod._reset_for_tests()
    assert isinstance(get_vendor_notifier(), LoggingVendorNotifier)


def test_sending_notifier_emails_the_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    SendingVendorNotifier().notify_vendor(
        {"to_email": "vendor@x.com", "body": "Please service unit 4B"}
    )
    assert _FakeSMTP.captured["to"] == "vendor@x.com"
    assert "unit 4B" in str(_FakeSMTP.captured["body"])


def test_sending_notifier_logs_when_no_email(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # No to_email -> falls back to the masked log line (nothing silently dropped).
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    with caplog.at_level("INFO"):
        SendingVendorNotifier().notify_vendor({"to_sms": "+15551234567", "body": "service call"})
    assert any("vendor_dispatch" in r.message for r in caplog.records)
