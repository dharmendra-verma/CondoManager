"""run_digest orchestration tests (CM-36)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.analytics import run_digest
from agents.analytics.models import EscalationEvent, WeeklyDigest
from agents.analytics.repository import InMemoryAnalyticsReader, NoopDigestStore
from agents.maintenance.schema import Priority, Ticket, TicketStatus

NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _ticket(days_ago: int, owner: str | None = None) -> Ticket:
    created = NOW - timedelta(days=days_ago)
    return Ticket(
        id=f"TKT-{days_ago}",
        tenant_id="t-1",
        unit="1A",
        issue_text="x",
        category="plumbing",
        priority=Priority.P3,
        status=TicketStatus.RESOLVED,
        owner=owner,
        created_at=created,
        updated_at=created + timedelta(hours=1),
    )


class _SpyNotifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


def test_run_digest_persists_notifies_and_tallies() -> None:
    reader = InMemoryAnalyticsReader(
        tickets=[_ticket(d, owner="bob") for d in (1, 2, 3, 4)],
        events=[EscalationEvent(ts=NOW, severity="critical", legal_risk=True)],
    )
    store = NoopDigestStore()
    notifier = _SpyNotifier()

    report = run_digest(
        tenant_id="t-1", reader=reader, digest_store=store, notifier=notifier, now=NOW
    )

    assert report.tenant_id == "t-1"
    assert report.recurring_count == 1
    assert report.contractor_count == 1
    assert report.prediction_count == 1
    assert report.escalation_count == 1
    assert report.notified is True

    # Digest persisted + Slack body sent.
    persisted = store.latest("t-1")
    assert isinstance(persisted, WeeklyDigest)
    assert persisted.headline
    assert len(notifier.sent) == 1
    assert "Weekly digest" in notifier.sent[0]


def test_run_digest_non_blocking_on_notifier_failure() -> None:
    reader = InMemoryAnalyticsReader(tickets=[], events=[])
    store = NoopDigestStore()
    report = run_digest(
        tenant_id="t-1",
        reader=reader,
        digest_store=store,
        notifier=_SpyNotifier(ok=False),
        now=NOW,
    )
    # Failed delivery is reported, not raised; digest still persisted.
    assert report.notified is False
    assert store.latest("t-1") is not None
