"""End-to-end VendorAgent tests (CM-35)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.maintenance import Priority  # noqa: F401  (kept for parity / future use)
from agents.orchestrator.state import Intent
from agents.vendor.agent import ROUTE_DONE, ROUTE_HITL, VendorAgent
from agents.vendor.repository import InMemoryVendorRepository


def _wednesday() -> datetime:
    base = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)
    return base + timedelta(days=(2 - base.weekday()) % 7)


class _CapturingManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def notify_manager(self, notification: dict[str, str]) -> None:
        self.calls.append(notification)


class _CapturingVendor:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def notify_vendor(self, notice: dict[str, str]) -> None:
        self.calls.append(notice)


def _agent(vendors, manager, vendor_notifier):  # noqa: ANN001, ANN202
    return VendorAgent(
        repository=InMemoryVendorRepository(vendors),
        vendor_notifier=vendor_notifier,
        manager_notifier=manager,
        now_fn=_wednesday,
    )


def test_auto_dispatch_routine(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    v = make_vendor(vendor_id="p1", categories=["plumbing"], pre_approved=True)
    out = _agent([v], mgr, ven).handle(make_state(category="plumbing", priority="P3"))

    assert out["output"]["vendor_status"] == "auto_dispatched"
    assert out["output"]["vendor_id"] == "p1"
    assert out["routes"] == [ROUTE_DONE]
    assert len(ven.calls) == 1 and not mgr.calls  # vendor notified, manager not paged


def test_not_preapproved_routes_to_approval(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    v = make_vendor(vendor_id="p2", categories=["plumbing"], pre_approved=False)
    out = _agent([v], mgr, ven).handle(make_state(category="plumbing", priority="P3"))

    assert out["output"]["vendor_status"] == "pending_approval"
    assert out["output"]["approval_reason"] == "not_preapproved"
    assert out["routes"] == [ROUTE_HITL]
    assert len(mgr.calls) == 1 and not ven.calls  # manager paged, vendor not yet
    assert "approval_requested_at" in out["output"]


def test_safety_emergency_routes_to_approval(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    v = make_vendor(vendor_id="p1", categories=["plumbing"], pre_approved=True)
    out = _agent([v], mgr, ven).handle(make_state(category="plumbing", priority="P1"))

    assert out["output"]["vendor_status"] == "pending_approval"
    assert out["output"]["approval_reason"] == "safety_or_legal"
    assert out["routes"] == [ROUTE_HITL]


def test_no_vendor_alerts_manager(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    only_electrical = make_vendor(vendor_id="e1", categories=["electrical"])
    out = _agent([only_electrical], mgr, ven).handle(make_state(category="plumbing"))

    assert out["output"]["vendor_status"] == "no_vendor"
    assert out["routes"] == [ROUTE_DONE]
    assert len(mgr.calls) == 1 and not ven.calls


def test_duplicate_passthrough(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    v = make_vendor(vendor_id="p1", categories=["plumbing"])
    out = _agent([v], mgr, ven).handle(make_state(status="duplicate", category="plumbing"))

    assert out == {"routes": [ROUTE_DONE]}
    assert not mgr.calls and not ven.calls


def test_escalation_intent_routes_to_approval(make_vendor, make_state) -> None:  # noqa: ANN001
    mgr, ven = _CapturingManager(), _CapturingVendor()
    v = make_vendor(vendor_id="p1", categories=["plumbing"], pre_approved=True)
    state = make_state(category="plumbing", priority="P3", intent=Intent.ESCALATION)
    out = _agent([v], mgr, ven).handle(state)

    assert out["output"]["vendor_status"] == "pending_approval"
    assert out["output"]["approval_reason"] == "safety_or_legal"
