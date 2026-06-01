"""Ticket resolution lifecycle + TTM emission (CM-46).

Jira: CM-46  | Epic: CM-14 (Eval / production readiness)  | Phase 2

The single resolution entry point: transition a ticket to RESOLVED via the
repository ``resolve`` seam and emit the time-to-mitigate (TTM) outcome metric
(``metric.ttm_resolution_ms`` = ``resolved_at - created_at``). Kept thin and
separate from :class:`agents.maintenance.MaintenanceAgent` (which only creates
tickets) so the resolution flow has one auditable home and the agent stays free
of observability imports.

Offline-safe: ``emit_metric`` no-ops when OTel is unconfigured, and the
repository seam degrades to the in-memory store with no Cosmos.
"""

from __future__ import annotations

from datetime import datetime

from agents.observability import METRIC_TTM_RESOLUTION_MS, emit_metric

from .repository import TicketRepository, get_ticket_repository
from .schema import Ticket


def resolve_ticket(
    tenant_id: str,
    ticket_id: str,
    *,
    now: datetime,
    owner: str | None = None,
    repository: TicketRepository | None = None,
) -> Ticket | None:
    """Resolve a ticket and emit the TTM outcome metric.

    Stamps ``resolved_at``/``updated_at`` via the repository ``resolve`` seam and
    emits ``metric.ttm_resolution_ms`` (value = resolution time in ms, clamped to
    ``>= 0`` to absorb clock skew; attrs ``category``, ``priority``). Returns the
    resolved ticket, or ``None`` if the id is unknown (no metric emitted).
    """
    repo = repository if repository is not None else get_ticket_repository()
    ticket = repo.resolve(tenant_id, ticket_id, now=now, owner=owner)
    if ticket is None:
        return None
    # ``resolve`` always sets resolved_at; fall back to ``now`` to satisfy the
    # optional type and never divide on a None.
    resolved_at = ticket.resolved_at or now
    ttm_ms = max(0.0, (resolved_at - ticket.created_at).total_seconds() * 1000.0)
    emit_metric(
        METRIC_TTM_RESOLUTION_MS,
        value=ttm_ms,
        category=ticket.category,
        priority=str(ticket.priority),
    )
    return ticket
