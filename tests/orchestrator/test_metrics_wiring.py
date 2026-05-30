"""CM-39: node decision points emit the expected PRD metric events."""

from __future__ import annotations

from agents.observability import (
    METRIC_KNOWLEDGE_REFUSED,
    METRIC_TRIAGE_ROUTE,
    with_request_id,
)
from agents.orchestrator import AgentState, Channel, build_graph
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_graph_emits_triage_and_knowledge_metrics(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """An inquiry runs triage → knowledge; both emit their PRD metric events.

    Offline (no COSMOS_ENDPOINT) the Knowledge node refuses, so the
    ``knowledge.refused`` metric fires (self-service/hallucination signal).
    """
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t1",
        request_id="r-metric",
        channel=Channel.WEB,
        raw_message="what are the quiet hours",
    )
    with with_request_id("r-metric"):
        graph.invoke(initial, config={"configurable": {"thread_id": "thr-metric"}})

    names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert METRIC_TRIAGE_ROUTE in names
    assert METRIC_KNOWLEDGE_REFUSED in names
