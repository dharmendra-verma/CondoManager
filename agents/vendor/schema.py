"""Vendor domain schema for the Vendor Agent.

Jira: CM-35  | Epic: CM-Epic 10 (Vendor dispatch)  | Phase 1

The Vendor Agent runs after the Maintenance Agent (CM-31): it reads the
created ticket from ``state.output`` (``category`` / ``priority`` / ``unit`` /
``ticket_id``), matches a contractor, and either auto-dispatches or routes to
the human-in-the-loop gate for manager approval.

All models are pure Pydantic; no persistence is wired here (see
:mod:`agents.vendor.repository` for the seeded in-memory store — a Cosmos
``vendors`` container is a deferred follow-up).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CostTier(StrEnum):
    """Coarse price band for a vendor. Cheaper tiers win ties in matching."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Vendor(BaseModel):
    """A contractor the platform can dispatch maintenance jobs to (AC1)."""

    id: str
    name: str
    #: Maintenance categories this vendor serves (match CM-31 categorize()).
    categories: list[str]
    #: Availability calendar as lowercase weekday abbreviations
    #: (``mon``/``tue``/…/``sun``). A 24/7 vendor lists all seven.
    available_days: list[str]
    #: Performance score in ``[0.0, 1.0]`` — higher ranks first.
    performance_score: float = Field(ge=0.0, le=1.0)
    cost_tier: CostTier
    #: Whether the manager has pre-approved auto-dispatch to this vendor.
    pre_approved: bool = False
    contact_email: str | None = None
    contact_sms: str | None = None


class VendorMatch(BaseModel):
    """A vendor that matched a ticket, with its ranking score."""

    vendor: Vendor
    score: float


class DispatchDecision(BaseModel):
    """Outcome of the auto-dispatch rule (AC3)."""

    auto: bool
    #: Machine-readable reason: ``routine`` / ``safety_or_legal`` /
    #: ``not_preapproved`` / ``over_threshold``.
    reason: str
    estimated_cost: float
    threshold: float
