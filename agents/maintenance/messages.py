"""SOP-aligned message composition (CM-31 AC4 + AC5).

Pure, deterministic templates — no LLM — so the confirmation/notification
text is exactly assertable in tests and renders identically every run.

* :func:`tenant_confirmation` — tenant-facing reply carrying the
  confirmation code + ETA (AC4). Has a duplicate variant that points the
  tenant at the already-logged ticket.
* :func:`manager_notification` — structured payload the :class:`Notifier`
  dispatches to the manager on a NEW ticket (AC5).
"""

from __future__ import annotations

from .schema import Ticket


def tenant_confirmation(ticket: Ticket, *, is_duplicate: bool = False) -> str:
    """Compose the tenant confirmation message.

    On a genuine new ticket, confirms logging with the code + ETA. On a
    detected duplicate, reassures the tenant the issue is already tracked
    under the original ticket's code + ETA (no second ticket is created).
    """
    if is_duplicate:
        return (
            f"Thanks for the update on your {ticket.category} issue for unit "
            f"{ticket.unit}. We already have this logged as ticket "
            f"{ticket.id} and our team is on it — expected resolution "
            f"{ticket.eta or 'as soon as possible'}. No need to do anything "
            f"further; we'll keep you posted."
        )
    return (
        f"Thanks for reporting your {ticket.category} issue for unit "
        f"{ticket.unit}. We've logged it as ticket {ticket.id} (priority "
        f"{ticket.priority}). Our team will address it {ticket.eta or 'shortly'}. "
        f"Reply with your ticket code {ticket.id} for any updates."
    )


def manager_notification(ticket: Ticket) -> dict[str, str]:
    """Structured manager alert for a new ticket (AC5).

    Returned as a dict so the :class:`Notifier` seam can render it for Slack,
    email, or a log line without re-parsing free text.
    """
    return {
        "title": f"New {ticket.priority} maintenance ticket — unit {ticket.unit}",
        "ticket_id": ticket.id,
        "tenant_id": ticket.tenant_id,
        "unit": ticket.unit,
        "category": ticket.category,
        "priority": str(ticket.priority),
        "status": str(ticket.status),
        "eta": ticket.eta or "",
        "summary": ticket.issue_text,
    }
