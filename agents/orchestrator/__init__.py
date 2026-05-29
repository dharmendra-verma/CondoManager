"""``agents.orchestrator`` — LangGraph spine for the CondoManager agents.

Jira: CM-28  | Epic: CM-Epic 4  | Phase 0

Public surface:

* :class:`AgentState` — shared Pydantic state passed between nodes.
* :class:`Channel`, :class:`Intent`, :class:`Urgency`, :class:`Tone` —
  enum values for the state fields.
* :func:`build_graph` — returns a compiled ``StateGraph`` ready for
  ``invoke()``. Honors the ``COSMOS_ENDPOINT`` env var to pick the
  Cosmos checkpointer; falls back to ``MemorySaver`` when unset.
* :func:`get_checkpointer` — env-var selector exposed for tests + callers
  that need to inspect / share the checkpointer.

Stub agent nodes (triage, knowledge, maintenance, escalation,
hitl_review, guardrail_terminated) live in ``nodes`` — CM-30/31/32
replace one stub each with real LLM logic.
"""

from __future__ import annotations

from .checkpointer import CosmosCheckpointSaver, get_checkpointer
from .graph import build_graph
from .guardrails import (
    COST_CAP_USD,
    EVENT_COST_CAP,
    EVENT_LOOP_CAP,
    LOOP_CAP,
    GuardrailResult,
    check,
)
from .state import AgentState, Channel, Intent, Tone, Urgency

__all__ = [
    "COST_CAP_USD",
    "EVENT_COST_CAP",
    "EVENT_LOOP_CAP",
    "LOOP_CAP",
    "AgentState",
    "Channel",
    "CosmosCheckpointSaver",
    "GuardrailResult",
    "Intent",
    "Tone",
    "Urgency",
    "build_graph",
    "check",
    "get_checkpointer",
]
