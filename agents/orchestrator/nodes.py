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
contract is testable end-to-end. CM-31 Maintenance and CM-32 Escalation
each still replace one stub with real LLM logic; the spine stays unchanged.

CM-30 has replaced the ``triage`` stub with a real classifier (see
:func:`triage` below + :mod:`agents.orchestrator.triage`). It still runs
without OpenAI credentials — :func:`~agents.orchestrator.triage.get_triage_classifier`
falls back to a deterministic heuristic when ``OPENAI_API_KEY`` is unset,
preserving the original stub's keyword routing.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from agents.observability import langgraph_node_span

from . import guardrails
from .history import get_history_provider
from .state import AgentState
from .triage import get_triage_classifier, route_for

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
    """Triage Agent (CM-30) — classify intent/urgency/tone, then route.

    Replaces the CM-28 keyword stub with real classification while keeping
    the spine and the no-credentials contract intact:

    1. Guardrail check first (CM-26/28 contract) — short-circuit to the
       terminal if a Stop Rule has tripped, before any classifier work.
    2. Look up the tenant's recent ticket history (AC #5) via the
       :func:`~agents.orchestrator.history.get_history_provider` seam.
    3. Classify the message — :func:`~agents.orchestrator.triage.get_triage_classifier`
       returns GPT-4o-mini when ``OPENAI_API_KEY`` is set, else a
       deterministic heuristic (so tests + demo run with no credentials).
       The message is the CM-29 PII-masked ``normalized.content`` when a
       channel adapter has run, else the raw ``raw_message``.
    4. Persist ``intent`` / ``urgency`` / ``tone`` / ``history``, bump
       ``cost_so_far`` by the classifier's per-call estimate (keeps the
       CM-26 cost cap meaningful), and route via
       :func:`~agents.orchestrator.triage.route_for` (AC #6).
    """
    with langgraph_node_span("triage", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)

        history = get_history_provider().recent_tickets(state.tenant_id)
        message = state.normalized.content if state.normalized is not None else state.raw_message
        classifier = get_triage_classifier()
        result = classifier.classify(message, history)

        return {
            "intent": result.intent,
            "urgency": result.urgency,
            "tone": result.tone,
            "history": history,
            "cost_so_far": state.cost_so_far + classifier.cost_per_call_usd,
            "routes": [route_for(result)],
        }


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


def vendor(state: AgentState) -> dict[str, Any]:
    """Vendor node — match a contractor and auto-dispatch or seek approval (CM-35).

    Runs after :func:`maintenance`. Delegates to
    :class:`agents.vendor.VendorAgent`, which matches a vendor to the created
    ticket and either auto-dispatches routine/low-cost/pre-approved/non-safety
    jobs or routes to ``hitl_review`` for manager approval. Non-``ticket_created``
    upstream outputs (duplicate / guardrail) pass straight through to END.
    """
    with langgraph_node_span("vendor", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        from agents.vendor import VendorAgent  # noqa: PLC0415

        return VendorAgent().handle(state)


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
    with langgraph_node_span("guardrail_terminated", tenant_id=state.tenant_id):
        return {}
