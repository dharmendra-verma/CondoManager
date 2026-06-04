"""CM-55 — FastAPI endpoints: flag gating + login/message behavior.

The app reads ``WEBCHAT_TEST_ENABLED`` at call time, so each test sets the flag
explicitly via ``monkeypatch`` — the channel must 404 when it is off.
"""

from __future__ import annotations

import pytest
from agents.webchat.app import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_login_404_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBCHAT_TEST_ENABLED", raising=False)
    res = client.post("/web/login", json={"mobile": "+919876543210"})
    assert res.status_code == 404


def test_message_404_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBCHAT_TEST_ENABLED", raising=False)
    res = client.post("/web/message", json={"mobile": "+919876543210", "content": "hi"})
    assert res.status_code == 404


def test_login_hit_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post("/web/login", json={"mobile": "+91 98765-43210"})
    assert res.status_code == 200
    body = res.json()
    assert body == {"tenant_id": "condo-tower-a", "name": "Asha Rao", "unit": "4B"}


def test_login_unknown_number_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post("/web/login", json={"mobile": "+10000000000"})
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_number"


def test_message_returns_reply_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post(
        "/web/message",
        json={"mobile": "+919876543210", "content": "There is a leak under the sink"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["channel"] == "web"
    assert isinstance(body["reply"], str) and body["reply"].strip() != ""
    assert isinstance(body["stub"], bool)


def test_message_masks_pii(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post(
        "/web/message",
        json={"mobile": "+919876543210", "content": "call +14155551234 about the leak"},
    )
    assert res.status_code == 200
    assert "+14155551234" not in res.json()["masked_content"]


def test_message_unknown_number_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post("/web/message", json={"mobile": "+10000000000", "content": "hi"})
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_number"
