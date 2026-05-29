"""Digest assembly + rendering tests (CM-36)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.analytics.digest import build_digest, render_digest_text
from agents.analytics.models import EscalationEvent
from agents.maintenance.schema import Priority, Ticket, TicketStatus

NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _ticket(unit: str, category: str, *, days_ago: int, owner: str | None = None) -> Ticket:
    created = NOW - timedelta(days=days_ago)
    return Ticket(
        id=f"TKT-{unit}-{days_ago}",
        tenant_id="t-1",
        unit=unit,
        issue_text="x",
        category=category,
        priority=Priority.P3,
        status=TicketStatus.RESOLVED,
        owner=owner,
        created_at=created,
        updated_at=created + timedelta(hours=2),
    )


def test_build_digest_populates_and_ids() -> None:
    tickets = [_ticket("1A", "plumbing", days_ago=d, owner="bob") for d in (1, 2, 3, 4)]
    events = [EscalationEvent(ts=NOW, severity="critical", legal_risk=True)]
    digest = build_digest(
        tenant_id="t-1", tickets=tickets, escalation_events=events, now=NOW
    )
    assert digest.tenant_id == "t-1"
    assert digest.digest_id == f"t-1:{(NOW - timedelta(days=7)).date().isoformat()}"
    assert digest.week_end == NOW.date().isoformat()
    assert len(digest.recurring) == 1          # 4 plumbing in 30d
    assert digest.contractors[0].owner == "bob"
    assert len(digest.predictions) == 1        # 4 boiler... actually plumbing in 14d >=3
    assert digest.sentiment.points             # non-empty trend


def test_build_digest_empty_is_valid() -> None:
    digest = build_digest(tenant_id="t-1", tickets=[], escalation_events=[], now=NOW)
    assert digest.recurring == []
    assert digest.contractors == []
    assert digest.predictions == []
    assert "0 recurring" in digest.headline
    assert digest.to_cosmos()["id"] == digest.digest_id
    assert digest.to_cosmos()["tenantId"] == "t-1"


def test_render_has_all_sections() -> None:
    tickets = [_ticket("1A", "plumbing", days_ago=d, owner="bob") for d in (1, 2, 3, 4)]
    digest = build_digest(tenant_id="t-1", tickets=tickets, escalation_events=[], now=NOW)
    text = render_digest_text(digest)
    assert "Recurring issues" in text
    assert "Contractor performance" in text
    assert "Sentiment" in text
    assert "Predicted hot-spots" in text
    assert "Unit 1A" in text


def test_render_empty_says_none() -> None:
    digest = build_digest(tenant_id="t-1", tickets=[], escalation_events=[], now=NOW)
    text = render_digest_text(digest)
    assert "none" in text
    assert "no tickets this period" in text
