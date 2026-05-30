"""Offline eval harness for the Knowledge Agent (CM-33 AC #7).

Runs the REAL retrieval + RAG pipeline over a small labeled dataset
(``tests/eval/knowledge_seed.jsonl``) using the deterministic
:class:`~agents.knowledge.llm.StubChatModel`, so the AC thresholds are
checked reproducibly in CI with no network:

* self-service resolution rate > 25% (answerable questions actually answered)
* hallucination rate < 1% (unanswerable questions must be refused, not faked)

The real-LLM eval is the same harness with ``OPENAI_API_KEY`` set (opt-in,
not run in CI). Retrieval is driven by a deterministic lexical double that
plays both the embedder and the search store — it exercises the genuine
``retrieve`` (RRF) + ``answer_question`` (confidence/refusal) code paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.knowledge.llm import StubChatModel
from agents.knowledge.models import VectorChunk
from agents.knowledge.rag import answer_question
from agents.knowledge.retrieval import retrieve, significant_terms

SEED = Path(__file__).resolve().parents[1] / "eval" / "knowledge_seed.jsonl"


def _load() -> list[dict[str, object]]:
    lines = SEED.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class _LexicalRetriever:
    """Deterministic double playing BOTH ``Embedder`` and ``SearchStore``.

    ``embed`` captures the question's significant terms; ``search_chunks``
    scores each KB chunk by query-term recall used directly as the cosine
    similarity (CM-47: VectorDistance returns a similarity, 1.0 = identical),
    so a chunk containing every question term ranks first with similarity 1.0.
    Off-topic questions get similarity 0 everywhere → the RAG layer refuses.
    """

    def __init__(self, kb: list[VectorChunk]) -> None:
        self._kb = kb
        self._terms: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._terms = significant_terms(texts[0])
        return [[0.0]]

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        q = set(self._terms)
        scored: list[tuple[VectorChunk, float]] = []
        for chunk in self._kb:
            chunk_terms = set(significant_terms(chunk.text))
            recall = len(q & chunk_terms) / len(q) if q else 0.0
            scored.append((chunk, recall))  # recall == cosine similarity here
        # Most-similar first (search_chunks contract), i.e. descending score.
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        hits = [c for c in self._kb if any(t in c.text.lower() for t in terms)]
        return hits[:top_k]


def _kb_chunk(doc_id: str, text: str) -> VectorChunk:
    return VectorChunk(
        id=f"kb:{doc_id}:0",
        tenantId="t-eval",
        doc_id=doc_id,
        doc_title=doc_id,
        chunk_index=0,
        text=text,
        embedding=[0.0],
        content_hash="h",
        doc_version=1,
        source="seed",
        ts="2026-05-29T00:00:00+00:00",
    )


def test_knowledge_eval_meets_prd_thresholds() -> None:
    rows = _load()
    kb = [
        _kb_chunk(str(r["outputs"]["gold_doc_id"]), str(r["outputs"]["gold_chunk_text"]))
        for r in rows
        if r["outputs"]["answerable"]
    ]
    retriever = _LexicalRetriever(kb)
    model = StubChatModel()

    answerable_total = 0
    answered_answerable = 0
    hallucinations = 0

    for row in rows:
        message = str(row["inputs"]["message"])
        retrieved = retrieve(
            message, tenant_id="t-eval", store=retriever, embedder=retriever
        )
        answer = answer_question(message, retrieved, model=model)
        if row["outputs"]["answerable"]:
            answerable_total += 1
            if not answer.refused:
                answered_answerable += 1
        elif not answer.refused:
            hallucinations += 1

    self_service = answered_answerable / answerable_total
    hallucination_rate = hallucinations / len(rows)

    # Dataset sanity — both classes present.
    assert answerable_total >= 10
    assert any(not r["outputs"]["answerable"] for r in rows)

    assert self_service >= 0.25, f"self-service {self_service:.0%} below 25% target"
    assert hallucination_rate < 0.01, (
        f"hallucination {hallucination_rate:.1%} >= 1% — an unanswerable "
        f"question was answered instead of refused"
    )
