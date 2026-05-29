"""LangGraph StateGraph builder for the CM-28 spine.

Jira: CM-28  | Epic: CM-Epic 4  | Phase 0

Topology::

    START
      |
      v
    triage  -- (router on state.routes[-1]) -->  knowledge -> END | maintenance
                                                 maintenance     --> END
                                                 escalation -> hitl_review -> END
                                                 guardrail_terminated      --> END

CM-33: the Knowledge node answers terminally, but on refusal (low
confidence) it routes to Maintenance via ``_knowledge_router``.

The router reads ``state.routes[-1]`` to pick the next node. Each stub
node appends its target route name (e.g. ``"knowledge"``) to
``state.routes`` so the router fires correctly. When a Stop Rule trips
inside a node, that node returns ``routes=['guardrail_terminated']``
and the router fans straight to the terminal.

CM-30 / CM-31 / CM-32 each replace one stub node body without touching
this builder.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from . import nodes
from .checkpointer import get_checkpointer
from .state import AgentState


def _router(state: AgentState) -> str:
    """Conditional-edge selector — returns the next node name.

    Defaults to ``"triage"`` when ``state.routes`` is empty so an
    empty-state ``invoke()`` still has a well-defined entry. Stub nodes
    push their next target so subsequent hops are deterministic.
    """
    if not state.routes:
        return "triage"
    return state.routes[-1]


def _knowledge_router(state: AgentState) -> str:
    """Post-Knowledge edge — hand off to Maintenance on refusal, else END.

    CM-33's Knowledge node appends ``"maintenance"`` to ``state.routes`` when
    it refuses (confidence below threshold / nothing retrieved); otherwise the
    answer is terminal.
    """
    if state.routes and state.routes[-1] == "maintenance":
        return "maintenance"
    return END


def build_graph(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the StateGraph.

    Args:
        checkpointer: ``BaseCheckpointSaver`` for state persistence /
            HITL resume. When ``None``, defaults to the
            ``get_checkpointer()`` selector (``CosmosCheckpointSaver`` when
            ``COSMOS_ENDPOINT`` is configured, else ``MemorySaver``).

    Returns:
        A compiled ``StateGraph(AgentState)`` ready for ``invoke()``.
        The compiled graph's ``invoke()`` accepts an initial
        ``AgentState`` and a ``config`` dict with
        ``configurable.thread_id`` for checkpoint scoping.
    """
    cp = checkpointer if checkpointer is not None else get_checkpointer()

    g: StateGraph[Any, Any, Any, Any] = StateGraph(AgentState)

    g.add_node("triage", nodes.triage)
    g.add_node("knowledge", nodes.knowledge)
    g.add_node("maintenance", nodes.maintenance)
    g.add_node("escalation", nodes.escalation)
    g.add_node("hitl_review", nodes.hitl_review)
    g.add_node("guardrail_terminated", nodes.guardrail_terminated)

    # Entry: START -> triage. Always.
    g.add_edge(START, "triage")

    # Router: triage fans out to one of four downstream nodes (or the
    # terminal, when its own guardrail tripped).
    g.add_conditional_edges(
        "triage",
        _router,
        {
            "knowledge": "knowledge",
            "maintenance": "maintenance",
            "escalation": "escalation",
            "guardrail_terminated": "guardrail_terminated",
        },
    )

    # Downstream nodes. Knowledge is terminal when it answers, but routes to
    # Maintenance when it refuses (CM-33 handoff) — hence a conditional edge.
    g.add_conditional_edges(
        "knowledge",
        _knowledge_router,
        {
            "maintenance": "maintenance",
            END: END,
        },
    )
    g.add_edge("maintenance", END)
    g.add_edge("escalation", "hitl_review")
    g.add_edge("hitl_review", END)
    g.add_edge("guardrail_terminated", END)

    return g.compile(checkpointer=cp)
