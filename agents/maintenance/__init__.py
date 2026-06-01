"""``agents.maintenance`` — Maintenance Agent ticket lifecycle + dedup (CM-31).

Replaces the CM-28 ``maintenance`` stub node with a real agent that detects
duplicates, creates priority-ranked tickets, sends SOP-aligned tenant
confirmations, and notifies the manager on new tickets.

Public API:

* :class:`MaintenanceAgent` — orchestrates the pipeline; called by
  ``agents.orchestrator.nodes.maintenance``.
* :class:`Ticket`, :class:`TicketStatus`, :class:`Priority` — domain schema.
* :func:`get_ticket_repository`, :func:`get_notifier` — env-gated seams.
"""

from __future__ import annotations

from .agent import MaintenanceAgent
from .lifecycle import resolve_ticket
from .notifier import Notifier, get_notifier
from .repository import TicketRepository, get_ticket_repository
from .schema import Priority, Ticket, TicketStatus

__all__ = [
    "MaintenanceAgent",
    "Notifier",
    "Priority",
    "Ticket",
    "TicketRepository",
    "TicketStatus",
    "get_notifier",
    "get_ticket_repository",
    "resolve_ticket",
]
