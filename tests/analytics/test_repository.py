"""I/O seam tests (CM-36) — reader, digest store, notifier, selectors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from agents.analytics.models import EscalationEvent, WeeklyDigest
from agents.analytics.repository import (
    AnalyticsReader,
    DigestNotifier,
    DigestStore,
    InMemoryAnalyticsReader,
    LogDigestNotifier,
    NoopDigestStore,
    SlackDigestNotifier,
    get_analytics_reader,
    get_digest_notifier,
    get_digest_store,
)
from agents.maintenance.schema import Priority, Ticket, TicketStatus

NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
_URL = "https://hooks.slack.test/services/yyy"


def _ticket(tenant: str, *, days_ago: int) -> Ticket:
    created = NOW - timedelta(days=days_ago)
    return Ticket(
        id=f"TKT-{tenant}-{days_ago}",
        tenant_id=tenant,
        unit="1A",
        issue_text="x",
        category="plumbing",
        priority=Priority.P3,
        status=TicketStatus.NEW,
        created_at=created,
        updated_at=created,
    )


def _digest(tenant: str) -> WeeklyDigest:
    return WeeklyDigest(
        digest_id=f"{tenant}:2026-05-18",
        tenant_id=tenant,
        generated_at=NOW.isoformat(),
        week_start="2026-05-18",
        week_end="2026-05-25",
        headline="test",
    )


# --- reader ------------------------------------------------------------------


def test_inmemory_reader_filters_by_tenant_and_since() -> None:
    reader = InMemoryAnalyticsReader(
        tickets=[
            _ticket("t-1", days_ago=1),
            _ticket("t-1", days_ago=40),
            _ticket("t-2", days_ago=1),
        ],
        events=[EscalationEvent(ts=NOW - timedelta(days=1), severity="high")],
    )
    since = NOW - timedelta(days=30)
    tickets = reader.recent_tickets("t-1", since=since)
    assert len(tickets) == 1  # t-2 excluded (tenant), 40d excluded (window)
    assert isinstance(reader, AnalyticsReader)
    assert len(reader.recent_escalation_events("t-1", since=since)) == 1


# --- digest store ------------------------------------------------------------


def test_noop_digest_store_roundtrip() -> None:
    store = NoopDigestStore()
    assert isinstance(store, DigestStore)
    assert store.latest("t-1") is None
    store.save(_digest("t-1"))
    latest = store.latest("t-1")
    assert latest is not None and latest.tenant_id == "t-1"


# --- notifier ----------------------------------------------------------------


def test_log_notifier_succeeds() -> None:
    assert isinstance(LogDigestNotifier(), DigestNotifier)
    assert LogDigestNotifier().send("hello") is True


@respx.mock
def test_slack_notifier_posts_text() -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200))
    assert SlackDigestNotifier(_URL).send("weekly digest body") is True
    assert route.called
    assert b"weekly digest body" in route.calls.last.request.content


@respx.mock
def test_slack_notifier_handles_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500))
    assert SlackDigestNotifier(_URL).send("x") is False


@respx.mock
def test_slack_notifier_swallows_network_error() -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert SlackDigestNotifier(_URL).send("x") is False


# --- selectors ---------------------------------------------------------------


def test_reader_and_store_none_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert get_analytics_reader() is None
    assert get_digest_store() is None


def test_reader_none_for_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "REPLACE-ME")
    assert get_analytics_reader() is None


def test_notifier_logs_without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert isinstance(get_digest_notifier(), LogDigestNotifier)


def test_notifier_slack_with_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _URL)
    assert isinstance(get_digest_notifier(), SlackDigestNotifier)
