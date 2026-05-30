"""Auto-dispatch rule truth table (CM-35 AC3)."""

from __future__ import annotations

from agents.maintenance import Priority
from agents.orchestrator.state import Intent
from agents.vendor.dispatch import decide, estimate_cost, is_safety_or_legal
from agents.vendor.schema import VendorMatch


def _match(make_vendor, **kw):  # noqa: ANN001, ANN003
    return VendorMatch(vendor=make_vendor(**kw), score=0.9)


def test_estimate_cost_scales_with_priority() -> None:
    assert estimate_cost("plumbing", Priority.P3) == 150.0
    assert estimate_cost("plumbing", Priority.P1) == 225.0  # 150 * 1.5
    assert estimate_cost("hvac", Priority.P3) == 300.0


def test_auto_dispatch_routine(make_vendor) -> None:  # noqa: ANN001
    d = decide(_match(make_vendor, pre_approved=True), "plumbing", Priority.P3, Intent.MAINTENANCE)
    assert d.auto is True
    assert d.reason == "routine"


def test_not_preapproved_needs_approval(make_vendor) -> None:  # noqa: ANN001
    d = decide(_match(make_vendor, pre_approved=False), "plumbing", Priority.P3, Intent.MAINTENANCE)
    assert d.auto is False
    assert d.reason == "not_preapproved"


def test_over_threshold_needs_approval(make_vendor) -> None:  # noqa: ANN001
    # hvac P3 = 300 >= 250 threshold.
    d = decide(_match(make_vendor, pre_approved=True), "hvac", Priority.P3, Intent.MAINTENANCE)
    assert d.auto is False
    assert d.reason == "over_threshold"


def test_emergency_is_safety(make_vendor) -> None:  # noqa: ANN001
    d = decide(_match(make_vendor, pre_approved=True), "plumbing", Priority.P1, Intent.MAINTENANCE)
    assert d.auto is False
    assert d.reason == "safety_or_legal"


def test_structural_is_safety(make_vendor) -> None:  # noqa: ANN001
    d = decide(
        _match(make_vendor, categories=["structural"], pre_approved=True),
        "structural",
        Priority.P3,
        Intent.MAINTENANCE,
    )
    assert d.auto is False
    assert d.reason == "safety_or_legal"


def test_escalation_intent_is_legal(make_vendor) -> None:  # noqa: ANN001
    d = decide(_match(make_vendor, pre_approved=True), "plumbing", Priority.P3, Intent.ESCALATION)
    assert d.auto is False
    assert d.reason == "safety_or_legal"


def test_is_safety_or_legal_helper() -> None:
    assert is_safety_or_legal("plumbing", Priority.P1, None) is True
    assert is_safety_or_legal("structural", Priority.P3, None) is True
    assert is_safety_or_legal("plumbing", Priority.P3, Intent.ESCALATION) is True
    assert is_safety_or_legal("plumbing", Priority.P3, Intent.MAINTENANCE) is False
