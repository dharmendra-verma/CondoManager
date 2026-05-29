"""Weekly-digest orchestrator (CM-36).

``run_weekly_digest`` is the single entrypoint the Functions timer calls. It is
**non-blocking** (AC6): any source/compute/delivery failure is logged and a
(possibly empty) report is returned — an exception must never escape into the
timer and wedge the schedule.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .delivery import DigestDelivery, get_digest_delivery
from .digest import DEFAULT_WINDOW_DAYS, build_digest
from .models import DigestReport
from .source import AnalyticsSource, get_analytics_source

_log = logging.getLogger(__name__)


def run_weekly_digest(
    *,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    source: AnalyticsSource | None = None,
    delivery: DigestDelivery | None = None,
) -> DigestReport:
    """Run one weekly-digest pass: source → compute → deliver. Never raises."""
    now = now or datetime.now(UTC)
    src = source if source is not None else get_analytics_source()
    sink = delivery if delivery is not None else get_digest_delivery()

    try:
        tickets = src.list_tickets(since=now - timedelta(days=window_days))
        report = build_digest(tickets, now=now, window_days=window_days)
    except Exception:  # noqa: BLE001 — non-blocking timer: log + emit empty report
        _log.exception("weekly digest computation failed; emitting empty report")
        report = build_digest([], now=now, window_days=window_days)

    try:
        sink.deliver(report)
    except Exception:  # noqa: BLE001 — delivery failure must not wedge the timer
        _log.exception("weekly digest delivery failed")

    return report
