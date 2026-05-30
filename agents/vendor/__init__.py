"""``agents.vendor`` — Vendor Agent matching + auto-dispatch (CM-35).

Runs after the Maintenance Agent (CM-31): matches a contractor to a ticket and
either auto-dispatches routine, low-cost, pre-approved, non-safety jobs or
routes higher-stakes jobs to the manager-approval HITL gate.

Public API:

* :class:`VendorAgent` — orchestrator; called by ``agents.orchestrator.nodes.vendor``.
* :class:`Vendor`, :class:`CostTier`, :class:`VendorMatch`, :class:`DispatchDecision`.
* :func:`get_vendor_repository`, :func:`get_vendor_notifier` — env-gated seams.
"""

from __future__ import annotations

from .agent import VendorAgent
from .notifier import VendorNotifier, get_vendor_notifier
from .repository import VendorRepository, get_vendor_repository
from .schema import CostTier, DispatchDecision, Vendor, VendorMatch

__all__ = [
    "CostTier",
    "DispatchDecision",
    "Vendor",
    "VendorAgent",
    "VendorMatch",
    "VendorNotifier",
    "VendorRepository",
    "get_vendor_notifier",
    "get_vendor_repository",
]
