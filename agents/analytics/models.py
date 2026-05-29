"""Pydantic models + constants for the Analytics Agent (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

The weekly-digest pipeline is deterministic and JSON-clean: analyzers take
lists of these models and return result models, and :class:`WeeklyDigest`
``upsert_item``s into the Cosmos ``digests`` container without massaging.
Mirrors the CM-34 ``agents.knowledge.models`` shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Cosmos DB database name from CM-17.
DATABASE_NAME = "condomanager"
#: Maintenance tickets container (CM-31), partition /tenantId.
TICKETS_CONTAINER = "tickets"
#: Escalation records container (CM-32), partition /tenantId.
ESCALATIONS_CONTAINER = "escalations"
#: Weekly-digest container added by CM-36's cosmos.bicep change, partition /tenantId.
DIGESTS_CONTAINER = "digests"
#: CM-18 Key Vault seed placeholder — treated as if-unset everywhere.
SECRET_PLACEHOLDER = "REPLACE-ME"


class EscalationEvent(BaseModel):
    """A timestamped escalation, projected for trend analysis.

    The CM-32 ``EscalationRecord`` carries no timestamp, so the reader pairs
    each record with the Cosmos ``_ts`` (server write time) to produce these
    events — the minimum the sentiment trend needs.
    """

    ts: datetime
    severity: str
    legal_risk: bool = False
    category: str = ""


# --- analyzer result models --------------------------------------------------


class RecurringIssue(BaseModel):
    """A (unit, category) that recurred > threshold times in the window (AC #1)."""

    unit: str
    category: str
    count: int
    first_seen: datetime
    last_seen: datetime


class ContractorScore(BaseModel):
    """Per-owner performance over the window (AC #2).

    ``owner`` is the ticket assignee (the closest thing to a contractor until
    the CM-35 Vendor entity exists); unassigned tickets bucket as
    ``"unassigned"``. ``avg_response_hours`` is best-effort
    (``updated_at - created_at`` over resolved tickets) — there is no
    per-transition timestamp on the ticket yet.
    """

    owner: str
    assigned: int
    resolved: int
    resolution_rate: float
    avg_response_hours: float | None = None


class SentimentPoint(BaseModel):
    """One ISO-week bucket of escalation signal (the sentiment proxy, AC #3)."""

    week_start: str  # ISO date (Monday) of the bucket
    escalations: int
    critical: int
    legal: int


class SentimentTrend(BaseModel):
    """Week-over-week escalation signal + a coarse direction (AC #3)."""

    points: list[SentimentPoint] = Field(default_factory=list)
    direction: Literal["rising", "falling", "flat"] = "flat"


class PredictiveFlag(BaseModel):
    """A simple threshold-rule forecast (AC #4)."""

    unit: str
    category: str
    count_in_window: int
    window_days: int
    message: str


# --- digest + run report -----------------------------------------------------


class WeeklyDigest(BaseModel):
    """The persisted weekly digest (one doc in the ``digests`` container).

    ``digest_id`` (== ``{tenant_id}:{week_start}``) is the Cosmos ``id`` and is
    idempotent — re-running the job for the same week upserts the same doc.
    """

    digest_id: str
    tenant_id: str
    generated_at: str
    week_start: str
    week_end: str
    headline: str
    recurring: list[RecurringIssue] = Field(default_factory=list)
    contractors: list[ContractorScore] = Field(default_factory=list)
    sentiment: SentimentTrend = Field(default_factory=SentimentTrend)
    predictions: list[PredictiveFlag] = Field(default_factory=list)

    def to_cosmos(self) -> dict[str, Any]:
        """Serialize to a Cosmos doc — adds ``id`` (== digest_id) + ``tenantId``."""
        data = self.model_dump(mode="json")
        data["id"] = self.digest_id
        data["tenantId"] = self.tenant_id  # partition-key path /tenantId
        return data

    @classmethod
    def from_cosmos(cls, doc: dict[str, Any]) -> WeeklyDigest:
        """Rebuild from a Cosmos doc; ignores system fields (_etag, _ts, …)."""
        return cls.model_validate(doc)


class DigestReport(BaseModel):
    """Aggregate outcome of one digest run (feeds the run-history log line)."""

    run_id: str
    tenant_id: str
    week_start: str
    recurring_count: int = 0
    contractor_count: int = 0
    prediction_count: int = 0
    escalation_count: int = 0
    notified: bool = False
