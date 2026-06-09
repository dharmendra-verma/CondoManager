"""Specialist agents exposed as callable LangChain tools (CM-86, Track B).

Jira: CM-86  | Epic: Track B (Coordinator / multi-intent orchestration)  | Phase 1

The future Coordinator (CM-87/88) needs to *select and call* the specialist
agents, but today they are reachable only through fixed LangGraph edges. This
module wraps each specialist as a typed ``StructuredTool`` the planner can pick:

* :data:`maintenance_tool`  -> :meth:`agents.maintenance.MaintenanceAgent.handle`
* :data:`vendor_tool`       -> :meth:`agents.vendor.VendorAgent.handle`
* :data:`knowledge_tool`    -> the CM-83/84/85 iterative Knowledge planner
* :data:`escalation_tool`   -> classify + ``build_record`` (CM-32 builders)

Each tool is a **thin, deterministic adapter**: a Pydantic args schema -> build a
state-like :class:`~agents.orchestrator.state.AgentState` -> call the existing
agent -> return its result dict **verbatim** (JSON-serializable). No specialist
agent behaviour changes — the agents stay the single source of truth, so
"tool result == direct agent call" holds by construction.

Determinism / DI seam: each agent is resolved through a module-level zero-arg
builder (:func:`_maintenance_agent` etc.). Tests monkeypatch these to inject
fixed code-generator / clock / record-id and fresh in-memory repositories, so
two independent runs produce byte-identical output. With nothing injected the
builders return the existing offline-safe singletons (in-memory repos, logging
notifiers, heuristic/stub models) — every tool runs with no credentials.

``langchain_core.tools`` is a locked, lightweight dependency the LangGraph spine
already pulls in, so it is imported at module top; the heavier specialist agents
are lazy-imported inside the builders (mirroring the node seam style) to keep
this module import-cheap and free of an ``agents.orchestrator`` import cycle.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agents.orchestrator.state import AgentState, Intent, Tone, Urgency

if TYPE_CHECKING:  # typing-only; the implementations are lazy-imported in builders
    from agents.knowledge.embeddings import Embedder
    from agents.knowledge.planner import KnowledgePlanner, PlannerResult
    from agents.knowledge.retrieval import SearchStore
    from agents.maintenance import MaintenanceAgent
    from agents.orchestrator.escalation import EscalationClassifier
    from agents.vendor import VendorAgent


# --- Agent builders (monkeypatched in tests for determinism) ------------------
#
# Each returns the agent/seam the matching adapter calls. Defaults resolve the
# existing offline-safe singletons; tests replace a builder to inject fixed
# clocks / code generators / fresh in-memory repositories so the equality test
# ("tool == direct call") is deterministic despite the agents' uuid/now use.


def _maintenance_agent() -> MaintenanceAgent:
    """The Maintenance specialist (in-memory repo + logging notifier offline)."""
    from agents.maintenance import MaintenanceAgent  # noqa: PLC0415

    return MaintenanceAgent()


def _vendor_agent() -> VendorAgent:
    """The Vendor specialist (seeded in-memory repo + logging notifiers offline)."""
    from agents.vendor import VendorAgent  # noqa: PLC0415

    return VendorAgent()


def _knowledge_planner() -> KnowledgePlanner:
    """The iterative Knowledge planner (stub loop with no ``OPENAI_API_KEY``)."""
    from agents.knowledge.planner import get_knowledge_planner  # noqa: PLC0415

    return get_knowledge_planner()


def _knowledge_store() -> SearchStore | None:
    """Injected retriever for the Knowledge tool.

    ``None`` (the default) lets the planner fall back to the env-driven vector
    store — which is absent offline, so the planner refuses without a model call
    (the deterministic no-credentials behaviour). Tests inject a lexical double
    here to drive the real loop over a synthetic KB.
    """
    return None


def _knowledge_embedder() -> Embedder | None:
    """Injected embedder for the Knowledge tool (``None`` -> env factory)."""
    return None


def _escalation_classifier() -> EscalationClassifier:
    """The Escalation classifier (heuristic with no ``OPENAI_API_KEY``)."""
    from agents.orchestrator.escalation import get_escalation_classifier  # noqa: PLC0415

    return get_escalation_classifier()


def _new_record_id() -> str:
    """Fresh escalation record id; monkeypatched to a fixed value in tests."""
    return f"esc-{uuid.uuid4().hex}"


# --- Args schemas -------------------------------------------------------------


class MaintenanceToolArgs(BaseModel):
    """Inputs for :data:`maintenance_tool`."""

    tenant_id: str = Field(description="Tenant identifier the ticket belongs to.")
    message: str = Field(description="The tenant's maintenance request text.")
    urgency: Urgency | None = Field(
        default=None, description="Urgency band; drives priority/ETA when set."
    )
    tone: Tone | None = Field(default=None, description="Emotional tone, if classified.")
    request_id: str = Field(default="", description="Correlation id for tracing.")


class VendorToolArgs(BaseModel):
    """Inputs for :data:`vendor_tool` — the created maintenance ticket fields."""

    tenant_id: str = Field(description="Tenant identifier the ticket belongs to.")
    category: str = Field(description="Maintenance category, e.g. 'plumbing'.")
    priority: str = Field(description="Ticket priority, e.g. 'P2'.")
    ticket_id: str = Field(description="The maintenance ticket id to dispatch for.")
    unit: str = Field(description="Unit the ticket is for, e.g. '4B'.")
    status: str = Field(
        default="ticket_created",
        description="Upstream ticket status; only 'ticket_created' dispatches.",
    )
    intent: Intent | None = Field(
        default=None, description="Triage intent; influences the dispatch decision."
    )
    request_id: str = Field(default="", description="Correlation id for tracing.")


class KnowledgeToolArgs(BaseModel):
    """Inputs for :data:`knowledge_tool`."""

    tenant_id: str = Field(description="Tenant identifier asking the question.")
    question: str = Field(description="The policy/inquiry question to answer.")
    request_id: str = Field(default="", description="Correlation id for tracing.")


class EscalationToolArgs(BaseModel):
    """Inputs for :data:`escalation_tool`."""

    tenant_id: str = Field(description="Tenant identifier raising the escalation.")
    message: str = Field(description="The escalation message text.")
    urgency: Urgency | None = Field(default=None, description="Urgency band, if classified.")
    tone: Tone | None = Field(default=None, description="Emotional tone, if classified.")
    history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Recent ticket history for this tenant (audit context).",
    )
    request_id: str = Field(default="", description="Correlation id for tracing.")


# --- Adapters (args -> state -> agent -> verbatim result) ---------------------


def run_maintenance(
    *,
    tenant_id: str,
    message: str,
    urgency: Urgency | None = None,
    tone: Tone | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Adapt args to ``AgentState`` and run the Maintenance agent.

    Returns the agent's result dict verbatim (``{"output": {...ticket...}}``).
    """
    state = AgentState(
        tenant_id=tenant_id,
        request_id=request_id,
        raw_message=message,
        urgency=urgency,
        tone=tone,
    )
    return _maintenance_agent().handle(state)


def run_vendor(
    *,
    tenant_id: str,
    category: str,
    priority: str,
    ticket_id: str,
    unit: str,
    status: str = "ticket_created",
    intent: Intent | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Adapt the created-ticket fields to ``state.output`` and run the Vendor agent.

    Returns the agent's result dict verbatim (``{"output": {...}, "routes": [...]}``).
    """
    state = AgentState(
        tenant_id=tenant_id,
        request_id=request_id,
        intent=intent,
        output={
            "status": status,
            "category": category,
            "priority": priority,
            "ticket_id": ticket_id,
            "unit": unit,
        },
    )
    return _vendor_agent().handle(state)


def _knowledge_result_to_dict(result: PlannerResult) -> dict[str, Any]:
    """Project a :class:`PlannerResult` onto the Knowledge node's output shape.

    Mirrors ``agents.orchestrator.nodes.knowledge`` (status / answer / citations /
    confidence) plus an explicit ``refused`` flag, all JSON-serializable.
    """
    answer = result.answer
    return {
        "status": "refused" if answer.refused else "answered",
        "answer": answer.answer,
        "citations": [c.model_dump() for c in answer.citations],
        "confidence": answer.confidence,
        "refused": answer.refused,
    }


def run_knowledge(
    *, tenant_id: str, question: str, request_id: str = ""
) -> dict[str, Any]:
    """Adapt args to ``AgentState`` and run the iterative Knowledge planner.

    Drives the planner over the injected ``_knowledge_store`` / ``_knowledge_embedder``
    (``None`` -> env factory -> offline refusal). Returns the node-shaped answer dict.
    """
    state = AgentState(
        tenant_id=tenant_id, request_id=request_id, raw_message=question
    )
    result = _knowledge_planner().run(
        question,
        state=state,
        store=_knowledge_store(),
        embedder=_knowledge_embedder(),
    )
    return _knowledge_result_to_dict(result)


def run_escalation(
    *,
    tenant_id: str,
    message: str,
    urgency: Urgency | None = None,
    tone: Tone | None = None,
    history: list[dict[str, Any]] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Classify the escalation and build its record (CM-32 builders).

    Pure builder: classify + ``build_record`` only — no manager-notify, no
    store-persist (those stay the node's/Coordinator's job). Returns the
    :class:`~agents.orchestrator.state.EscalationRecord` as a dumped dict.
    """
    from agents.orchestrator.escalation import build_record  # noqa: PLC0415

    classification = _escalation_classifier().classify(message, history or [])
    record = build_record(
        record_id=_new_record_id(),
        tenant_id=tenant_id,
        request_id=request_id,
        classification=classification,
        urgency=urgency,
        tone=tone,
        message=message,
    )
    return record.model_dump()


# --- The tools ----------------------------------------------------------------

maintenance_tool: StructuredTool = StructuredTool.from_function(
    func=run_maintenance,
    name="maintenance_agent",
    description=(
        "Create or de-duplicate a maintenance ticket from a tenant's request. "
        "Assigns priority and ETA, notifies the manager, and returns the ticket "
        "details. Use for repair/maintenance issues (leaks, heating, appliances)."
    ),
    args_schema=MaintenanceToolArgs,
)

vendor_tool: StructuredTool = StructuredTool.from_function(
    func=run_vendor,
    name="vendor_agent",
    description=(
        "Match a contractor to a created maintenance ticket and either "
        "auto-dispatch a routine/low-cost job or request manager approval. "
        "Call after a ticket has been created (status 'ticket_created')."
    ),
    args_schema=VendorToolArgs,
)

knowledge_tool: StructuredTool = StructuredTool.from_function(
    func=run_knowledge,
    name="knowledge_agent",
    description=(
        "Answer a tenant's policy or building-inquiry question from the policy "
        "knowledge base, with citations and a confidence score. Refuses (rather "
        "than guessing) when the answer is not grounded in the documents."
    ),
    args_schema=KnowledgeToolArgs,
)

escalation_tool: StructuredTool = StructuredTool.from_function(
    func=run_escalation,
    name="escalation_agent",
    description=(
        "Sub-classify an escalation, flag legal/safety risk, and build the "
        "internal record with a held empathetic draft and manager alert text. "
        "Use when a tenant message needs management attention."
    ),
    args_schema=EscalationToolArgs,
)

#: All four specialist tools, in routing order, for the Coordinator to bind.
ALL_TOOLS: list[StructuredTool] = [
    maintenance_tool,
    vendor_tool,
    knowledge_tool,
    escalation_tool,
]
