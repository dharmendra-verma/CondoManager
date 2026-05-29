"""Predictive rules (CM-36 AC4).

Simple, deterministic threshold rules over recent tickets — "predictive" in the
AC sense ("e.g. boiler likely to need service if X tickets in last N days"), no
ML. Each rule that fires yields a :class:`Prediction` the digest surfaces.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import AnalyticsTicket, Prediction


@dataclass(frozen=True)
class Rule:
    """A threshold rule: ``>= count`` ``category`` tickets for one unit within
    ``window_days`` predicts an impending failure."""

    key: str
    category: str
    window_days: int
    count: int
    severity: str
    message: str


#: Near-term rules. Extend as operations learns the building's failure modes.
RULES: tuple[Rule, ...] = (
    Rule(
        key="boiler-service",
        category="hvac",
        window_days=14,
        count=3,
        severity="warn",
        message="Repeated HVAC tickets — boiler/heating likely needs preventive service.",
    ),
    Rule(
        key="plumbing-recurrence",
        category="plumbing",
        window_days=30,
        count=4,
        severity="watch",
        message="Frequent plumbing issues — inspect for a systemic leak/pipe problem.",
    ),
)


def predict(tickets: list[AnalyticsTicket], *, now: datetime) -> list[Prediction]:
    """Return predictions for every (rule, unit) whose threshold is met."""
    predictions: list[Prediction] = []
    for rule in RULES:
        cutoff = now - timedelta(days=rule.window_days)
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for t in tickets:
            if t.category == rule.category and t.created_at >= cutoff:
                counts[(t.tenant_id, t.unit)] += 1
        for (tenant_id, unit), n in counts.items():
            if n >= rule.count:
                predictions.append(
                    Prediction(
                        tenant_id=tenant_id,
                        unit=unit,
                        category=rule.category,
                        rule=rule.key,
                        message=rule.message,
                        severity=rule.severity,
                    )
                )
    predictions.sort(key=lambda p: (p.tenant_id, p.unit, p.rule))
    return predictions
