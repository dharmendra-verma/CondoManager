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
from uuid import uuid4

from langgraph.types import interrupt

from agents.observability import langgraph_node_span

from . import guardrails
from .escalation import build_record, get_escalation_classifier
from .escalation_store import get_escalation_store
from .history import get_history_provider
from .notify import get_manager_notifier
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
        message = (
            state.normalized.content if state.normalized is not None
            else state.raw_message
        )
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
    """Maintenance stub — CM-31 replaces with ticket lifecycle + dedup."""
    with langgraph_node_span("maintenance", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        return {
            "output": {"status": "ticket_stub", "ticket_id": "stub-ticket-0001"},
        }


def escalation(state: AgentState) -> dict[str, Any]:
    """Escalation Agent (CM-32) — classify, record, alert, hold a draft.

    Reached when CM-30 triage sets ``intent=escalation``. Steps:

    1. Guardrail check first (CM-26/28 contract).
    2. Sub-classify the escalation + raise the semantic ``legal_risk`` flag
       (AC #1/#2) via :func:`~agents.orchestrator.escalation.get_escalation_classifier`
       (GPT-4o-mini, or the offline heuristic with no ``OPENAI_API_KEY``).
    3. Build the :class:`~agents.orchestrator.state.EscalationRecord` —
       internal summary, manager alert, and an empathetic tenant draft that
       is **held** (never sent here) — and persist it to Cosmos (AC #3).
    4. Post the manager alert (AC #4); delivery is best-effort and never
       breaks the graph.
    5. Route to ``hitl_review`` (AC #5/#6 — every escalation is gated, and
       the draft is only ever marked sent on explicit approval there).
    """
    with langgraph_node_span("escalation", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)

        message = (
            state.normalized.content if state.normalized is not None
            else state.raw_message
        )
        classifier = get_escalation_classifier()
        classification = classifier.classify(message, state.history)
        record = build_record(
            record_id=f"esc-{uuid4().hex}",
            tenant_id=state.tenant_id,
            request_id=state.request_id,
            classification=classification,
            urgency=state.urgency,
            tone=state.tone,
            message=message,
        )
        get_escalation_store().save(record)
        notified = get_manager_notifier().notify(record)

        return {
            "escalation": record,
            "cost_so_far": state.cost_so_far + classifier.cost_per_call_usd,
            "output": {
                "status": "escalation_pending_review",
                "draft": record.tenant_draft,
                "legal_risk": record.legal_risk,
                "manager_notified": notified,
            },
            "routes": ["hitl_review"],
        }


def hitl_review(state: AgentState) -> dict[str, Any]:
    """Human-in-the-loop interrupt — pauses for manager review (CM-32 gate).

    Uses LangGraph's ``interrupt()`` primitive: the checkpointer persists
    state and ``graph.invoke(...)`` returns with a ``__interrupt__`` marker;
    the caller resumes via ``graph.invoke(Command(resume=<payload>), ...)``.

    The pause payload surfaces the escalation record (category, legal flag,
    severity, the held draft, the manager alert) so a UI/operator can decide.

    **Legal gate (AC #6).** The tenant draft is only ever marked ``sent`` —
    and the record transitioned to ``approved_sent`` — when the resume payload
    explicitly approves (``approved is True``). There is no auto-approve path,
    so a legal-flagged case can never be sent without a human. Anything else
    (reject, missing/false approval) yields ``sent=False`` / ``rejected``.
    """
    with langgraph_node_span("hitl_review", tenant_id=state.tenant_id):
        rec = state.escalation
        approval = interrupt(
            {
                "reason": "escalation_review",
                "category": rec.category.value if rec else None,
                "legal_risk": rec.legal_risk if rec else False,
                "severity": rec.severity if rec else None,
                "draft": (state.output or {}).get("draft"),
                "manager_alert": rec.manager_alert if rec else None,
            }
        )
        # Explicit-approval-only: dict payload must carry approved==True, or a
        # bare resume value must itself be True. Everything else withholds.
        approved = (
            approval.get("approved") is True
            if isinstance(approval, dict)
            else approval is True
        )
        updates: dict[str, Any] = {
            "output": {"approved": approval, "via": "hitl", "sent": approved},
        }
        if rec is not None:
            updates["escalation"] = rec.model_copy(
                update={"status": "approved_sent" if approved else "rejected"}
            )
        return updates


def guardrail_terminated(state: AgentState) -> dict[str, Any]:
    """Terminal node entered when a Stop Rule trips.

    The tripping node has already set ``state.output`` with the reason;
    we just need a span so the trace shows the termination clearly.
    """
    with langgraph_node_span(
        "guardrail_terminated", tenant_id=state.tenant_id
    ):
        return {}
