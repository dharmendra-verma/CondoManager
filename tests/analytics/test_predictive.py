"""Predictive-rule tests (CM-36 AC4)."""

from __future__ import annotations

from agents.analytics.predictive import predict


def test_boiler_rule_fires_on_three_hvac_in_14_days(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="hvac", days_ago=d) for d in (1, 5, 10)]
    preds = predict(tickets, now=now)
    assert any(p.rule == "boiler-service" and p.unit == "4b" for p in preds)


def test_boiler_rule_silent_below_threshold(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="hvac", days_ago=d) for d in (1, 5)]
    assert [p for p in predict(tickets, now=now) if p.rule == "boiler-service"] == []


def test_hvac_outside_window_not_counted(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="hvac", days_ago=d) for d in (1, 5, 20)]  # 20d > 14d window
    assert [p for p in predict(tickets, now=now) if p.rule == "boiler-service"] == []


def test_plumbing_recurrence_rule(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(category="plumbing", days_ago=d) for d in (1, 5, 10, 20)]
    preds = predict(tickets, now=now)
    assert any(p.rule == "plumbing-recurrence" for p in preds)
