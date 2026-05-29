"""Orchestrator tests (CM-36) — run_weekly_digest is non-blocking (AC6)."""

from __future__ import annotations

from datetime import datetime

from agents.analytics.models import DigestReport
from agents.analytics.run import run_weekly_digest
from agents.analytics.source import InMemoryAnalyticsSource


class _CapturingDelivery:
    def __init__(self) -> None:
        self.reports: list[DigestReport] = []

    def deliver(self, report: DigestReport) -> None:
        self.reports.append(report)


class _ExplodingSource:
    def list_tickets(self, *, since: datetime):  # noqa: ANN202
        raise RuntimeError("cosmos down")


def test_happy_path_computes_and_delivers(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="plumbing", days_ago=d) for d in (1, 5, 10, 20)]
    delivery = _CapturingDelivery()
    report = run_weekly_digest(now=now, source=InMemoryAnalyticsSource(tickets), delivery=delivery)
    assert report.ticket_count == 4
    assert len(report.recurring) == 1
    assert delivery.reports == [report]


def test_source_failure_is_non_blocking(now) -> None:  # noqa: ANN001
    delivery = _CapturingDelivery()
    # Must NOT raise — the timer would otherwise wedge.
    report = run_weekly_digest(now=now, source=_ExplodingSource(), delivery=delivery)
    assert report.ticket_count == 0
    assert len(delivery.reports) == 1  # empty report still delivered


def test_delivery_failure_is_non_blocking(make_ticket, now) -> None:  # noqa: ANN001
    class _ExplodingDelivery:
        def deliver(self, report: DigestReport) -> None:
            raise RuntimeError("smtp down")

    report = run_weekly_digest(
        now=now,
        source=InMemoryAnalyticsSource([make_ticket()]),
        delivery=_ExplodingDelivery(),
    )
    assert report.ticket_count == 1  # returned despite delivery blowing up
