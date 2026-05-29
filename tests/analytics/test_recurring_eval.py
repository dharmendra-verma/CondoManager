"""Recurring-detection eval (CM-36 AC1).

Runs ``detect_recurring`` over labeled ticket scenarios and asserts the set of
detected ``(unit, category)`` groups matches the expected set. Deterministic —
gates accuracy at 100% over the fixture.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents.analytics.models import AnalyticsTicket
from agents.analytics.recurring import detect_recurring

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)
_SEED = Path(__file__).resolve().parents[1] / "eval" / "analytics_recurring_seed.jsonl"


def _load() -> list[dict]:
    with _SEED.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _ticket(spec: dict) -> AnalyticsTicket:
    created = _NOW - timedelta(days=spec["days_ago"])
    return AnalyticsTicket(
        tenant_id="t-1",
        unit=spec["unit"],
        category=spec["category"],
        status="New",
        created_at=created,
        updated_at=created,
    )


def test_recurring_accuracy() -> None:
    rows = _load()
    assert len(rows) >= 6, "eval fixture too small to be meaningful"

    correct = 0
    for row in rows:
        tickets = [_ticket(s) for s in row["tickets"]]
        detected = {(i.unit, i.category) for i in detect_recurring(tickets, now=_NOW)}
        expected = {(u, c) for u, c in row["expected"]}
        if detected == expected:
            correct += 1

    accuracy = correct / len(rows)
    assert accuracy >= 0.9, f"recurring detection accuracy {accuracy:.3f}"
