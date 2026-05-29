"""Pydantic models for the Analytics Agent (CM-36).

The Analytics Agent is an offline batch job (Functions Timer) over the CM-31
``tickets`` container. :class:`AnalyticsTicket` is the input view — deliberately
decoupled from ``agents.maintenance.Ticket`` so the analytics engine can carry
optional fields (``vendor_id`` / ``tone`` / ``resolved_at``) that aren't
persisted on the ticket yet (see ``docs/ANALYTICS.md`` data-gaps note). The
Cosmos source fills what exists today and leaves the rest ``None``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AnalyticsTicket(BaseModel):
    """A ticket as the analytics engine consumes it.

    ``building`` is ``tenant_id`` and ``location`` is ``unit`` for MVP (tickets
    carry no building field). Optional fields are populated when upstream
    stories persist them; until then the scorers degrade explicitly.
    """

    tenant_id: str
    unit: str
    category: str
    status: str
    priority: str = "P3"
    created_at: datetime
    updated_at: datetime | None = None
    resolved_at: datetime | None = None
    vendor_id: str | None = None
    tone: str | None = None


class RecurringIssue(BaseModel):
    """A recurring problem: same unit + category over the window (AC1)."""

    tenant_id: str
    unit: str
    category: str
    count: int
    first_seen: datetime
    last_seen: datetime


class ContractorScore(BaseModel):
    """Per-vendor performance (AC2). ``status`` flags missing data honestly."""

    vendor_id: str
    jobs: int
    resolved: int
    resolution_rate: float
    avg_response_hours: float | None = None
    status: str = "ok"  # "ok" | "insufficient_data"


class SentimentPoint(BaseModel):
    """One week-over-week sentiment sample for a building (AC3)."""

    building: str
    week_start: datetime
    score: float  # mean tone score in [-1.0, 0.0]; higher = calmer
    sample_size: int


class Prediction(BaseModel):
    """A predictive-rule hit (AC4)."""

    tenant_id: str
    unit: str
    category: str
    rule: str
    message: str
    severity: str = "info"  # "info" | "watch" | "warn"


class DigestReport(BaseModel):
    """The composed weekly digest (AC5)."""

    generated_at: datetime
    window_days: int
    ticket_count: int
    recurring: list[RecurringIssue] = Field(default_factory=list)
    contractor_scores: list[ContractorScore] = Field(default_factory=list)
    sentiment: list[SentimentPoint] = Field(default_factory=list)
    predictions: list[Prediction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    body: str = ""
