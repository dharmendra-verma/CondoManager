"""Human-in-the-loop ``interrupt()`` / resume tests.

The CM-32 escalation flow depends on this pattern: graph pauses at
``hitl_review`` until a human supplies an approval payload via
``graph.invoke(Command(resume=...), config=...)``.
"""

from __future__ import annotations

from agents.orchestrator import AgentState, Channel, build_graph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


def _initial_state() -> AgentState:
    """Message contains 'human' so the triage stub routes to escalation."""
    return AgentState(
        tenant_id="t-1",
        request_id="r-hitl-1",
        channel=Channel.WEB,
        raw_message="I want to speak to a human",
    )


def test_graph_pauses_at_hitl_review_with_interrupt_marker(
    memory_checkpointer: MemorySaver,
) -> None:
    """First invoke returns with the ``__interrupt__`` marker; not finished."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-1"}}
    result = graph.invoke(_initial_state(), config=config)

    # LangGraph 0.2+ surfaces interrupts via the `__interrupt__` key on
    # the returned state dict (or on get_state(...).next).
    assert "__interrupt__" in result or graph.get_state(config).next != ()


def test_graph_resumes_with_approval_payload(
    memory_checkpointer: MemorySaver,
) -> None:
    """Resume with ``Command(resume=...)`` returns the final state with
    the supplied approval threaded into ``state.output``.
    """
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-2"}}

    # 1. First invoke — pauses at hitl_review.
    graph.invoke(_initial_state(), config=config)

    # 2. Resume with the human's approval payload.
    approval = {"approved": True, "reviewer": "ops-1"}
    final = graph.invoke(Command(resume=approval), config=config)

    # The hitl_review node returns ``{"output": {"approved": approval, "via": "hitl"}}``.
    assert final["output"]["approved"] == approval
    assert final["output"]["via"] == "hitl"


def test_interrupt_payload_includes_draft(
    memory_checkpointer: MemorySaver,
) -> None:
    """The pause-time payload carries the escalation draft for the UI."""
    graph = build_graph(checkpointer=memory_checkpointer)
    config = {"configurable": {"thread_id": "thr-hitl-3"}}
    result = graph.invoke(_initial_state(), config=config)

    interrupts = result.get("__interrupt__")
    if not interrupts:
        # Older / newer LangGraph surface — fall back to get_state inspection.
        snapshot = graph.get_state(config)
        interrupts = getattr(snapshot, "interrupts", None) or ()

    assert interrupts, "expected an interrupt payload at hitl_review"
    payloads = [getattr(i, "value", i) for i in interrupts]
    assert any(
        isinstance(p, dict) and p.get("reason") == "stub-hitl-review"
        for p in payloads
    )
