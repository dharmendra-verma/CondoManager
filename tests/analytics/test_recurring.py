"""Recurring-issue detection tests (CM-36 AC1)."""

from __future__ import annotations

from agents.analytics.recurring import detect_recurring


def test_more_than_three_in_window_is_recurring(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(days_ago=d) for d in (1, 5, 10, 20)]  # 4 same unit+category
    issues = detect_recurring(tickets, now=now)
    assert len(issues) == 1
    assert issues[0].count == 4
    assert issues[0].unit == "4b"
    assert issues[0].category == "plumbing"


def test_exactly_three_is_not_recurring(make_ticket, now) -> None:  # noqa: ANN001
    tickets = [make_ticket(days_ago=d) for d in (1, 5, 10)]
    assert detect_recurring(tickets, now=now) == []


def test_outside_window_excluded(make_ticket, now) -> None:  # noqa: ANN001
    # 3 inside + 2 older than 30 days -> only 3 in window -> not recurring.
    tickets = [make_ticket(days_ago=d) for d in (1, 5, 10, 40, 50)]
    assert detect_recurring(tickets, now=now) == []


def test_separate_units_not_merged(make_ticket, now) -> None:  # noqa: ANN001
    a = [make_ticket(unit="4b", days_ago=d) for d in (1, 2, 3, 4)]
    b = [make_ticket(unit="9c", days_ago=d) for d in (1, 2)]
    issues = detect_recurring(a + b, now=now)
    assert len(issues) == 1
    assert issues[0].unit == "4b"


def test_different_category_separate(make_ticket, now) -> None:  # noqa: ANN001
    plumb = [make_ticket(category="plumbing", days_ago=d) for d in (1, 2, 3, 4)]
    elec = [make_ticket(category="electrical", days_ago=d) for d in (1, 2, 3, 4)]
    issues = detect_recurring(plumb + elec, now=now)
    assert {i.category for i in issues} == {"plumbing", "electrical"}
