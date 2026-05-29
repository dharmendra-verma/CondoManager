"""End-to-end hello-world: full graph run + trace contract assertions."""

from __future__ import annotations

from agents.observability import with_request_id
from agents.orchestrator import AgentState, Channel, build_graph
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_hello_world_runs_to_completion(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Triage -> knowledge (refuses: no KB) -> maintenance -> END, no creds.

    CM-33: with no ``COSMOS_ENDPOINT`` configured the Knowledge node cannot
    retrieve, so it refuses and hands off to Maintenance — all credential-free
    and with no LLM/network calls. The terminal output is the Maintenance
    stub's, confirming the handoff edge fired.
    """
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-hello",
        request_id="r-hello",
        channel=Channel.WEB,
        raw_message="ping",
    )
    config = {"configurable": {"thread_id": "thr-hello"}}

    with with_request_id("r-hello"):
        final = graph.invoke(initial, config=config)

    # Knowledge refused (no KB) and handed off; Maintenance stub is terminal.
    assert final["output"]["status"] == "ticket_stub"


def test_hello_world_emits_expected_node_spans(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Trace shape — CM-22 + CM-23 backends rely on these span names."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-hello",
        request_id="r-hello-span",
        channel=Channel.WEB,
        raw_message="ping",
    )
    config = {"configurable": {"thread_id": "thr-hello-span"}}

    with with_request_id("r-hello-span"):
        graph.invoke(initial, config=config)

    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.triage" in span_names
    assert "langgraph.node.knowledge" in span_names


def test_hello_world_node_spans_carry_request_id(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Cross-backend correlation: same request_id on every node span."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-corr",
        request_id="r-corr-1",
        channel=Channel.WEB,
        raw_message="ping",
    )
    config = {"configurable": {"thread_id": "thr-corr"}}

    with with_request_id("r-corr-1"):
        graph.invoke(initial, config=config)

    node_spans = [
        s for s in in_memory_spans.get_finished_spans()
        if s.name.startswith("langgraph.node.")
    ]
    assert node_spans, "expected at least one langgraph.node.* span"
    for s in node_spans:
        attrs = s.attributes or {}
        assert attrs.get("request_id") == "r-corr-1"


def test_guardrail_termination_short_circuits_graph(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """Cost cap tripped at triage => terminal node fires, no downstream work."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-trip",
        request_id="r-trip",
        channel=Channel.WEB,
        raw_message="ping",
        cost_so_far=999.0,  # well over the $5 cap
    )
    config = {"configurable": {"thread_id": "thr-trip"}}

    with with_request_id("r-trip"):
        final = graph.invoke(initial, config=config)

    assert final["output"]["status"] == "guardrail_terminated"
    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.guardrail_terminated" in span_names
    # Downstream knowledge / maintenance nodes did NOT fire.
    assert "langgraph.node.knowledge" not in span_names
    assert "langgraph.node.maintenance" not in span_names
