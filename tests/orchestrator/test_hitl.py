"""Human-in-the-loop ``interrupt()`` / resume tests + CM-32 legal gate.

The escalation flow pauses at ``hitl_review`` until a human supplies an
approval payload via ``graph.invoke(Command(resume=...), config=...)``. CM-32
makes ``escalation`` a real node and ``hitl_review`` the approval gate: the
tenant draft is only ever marked ``sent`` on explicit approval, and a
legal-flagged escalation can never reach END without passing this pause.
"""

from __future__ import annotations

from agents.orchestrator import AgentState, Channel, build_graph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


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
