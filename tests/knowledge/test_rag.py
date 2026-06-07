"""Tests for ``agents.knowledge.rag`` (CM-33)."""

from __future__ import annotations

from agents.knowledge.llm import RagModelOutput
from agents.knowledge.models import CONFIDENCE_THRESHOLD, RetrievedChunk, VectorChunk
from agents.knowledge.rag import REFUSAL_TEXT, answer_question


def _rc(doc_id: str, *, similarity: float, text: str = "The gym closes at 10pm.") -> RetrievedChunk:
    chunk = VectorChunk(
        id=f"t1:{doc_id}:0",
        tenantId="t1",
        doc_id=doc_id,
        doc_title=f"Doc {doc_id}",
        chunk_index=0,
        text=text,
        embedding=[0.1],
        content_hash="h",
        doc_version=1,
        source="gdrive",
        ts="2026-05-29T00:00:00+00:00",
    )
    return RetrievedChunk(chunk=chunk, score=1.0, similarity=similarity)


class _FakeModel:
    cost_per_call_usd = 0.0

    def __init__(self, out: RagModelOutput) -> None:
        self._out = out
        self.called = False

    def answer(self, question: str, context_blocks: list[str]) -> RagModelOutput:
        self.called = True
        return self._out


class _BoomModel:
    cost_per_call_usd = 0.0

    def answer(self, question: str, context_blocks: list[str]) -> RagModelOutput:
        raise AssertionError("model must not be called")


def test_grounded_answer_with_citation() -> None:
    model = _FakeModel(
        RagModelOutput(can_answer=True, answer="The gym closes at 10pm.", used_chunks=[1])
    )
    out = answer_question("gym hours?", [_rc("d1", similarity=0.9)], model=model)
    assert out.refused is False
    assert out.answer == "The gym closes at 10pm."
    assert out.confidence == 0.9
    assert len(out.citations) == 1
    assert out.citations[0].doc_id == "d1"
    assert out.citations[0].index == 1


def test_refuses_below_confidence_threshold() -> None:
    # Model is willing, but the top similarity is below CONFIDENCE_THRESHOLD → refuse.
    # Derived from the constant so the test tracks the threshold automatically.
    low_similarity = round(CONFIDENCE_THRESHOLD - 0.1, 4)
    model = _FakeModel(RagModelOutput(can_answer=True, answer="x", used_chunks=[1]))
    out = answer_question("q", [_rc("d1", similarity=low_similarity)], model=model)
    assert out.refused is True
    assert out.answer == REFUSAL_TEXT
    assert out.confidence == low_similarity


def test_refuses_when_model_cannot_answer_even_if_similar() -> None:
    model = _FakeModel(RagModelOutput(can_answer=False))
    out = answer_question("q", [_rc("d1", similarity=0.95)], model=model)
    assert out.refused is True
    assert out.confidence == 0.0


def test_empty_retrieved_refuses_without_calling_model() -> None:
    out = answer_question("q", [], model=_BoomModel())
    assert out.refused is True
    assert out.answer == REFUSAL_TEXT


def test_invalid_citation_indices_are_dropped() -> None:
    # used_chunks references index 5 (out of range) — dropped; valid 1 kept.
    model = _FakeModel(RagModelOutput(can_answer=True, answer="a", used_chunks=[5, 1]))
    out = answer_question("q", [_rc("d1", similarity=0.8)], model=model)
    assert [c.index for c in out.citations] == [1]


def test_confident_answer_without_citations_falls_back_to_top_chunk() -> None:
    model = _FakeModel(RagModelOutput(can_answer=True, answer="a", used_chunks=[]))
    out = answer_question("q", [_rc("d7", similarity=0.8)], model=model)
    assert out.refused is False
    assert len(out.citations) == 1
    assert out.citations[0].doc_id == "d7"
