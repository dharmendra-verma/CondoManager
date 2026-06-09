"""Per-step trajectory spans for the iterative Knowledge loop (CM-85).

Each loop iteration must emit a ``langgraph.node.knowledge.step`` span carrying
``step_index`` / ``decision`` / ``query`` / ``top_similarity`` / ``request_id``,
nested under the ``langgraph.node.knowledge`` node span.
"""

from __future__ import annotations

from agents.knowledge.models import VectorChunk
from agents.knowledge.planner import StubKnowledgePlanner
from agents.observability import langgraph_node_span, with_request_id
from agents.orchestrator import AgentState
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _vc(doc_id: str = "d1", text: str = "Quiet hours are 10pm to 7am.") -> VectorChunk:
    return VectorChunk(
        id=f"t1:{doc_id}:0",
        tenantId="t1",
        doc_id=doc_id,
        doc_title=doc_id,
        chunk_index=0,
        text=text,
        embedding=[0.1, 0.2],
        content_hash="h",
        doc_version=1,
        source="seed",
        ts="2026-05-29T00:00:00+00:00",
    )


class _Embedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.5] for _ in texts]


class _Store:
    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        return [(_vc(), 0.8)]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        return []


def test_step_spans_nest_under_node_with_required_attrs(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    state = AgentState(tenant_id="t1", request_id="req_x")
    # Run the planner inside the node span, as the orchestrator does.
    with with_request_id("req_x"), langgraph_node_span("knowledge", tenant_id="t1"):
        StubKnowledgePlanner().run(
            "what are the quiet hours", state=state, store=_Store(), embedder=_Embedder()
        )

    spans = in_memory_spans.get_finished_spans()
    node = next(s for s in spans if s.name == "langgraph.node.knowledge")
    steps = [s for s in spans if s.name == "langgraph.node.knowledge.step"]

    # The stub runs a fixed 2-hop trajectory -> two step spans.
    assert len(steps) == 2
    for s in steps:
        attrs = s.attributes or {}
        # Rolls up under the node span.
        assert s.parent is not None
        assert s.parent.span_id == node.context.span_id
        # Required attributes (AC).
        assert attrs["step_index"] in (0, 1)
        assert attrs["request_id"] == "req_x"
        assert "query" in attrs
        assert "top_similarity" in attrs
        assert "decision" in attrs

    assert {s.attributes["step_index"] for s in steps if s.attributes} == {0, 1}
    assert {s.attributes["decision"] for s in steps if s.attributes} == {
        "reformulate",
        "answer",
    }
