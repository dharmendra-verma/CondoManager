"""Maintenance Agent — ticket lifecycle orchestration (CM-31).

Pipeline (all deterministic, offline-safe):

    resolve unit + category
      -> dedup query (7-day window, same unit)
      -> OPEN duplicate?  yes: link to original, confirm tenant, STOP
                          no:  assign priority + ETA, persist new ticket,
                               notify manager, confirm tenant

The agent is pure of LangGraph/span/guardrail concerns — those stay in
``agents.orchestrator.nodes.maintenance``. Repository / notifier / code-gen /
clock are injectable so tests run without Cosmos, Slack, or wall-clock flake.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from agents.orchestrator.state import AgentState

from . import dedup
from .messages import manager_notification, tenant_confirmation
from .notifier import Notifier, get_notifier
from .priority import assign_priority, estimate_eta
from .repository import TicketRepository, get_ticket_repository
from .schema import Ticket, TicketStatus


def _default_code() -> str:
    """Tenant-facing confirmation code, e.g. ``TKT-3F8A2C9D``."""
    return f"TKT-{uuid.uuid4().hex[:8].upper()}"


class MaintenanceAgent:
    """Stateless orchestrator over the ticket domain.

    Construct once per node invocation (cheap — seams are cached singletons).
    Inject ``repository`` / ``notifier`` / ``code_generator`` / ``now_fn`` in
    tests for determinism.
    """

    def __init__(
        self,
        *,
        repository: TicketRepository | None = None,
        notifier: Notifier | None = None,
        code_generator: Callable[[], str] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository if repository is not None else get_ticket_repository()
        self._notifier = notifier if notifier is not None else get_notifier()
        self._gen_code = code_generator if code_generator is not None else _default_code
        self._now = now_fn if now_fn is not None else (lambda: datetime.now(UTC))

    def handle(self, state: AgentState) -> dict[str, Any]:
        """Run the ticket pipeline; return an ``AgentState``-mergeable update."""
        text = self._source_text(state)
        unit = dedup.extract_unit(text)
        category = dedup.categorize(text)
        now = self._now()

        existing: list[Ticket] = []
        if unit != dedup.UNKNOWN_UNIT:
            since = now - timedelta(days=dedup.DEDUP_WINDOW_DAYS)
            existing = self._repo.recent_for_unit(state.tenant_id, unit, since=since)

        duplicate = dedup.find_open_duplicate(
            unit=unit, issue_text=text, existing=existing, now=now
        )
        if duplicate is not None:
            return {
                "output": {
                    "status": "duplicate",
                    "ticket_id": duplicate.id,
                    "duplicate_of": duplicate.id,
                    "unit": duplicate.unit,
                    "category": duplicate.category,
                    "priority": str(duplicate.priority),
                    "eta": duplicate.eta,
                    "confirmation": tenant_confirmation(duplicate, is_duplicate=True),
                }
            }

        repeat = dedup.is_repeat(unit=unit, issue_text=text, existing=existing, now=now)
        priority = assign_priority(state.urgency, state.tone, is_repeat=repeat)
        eta = estimate_eta(priority, category)
        ticket = Ticket(
            id=self._gen_code(),
            tenant_id=state.tenant_id,
            unit=unit,
            issue_text=text,
            category=category,
            priority=priority,
            status=TicketStatus.NEW,
            owner=None,
            created_at=now,
            updated_at=now,
            request_id=state.request_id,
            eta=eta,
        )
        self._repo.add(ticket)
        self._notifier.notify_manager(manager_notification(ticket))

        return {
            "output": {
                "status": "ticket_created",
                "ticket_id": ticket.id,
                "unit": unit,
                "category": category,
                "priority": str(priority),
                "eta": eta,
                "is_repeat": repeat,
                "confirmation": tenant_confirmation(ticket),
            }
        }

    @staticmethod
    def _source_text(state: AgentState) -> str:
        """Prefer the PII-masked normalized content; fall back to raw."""
        if state.normalized is not None:
            return state.normalized.content
        return state.raw_message or ""
