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

from agents.knowledge import (
    KnowledgeAnswer,
    answer_question,
    default_embedder,
    get_chat_model,
    get_vector_store,
    retrieve,
)
from agents.knowledge.rag import REFUSAL_TEXT
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

#: Route the Knowledge node appends when it refuses — the ``graph.py`` router
#: hands the request off to the Maintenance agent (AC). Matches the triage
#: route string + the graph conditional-edge key.
ROUTE_MAINTENANCE: str = "maintenance"

#: Flat estimate for one query embedding (text-embedding-3-small, ~20 tokens).
#: Negligible next to the LLM call but kept so the CM-26 cost cap is honest.
KNOWLEDGE_QUERY_EMBED_COST_USD: float = 0.000001


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


def _answer_knowledge(message: str, tenant_id: str) -> tuple[KnowledgeAnswer, float, int]:
    """Run the RAG flow for one question.

    Returns ``(answer, cost_usd, searches_run)``. When the Cosmos store or the
    embedder is unconfigured (offline / dev / CI), we cannot retrieve — so we
    refuse WITHOUT any model or network call, preserving the spine's
    no-credentials contract (CM-28). Refusal then routes to Maintenance.
    """
    store = get_vector_store()
    embedder = default_embedder()
    if store is None or embedder is None:
        return KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.0, refused=True), 0.0, 0

    model = get_chat_model()
    retrieved = retrieve(message, tenant_id=tenant_id, store=store, embedder=embedder)
    answer = answer_question(message, retrieved, model=model)
    # Bill the LLM only when it was actually invoked (retrieved non-empty);
    # a vector search ran either way, so always count one search.
    cost = (model.cost_per_call_usd if retrieved else 0.0) + KNOWLEDGE_QUERY_EMBED_COST_USD
    return answer, cost, 1


def knowledge(state: AgentState) -> dict[str, Any]:
    """Knowledge Agent (CM-33) — RAG over the Cosmos ``policies-vector`` store.

    Hybrid-retrieves policy chunks, generates a grounded, citation-bearing
    answer, and scores confidence. Below
    :data:`~agents.knowledge.models.CONFIDENCE_THRESHOLD` (or when nothing can
    be retrieved) it refuses and hands off to Maintenance via ``routes``.
    Bumps ``search_count`` / ``cost_so_far`` so the CM-26 guardrails stay
    meaningful. Runs credential-free (refusal path) when unconfigured.
    """
    with langgraph_node_span("knowledge", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)

        message = (
            state.normalized.content if state.normalized is not None
            else state.raw_message
        )
        answer, cost, searches = _answer_knowledge(message, state.tenant_id)

        update: dict[str, Any] = {
            "output": {
                "status": "refused" if answer.refused else "answered",
                "answer": answer.answer,
                "citations": [c.model_dump() for c in answer.citations],
                "confidence": answer.confidence,
            },
            "search_count": state.search_count + searches,
            "cost_so_far": state.cost_so_far + cost,
        }
        if answer.refused:
            # Hand off to the Maintenance agent (router in graph.py).
            update["routes"] = [ROUTE_MAINTENANCE]
        return update


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
    with langgraph_node_span("guardrail_terminated", tenant_id=state.tenant_id):
        return {}
