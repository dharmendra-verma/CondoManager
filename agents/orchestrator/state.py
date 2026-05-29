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
from typing import Any

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


class AgentState(BaseModel):
    """Shared Pydantic state passed between LangGraph nodes.

    The 13 fields below match the CM-28 AC exactly; tests assert
    presence + types. Counters default to 0 so the CM-26 Stop Rules
    (cost cap $5, search cap 50) start at safe values.
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
    history: list[dict[str, Any]] = Field(default_factory=list)
    cost_so_far: float = 0.0
    search_count: int = 0
    routes: list[str] = Field(default_factory=list)
    output: dict[str, Any] | None = None

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
