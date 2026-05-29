"""Vendor schema tests (CM-35 AC1)."""

from __future__ import annotations

import pytest
from agents.vendor.schema import CostTier, Vendor
from pydantic import ValidationError


def test_cost_tier_values() -> None:
    assert [t.value for t in CostTier] == ["low", "medium", "high"]


def test_vendor_carries_all_ac1_fields(make_vendor) -> None:  # noqa: ANN001
    v = make_vendor()
    assert v.categories and v.available_days
    assert 0.0 <= v.performance_score <= 1.0
    assert isinstance(v.cost_tier, CostTier)
    assert v.pre_approved is True


def test_performance_score_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Vendor(
            id="v",
            name="bad",
            categories=["plumbing"],
            available_days=["mon"],
            performance_score=1.5,
            cost_tier=CostTier.LOW,
        )
