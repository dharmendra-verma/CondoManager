"""Assemble + render the weekly digest (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

``build_digest`` composes the four analyzers into a :class:`WeeklyDigest`;
``render_digest_text`` formats it into the Slack/email body. Both pure.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agents.maintenance.schema import Ticket

from .analyze import (
    RECURRING_WINDOW_DAYS,
    detect_recurring,
    predictive_flags,
    score_contractors,
    sentiment_trend,
)
from .models import EscalationEvent, WeeklyDigest


def build_digest(
    *,
    tenant_id: str,
    tickets: list[Ticket],
    escalation_events: list[EscalationEvent],
    now: datetime,
    window_days: int = RECURRING_WINDOW_DAYS,
) -> WeeklyDigest:
    """Compose a :class:`WeeklyDigest` for ``tenant_id`` as of ``now``."""
    cutoff = now - timedelta(days=window_days)
    window_tickets = [t for t in tickets if t.created_at >= cutoff]

    recurring = detect_recurring(tickets, now=now, window_days=window_days)
    contractors = score_contractors(window_tickets)
    sentiment = sentiment_trend(escalation_events, now=now)
    predictions = predictive_flags(tickets, now=now)

    week_end = now.date()
    week_start = (now - timedelta(days=7)).date()
    headline = (
        f"{len(recurring)} recurring issue(s), {len(predictions)} predicted hot-spot(s); "
        f"escalations {sentiment.direction}"
    )

    return WeeklyDigest(
        digest_id=f"{tenant_id}:{week_start.isoformat()}",
        tenant_id=tenant_id,
        generated_at=now.isoformat(),
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        headline=headline,
        recurring=recurring,
        contractors=contractors,
        sentiment=sentiment,
        predictions=predictions,
    )


def render_digest_text(digest: WeeklyDigest) -> str:
    """Render the digest as a plain-text body for Slack / email / the portal."""
    lines: list[str] = [
        f"*Weekly digest — {digest.tenant_id}* ({digest.week_start} → {digest.week_end})",
        digest.headline,
        "",
    ]

    lines.append("*Recurring issues (>3 in 30d):*")
    if digest.recurring:
        lines += [
            f"  - Unit {r.unit} / {r.category}: {r.count}× (last {r.last_seen.date()})"
            for r in digest.recurring
        ]
    else:
        lines.append("  - none")

    lines.append("*Contractor performance:*")
    if digest.contractors:
        for c in digest.contractors:
            resp = f", avg {c.avg_response_hours}h" if c.avg_response_hours is not None else ""
            lines.append(
                f"  - {c.owner}: {c.resolved}/{c.assigned} resolved "
                f"({c.resolution_rate:.0%}){resp}"
            )
    else:
        lines.append("  - no tickets this period")

    lines.append(f"*Sentiment (escalations, week-over-week): {digest.sentiment.direction}*")
    lines += [
        f"  - {p.week_start}: {p.escalations} escalations "
        f"({p.critical} critical, {p.legal} legal)"
        for p in digest.sentiment.points
    ]

    lines.append("*Predicted hot-spots:*")
    if digest.predictions:
        lines += [f"  - {f.message}" for f in digest.predictions]
    else:
        lines.append("  - none")

    return "\n".join(lines)
