"""Human-in-the-loop ``interrupt()`` / resume tests + CM-32 legal gate.

The escalation flow pauses at ``hitl_review`` until a human supplies an
approval payload via ``graph.invoke(Command(resume=...), config=...)``. CM-32
makes ``escalation`` a real node and ``hitl_review`` the approval gate: the
tenant draft is only ever marked ``sent`` on explicit approval, and a
legal-flagged escalation can never reach END without passing this pause.
"""

from __future__ import annotations

import pytest
from agents.observability import METRIC_HITL_RATING
from agents.orchestrator import AgentState, Channel, build_graph
from agents.orchestrator.escalation_store import NoopEscalationStore
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _initial_state() -> AgentState:
    """Message contains 'human' so triage routes to escalation."""
    return AgentState(
        tenant_id="t-1",
        request_id="r-hitl-1",
        channel=Channel.WEB,
        raw_message="I want to speak to a human",
    )


def _legal_state(thread_suffix: str) -> AgentState:
    """Message with explicit legal exposure -> escalation, legal_risk=True."""
    return AgentState(
        tenant_id=f"t-legal-{thread_suffix}",
        request_id=f"r-legal-{thread_suffix}",
        channel=Channel.WEB,
        raw_message="I am going to sue you and I have already called my lawyer",
    )


def _is_paused(graph, config, result) -> bool:  # noqa: ANN001
    return "__interrupt__" in result or graph.get_state(config).next != ()


def _escalation_status(final) -> str:  # noqa: ANN001
    esc = final["escalation"]
    return esc.status if hasattr(esc, "status") else esc["status"]


def test_graph_pauses_at_hitl_review_with_interrupt_marker(
    memory_checkpointer: MemorySaver,
) -> None:
    """First invoke returns with the ``__interrupt__`` marker; not finished."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-1"}}
    result = graph.invoke(_initial_state(), config=config)
    assert _is_paused(graph, config, result)


def test_graph_resumes_with_approval_payload(
    memory_checkpointer: MemorySaver,
) -> None:
    """Resume threads the approval into ``state.output`` and marks it sent."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-2"}}

    graph.invoke(_initial_state(), config=config)
    approval = {"approved": True, "reviewer": "ops-1"}
    final = graph.invoke(Command(resume=approval), config=config)

    assert final["output"]["approved"] == approval
    assert final["output"]["via"] == "hitl"
    assert final["output"]["sent"] is True


def test_interrupt_payload_carries_escalation_context(
    memory_checkpointer: MemorySaver,
) -> None:
    """The pause payload surfaces the escalation review context for the UI."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-3"}}
    result = graph.invoke(_initial_state(), config=config)

    interrupts = result.get("__interrupt__")
    if not interrupts:
        snapshot = graph.get_state(config)
        interrupts = getattr(snapshot, "interrupts", None) or ()

    assert interrupts, "expected an interrupt payload at hitl_review"
    payloads = [getattr(i, "value", i) for i in interrupts]
    review = next(
        (p for p in payloads if isinstance(p, dict) and p.get("reason") == "escalation_review"),
        None,
    )
    assert review is not None
    assert "draft" in review and review["draft"]
    assert "legal_risk" in review
    assert "category" in review


# --- CM-32 legal gate (AC #6) ------------------------------------------------


def test_legal_escalation_pauses_and_is_not_sent_before_approval(
    memory_checkpointer: MemorySaver,
) -> None:
    """A legal-flagged case cannot reach END without the HITL pause, and the
    draft is not marked sent until a human acts."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-legal-1"}}
    result = graph.invoke(_legal_state("1"), config=config)

    assert _is_paused(graph, config, result)
    out = result.get("output") or {}
    assert out.get("legal_risk") is True
    assert out.get("sent") is not True  # never auto-sent


def test_legal_escalation_withheld_when_not_approved(
    memory_checkpointer: MemorySaver,
) -> None:
    """Resume WITHOUT approval -> draft withheld, record rejected."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-legal-2"}}
    graph.invoke(_legal_state("2"), config=config)

    final = graph.invoke(Command(resume={"approved": False}), config=config)
    assert final["output"]["sent"] is False
    assert _escalation_status(final) == "rejected"


def test_legal_escalation_sent_only_on_explicit_approval(
    memory_checkpointer: MemorySaver,
) -> None:
    """Resume WITH explicit approval -> draft sent, record approved_sent."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-legal-3"}}
    graph.invoke(_legal_state("3"), config=config)

    final = graph.invoke(Command(resume={"approved": True, "reviewer": "mgr-1"}), config=config)
    assert final["output"]["sent"] is True
    assert _escalation_status(final) == "approved_sent"


# --- CM-44: HITL rating persistence + metric.hitl.rating ---------------------


def _rating_span(spans: InMemorySpanExporter):  # noqa: ANN202
    """Return the one ``metric.hitl.rating`` span, asserting exactly one exists."""
    hits = [s for s in spans.get_finished_spans() if s.name == METRIC_HITL_RATING]
    assert len(hits) == 1, f"expected one {METRIC_HITL_RATING} span, got {len(hits)}"
    return hits[0]


def _run_review(graph, config, resume):  # noqa: ANN001
    """Drive escalation → pause → resume with ``resume`` and return the final state."""
    graph.invoke(_initial_state(), config=config)
    return graph.invoke(Command(resume=resume), config=config)


def test_approve_with_rating_persists_and_emits(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approve + rating: record persisted (approved_sent + rating) and exactly one
    ``metric.hitl.rating`` (decision=approve, value=5) is emitted."""
    store = NoopEscalationStore()
    monkeypatch.setattr("agents.orchestrator.nodes.get_escalation_store", lambda: store)
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-rate-1"}}

    final = _run_review(
        graph, config, {"approved": True, "rating": 5, "comment": "clear and empathetic"}
    )

    assert final["output"]["sent"] is True
    assert _escalation_status(final) == "approved_sent"

    rated = store.recent("t-1")
    assert len(rated) == 1  # record_rating replaced the pending record in place
    rec = rated[0]
    assert rec.status == "approved_sent"
    assert rec.manager_rating == 5
    assert rec.rating_comment == "clear and empathetic"
    assert rec.rated_at is not None

    span = _rating_span(in_memory_spans)
    assert span.attributes["decision"] == "approve"
    assert span.attributes["metric.value"] == 5.0
    assert span.attributes["has_rating"] is True


def test_reject_with_rating_persists_reject_decision(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject + rating: record ``rejected``, rating kept, metric decision=reject."""
    store = NoopEscalationStore()
    monkeypatch.setattr("agents.orchestrator.nodes.get_escalation_store", lambda: store)
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-rate-2"}}

    final = _run_review(graph, config, {"approved": False, "rating": 2})

    assert final["output"]["sent"] is False
    assert _escalation_status(final) == "rejected"

    rec = store.recent("t-1")[0]
    assert rec.status == "rejected"
    assert rec.manager_rating == 2

    span = _rating_span(in_memory_spans)
    assert span.attributes["decision"] == "reject"
    assert span.attributes["metric.value"] == 2.0


def test_bare_bool_resume_records_no_rating_but_emits_metric(
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare-bool resume carries no rating, but the decision is still measured."""
    store = NoopEscalationStore()
    monkeypatch.setattr("agents.orchestrator.nodes.get_escalation_store", lambda: store)
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-rate-3"}}

    _run_review(graph, config, True)

    rec = store.recent("t-1")[0]
    assert rec.status == "approved_sent"
    assert rec.manager_rating is None
    assert rec.rating_comment == ""

    span = _rating_span(in_memory_spans)
    assert span.attributes["decision"] == "approve"
    assert span.attributes["metric.value"] == 1.0
    assert span.attributes["has_rating"] is False
