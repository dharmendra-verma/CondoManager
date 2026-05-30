"""Shared fixtures for ``tests/vendor/``.

Resets the cached vendor seams (repository + vendor notifier) and the reused
maintenance manager-notifier around every test, and provides factories for
vendors and post-maintenance ``AgentState``s.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from agents.maintenance import notifier as maint_notifier_mod
from agents.orchestrator.state import AgentState
from agents.vendor import notifier as vendor_notifier_mod
from agents.vendor import repository as vendor_repo_mod
from agents.vendor.schema import CostTier, Vendor


@pytest.fixture(autouse=True)
def reset_seams() -> Generator[None, None, None]:
    vendor_repo_mod._reset_for_tests()
    vendor_notifier_mod._reset_for_tests()
    maint_notifier_mod._reset_for_tests()
    yield
    vendor_repo_mod._reset_for_tests()
    vendor_notifier_mod._reset_for_tests()
    maint_notifier_mod._reset_for_tests()


@pytest.fixture
def make_vendor() -> Callable[..., Vendor]:
    def _make(
        *,
        vendor_id: str = "v-1",
        name: str = "Test Vendor",
        categories: list[str] | None = None,
        available_days: list[str] | None = None,
        performance_score: float = 0.9,
        cost_tier: CostTier = CostTier.LOW,
        pre_approved: bool = True,
    ) -> Vendor:
        return Vendor(
            id=vendor_id,
            name=name,
            categories=categories or ["plumbing"],
            available_days=available_days or ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            performance_score=performance_score,
            cost_tier=cost_tier,
            pre_approved=pre_approved,
            contact_email=f"{vendor_id}@example.test",
        )

    return _make


@pytest.fixture
def make_state() -> Callable[..., AgentState]:
    def _make(
        *,
        status: str = "ticket_created",
        category: str = "plumbing",
        priority: str = "P3",
        unit: str = "4b",
        ticket_id: str = "TKT-AAAA0001",
        intent: object | None = None,
    ) -> AgentState:
        return AgentState(
            tenant_id="t-1",
            request_id="r-1",
            intent=intent,  # type: ignore[arg-type]
            output={
                "status": status,
                "category": category,
                "priority": priority,
                "unit": unit,
                "ticket_id": ticket_id,
            },
        )

    return _make
