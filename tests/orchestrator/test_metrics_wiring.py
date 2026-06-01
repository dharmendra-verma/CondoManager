"""CM-39 + CM-46: node decision points emit the expected PRD metric events."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from agents.maintenance import get_ticket_repository
from agents.maintenance import notifier as maint_notifier_mod
from agents.maintenance import repository as maint_repo_mod
from agents.maintenance.schema import Priority, Ticket, TicketStatus
from agents.observability import (
    METRIC_ESCALATION_LEGAL_FLAG,
    METRIC_FOLLOWUP,
    METRIC_KNOWLEDGE_REFUSED,
    METRIC_MAINTENANCE_DEDUP,
    METRIC_TRIAGE_ROUTE,
    METRIC_VENDOR_AUTO_DISPATCH,
    METRIC_VENDOR_HITL,
    with_request_id,
)
from agents.orchestrator import AgentState, Channel, build_graph, nodes
from agents.vendor import notifier as vendor_notifier_mod
from agents.vendor import repository as vendor_repo_mod
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(autouse=True)
def _reset_seams() -> Generator[None, None, None]:
    """Keep the cached in-memory repos/notifiers from leaking across tests."""
    mods = (maint_repo_mod, maint_notifier_mod, vendor_repo_mod, vendor_notifier_mod)
    for mod in mods:
        mod._reset_for_tests()
    yield
    for mod in mods:
        mod._reset_for_tests()


def _span(spans: InMemorySpanExporter, name: str) -> object:
    hits = [s for s in spans.get_finished_spans() if s.name == name]
    assert len(hits) == 1, f"expected exactly one {name} span, got {len(hits)}"
    return hits[0]


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


def test_maintenance_node_emits_dedup_new_then_duplicate(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """First report emits ``dedup outcome=new``; an immediate repeat → ``duplicate``."""
    msg = "leak under the kitchen sink in unit 4B"
    first = AgentState(tenant_id="t-m", request_id="r-m1", raw_message=msg)
    with with_request_id("r-m1"):
        out1 = nodes.maintenance(first)
    assert out1["output"]["status"] == "ticket_created"
    assert _span(in_memory_spans, METRIC_MAINTENANCE_DEDUP).attributes["outcome"] == "new"

    in_memory_spans.clear()
    second = AgentState(tenant_id="t-m", request_id="r-m2", raw_message=msg)
    with with_request_id("r-m2"):
        out2 = nodes.maintenance(second)
    assert out2["output"]["status"] == "duplicate"
    assert _span(in_memory_spans, METRIC_MAINTENANCE_DEDUP).attributes["outcome"] == "duplicate"


def test_maintenance_node_emits_followup_against_resolved(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A fresh ticket recurring against a RESOLVED issue emits ``metric.followup``."""
    past = datetime.now(UTC) - timedelta(days=1)
    get_ticket_repository().add(
        Ticket(
            id="TKT-OLD",
            tenant_id="t-fu",
            unit="4b",
            issue_text="kitchen sink leaking",
            category="plumbing",
            priority=Priority.P3,
            status=TicketStatus.RESOLVED,
            created_at=past,
            updated_at=past,
            resolved_at=past,
        )
    )
    state = AgentState(
        tenant_id="t-fu", request_id="r-fu", raw_message="the sink is leaking again in unit 4B"
    )
    with with_request_id("r-fu"):
        out = nodes.maintenance(state)

    assert out["output"]["status"] == "ticket_created"
    assert out["output"]["is_followup"] is True
    assert out["output"]["followup_of"] == "TKT-OLD"
    fu = _span(in_memory_spans, METRIC_FOLLOWUP)
    assert fu.attributes["prior_ticket_id"] == "TKT-OLD"


def test_graph_emits_maintenance_and_vendor_metrics(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A maintenance request emits the dedup metric and a vendor decision metric."""
    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t-v",
        request_id="r-v",
        channel=Channel.WEB,
        raw_message="There is a leak under the sink in unit 4B",
    )
    with with_request_id("r-v"):
        final = graph.invoke(initial, config={"configurable": {"thread_id": "thr-v"}})

    names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert METRIC_MAINTENANCE_DEDUP in names
    vendor_status = final["output"]["vendor_status"]
    if vendor_status == "auto_dispatched":
        assert METRIC_VENDOR_AUTO_DISPATCH in names
        assert METRIC_VENDOR_HITL not in names
    else:
        assert vendor_status == "pending_approval"
        assert METRIC_VENDOR_HITL in names
        assert METRIC_VENDOR_AUTO_DISPATCH not in names


def test_escalation_node_emits_legal_flag_only_when_flagged(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """The legal-flag metric fires for a legal-risk escalation, not a plain one."""
    legal = AgentState(
        tenant_id="t-e1", request_id="r-e1", raw_message="My lawyer will sue you over this"
    )
    with with_request_id("r-e1"):
        out = nodes.escalation(legal)
    assert out["output"]["legal_risk"] is True
    assert METRIC_ESCALATION_LEGAL_FLAG in {s.name for s in in_memory_spans.get_finished_spans()}

    in_memory_spans.clear()
    plain = AgentState(
        tenant_id="t-e2",
        request_id="r-e2",
        raw_message="nobody has responded to me, I feel ignored",
    )
    with with_request_id("r-e2"):
        out2 = nodes.escalation(plain)
    assert out2["output"]["legal_risk"] is False
    assert METRIC_ESCALATION_LEGAL_FLAG not in {
        s.name for s in in_memory_spans.get_finished_spans()
    }
