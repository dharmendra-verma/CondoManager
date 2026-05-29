"""Analytics source + selector tests (CM-36)."""

from __future__ import annotations

from datetime import timedelta

from agents.analytics.source import InMemoryAnalyticsSource, get_analytics_source


def test_in_memory_filters_by_since(make_ticket, now) -> None:  # noqa: ANN001
    src = InMemoryAnalyticsSource([make_ticket(days_ago=2), make_ticket(days_ago=40)])
    recent = src.list_tickets(since=now - timedelta(days=30))
    assert len(recent) == 1


def test_selector_offline_is_in_memory(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert isinstance(get_analytics_source(), InMemoryAnalyticsSource)


def test_selector_placeholder_is_in_memory(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("COSMOS_ENDPOINT", "REPLACE-ME")
    assert isinstance(get_analytics_source(), InMemoryAnalyticsSource)


def test_selector_cached(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert get_analytics_source() is get_analytics_source()
