"""Shared Pydantic state for the LangGraph spine.

Jira: CM-28 (initial)  | CM-29 (Channel + NormalizedMessage typing)
Epic: CM-Epic 4 (LangGraph Orchestrator)  | Phase 0

LangGraph's idiomatic state is a ``TypedDict``; the CM-28 AC mandates
Pydantic. We use a ``BaseModel`` and rely on the standard LangGraph
reducer pattern — node functions return ``dict[str, Any]`` of fields to
update, and LangGraph merges via ``model_copy(update=...)`` (see
:meth:`AgentState.merge`).

Every node body is expected to:
  1. Wrap its work in ``langgraph_node_span(...)`` from CM-21.
  2. Call ``agents.orchestrator.guardrails.check(state)`` as the first
     statement (the CM-26 Stop Rules).
  3. Return only ``AgentState``-known keys; unknown keys raise
     ``ValidationError`` at merge time — see
     ``tests/orchestrator/test_state.py::test_merge_rejects_unknown_keys``.

CM-29 took ownership of the ``Channel`` enum (now in
``agents.channels.schema``) and tightened ``normalized`` from
``dict[str, Any]`` to ``NormalizedMessage | None`` — the typed coupling
the original CM-28 docstring anticipated.

Future stories (CM-30/31/32) consume the same model; adding a field
means editing this file + updating the consumer prompts + tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# CM-29: Channel + NormalizedMessage now live in the channels package.
# Re-imported here so any code doing ``from agents.orchestrator.state
# import Channel`` (e.g. the orchestrator __init__'s re-export and the
# CM-28 demo) keeps working — the name remains in this module's
# namespace. The explicit ``X as X`` form marks these as intentional
# re-exports so mypy --strict (no_implicit_reexport) accepts the
# downstream ``from .state import Channel`` in orchestrator/__init__.py.
from agents.channels.schema import Channel as Channel
from agents.channels.schema import NormalizedMessage as NormalizedMessage


class Intent(StrEnum):
    """High-level classification produced by CM-30 Triage."""

    MAINTENANCE = "maintenance"
    INQUIRY = "inquiry"
    ESCALATION = "escalation"
    FOLLOW_UP = "follow-up"
    UNKNOWN = "unknown"


class Urgency(StrEnum):
    """Urgency band — drives routing priorities in CM-31 Maintenance."""

    EMERGENCY = "emergency"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Tone(StrEnum):
    """Emotional tone classification — feeds CM-32 Escalation Agent."""

    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"
    URGENT = "urgent"


class EscalationCategory(StrEnum):
    """Sub-classification produced by the CM-32 Escalation Agent.

    Lives here next to the other classification enums (rather than in
    ``escalation.py``) so ``state.py`` has no import dependency on the
    escalation module — :class:`EscalationRecord` below references it, and
    ``escalation.py`` imports both back from here. Keeps the dependency
    one-directional (escalation -> state, never the reverse).
    """

    REPEAT = "repeat"
    SERVICE_FAILURE = "service_failure"
    SAFETY = "safety"
    COMMUNICATION_BREAKDOWN = "communication_breakdown"
    MULTI_ISSUE = "multi_issue"
    LEGAL = "legal"


class EscalationRecord(BaseModel):
    """Internal escalation record (CM-32 AC #3).

    Built by the ``escalation`` node, persisted to the Cosmos
    ``escalations`` container, and carried on :class:`AgentState` so it
    survives the ``hitl_review`` step (which overwrites ``output``).
    All fields are JSON-primitive so ``model_dump()`` upserts cleanly into
    Cosmos. Downstream consumers: CM-36 Analytics, CM-37 portal.

    ``status`` is the HITL lifecycle: ``pending_review`` at creation,
    transitioned to ``approved_sent`` ONLY on explicit manager approval, or
    ``rejected`` otherwise. ``tenant_draft`` is the empathetic reply held
    behind the gate — nothing in CM-32 sends it; that is a follow-up story.
    """

    record_id: str
    tenant_id: str
    request_id: str
    category: EscalationCategory
    legal_risk: bool = False
    severity: Literal["high", "critical"] = "high"
    internal_summary: str = ""
    manager_alert: str = ""
    tenant_draft: str = ""
    status: Literal["pending_review", "approved_sent", "rejected"] = "pending_review"
    # Every escalation routes through HITL; legal_risk makes that mandatory
    # (no auto-approve path exists — see hitl_review + the CM-32 legal gate).
    hitl_required: bool = True
    # CM-44: the manager's HITL feedback, captured on resume in ``hitl_review``.
    # ``manager_rating`` is an optional quality score (e.g. 1–5; ``None`` = decided
    # but unrated); ``rated_at`` is an ISO-8601 UTC timestamp. The decision itself
    # (approve/reject) is carried by ``status`` — not duplicated here. All
    # JSON-primitive so the record still upserts cleanly into Cosmos.
    manager_rating: int | None = None
    rating_comment: str = ""
    rated_at: str | None = None


class AgentState(BaseModel):
    """Shared Pydantic state passed between LangGraph nodes.

    16 fields: the original 13 from the CM-28 AC, ``escalation`` (CM-32),
    ``sub_intents`` (CM-87), and ``sub_results`` (CM-88). Tests assert presence +
    types. Counters default to 0 so the CM-26 Stop Rules (cost cap $5, search cap
    50) start at safe values.
    """

    tenant_id: str
    request_id: str
    channel: Channel = Channel.UNKNOWN
    raw_message: str = ""
    # CM-29: tightened from ``dict[str, Any]`` (CM-28's interim shape)
    # to the strongly-typed ``NormalizedMessage``. Default is ``None``
    # (pre-normalization); the channel adapter sets it at the
    # orchestrator entry — see ``agents/channels/web.py``.
    normalized: NormalizedMessage | None = None
    intent: Intent | None = None
    urgency: Urgency | None = None
    tone: Tone | None = None
    # CM-87 (Track B): sub-intents detected by triage when the message is
    # compound / ambiguous (``TriageClassification.multi_intent`` True). Empty on
    # the single-intent fast path. Stored as ``Intent`` string values so the
    # state stays JSON-primitive for the Cosmos checkpointer; the Coordinator
    # (B3) consumes this to decompose the request.
    sub_intents: list[str] = Field(default_factory=list)
    # CM-88 (Track B): the ordered specialist-tool results the Coordinator
    # accumulated across its plan-execute trajectory — each a
    # ``{"tool": <name>, "result": <verbatim tool output>}`` record. Empty on
    # every path except a Coordinator run. The B4 synthesis step (CM-89) consumes
    # this to compose one tenant-facing reply; kept JSON-primitive so it survives
    # the Cosmos checkpointer.
    sub_results: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    cost_so_far: float = 0.0
    search_count: int = 0
    routes: list[str] = Field(default_factory=list)
    output: dict[str, Any] | None = None
    # CM-32: the Escalation Agent's structured output. Its own field (not
    # ``output``) because ``hitl_review`` overwrites ``output`` on resume,
    # and the record must survive that step for the HITL gate + downstream
    # consumers (CM-36/37). ``None`` until the escalation node runs.
    escalation: EscalationRecord | None = None

    def merge(self, updates: dict[str, Any]) -> AgentState:
        """Return a new ``AgentState`` with the given field updates applied.

        Unknown keys raise ``ValidationError`` — caller bug, not silent.
        Pydantic's ``model_copy(update=...)`` silently accepts unknown keys,
        so we route through a full ``model_validate`` round-trip which
        rejects them.
        """
        unknown = set(updates) - set(type(self).model_fields)
        if unknown:
            from pydantic import ValidationError
            from pydantic_core import InitErrorDetails, PydanticCustomError
            raise ValidationError.from_exception_data(
                title=type(self).__name__,
                line_errors=[
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "extra_forbidden",
                            "Unknown field for AgentState.merge: {field}",
                            {"field": k},
                        ),
                        loc=(k,),
                        input=updates[k],
                    )
                    for k in unknown
                ],
            )
        return self.model_copy(update=updates)
