"""Graph topology + router tests."""

from __future__ import annotations

from agents.orchestrator import AgentState, build_graph
from agents.orchestrator.graph import _router
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph


def test_build_graph_returns_compiled(memory_checkpointer: MemorySaver) -> None:
    g = build_graph(checkpointer=memory_checkpointer)
    assert isinstance(g, CompiledStateGraph)


def test_router_defaults_to_triage_on_empty_routes() -> None:
    """Empty state must still pick a well-defined entry."""
    s = AgentState(tenant_id="t-1", request_id="r-1")
    assert _router(s) == "triage"


def test_router_uses_last_route_entry() -> None:
    s = AgentState(
        tenant_id="t-1",
        request_id="r-1",
        routes=["knowledge", "maintenance"],
    )
    assert _router(s) == "maintenance"


def test_graph_has_six_nodes(memory_checkpointer: MemorySaver) -> None:
    """Spine declares triage/knowledge/maintenance/escalation/hitl_review/guardrail_terminated."""
    g = build_graph(checkpointer=memory_checkpointer)
    nodes = set(g.get_graph().nodes.keys())
    expected = {
        "triage",
        "knowledge",
        "maintenance",
        "escalation",
        "hitl_review",
        "guardrail_terminated",
    }
    # __start__ and __end__ are LangGraph-internal; subset assertion.
    assert expected.issubset(nodes)


def test_build_graph_defaults_to_get_checkpointer(monkeypatch) -> None:
    """No checkpointer arg => env-var selector (MemorySaver when unset)."""
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    g = build_graph()
    assert isinstance(g, CompiledStateGraph)
