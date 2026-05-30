"""Deterministic lexical retriever for the Knowledge eval case (CM-39).

Mirrors the double used by ``tests/knowledge/test_eval_knowledge.py`` so the
suite exercises the real ``retrieve`` (RRF) + ``answer_question`` paths with no
network: it plays BOTH the embedder and the search store, scoring KB chunks by
query-term recall so a fully-covering chunk ranks first (similarity 1.0) and
off-topic questions score 0 everywhere (the RAG layer then refuses).
"""

from __future__ import annotations

from agents.knowledge.models import VectorChunk
from agents.knowledge.retrieval import significant_terms


class LexicalRetriever:
    """Plays both ``Embedder`` and ``SearchStore`` over an in-memory KB."""

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
            scored.append((chunk, 1.0 - recall))
        scored.sort(key=lambda pair: pair[1])
        return scored[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        hits = [c for c in self._kb if any(t in c.text.lower() for t in terms)]
        return hits[:top_k]


def kb_chunk(doc_id: str, text: str) -> VectorChunk:
    """Build a minimal KB ``VectorChunk`` from a gold doc id + text."""
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
