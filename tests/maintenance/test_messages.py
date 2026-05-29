"""Message-composition tests (CM-31 AC4 + AC5)."""

from __future__ import annotations

from agents.maintenance.messages import manager_notification, tenant_confirmation


def test_tenant_confirmation_has_code_and_eta(make_ticket) -> None:  # noqa: ANN001
    t = make_ticket(ticket_id="TKT-12345678", eta="within 2 hours")
    msg = tenant_confirmation(t)
    assert "TKT-12345678" in msg
    assert "within 2 hours" in msg


def test_tenant_confirmation_duplicate_variant(make_ticket) -> None:  # noqa: ANN001
    t = make_ticket(ticket_id="TKT-DUP00001")
    msg = tenant_confirmation(t, is_duplicate=True)
    assert "already have this logged" in msg
    assert "TKT-DUP00001" in msg


def test_manager_notification_payload(make_ticket) -> None:  # noqa: ANN001
    t = make_ticket(ticket_id="TKT-AAAA0001", unit="4b")
    note = manager_notification(t)
    assert note["ticket_id"] == "TKT-AAAA0001"
    assert note["unit"] == "4b"
    assert note["priority"] == str(t.priority)
    assert "4b" in note["title"]
