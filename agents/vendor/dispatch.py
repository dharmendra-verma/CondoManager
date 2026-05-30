"""Auto-dispatch rule (CM-35 AC3).

Auto-dispatch only if **all** hold:
  * the matched vendor is pre-approved, AND
  * the estimated cost is below the threshold, AND
  * the issue is not safety/legal.

Otherwise the job goes to the manager-approval path. Cost is a deterministic
per-category estimate scaled by the CM-31 priority band; safety/legal is
derived conservatively (CM-31 tickets carry no explicit legal flag — CM-32
owns the semantic legal classifier — so we err toward requiring approval).
"""

from __future__ import annotations

from agents.maintenance import Priority
from agents.orchestrator.state import Intent

from .schema import DispatchDecision, VendorMatch

#: Default auto-dispatch ceiling in USD.
DEFAULT_THRESHOLD = 250.0

#: Base job-cost estimate per CM-31 maintenance category (USD).
_BASE_COST: dict[str, float] = {
    "plumbing": 150.0,
    "electrical": 200.0,
    "hvac": 300.0,
    "appliance": 180.0,
    "structural": 500.0,
    "pest": 120.0,
    "general": 100.0,
}
_DEFAULT_BASE_COST = 150.0

#: Priority surcharge — emergencies cost more to service same-day.
_PRIORITY_MULT: dict[Priority, float] = {
    Priority.P1: 1.5,
    Priority.P2: 1.2,
    Priority.P3: 1.0,
    Priority.P4: 0.8,
}

#: Categories that are inherently safety-sensitive -> always manager approval.
_SAFETY_CATEGORIES = frozenset({"structural"})


def estimate_cost(category: str, priority: Priority) -> float:
    """Deterministic job-cost estimate from category + priority band."""
    base = _BASE_COST.get(category, _DEFAULT_BASE_COST)
    return round(base * _PRIORITY_MULT[priority], 2)


def is_safety_or_legal(category: str, priority: Priority, intent: Intent | None) -> bool:
    """Conservative safety/legal gate.

    True when the band is an emergency (``P1``), the category is inherently
    safety-sensitive, or triage routed this as an escalation (a proxy for the
    legal/safety risk CM-32 will classify precisely).
    """
    return priority is Priority.P1 or category in _SAFETY_CATEGORIES or intent is Intent.ESCALATION


def decide(
    match: VendorMatch,
    category: str,
    priority: Priority,
    intent: Intent | None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> DispatchDecision:
    """Decide auto-dispatch vs manager approval for a matched vendor (AC3)."""
    estimated = estimate_cost(category, priority)
    if is_safety_or_legal(category, priority, intent):
        return DispatchDecision(
            auto=False, reason="safety_or_legal", estimated_cost=estimated, threshold=threshold
        )
    if not match.vendor.pre_approved:
        return DispatchDecision(
            auto=False, reason="not_preapproved", estimated_cost=estimated, threshold=threshold
        )
    if estimated >= threshold:
        return DispatchDecision(
            auto=False, reason="over_threshold", estimated_cost=estimated, threshold=threshold
        )
    return DispatchDecision(
        auto=True, reason="routine", estimated_cost=estimated, threshold=threshold
    )
