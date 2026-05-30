"""Manager-notifier tests (CM-32 AC #4)."""

from __future__ import annotations

import httpx
import pytest
import respx
from agents.orchestrator.notify import (
    LogNotifier,
    ManagerNotifier,
    SlackWebhookNotifier,
    get_manager_notifier,
)
from agents.orchestrator.state import EscalationCategory, EscalationRecord

_URL = "https://hooks.slack.test/services/xxx"


def _rec() -> EscalationRecord:
    return EscalationRecord(
        record_id="esc-1",
        tenant_id="t-1",
        request_id="r-1",
        category=EscalationCategory.LEGAL,
        legal_risk=True,
        manager_alert="ALERT: legal escalation",
    )


def test_log_notifier_is_a_notifier_and_succeeds() -> None:
    n = LogNotifier()
    assert isinstance(n, ManagerNotifier)
    assert n.notify(_rec()) is True


@respx.mock
def test_slack_notifier_posts_alert_text() -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200))
    assert SlackWebhookNotifier(_URL).notify(_rec()) is True
    assert route.called
    sent = route.calls.last.request
    assert b"legal escalation" in sent.content


@respx.mock
def test_slack_notifier_returns_false_on_http_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500))
    assert SlackWebhookNotifier(_URL).notify(_rec()) is False


@respx.mock
def test_slack_notifier_swallows_network_error() -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectError("boom"))
    # Must NOT raise into the graph — returns False.
    assert SlackWebhookNotifier(_URL).notify(_rec()) is False


def test_selector_logs_without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert isinstance(get_manager_notifier(), LogNotifier)


def test_selector_placeholder_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "REPLACE-ME")
    assert isinstance(get_manager_notifier(), LogNotifier)


def test_selector_slack_with_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _URL)
    assert isinstance(get_manager_notifier(), SlackWebhookNotifier)
