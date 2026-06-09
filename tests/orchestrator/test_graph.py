"""Graph topology + router tests."""

from __future__ import annotations

from agents.observability import with_request_id
from agents.orchestrator import AgentState, Channel, build_graph
from agents.orchestrator import nodes as orch_nodes
from agents.orchestrator.graph import _router
from agents.orchestrator.state import Intent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


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


# --- CM-87: Coordinator node + conditional routing ---------------------------


def test_graph_registers_coordinator_node(memory_checkpointer: MemorySaver) -> None:
    """CM-87 adds the ``coordinator`` node to the spine (AC #2)."""
    g = build_graph(checkpointer=memory_checkpointer)
    assert "coordinator" in set(g.get_graph().nodes.keys())


def test_coordinator_runs_plan_execute_trajectory() -> None:
    """CM-88: the real Coordinator runs the decompose→call-tools loop inline and
    accumulates the sub-results on state (replaces the CM-87 pass-through stub)."""
    state = AgentState(
        tenant_id="t-c",
        request_id="r-c",
        raw_message="what's the pet policy and the sink is leaking in 4B",
        intent=Intent.INQUIRY,
        sub_intents=["inquiry", "maintenance"],
        routes=["coordinator"],
    )
    with with_request_id("r-c"):
        out = orch_nodes.coordinator(state)
    assert out["output"]["status"] == "coordinated"
    assert [o["tool"] for o in out["sub_results"]] == [
        "knowledge_agent",
        "maintenance_agent",
    ]
    # Terminal (no escalation gate) → ends the graph.
    assert out["routes"] == ["coordinator_done"]


def test_coordinator_short_circuits_on_guardrail() -> None:
    """The node honours the CM-26 guardrail contract before running the loop."""
    state = AgentState(
        tenant_id="t-c",
        request_id="r-c",
        intent=Intent.MAINTENANCE,
        routes=["coordinator"],
        cost_so_far=999.0,  # over the $5 cap
    )
    with with_request_id("r-c"):
        out = orch_nodes.coordinator(state)
    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == ["guardrail_terminated"]


def test_coordinator_escalation_subresult_routes_to_hitl() -> None:
    """Legal gate (AC #5): an escalation sub-result holds the trajectory for HITL
    review — the record is carried on state and nothing is auto-sent."""
    state = AgentState(
        tenant_id="t-c",
        request_id="r-c",
        raw_message="fix the leak and I'm calling my lawyer about this",
        intent=Intent.MAINTENANCE,
        sub_intents=["maintenance", "escalation"],
        routes=["coordinator"],
    )
    with with_request_id("r-c"):
        out = orch_nodes.coordinator(state)
    assert out["routes"] == ["hitl_review"]
    assert out["escalation"].legal_risk is True
    assert out["output"]["legal_risk"] is True
    # The draft is held, never sent here.
    assert out["output"].get("draft")


def test_graph_dispatches_multi_intent_through_coordinator(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A compound message (heuristic flags multi_intent) takes the
    triage → coordinator path; the Coordinator calls the specialist tools inline
    (per-step spans) rather than handing off to the specialist graph nodes."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-mi",
        request_id="r-mi",
        channel=Channel.WEB,
        # Two maintenance issues joined by "and" → heuristic multi_intent=True.
        raw_message="the sink is leaking and the heater is broken in unit 4B",
    )
    with with_request_id("r-mi"):
        final = graph.invoke(initial, config={"configurable": {"thread_id": "thr-mi"}})

    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    # The Coordinator ran the loop with at least one per-step span (AC #6)...
    assert "langgraph.node.coordinator" in span_names
    assert "langgraph.node.coordinator.step" in span_names
    # ...calling the maintenance tool inline, NOT the maintenance graph node.
    assert "langgraph.node.maintenance" not in span_names
    assert final["output"]["status"] == "coordinated"
    tools = [o["tool"] for o in final["output"]["sub_results"]]
    assert "maintenance_agent" in tools
    # The accumulated sub-results are persisted on state for B4 synthesis (CM-89).
    assert final["sub_results"] == final["output"]["sub_results"]


def test_graph_single_intent_skips_coordinator(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A single-intent message takes the identical old path — the Coordinator
    node never runs (AC #2/#6)."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-si",
        request_id="r-si",
        channel=Channel.WEB,
        raw_message="the kitchen sink has a leak in unit 4B",
    )
    with with_request_id("r-si"):
        graph.invoke(initial, config={"configurable": {"thread_id": "thr-si"}})

    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.coordinator" not in span_names
    assert "langgraph.node.maintenance" in span_names
