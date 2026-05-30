"""Weekly-digest orchestration (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

:func:`run_digest` is the heart of the story. It depends only on three
Protocols (reader, digest store, notifier) so it's fully unit-testable with
in-memory fakes — the Function App (``functions/analytics-digest``) wires the
real Cosmos + Slack implementations. Mirrors ``agents.knowledge.sync.run_sync``.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta

from .analyze import RECURRING_WINDOW_DAYS
from .digest import build_digest, render_digest_text
from .models import DigestReport
from .repository import AnalyticsReader, DigestNotifier, DigestStore

_log = logging.getLogger(__name__)


def run_digest(
    *,
    tenant_id: str,
    reader: AnalyticsReader,
    digest_store: DigestStore,
    notifier: DigestNotifier,
    now: datetime,
    run_id: str | None = None,
    window_days: int = RECURRING_WINDOW_DAYS,
) -> DigestReport:
    """Build the weekly digest for ``tenant_id``, persist it, and notify.

    Non-blocking (AC #6): a notifier failure is reported in the returned
    :class:`DigestReport`, never raised — the run still persisted the digest.
    """
    rid = run_id or uuid.uuid4().hex
    started = time.monotonic()
    since = now - timedelta(days=window_days)

    tickets = reader.recent_tickets(tenant_id, since=since)
    events = reader.recent_escalation_events(tenant_id, since=since)

    digest = build_digest(
        tenant_id=tenant_id,
        tickets=tickets,
        escalation_events=events,
        now=now,
        window_days=window_days,
    )
    digest_store.save(digest)
    notified = notifier.send(render_digest_text(digest))

    duration_ms = (time.monotonic() - started) * 1000
    _log.info(
        "analytics.run run_id=%s tenant=%s week_start=%s recurring=%d "
        "contractors=%d predictions=%d escalations=%d notified=%s duration_ms=%.1f",
        rid,
        tenant_id,
        digest.week_start,
        len(digest.recurring),
        len(digest.contractors),
        len(digest.predictions),
        len(events),
        notified,
        duration_ms,
    )
    return DigestReport(
        run_id=rid,
        tenant_id=tenant_id,
        week_start=digest.week_start,
        recurring_count=len(digest.recurring),
        contractor_count=len(digest.contractors),
        prediction_count=len(digest.predictions),
        escalation_count=len(events),
        notified=notified,
    )
