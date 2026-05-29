"""Weekly digest composition (CM-36 AC5).

Runs the four computations over the window and renders a board-facing text body.
``notes`` records honest data-coverage caveats (e.g. no vendor-attributed
tickets yet) so the digest never implies coverage it doesn't have.
"""

from __future__ import annotations

from datetime import datetime

from .models import AnalyticsTicket, DigestReport
from .performance import score_contractors
from .predictive import predict
from .recurring import detect_recurring
from .sentiment import sentiment_trend

DEFAULT_WINDOW_DAYS = 30


def build_digest(
    tickets: list[AnalyticsTicket],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> DigestReport:
    """Compute all sections and render the digest body."""
    recurring = detect_recurring(tickets, now=now, window_days=window_days)
    contractors = score_contractors(tickets)
    sentiment = sentiment_trend(tickets, now=now)
    predictions = predict(tickets, now=now)

    notes: list[str] = []
    if not any(t.vendor_id for t in tickets):
        notes.append(
            "Contractor performance: insufficient data — no vendor-attributed "
            "tickets yet (pending CM-35 dispatch persistence)."
        )
    if not any(t.tone for t in tickets):
        notes.append(
            "Sentiment trend: insufficient data — tone is not yet persisted on "
            "tickets (pending a tone-persistence follow-up)."
        )

    report = DigestReport(
        generated_at=now,
        window_days=window_days,
        ticket_count=len(tickets),
        recurring=recurring,
        contractor_scores=contractors,
        sentiment=sentiment,
        predictions=predictions,
        notes=notes,
    )
    report.body = _render(report)
    return report


def _render(report: DigestReport) -> str:
    """Render a plain-text digest body (stable for snapshot tests)."""
    lines: list[str] = [
        "CondoManager — Weekly Building Health Digest",
        f"Window: last {report.window_days} days | tickets: {report.ticket_count}",
        "",
        f"Recurring issues ({len(report.recurring)}):",
    ]
    if report.recurring:
        lines += [
            f"  - unit {i.unit} / {i.category}: {i.count} occurrences" for i in report.recurring
        ]
    else:
        lines.append("  - none")

    lines += ["", f"Contractor performance ({len(report.contractor_scores)}):"]
    if report.contractor_scores:
        for s in report.contractor_scores:
            resp = f"{s.avg_response_hours}h avg" if s.avg_response_hours is not None else "n/a"
            lines.append(
                f"  - {s.vendor_id}: {s.resolved}/{s.jobs} resolved "
                f"({s.resolution_rate:.0%}), response {resp} [{s.status}]"
            )
    else:
        lines.append("  - none")

    lines += ["", f"Predictions ({len(report.predictions)}):"]
    if report.predictions:
        lines += [
            f"  - [{p.severity}] unit {p.unit} ({p.category}): {p.message}"
            for p in report.predictions
        ]
    else:
        lines.append("  - none")

    lines += ["", f"Sentiment points ({len(report.sentiment)}):"]
    if report.sentiment:
        lines += [
            f"  - {p.building} wk {p.week_start.date()}: {p.score:+.2f} (n={p.sample_size})"
            for p in report.sentiment
        ]
    else:
        lines.append("  - none")

    if report.notes:
        lines += ["", "Notes:"] + [f"  * {n}" for n in report.notes]

    return "\n".join(lines)
