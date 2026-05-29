"""Maintenance node integration (CM-31) — real ticket lifecycle via the graph."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from agents.maintenance import notifier as notifier_mod
from agents.maintenance import repository as repo_mod
from agents.orchestrator import AgentState, Channel, build_graph, nodes
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(autouse=True)
def _reset_maintenance_seams() -> Generator[None, None, None]:
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()
    yield
    repo_mod._reset_for_tests()
    notifier_mod._reset_for_tests()


def test_maintenance_route_creates_ticket(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-maint",
        request_id="r-maint",
        channel=Channel.WEB,
        raw_message="There is a leak under the sink in unit 4B",
    )
    final = graph.invoke(initial, config={"configurable": {"thread_id": "thr-maint"}})

    assert final["output"]["status"] == "ticket_created"
    assert final["output"]["unit"] == "4b"
    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.maintenance" in span_names


def test_maintenance_node_guardrail_short_circuits(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    tripped = AgentState(
        tenant_id="t-trip",
        request_id="r-trip",
        raw_message="leak in unit 4B",
        cost_so_far=999.0,  # over the $5 cap
    )
    out = nodes.maintenance(tripped)

    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == [nodes.ROUTE_GUARDRAIL_TERMINATED]
    # No ticket persisted when the guardrail trips before any work.
    repo = repo_mod.get_ticket_repository()
    assert isinstance(repo, repo_mod.InMemoryTicketRepository)
    since = datetime.now(UTC) - timedelta(days=7)
    assert repo.recent_for_unit("t-trip", "4b", since=since) == []
