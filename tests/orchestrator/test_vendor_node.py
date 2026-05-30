"""Vendor node integration (CM-35) — maintenance -> vendor via the graph."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from agents.maintenance import notifier as maint_notifier_mod
from agents.maintenance import repository as maint_repo_mod
from agents.orchestrator import AgentState, Channel, build_graph, nodes
from agents.vendor import notifier as vendor_notifier_mod
from agents.vendor import repository as vendor_repo_mod
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(autouse=True)
def _reset_seams() -> Generator[None, None, None]:
    for mod in (maint_repo_mod, maint_notifier_mod, vendor_repo_mod, vendor_notifier_mod):
        mod._reset_for_tests()
    yield
    for mod in (maint_repo_mod, maint_notifier_mod, vendor_repo_mod, vendor_notifier_mod):
        mod._reset_for_tests()


def test_maintenance_flows_into_vendor(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-vendor",
        request_id="r-vendor",
        channel=Channel.WEB,
        raw_message="There is a leak under the sink in unit 4B",
    )
    final = graph.invoke(initial, config={"configurable": {"thread_id": "thr-vendor"}})

    # Maintenance still created the ticket; vendor then ran and stamped a status.
    assert final["output"]["status"] == "ticket_created"
    assert final["output"]["vendor_status"] in {"auto_dispatched", "pending_approval"}
    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.maintenance" in span_names
    assert "langgraph.node.vendor" in span_names


def test_vendor_node_guardrail_short_circuits(in_memory_spans: InMemorySpanExporter) -> None:
    tripped = AgentState(
        tenant_id="t-trip",
        request_id="r-trip",
        cost_so_far=999.0,  # over the $5 cap
        output={"status": "ticket_created", "category": "plumbing", "priority": "P3", "unit": "4b"},
    )
    out = nodes.vendor(tripped)
    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == [nodes.ROUTE_GUARDRAIL_TERMINATED]


def test_vendor_node_passes_through_duplicate(in_memory_spans: InMemorySpanExporter) -> None:
    dup = AgentState(
        tenant_id="t-dup",
        request_id="r-dup",
        output={"status": "duplicate", "ticket_id": "TKT-OLD"},
    )
    out = nodes.vendor(dup)
    assert out == {"routes": ["vendor_done"]}
