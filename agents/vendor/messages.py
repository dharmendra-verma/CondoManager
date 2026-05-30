"""Message composition for vendor dispatch (CM-35 AC4 + AC5).

Pure, deterministic payloads — no transport. The manager approval request is a
structured dict a Slack/email renderer (CM-32's shared transport) turns into an
interactive message with approve/deny affordances; the vendor dispatch notice
is the email/SMS body the (deferred) real notifier will send.
"""

from __future__ import annotations

from .schema import DispatchDecision, Vendor


def manager_approval_request(
    *,
    ticket_id: str,
    unit: str,
    category: str,
    priority: str,
    vendor: Vendor,
    decision: DispatchDecision,
) -> dict[str, str]:
    """Structured approval request for the manager (AC4).

    ``actions`` names the affordances a Slack interactive message / email
    buttons should render; the resume payload (approve/deny) flows back
    through the ``hitl_review`` interrupt.
    """
    return {
        "title": f"Approve {priority} {category} dispatch — unit {unit}?",
        "ticket_id": ticket_id,
        "unit": unit,
        "category": category,
        "priority": priority,
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
        "estimated_cost": f"{decision.estimated_cost:.2f}",
        "reason": decision.reason,
        "actions": "approve,deny",
    }


def vendor_dispatch_notice(
    *,
    ticket_id: str,
    unit: str,
    category: str,
    vendor: Vendor,
) -> dict[str, str]:
    """Email/SMS payload notifying the chosen vendor of the job (AC5)."""
    return {
        "vendor_id": vendor.id,
        "to_email": vendor.contact_email or "",
        "to_sms": vendor.contact_sms or "",
        "subject": f"New job: {category} at unit {unit} (ticket {ticket_id})",
        "body": (
            f"Hi {vendor.name}, you've been dispatched for a {category} job at "
            f"unit {unit}. Reference ticket {ticket_id}. Please confirm your ETA."
        ),
    }
