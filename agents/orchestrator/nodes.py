"""Stub LangGraph nodes — the spine that CM-30/31/32 fill in.

Jira: CM-28  | Epic: CM-Epic 4  | Phase 0

Each node body is required to:
  1. Wrap its work in ``langgraph_node_span(...)`` from CM-21 so the
     trace appears in App Insights (CM-22) + LangSmith (CM-23) under
     ``langgraph.node.<name>``.
  2. Call ``guardrails.check(state)`` as the first statement. If it
     trips, return a state update that routes to ``guardrail_terminated``
     and DO NOT do any further work in this node.

Stub nodes here return trivial state updates (e.g. ``{"intent": "stub"}``)
so the hello-world demo runs without OpenAI credentials and the trace
contract is testable end-to-end. CM-30 Triage, CM-31 Maintenance, and
CM-32 Escalation each replace one stub with real LLM logic; the spine
stays unchanged.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from agents.observability import langgraph_node_span

from . import guardrails
from .state import AgentState

#: Canonical route name for the guardrail-terminated terminal. The router
#: in ``graph.py`` maps this to the ``guardrail_terminated`` node.
ROUTE_GUARDRAIL_TERMINATED: str = "guardrail_terminated"


def _guardrail_termination(reason: str | None) -> dict[str, Any]:
    """Build the state update a node returns when its guardrail trips."""
    return {
        "output": {
            "status": "guardrail_terminated",
            "reason": reason,
        },
        "routes": [ROUTE_GUARDRAIL_TERMINATED],
    }


def triage(state: AgentState) -> dict[str, Any]:
    """Triage stub — CM-30 replaces this with real GPT-4o-mini classification.

    Picks a route from a tiny keyword heuristic over ``state.raw_message``
    so the spine is fully testable end-to-end without an LLM:

    * ``"human"`` / ``"escalat"`` -> ``escalation`` (-> ``hitl_review``)
    * ``"fix"`` / ``"broken"`` / ``"leak"`` -> ``maintenance``
    * everything else -> ``knowledge``

    Real Triage (CM-30) will replace this with GPT-4o-mini classification
    that emits the matching :class:`Intent` / :class:`Urgency` / :class:`Tone`.
    """
    with langgraph_node_span("triage", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        msg = (state.raw_message or "").lower()
        if "human" in msg or "escalat" in msg:
            return {"intent": "escalation", "routes": ["escalation"]}
        if "fix" in msg or "broken" in msg or "leak" in msg:
            return {"intent": "maintenance", "routes": ["maintenance"]}
        return {"intent": "inquiry", "routes": ["knowledge"]}


def knowledge(state: AgentState) -> dict[str, Any]:
    """Knowledge stub — future CM-Epic 6 replaces with RAG over policies."""
    with langgraph_node_span("knowledge", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        return {
            "output": {"status": "answered_stub", "answer": "stub-answer"},
        }


def maintenance(state: AgentState) -> dict[str, Any]:
    """Maintenance node — ticket lifecycle + dedup (CM-31).

    Delegates to :class:`agents.maintenance.MaintenanceAgent`, which detects
    duplicates (same unit + similar issue within 7 days), creates a
    priority-ranked ticket, sends an SOP-aligned tenant confirmation, and
    notifies the manager on new tickets. The span + guardrail contract stays
    here; all domain logic lives in the ``agents.maintenance`` package.
    """
    with langgraph_node_span("maintenance", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        # Imported lazily so the orchestrator package has no import-time
        # dependency on the maintenance package (keeps the spine importable
        # in isolation, mirroring the rest of the codebase's seam style).
        from agents.maintenance import MaintenanceAgent  # noqa: PLC0415

        return MaintenanceAgent().handle(state)


def escalation(state: AgentState) -> dict[str, Any]:
    """Escalation stub — CM-32 replaces with empathetic agent + HITL gate.

    Routes to ``hitl_review`` so the HITL interrupt fires and the graph
    pauses. CM-32 will also draft an internal escalation record + tenant
    reply behind the same gate.
    """
    with langgraph_node_span("escalation", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        return {
            "output": {"status": "escalated_stub", "draft": "stub-tenant-reply"},
            "routes": ["hitl_review"],
        }


def hitl_review(state: AgentState) -> dict[str, Any]:
    """Human-in-the-loop interrupt — pauses graph execution.

    Uses LangGraph's ``interrupt()`` primitive (0.2+). When called, the
    checkpointer persists state and ``graph.invoke(...)`` returns with
    a ``__interrupt__`` marker; the caller resumes by re-invoking with
    the same ``thread_id`` and a resume payload.

    CM-32 is the first real consumer — its escalation agent populates
    ``state.output['draft']`` with a tenant reply, and HITL approval
    here gates whether that draft goes out. For CM-28 we just pause and
    accept whatever the resumer sends back.
    """
    with langgraph_node_span("hitl_review", tenant_id=state.tenant_id):
        # The dict passed to interrupt() shows up in the resumer's
        # `graph.invoke(Command(resume=...), config=...)` context. We
        # include the draft (if any) and a stub reason so a UI can render
        # something for the human reviewer.
        approval = interrupt(
            {
                "reason": "stub-hitl-review",
                "draft": (state.output or {}).get("draft"),
            }
        )
        return {
            "output": {"approved": approval, "via": "hitl"},
        }


def guardrail_terminated(state: AgentState) -> dict[str, Any]:
    """Terminal node entered when a Stop Rule trips.

    The tripping node has already set ``state.output`` with the reason;
    we just need a span so the trace shows the termination clearly.
    """
    with langgraph_node_span(
        "guardrail_terminated", tenant_id=state.tenant_id
    ):
        return {}
