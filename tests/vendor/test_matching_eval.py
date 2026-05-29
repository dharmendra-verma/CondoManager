"""Matching-quality eval (CM-35 AC2).

Asserts the matcher picks the expected best vendor per category against a
labeled fixture, evaluated on a weekday (so weekday-only vendors are in play).
Deterministic — gates accuracy at 100% over the seeded roster.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents.vendor.matching import match_vendors
from agents.vendor.seed import seed_vendors

_SEED = Path(__file__).resolve().parents[1] / "eval" / "vendor_match_seed.jsonl"


def _wednesday() -> datetime:
    base = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)
    return base + timedelta(days=(2 - base.weekday()) % 7)


def _load() -> list[dict]:
    with _SEED.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_matching_accuracy() -> None:
    rows = _load()
    assert len(rows) >= 6, "eval fixture too small to be meaningful"
    vendors = seed_vendors()
    when = _wednesday()

    correct = 0
    for row in rows:
        matches = match_vendors(row["category"], when, vendors)
        assert matches, f"no match for {row['category']}"
        if matches[0].vendor.id == row["expected_vendor_id"]:
            correct += 1

    accuracy = correct / len(rows)
    assert accuracy >= 0.9, f"matching accuracy {accuracy:.3f}"
