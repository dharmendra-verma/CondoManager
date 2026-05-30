"""Vendor matching tests (CM-35 AC2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.vendor.matching import match_vendors, weekday_name
from agents.vendor.schema import CostTier


def _on_weekday(weekday: int) -> datetime:
    base = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)  # arbitrary reference
    return base + timedelta(days=(weekday - base.weekday()) % 7)


WEDNESDAY = _on_weekday(2)
SATURDAY = _on_weekday(5)


def test_filters_by_category(make_vendor) -> None:  # noqa: ANN001
    plumber = make_vendor(vendor_id="p", categories=["plumbing"])
    matches = match_vendors("electrical", WEDNESDAY, [plumber])
    assert matches == []


def test_filters_by_availability(make_vendor) -> None:  # noqa: ANN001
    weekday_only = make_vendor(vendor_id="w", available_days=["mon", "tue", "wed", "thu", "fri"])
    assert match_vendors("plumbing", SATURDAY, [weekday_only]) == []
    assert len(match_vendors("plumbing", WEDNESDAY, [weekday_only])) == 1


def test_ranks_by_performance(make_vendor) -> None:  # noqa: ANN001
    low = make_vendor(vendor_id="low", performance_score=0.80)
    high = make_vendor(vendor_id="high", performance_score=0.95)
    matches = match_vendors("plumbing", WEDNESDAY, [low, high])
    assert [m.vendor.id for m in matches] == ["high", "low"]


def test_cost_tier_breaks_ties(make_vendor) -> None:  # noqa: ANN001
    pricey = make_vendor(vendor_id="pricey", performance_score=0.9, cost_tier=CostTier.HIGH)
    cheap = make_vendor(vendor_id="cheap", performance_score=0.9, cost_tier=CostTier.LOW)
    matches = match_vendors("plumbing", WEDNESDAY, [pricey, cheap])
    assert [m.vendor.id for m in matches] == ["cheap", "pricey"]


def test_weekday_name() -> None:
    assert weekday_name(WEDNESDAY) == "wed"
    assert weekday_name(SATURDAY) == "sat"
