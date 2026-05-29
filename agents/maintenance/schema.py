"""Ticket domain schema for the Maintenance Agent.

Jira: CM-31  | Epic: CM-Epic 4 (LangGraph Orchestrator) / CM-Epic 7  | Phase 0

The :class:`Ticket` model is the persisted shape in the CM-17 ``tickets``
Cosmos container (partition key ``/tenantId``). ``status`` is exactly the
four-state lifecycle from the CM-31 AC; ``owner`` carries ownership.

Serialization note: the model uses snake_case (``tenant_id``); the Cosmos
document uses ``tenantId`` for the partition-key path. The mapping lives
in :mod:`agents.maintenance.repository` (``_to_doc`` / ``_from_doc``) so
the domain model stays Pythonic and the storage shape stays Cosmos-native.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TicketStatus(StrEnum):
    """Ticket lifecycle — exactly the four states from the CM-31 AC."""

    NEW = "New"
    IN_PROGRESS = "In Progress"
    WAITING = "Waiting"
    RESOLVED = "Resolved"


class Priority(StrEnum):
    """Maintenance priority band. P1 is most urgent.

    Maps from :class:`agents.orchestrator.state.Urgency` and is then bumped
    by tone / repeat-status — see :mod:`agents.maintenance.priority`.
    """

    P1 = "P1"  # emergency — safety / active damage
    P2 = "P2"  # high
    P3 = "P3"  # medium (default when urgency is unknown)
    P4 = "P4"  # low


class Ticket(BaseModel):
    """A maintenance ticket persisted to the ``tickets`` Cosmos container.

    ``id`` is the tenant-facing confirmation code (e.g. ``TKT-3F8A2C9D``).
    ``duplicate_of`` is ``None`` for genuine new tickets; when a duplicate
    is detected the agent does NOT persist a new ticket — it returns a
    reference to the original instead (see :class:`MaintenanceAgent`).
    """

    id: str
    tenant_id: str
    unit: str
    issue_text: str
    category: str
    priority: Priority
    status: TicketStatus = TicketStatus.NEW
    owner: str | None = None
    created_at: datetime
    updated_at: datetime
    request_id: str | None = None
    duplicate_of: str | None = None
    eta: str | None = None
    history: list[str] = Field(default_factory=list)
