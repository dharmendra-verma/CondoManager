"""Dedup-precision eval (CM-31 AC6: precision > 95%).

Grades the deterministic :func:`agents.maintenance.dedup.is_duplicate_pair`
predicate against a hand-labeled fixture of issue pairs. Precision (not
recall) is the gated metric — a false *positive* (auto-merging two distinct
issues) is the costly error, so the fixture is rich in hard negatives.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.maintenance.dedup import is_duplicate_pair

_SEED = Path(__file__).resolve().parents[1] / "eval" / "maintenance_dedup_seed.jsonl"


def _load() -> list[dict]:
    with _SEED.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_dedup_precision_above_95_percent() -> None:
    rows = _load()
    assert len(rows) >= 20, "eval fixture too small to be meaningful"

    tp = fp = fn = 0
    for row in rows:
        existing, candidate = row["existing"], row["candidate"]
        predicted = is_duplicate_pair(
            existing_unit=existing["unit"],
            existing_issue=existing["issue"],
            candidate_unit=candidate["unit"],
            candidate_issue=candidate["issue"],
            age_days=existing["created_days_ago"],
        )
        actual = bool(row["is_duplicate"])
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1

    assert tp > 0, "no true positives — predicate or fixture is broken"
    precision = tp / (tp + fp)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    # AC6 gate: precision > 0.95. Recall is reported for visibility only.
    assert precision > 0.95, f"precision {precision:.3f} (recall {recall:.3f}, fp={fp})"
