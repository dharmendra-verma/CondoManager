"""Tests for the CM-33 Knowledge node in ``agents.orchestrator.nodes``.

CM-83: the node delegates the RAG flow to a ``KnowledgePlanner`` obtained from
``nodes.get_knowledge_planner()``. We monkeypatch that seam to a fake planner to
drive each branch deterministically; the planner-internal behaviour (looping,
per-iteration guardrail re-checks, search/cost accounting, the offline contract)
is covered in ``tests/knowledge/test_planner.py``. This module asserts only how
the node translates a ``PlannerResult`` into the state update.
"""

from __future__ import annotations

import pytest
from agents.knowledge.models import Citation, KnowledgeAnswer
from agents.knowledge.planner import PlannerResult
from agents.knowledge.rag import REFUSAL_TEXT
from agents.observability import with_request_id
from agents.orchestrator import AgentState, Channel, build_graph, nodes
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class _FakePlanner:
    """Stands in for the env-selected ``KnowledgePlanner``."""

    def __init__(self, result: PlannerResult) -> None:
        self._result = result
        self.calls: list[tuple[str, AgentState]] = []

    def run(self, message: str, *, state: AgentState) -> PlannerResult:
        self.calls.append((message, state))
        return self._result


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "request_id": "r1",
        "raw_message": "what are the quiet hours",
    }
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def _wire(monkeypatch: pytest.MonkeyPatch, result: PlannerResult) -> _FakePlanner:
    planner = _FakePlanner(result)
    monkeypatch.setattr(nodes, "get_knowledge_planner", lambda: planner)
    return planner


def test_answered_sets_output_and_bumps_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = KnowledgeAnswer(
        answer="Quiet hours are 10pm-7am.",
        citations=[Citation(index=1, doc_id="d1", doc_title="Quiet Hours")],
        confidence=0.9,
        refused=False,
    )
    planner = _wire(
        monkeypatch, PlannerResult(answer=answer, cost_usd=0.011, searches=1, steps=1)
    )

    out = nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    assert out["output"]["status"] == "answered"
    assert out["output"]["answer"].startswith("Quiet hours")
    assert out["output"]["citations"][0]["doc_id"] == "d1"
    assert out["output"]["confidence"] == 0.9
    assert out["search_count"] == 1
    assert out["cost_so_far"] == pytest.approx(0.011)
    assert "routes" not in out  # answered = terminal
    # The node forwards the (masked) message + state to the planner.
    assert planner.calls[0][0] == "what are the quiet hours"


def test_counters_accumulate_onto_existing_state(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = KnowledgeAnswer(answer="ok", confidence=0.8, refused=False)
    _wire(monkeypatch, PlannerResult(answer=answer, cost_usd=0.02, searches=2, steps=2))

    out = nodes.knowledge(_state(search_count=3, cost_so_far=0.5))

    assert out["search_count"] == 5
    assert out["cost_so_far"] == pytest.approx(0.52)


def test_refusal_routes_to_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.3, refused=True)
    _wire(monkeypatch, PlannerResult(answer=answer, cost_usd=0.011, searches=1, steps=1))

    out = nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    assert out["output"]["status"] == "refused"
    assert out["routes"] == ["maintenance"]
    assert out["search_count"] == 1


def test_zero_cost_refusal_keeps_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    """The planner's offline refusal (no search/cost) passes through unchanged."""
    answer = KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.0, refused=True)
    _wire(monkeypatch, PlannerResult(answer=answer, cost_usd=0.0, searches=0, steps=0))

    out = nodes.knowledge(_state(search_count=2, cost_so_far=1.0))

    assert out["output"]["status"] == "refused"
    assert out["routes"] == ["maintenance"]
    assert out["search_count"] == 2  # no search ran
    assert out["cost_so_far"] == 1.0  # no spend


def test_emits_trajectory_metrics(
    monkeypatch: pytest.MonkeyPatch, in_memory_spans: InMemorySpanExporter
) -> None:
    """CM-85: the node emits steps-to-answer + reformulation-count metrics."""
    answer = KnowledgeAnswer(answer="ok", confidence=0.8, refused=False)
    _wire(
        monkeypatch,
        PlannerResult(
            answer=answer,
            cost_usd=0.02,
            searches=3,
            steps=3,
            reformulations=2,
            termination="answer",
        ),
    )

    with with_request_id("r-traj"):
        nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    spans = in_memory_spans.get_finished_spans()
    steps = next(s for s in spans if s.name == "metric.knowledge.steps")
    assert (steps.attributes or {})["metric.value"] == 3.0
    assert (steps.attributes or {})["termination"] == "answer"
    reformulated = next(s for s in spans if s.name == "metric.knowledge.reformulated")
    assert (reformulated.attributes or {})["metric.value"] == 2.0


def test_node_guardrail_short_circuits_before_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The node's first-statement guardrail check trips before the planner runs."""

    def _boom() -> object:
        raise AssertionError("planner must not run when the node guardrail trips")

    monkeypatch.setattr(nodes, "get_knowledge_planner", _boom)

    out = nodes.knowledge(_state(cost_so_far=999.0))  # over the $5 cap

    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == ["guardrail_terminated"]


def test_mid_loop_guardrail_reason_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-loop guardrail trip surfaced by the planner terminates the node
    exactly like its own check — counters are NOT bumped."""
    answer = KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.0, refused=True)
    _wire(
        monkeypatch,
        PlannerResult(
            answer=answer,
            cost_usd=0.05,
            searches=4,
            steps=3,
            guardrail_reason="search_count 51 > 50",
        ),
    )

    out = nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    assert out["output"]["status"] == "guardrail_terminated"
    assert out["output"]["reason"] == "search_count 51 > 50"
    assert out["routes"] == ["guardrail_terminated"]
    assert "search_count" not in out  # no counter bump on termination
    assert "cost_so_far" not in out


def test_graph_answer_is_terminal_no_maintenance(
    monkeypatch: pytest.MonkeyPatch,
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A confident answer ends the graph; the Maintenance handoff edge stays cold."""
    answer = KnowledgeAnswer(
        answer="Quiet hours are 10pm-7am.",
        citations=[Citation(index=1, doc_id="d1", doc_title="Quiet Hours")],
        confidence=0.9,
        refused=False,
    )
    _wire(monkeypatch, PlannerResult(answer=answer, cost_usd=0.011, searches=1, steps=1))

    graph = build_graph(checkpointer=memory_checkpointer)
    initial = AgentState(
        tenant_id="t1",
        request_id="r-k",
        channel=Channel.WEB,
        raw_message="what are the quiet hours",
    )
    with with_request_id("r-k"):
        final = graph.invoke(initial, config={"configurable": {"thread_id": "thr-k"}})

    assert final["output"]["status"] == "answered"
    span_names = {s.name for s in in_memory_spans.get_finished_spans()}
    assert "langgraph.node.knowledge" in span_names
    assert "langgraph.node.maintenance" not in span_names
