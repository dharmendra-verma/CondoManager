"""Vendor Agent — match a contractor and auto-dispatch or seek approval (CM-35).

Runs after the Maintenance Agent (CM-31). Reads the created ticket from
``state.output`` and:

  * no real ticket (duplicate / guardrail / missing)  -> pass through to END
  * no vendor matches the category/day                -> alert manager, END
  * matched + auto-dispatchable (AC3)                  -> notify vendor, END
  * matched but needs approval                         -> alert manager, route
                                                          to ``hitl_review``

All decisions are deterministic; repository / notifiers / clock are injectable
for tests. The span + guardrail contract lives in the ``vendor`` node.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agents.maintenance import Priority, get_notifier
from agents.maintenance.notifier import Notifier
from agents.orchestrator.state import AgentState

from . import dispatch
from .matching import match_vendors
from .messages import manager_approval_request, vendor_dispatch_notice
from .notifier import VendorNotifier, get_vendor_notifier
from .repository import VendorRepository, get_vendor_repository

#: Route names the vendor node emits; the graph router maps these.
ROUTE_HITL = "hitl_review"
ROUTE_DONE = "vendor_done"


class VendorAgent:
    """Stateless orchestrator over the vendor domain."""

    def __init__(
        self,
        *,
        repository: VendorRepository | None = None,
        vendor_notifier: VendorNotifier | None = None,
        manager_notifier: Notifier | None = None,
        now_fn: Callable[[], datetime] | None = None,
        threshold: float = dispatch.DEFAULT_THRESHOLD,
    ) -> None:
        self._repo = repository if repository is not None else get_vendor_repository()
        self._vendor_notifier = (
            vendor_notifier if vendor_notifier is not None else get_vendor_notifier()
        )
        self._manager = manager_notifier if manager_notifier is not None else get_notifier()
        self._now = now_fn if now_fn is not None else (lambda: datetime.now(UTC))
        self._threshold = threshold

    def handle(self, state: AgentState) -> dict[str, Any]:
        """Run the vendor pipeline; return an ``AgentState``-mergeable update."""
        output = state.output or {}
        # Only dispatch for a freshly created maintenance ticket. Duplicates,
        # guardrail terminations, and non-maintenance outputs pass straight
        # through to END (route only, output untouched).
        if output.get("status") != "ticket_created":
            return {"routes": [ROUTE_DONE]}

        category = str(output.get("category", "general"))
        priority = _as_priority(output.get("priority"))
        ticket_id = str(output.get("ticket_id", ""))
        unit = str(output.get("unit", "unknown"))

        matches = match_vendors(category, self._now(), self._repo.list_vendors())
        if not matches:
            self._manager.notify_manager(
                {
                    "title": f"No vendor available for {category} — unit {unit}",
                    "ticket_id": ticket_id,
                    "unit": unit,
                    "category": category,
                }
            )
            return {
                "output": {**output, "vendor_status": "no_vendor"},
                "routes": [ROUTE_DONE],
            }

        best = matches[0]
        decision = dispatch.decide(
            best, category, priority, state.intent, threshold=self._threshold
        )

        if decision.auto:
            self._vendor_notifier.notify_vendor(
                vendor_dispatch_notice(
                    ticket_id=ticket_id, unit=unit, category=category, vendor=best.vendor
                )
            )
            return {
                "output": {
                    **output,
                    "vendor_status": "auto_dispatched",
                    "vendor_id": best.vendor.id,
                    "estimated_cost": decision.estimated_cost,
                },
                "routes": [ROUTE_DONE],
            }

        # Needs manager approval -> compose request + route to the HITL gate.
        self._manager.notify_manager(
            manager_approval_request(
                ticket_id=ticket_id,
                unit=unit,
                category=category,
                priority=str(priority),
                vendor=best.vendor,
                decision=decision,
            )
        )
        return {
            "output": {
                **output,
                "vendor_status": "pending_approval",
                "vendor_id": best.vendor.id,
                "estimated_cost": decision.estimated_cost,
                "approval_reason": decision.reason,
                "approval_requested_at": self._now().isoformat(),
            },
            "routes": [ROUTE_HITL],
        }


def _as_priority(value: object) -> Priority:
    """Coerce a ``state.output['priority']`` string back to :class:`Priority`."""
    try:
        return Priority(str(value))
    except ValueError:
        return Priority.P3
