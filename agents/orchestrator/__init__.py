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
* :class:`TriageClassification`, :class:`TriageClassifier`,
  :func:`get_triage_classifier`, :func:`route_for` — the CM-30 Triage Agent
  surface (classification schema, classifier selector, routing matrix).
* :class:`TicketHistoryProvider`, :func:`get_history_provider` — the CM-30
  ticket-history seam (CM-31 swaps in the Cosmos-backed provider).
* :class:`EscalationCategory`, :class:`EscalationRecord`,
  :class:`EscalationClassification`, :func:`get_escalation_classifier`,
  :func:`get_escalation_store`, :func:`get_manager_notifier` — the CM-32
  Escalation Agent surface (sub-classification + legal flag, Cosmos record
  store, manager-alert notifier).

The ``triage`` (CM-30) and ``escalation`` (CM-32) nodes are real agents;
knowledge, maintenance, and guardrail_terminated remain stubs in ``nodes``
— CM-31 / CM-Epic 6 replace those.
"""

from __future__ import annotations

# ``Channel`` is owned by the channels package (CM-29); import it from the
# source so mypy's ``no_implicit_reexport`` is satisfied (``state`` re-imports
# it but doesn't explicitly re-export). ``from agents.orchestrator import
# Channel`` continues to resolve via ``__all__`` below.
from agents.channels.schema import Channel

from .checkpointer import CosmosCheckpointSaver, get_checkpointer
from .escalation import (
    EscalationClassification,
    EscalationClassifier,
    HeuristicEscalationClassifier,
    LLMEscalationClassifier,
    get_escalation_classifier,
)
from .escalation_store import (
    EscalationStore,
    NoopEscalationStore,
    get_escalation_store,
)
from .graph import build_graph
from .guardrails import (
    COST_CAP_USD,
    EVENT_COST_CAP,
    EVENT_LOOP_CAP,
    LOOP_CAP,
    GuardrailResult,
    check,
)
from .history import NoopTicketHistory, TicketHistoryProvider, get_history_provider
from .notify import LogNotifier, ManagerNotifier, get_manager_notifier
from .state import (
    AgentState,
    EscalationCategory,
    EscalationRecord,
    Intent,
    Tone,
    Urgency,
)
from .triage import (
    HeuristicTriageClassifier,
    LLMTriageClassifier,
    TriageClassification,
    TriageClassifier,
    get_triage_classifier,
    needs_coordinator,
    route_for,
    route_for_intent,
)

__all__ = [
    "COST_CAP_USD",
    "EVENT_COST_CAP",
    "EVENT_LOOP_CAP",
    "LOOP_CAP",
    "AgentState",
    "Channel",
    "CosmosCheckpointSaver",
    "EscalationCategory",
    "EscalationClassification",
    "EscalationClassifier",
    "EscalationRecord",
    "EscalationStore",
    "GuardrailResult",
    "HeuristicEscalationClassifier",
    "HeuristicTriageClassifier",
    "Intent",
    "LLMEscalationClassifier",
    "LLMTriageClassifier",
    "LogNotifier",
    "ManagerNotifier",
    "NoopEscalationStore",
    "NoopTicketHistory",
    "Tone",
    "TicketHistoryProvider",
    "TriageClassification",
    "TriageClassifier",
    "Urgency",
    "build_graph",
    "check",
    "get_checkpointer",
    "get_escalation_classifier",
    "get_escalation_store",
    "get_history_provider",
    "get_manager_notifier",
    "get_triage_classifier",
    "needs_coordinator",
    "route_for",
    "route_for_intent",
]
