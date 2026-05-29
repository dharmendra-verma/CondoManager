"""Sentiment trend per building, week-over-week (CM-36 AC3).

Maps the CM-30 ``tone`` to a score in ``[-1.0, 0.0]`` (higher = calmer) and
averages per building per ISO week. ``tone`` is not yet persisted on tickets,
so this returns an empty trend until it is (the digest notes the gap) — the
computation itself is fully exercised by fixtures that carry ``tone``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .models import AnalyticsTicket, SentimentPoint

#: Tone -> sentiment score. Unknown / absent tone is skipped.
_TONE_SCORE: dict[str, float] = {
    "neutral": 0.0,
    "urgent": -0.25,
    "frustrated": -0.5,
    "angry": -1.0,
}

DEFAULT_WEEKS = 4


def _week_start(when: datetime) -> datetime:
    """Monday 00:00 of ``when``'s ISO week, preserving tzinfo."""
    monday = when - timedelta(days=when.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=when.tzinfo)


def sentiment_trend(
    tickets: list[AnalyticsTicket],
    *,
    now: datetime,
    weeks: int = DEFAULT_WEEKS,
) -> list[SentimentPoint]:
    """Return week-over-week mean sentiment per building."""
    cutoff = _week_start(now) - timedelta(weeks=weeks - 1)
    buckets: dict[tuple[str, datetime], list[float]] = defaultdict(list)
    for t in tickets:
        if t.tone is None:
            continue
        score = _TONE_SCORE.get(t.tone)
        if score is None:
            continue
        ws = _week_start(t.created_at)
        if ws < cutoff:
            continue
        buckets[(t.tenant_id, ws)].append(score)

    points = [
        SentimentPoint(
            building=building,
            week_start=ws,
            score=round(sum(scores) / len(scores), 3),
            sample_size=len(scores),
        )
        for (building, ws), scores in buckets.items()
    ]
    points.sort(key=lambda p: (p.building, p.week_start))
    return points
