"""Message-composition tests (CM-35 AC4 + AC5)."""

from __future__ import annotations

from agents.vendor.messages import manager_approval_request, vendor_dispatch_notice
from agents.vendor.schema import DispatchDecision


def test_manager_approval_request(make_vendor) -> None:  # noqa: ANN001
    v = make_vendor(vendor_id="v-9", name="Premium Pipes")
    decision = DispatchDecision(
        auto=False, reason="over_threshold", estimated_cost=312.5, threshold=250.0
    )
    req = manager_approval_request(
        ticket_id="TKT-1",
        unit="4b",
        category="plumbing",
        priority="P2",
        vendor=v,
        decision=decision,
    )
    assert req["ticket_id"] == "TKT-1"
    assert req["vendor_id"] == "v-9"
    assert req["estimated_cost"] == "312.50"
    assert req["reason"] == "over_threshold"
    assert "approve" in req["actions"] and "deny" in req["actions"]


def test_vendor_dispatch_notice(make_vendor) -> None:  # noqa: ANN001
    v = make_vendor(vendor_id="v-2", name="AquaFix")
    notice = vendor_dispatch_notice(ticket_id="TKT-7", unit="3c", category="plumbing", vendor=v)
    assert notice["vendor_id"] == "v-2"
    assert "TKT-7" in notice["subject"]
    assert "3c" in notice["body"]
    assert notice["to_email"] == "v-2@example.test"
