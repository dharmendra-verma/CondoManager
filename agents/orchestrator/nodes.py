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

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import interrupt

from agents.knowledge import get_knowledge_planner
from agents.observability import (
    METRIC_COORDINATOR_STEPS,
    METRIC_COORDINATOR_SUBTASKS,
    METRIC_COORDINATOR_TOOL_CALLS,
    METRIC_ESCALATION_LEGAL_FLAG,
    METRIC_FOLLOWUP,
    METRIC_HITL_RATING,
    METRIC_KNOWLEDGE_ANSWERED,
    METRIC_KNOWLEDGE_REFORMULATED,
    METRIC_KNOWLEDGE_REFUSED,
    METRIC_KNOWLEDGE_STEPS,
    METRIC_MAINTENANCE_DEDUP,
    METRIC_TRIAGE_ROUTE,
    METRIC_VENDOR_AUTO_DISPATCH,
    METRIC_VENDOR_HITL,
    emit_metric,
    langgraph_node_span,
)
from agents.observability.langfuse_export import observe_node

from . import guardrails
from .escalation import build_record, get_escalation_classifier
from .escalation_store import get_escalation_store
from .history import get_history_provider
from .notify import get_manager_notifier
from .state import AgentState
from .triage import (
    ROUTE_COORDINATOR,
    get_triage_classifier,
    needs_coordinator,
    route_for,
)

#: Canonical route name for the guardrail-terminated terminal. The router
#: in ``graph.py`` maps this to the ``guardrail_terminated`` node.
ROUTE_GUARDRAIL_TERMINATED: str = "guardrail_terminated"

#: Route the Knowledge node appends when it refuses — the ``graph.py`` router
#: hands the request off to the Maintenance agent (AC). Matches the triage
#: route string + the graph conditional-edge key.
ROUTE_MAINTENANCE: str = "maintenance"

#: Module logger — structured JSON to stdout via the CM-21 logging config. Used
#: for CM-77 real-time decision lines (intent/route/confidence) that show up in
#: the Container Apps log stream instantly, unlike the minutes-batched Langfuse /
#: App Insights spans.
_log = logging.getLogger(__name__)


def _guardrail_termination(reason: str | None) -> dict[str, Any]:
    """Build the state update a node returns when its guardrail trips."""
    return {
        "output": {
            "status": "guardrail_terminated",
            "reason": reason,
        },
        "routes": [ROUTE_GUARDRAIL_TERMINATED],
    }


@observe_node("triage")
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
        # CM-87 (Track B): compound / ambiguous messages route to the Coordinator;
        # everything else takes the unchanged single-intent fast path. ``route``
        # is what the graph router (``state.routes[-1]``) reads next.
        coordinated = needs_coordinator(result)
        route = ROUTE_COORDINATOR if coordinated else route_for(result)

        # CM-39: PRD metric — intent → route (feeds triage-accuracy + routing
        # dashboards; the offline eval gates the same quantity on golden data).
        # CM-87: also carry the multi_intent signal so the Coordinator-vs-fast-path
        # split (and any over-triggering) is measurable on the route metric.
        emit_metric(
            METRIC_TRIAGE_ROUTE,
            intent=result.intent.value,
            route=route,
            multi_intent=result.multi_intent,
        )

        # CM-77: real-time decision log so the Container Apps log stream shows the
        # routing within seconds (Langfuse/App Insights ingestion lags minutes).
        # Metadata only — no message content — so PII never lands in logs;
        # request_id + tenant_id are auto-attached by the JSON formatter.
        _log.info(
            "triage decision: intent=%s urgency=%s tone=%s multi_intent=%s route=%s",
            result.intent.value,
            result.urgency.value,
            result.tone.value,
            result.multi_intent,
            route,
        )

        return {
            "intent": result.intent,
            "urgency": result.urgency,
            "tone": result.tone,
            # CM-87: persist detected sub-intents (Intent string values) so the
            # Coordinator can decompose; empty on the single-intent path.
            "sub_intents": [si.value for si in result.sub_intents],
            "history": history,
            "cost_so_far": state.cost_so_far + classifier.cost_per_call_usd,
            "routes": [route],
        }


@observe_node("coordinator")
def coordinator(state: AgentState) -> dict[str, Any]:
    """Coordinator node (CM-88, Track B) — plan-execute reasoning loop.

    Reached only when triage flips the multi-intent / ambiguity signal
    (:func:`~agents.orchestrator.triage.needs_coordinator`). Replaces the CM-87
    pass-through stub with the real "true agent": it decomposes the message into
    sub-tasks and runs a bounded observe→think→act loop
    (:mod:`agents.coordinator.planner`), selecting a B1 specialist tool each step
    based on the accumulated observations — a non-deterministic trajectory whose
    length depends on the message, not a fixed hop count.

    The loop owns the per-iteration guardrail check, the
    :data:`~agents.coordinator.planner.COORDINATOR_MAX_STEPS` bound, the cost /
    loop-step accrual (so the CM-26 caps bound the whole trajectory), and the
    per-step ``langgraph.node.coordinator.step`` spans. This node:

    1. Runs the standard span + guardrail-first contract (a Stop Rule tripped
       *inside* the loop is surfaced as ``guardrail_reason`` and handled here
       exactly like the node's own first-statement check).
    2. Accumulates the sub-results on ``state.sub_results`` and synthesizes them
       (CM-89, :func:`agents.coordinator.synthesis.get_synthesizer`) into one
       coherent tenant ``output.reply``; bumps the CM-26 counters once for the
       trajectory.
    3. **Legal gate (AC #5):** if any sub-result is an escalation, the record is
       persisted + the manager alerted (as the escalation node does), the held
       draft is surfaced but **never** auto-sent, and the trajectory routes
       through ``hitl_review``. Otherwise it ends the graph (``coordinator_done``).
    """
    with langgraph_node_span("coordinator", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)

        # Imported lazily so the orchestrator package has no import-time
        # dependency on the coordinator package (which imports orchestrator.state)
        # — mirrors the maintenance/vendor lazy-import seam style.
        from agents.coordinator.planner import get_planner  # noqa: PLC0415

        result = get_planner().run(state)
        if result.guardrail_reason is not None:
            # A Stop Rule tripped mid-loop — terminate exactly as a node would on
            # its own guardrail check.
            return _guardrail_termination(result.guardrail_reason)

        # CM-89: weave the accumulated sub-results into one coherent tenant reply.
        # Pure + deterministic offline (template); the LLM weaver behind the key
        # is validated against the sub-results, so it can never invent a TKT code
        # or [n] citation. Imported lazily (same seam style as the planner).
        from agents.coordinator.synthesis import get_synthesizer  # noqa: PLC0415
        from agents.observability.pii import mask_pii  # noqa: PLC0415

        synthesis = get_synthesizer().synthesize(result.sub_results)

        # CM-77-style real-time decision log: metadata + a PII-masked reply preview
        # (the reply itself is tenant-facing and stays unmasked in ``output``).
        _log.info(
            "coordinator decision: steps=%s termination=%s sub_results=%s "
            "held=%s route=%s reply=%s",
            result.steps,
            result.termination,
            len(result.sub_results),
            synthesis.held_for_review,
            result.route,
            mask_pii(synthesis.reply[:160]),
        )

        # CM-90: trajectory metrics. ``subtasks`` carries the attempted count +
        # how many *resolved* (not skipped/refused/no_vendor/error) — the headline
        # "compound requests no longer drop sub-tasks" signal vs the old single
        # route. ``steps`` is the trajectory length; ``tool_calls`` is one event
        # per specialist invoked so per-tool volume is dashboardable. Metadata
        # only (no message content) so the emits stay PII-safe.
        _UNRESOLVED = ("skipped", "refused", "no_vendor", "error")
        resolved = 0
        for obs in result.sub_results:
            res = obs.get("result", {}) if isinstance(obs, dict) else {}
            status = res.get("status") or (res.get("output") or {}).get("status")
            if status not in _UNRESOLVED:
                resolved += 1
            emit_metric(METRIC_COORDINATOR_TOOL_CALLS, tool=obs.get("tool"))
        emit_metric(
            METRIC_COORDINATOR_SUBTASKS,
            value=float(len(result.sub_results)),
            resolved=resolved,
            held=synthesis.held_for_review,
        )
        emit_metric(
            METRIC_COORDINATOR_STEPS,
            value=float(result.steps),
            termination=result.termination,
        )

        output: dict[str, Any] = {
            "status": "coordinated",
            "sub_results": result.sub_results,
            "steps": result.steps,
            "termination": result.termination,
            # CM-89: the single coherent reply addressing every sub-task, plus the
            # held-state flag (legal gate) and any tenant-visible unresolved items.
            "reply": synthesis.reply,
            "held_for_review": synthesis.held_for_review,
            "unresolved": synthesis.unresolved,
        }
        update: dict[str, Any] = {
            "sub_results": result.sub_results,
            "cost_so_far": state.cost_so_far + result.cost_usd,
            "search_count": state.search_count + result.searches,
            "output": output,
            "routes": [result.route],
        }
        if result.escalation is not None:
            # Persist the held record + alert the manager (mirrors the escalation
            # node), then route through HITL. The draft is surfaced but the
            # legal-gate invariant holds: nothing is sent without explicit
            # approval in ``hitl_review``.
            record = result.escalation
            get_escalation_store().save(record)
            notified = get_manager_notifier().notify(record)
            update["escalation"] = record
            output["draft"] = record.tenant_draft
            output["legal_risk"] = record.legal_risk
            output["manager_notified"] = notified
        return update


@observe_node("knowledge")
def knowledge(state: AgentState) -> dict[str, Any]:
    """Knowledge Agent (CM-33) — RAG over the Cosmos ``policies-vector`` store.

    Hybrid-retrieves policy chunks, generates a grounded, citation-bearing
    answer, and scores confidence. Below
    :data:`~agents.knowledge.models.CONFIDENCE_THRESHOLD` (or when nothing can
    be retrieved) it refuses and hands off to Maintenance via ``routes``.
    Bumps ``search_count`` / ``cost_so_far`` so the CM-26 guardrails stay
    meaningful. Runs credential-free (refusal path) when unconfigured.

    CM-83: the RAG flow is delegated to a :class:`~agents.knowledge.planner`
    reasoning loop (env-selected via :func:`get_knowledge_planner`). The loop
    re-checks the guardrails on every iteration; a mid-loop trip is surfaced as
    ``PlannerResult.guardrail_reason`` and handled here exactly like this node's
    own first-statement check. The default policy is single-pass, so behaviour
    is byte-identical to the pre-CM-83 single-shot path.
    """
    with langgraph_node_span("knowledge", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)

        message = (
            state.normalized.content if state.normalized is not None
            else state.raw_message
        )
        result = get_knowledge_planner().run(message, state=state)
        if result.guardrail_reason is not None:
            # A Stop Rule tripped inside the loop — terminate exactly as a node
            # would on its own guardrail check.
            return _guardrail_termination(result.guardrail_reason)
        answer, cost, searches = result.answer, result.cost_usd, result.searches

        # CM-77: real-time decision log (see triage). confidence + refusal +
        # handoff are exactly what's needed to debug "why did this go to
        # Maintenance" live, instead of waiting on batched Langfuse traces.
        # Metadata only (no answer/question text) so PII stays out of logs.
        # All %s: the PII masking filter (logging.py) stringifies every log arg
        # before the formatter runs, so numeric specifiers (%d/%.3f) would
        # TypeError on the now-string args. Pre-format confidence to 3 decimals.
        _log.info(
            "knowledge decision: status=%s confidence=%s search_count=%s citations=%s route=%s",
            "refused" if answer.refused else "answered",
            f"{answer.confidence:.3f}",
            searches,
            len(answer.citations),
            ROUTE_MAINTENANCE if answer.refused else "respond",
        )

        # CM-39: PRD metrics — self-service (answered) vs hallucination-control
        # (refused → handoff). Same quantities the knowledge offline eval gates.
        emit_metric(
            METRIC_KNOWLEDGE_REFUSED if answer.refused else METRIC_KNOWLEDGE_ANSWERED,
            confidence=answer.confidence,
        )
        # CM-85: agentic-RAG trajectory shape — steps-to-answer + reformulation
        # count, with the termination reason as context. Feeds the CM-85
        # trajectory dashboards; the per-step detail lives in the step spans.
        emit_metric(
            METRIC_KNOWLEDGE_STEPS, value=float(result.steps), termination=result.termination
        )
        emit_metric(
            METRIC_KNOWLEDGE_REFORMULATED, value=float(result.reformulations)
        )

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


@observe_node("maintenance")
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

        result = MaintenanceAgent().handle(state)
        output = result.get("output", {})
        status = output.get("status")
        # CM-46: dedup precision signal — one event per maintenance decision,
        # ``outcome`` distinguishing a deduped report from a fresh ticket. The
        # offline ``maintenance.dedup_precision`` eval gates the same quantity.
        if status in ("duplicate", "ticket_created"):
            emit_metric(
                METRIC_MAINTENANCE_DEDUP,
                outcome="duplicate" if status == "duplicate" else "new",
                category=output.get("category"),
                is_repeat=bool(output.get("is_repeat", False)),
            )
        # CM-46: follow-up signal — a fresh ticket that recurs against a
        # previously-RESOLVED issue (the fix didn't hold / the tenant came
        # back). Feeds the follow-up-reduction outcome metric.
        if output.get("is_followup"):
            emit_metric(
                METRIC_FOLLOWUP,
                category=output.get("category"),
                prior_ticket_id=output.get("followup_of"),
            )
        return result


@observe_node("vendor")
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

        result = VendorAgent().handle(state)
        output = result.get("output") or {}
        vendor_status = output.get("vendor_status")
        # CM-46: auto-dispatch rate — one event per vendor decision so the
        # ``auto_dispatch vs hitl`` share is dashboardable. ``no_vendor`` and
        # the pass-through (duplicate / guardrail) cases emit nothing.
        if vendor_status == "auto_dispatched":
            emit_metric(
                METRIC_VENDOR_AUTO_DISPATCH,
                category=output.get("category"),
                vendor_id=output.get("vendor_id"),
            )
        elif vendor_status == "pending_approval":
            emit_metric(
                METRIC_VENDOR_HITL,
                category=output.get("category"),
                vendor_id=output.get("vendor_id"),
            )
        return result


@observe_node("escalation")
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

        # CM-46: legal-flag signal — emitted only when the semantic legal-risk
        # flag is raised, so legal-flag recall is measurable in prod against the
        # offline ``escalation.legal_recall`` gate.
        if record.legal_risk:
            emit_metric(
                METRIC_ESCALATION_LEGAL_FLAG,
                category=record.category.value,
                severity=record.severity,
            )

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


@observe_node("hitl_review")
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
        # CM-44: capture the manager's optional rating + comment from the resume
        # payload. A bare-bool resume carries neither (rating stays None) but the
        # decision is still recorded + measured. ``manager_rating`` counts only
        # when it's a positive int (``bool`` is an ``int`` subclass — exclude it).
        rating: int | None = None
        comment = ""
        if isinstance(approval, dict):
            raw_rating = approval.get("rating")
            if isinstance(raw_rating, int) and not isinstance(raw_rating, bool) and raw_rating > 0:
                rating = raw_rating
            raw_comment = approval.get("comment")
            if isinstance(raw_comment, str):
                comment = raw_comment

        updates: dict[str, Any] = {
            "output": {"approved": approval, "via": "hitl", "sent": approved},
        }
        if rec is not None:
            decision = "approve" if approved else "reject"
            rated = rec.model_copy(
                update={
                    "status": "approved_sent" if approved else "rejected",
                    "manager_rating": rating,
                    "rating_comment": comment,
                    "rated_at": datetime.now(UTC).isoformat(),
                }
            )
            # Persist the post-decision record (reuse the escalations container)
            # and emit the HITL feedback signal for the dashboards (CM-45).
            get_escalation_store().record_rating(rated)
            emit_metric(
                METRIC_HITL_RATING,
                value=float(rating) if rating is not None else 1.0,
                decision=decision,
                category=rec.category.value,
                legal_risk=rec.legal_risk,
                has_rating=rating is not None,
            )
            updates["escalation"] = rated
        return updates


def guardrail_terminated(state: AgentState) -> dict[str, Any]:
    """Terminal node entered when a Stop Rule trips.

    The tripping node has already set ``state.output`` with the reason;
    we just need a span so the trace shows the termination clearly.
    """
    with langgraph_node_span("guardrail_terminated", tenant_id=state.tenant_id):
        return {}
