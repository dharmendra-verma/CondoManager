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


# CM-101 — request_id correlation middleware


def test_response_carries_generated_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post("/web/login", json={"mobile": "+919876543210"})
    assert res.headers["x-request-id"].startswith("req_")


def test_incoming_request_id_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    res = client.post(
        "/web/login",
        json={"mobile": "+919876543210"},
        headers={"X-Request-ID": "req_upstream123"},
    )
    assert res.headers["x-request-id"] == "req_upstream123"


def test_handler_runs_inside_request_id_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CM-21 ContextVar must be set while the route handler runs — this is
    exactly what was broken: logs emitted during handling fell back to the
    ``"unknown"`` sentinel."""
    from agents.observability.correlation import UNKNOWN_REQUEST_ID, get_request_id
    from agents.webchat import app as app_module

    seen: list[str] = []
    real_resolve = app_module.resolve_tenant

    def spying_resolve(mobile: str):  # noqa: ANN202
        seen.append(get_request_id())
        return real_resolve(mobile)

    monkeypatch.setenv("WEBCHAT_TEST_ENABLED", "1")
    monkeypatch.setattr(app_module, "resolve_tenant", spying_resolve)
    res = client.post("/web/login", json={"mobile": "+919876543210"})
    assert res.status_code == 200
    assert seen and seen[0] != UNKNOWN_REQUEST_ID
    assert seen[0] == res.headers["x-request-id"]
