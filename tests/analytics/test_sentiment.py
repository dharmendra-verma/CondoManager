"""Sentiment-trend tests (CM-36 AC3)."""

from __future__ import annotations

from agents.analytics.sentiment import sentiment_trend


def test_weekly_mean_per_building(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [
        make_ticket(tone="angry", days_ago=0),  # -1.0
        make_ticket(tone="neutral", days_ago=0),  # 0.0
    ]
    points = sentiment_trend(tickets, now=now)
    assert len(points) == 1
    assert points[0].building == "t-1"
    assert points[0].score == -0.5
    assert points[0].sample_size == 2


def test_separate_weeks(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [
        make_ticket(tone="angry", days_ago=0),
        make_ticket(tone="neutral", days_ago=8),  # prior ISO week
    ]
    points = sentiment_trend(tickets, now=now)
    assert len(points) == 2
    assert {p.week_start for p in points} == {p.week_start for p in points}  # distinct weeks
    assert sorted(p.score for p in points) == [-1.0, 0.0]


def test_no_tone_is_empty(make_ticket, now) -> None:  # noqa: ANN001
    assert sentiment_trend([make_ticket(tone=None)], now=now) == []


def test_unknown_tone_skipped(make_ticket, now) -> None:  # noqa: ANN001
    assert sentiment_trend([make_ticket(tone="ecstatic")], now=now) == []
