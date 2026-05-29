"""Tests for the CM-33 Knowledge node in ``agents.orchestrator.nodes``.

The node reads its collaborators from module-level factories
(``get_vector_store`` / ``default_embedder`` / ``get_chat_model``) and the
``retrieve`` / ``answer_question`` functions — all imported into the ``nodes``
namespace, so we monkeypatch them there to drive each branch deterministically.
"""

from __future__ import annotations

import pytest
from agents.knowledge.models import Citation, KnowledgeAnswer
from agents.knowledge.rag import REFUSAL_TEXT
from agents.observability import with_request_id
from agents.orchestrator import AgentState, Channel, build_graph, nodes
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class _Model:
    cost_per_call_usd = 0.01


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "request_id": "r1",
        "raw_message": "what are the quiet hours",
    }
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def _wire_answered(monkeypatch: pytest.MonkeyPatch, answer: KnowledgeAnswer) -> None:
    monkeypatch.setattr(nodes, "get_vector_store", lambda: object())
    monkeypatch.setattr(nodes, "default_embedder", lambda: object())
    monkeypatch.setattr(nodes, "get_chat_model", lambda: _Model())
    monkeypatch.setattr(nodes, "retrieve", lambda *a, **k: ["chunk"])
    monkeypatch.setattr(nodes, "answer_question", lambda *a, **k: answer)


def test_answered_sets_output_and_bumps_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = KnowledgeAnswer(
        answer="Quiet hours are 10pm-7am.",
        citations=[Citation(index=1, doc_id="d1", doc_title="Quiet Hours")],
        confidence=0.9,
        refused=False,
    )
    _wire_answered(monkeypatch, answer)

    out = nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    assert out["output"]["status"] == "answered"
    assert out["output"]["answer"].startswith("Quiet hours")
    assert out["output"]["citations"][0]["doc_id"] == "d1"
    assert out["output"]["confidence"] == 0.9
    assert out["search_count"] == 1
    assert out["cost_so_far"] == 0.01 + nodes.KNOWLEDGE_QUERY_EMBED_COST_USD
    assert "routes" not in out  # answered = terminal


def test_refusal_routes_to_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.3, refused=True)
    _wire_answered(monkeypatch, answer)

    out = nodes.knowledge(_state(search_count=0, cost_so_far=0.0))

    assert out["output"]["status"] == "refused"
    assert out["routes"] == ["maintenance"]
    assert out["search_count"] == 1


def test_unconfigured_refuses_without_search_or_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nodes, "get_vector_store", lambda: None)
    monkeypatch.setattr(nodes, "default_embedder", lambda: None)

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("retrieval/answer must not run when unconfigured")

    monkeypatch.setattr(nodes, "retrieve", _boom)
    monkeypatch.setattr(nodes, "answer_question", _boom)

    out = nodes.knowledge(_state(search_count=2, cost_so_far=1.0))

    assert out["output"]["status"] == "refused"
    assert out["routes"] == ["maintenance"]
    assert out["search_count"] == 2  # no search ran
    assert out["cost_so_far"] == 1.0  # no spend


def test_guardrail_trip_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise AssertionError("guardrail must short-circuit before retrieval setup")

    monkeypatch.setattr(nodes, "get_vector_store", _boom)

    out = nodes.knowledge(_state(cost_so_far=999.0))  # over the $5 cap

    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == ["guardrail_terminated"]


def test_graph_answer_is_terminal_no_maintenance(
    monkeypatch: pytest.MonkeyPatch,
    memory_checkpointer: MemorySaver,
    in_memory_spans: InMemorySpanExporter,
) -> None:
    """A confident answer ends the graph; the Maintenance handoff edge stays cold."""
    _wire_answered(
        monkeypatch,
        KnowledgeAnswer(
            answer="Quiet hours are 10pm-7am.",
            citations=[Citation(index=1, doc_id="d1", doc_title="Quiet Hours")],
            confidence=0.9,
            refused=False,
        ),
    )
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
