"""Hybrid retrieval for the Knowledge Agent (CM-33).

Jira: CM-33  | Epic: CM-Epic 6  | Phase 1

Embeds the question (reusing CM-34's :class:`~agents.knowledge.embeddings.Embedder`),
runs a vector search + a keyword search against the Cosmos ``policies-vector``
container, and fuses the two rank lists with **Reciprocal Rank Fusion (RRF)** —
a simple, deterministic, dependency-light reranker. A cross-encoder reranker is
a clean future upgrade behind the same :func:`retrieve` surface.
"""

from __future__ import annotations

import re
from typing import Protocol

from agents.knowledge.embeddings import Embedder
from agents.knowledge.models import RetrievedChunk, VectorChunk

#: Standard RRF dampening constant — higher = flatter contribution by rank.
RRF_K = 60
#: How many results to return after fusion (the LLM answer context). CM-99: raised
#: 5 → 8 so a rule/eligibility chunk that ranks just behind a document's own
#: boilerplate (header, fee/refund table) still lands in the context the LLM sees,
#: instead of being cut at 5 and leaving the model to echo the header.
DEFAULT_TOP_K = 8
#: How many candidates to pull from each retriever before fusing. CM-99: raised
#: 10 → 20 so RRF fuses over a deeper candidate pool (and the top_k=8 slice is
#: drawn from real signal, not a shallow 10-candidate cut).
DEFAULT_FETCH_K = 20

#: Tiny English stop-list — keyword search is a coarse lexical signal, so we
#: just drop the highest-frequency words that would match almost any chunk.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
        "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "it",
        "this", "that", "these", "those", "i", "you", "we", "they", "my", "our",
        "do", "does", "can", "could", "would", "should", "what", "when", "where",
        "how", "why", "who", "which", "there", "here", "about", "please",
    }
)


class SearchStore(Protocol):
    """The read surface :func:`retrieve` needs (satisfied by ``CosmosVectorStore``)."""

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]: ...

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]: ...


def significant_terms(text: str, *, max_terms: int = 8) -> list[str]:
    """Extract de-duplicated, lowercased keyword tokens from ``text``.

    Drops stop-words and tokens shorter than 3 chars; keeps insertion order
    and caps at ``max_terms`` so a long question doesn't blow up the keyword
    query's OR-clause count.
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max_terms:
            break
    return out


def retrieve(
    question: str,
    *,
    tenant_id: str,
    store: SearchStore,
    embedder: Embedder,
    top_k: int = DEFAULT_TOP_K,
    fetch_k: int = DEFAULT_FETCH_K,
) -> list[RetrievedChunk]:
    """Embed → vector + keyword search → RRF-fuse → top-k :class:`RetrievedChunk`."""
    qvecs = embedder.embed([question])
    vector_hits: list[tuple[VectorChunk, float]] = (
        store.search_chunks(tenant_id, qvecs[0], top_k=fetch_k) if qvecs else []
    )
    keyword_hits = store.keyword_search(
        tenant_id, significant_terms(question), top_k=fetch_k
    )
    return _fuse(vector_hits, keyword_hits, top_k=top_k)


def _fuse(
    vector_hits: list[tuple[VectorChunk, float]],
    keyword_hits: list[VectorChunk],
    *,
    top_k: int,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion over the vector + keyword rank lists.

    Each list contributes ``1 / (RRF_K + rank)`` per chunk id; scores sum
    across lists. Vector hits also carry a cosine ``similarity`` — the raw
    ``VectorDistance`` cosine score (``1.0`` = identical), clamped to [0, 1] —
    used downstream for answer confidence; keyword-only hits get similarity 0.0.
    """
    scores: dict[str, float] = {}
    sims: dict[str, float] = {}
    chunks: dict[str, VectorChunk] = {}

    for rank, (chunk, score) in enumerate(vector_hits):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        # CM-47: ``score`` is the cosine *similarity* from VectorDistance
        # (1.0 = identical), NOT a distance — use it directly, clamped to [0, 1].
        # (The old ``1 - score`` inverted it and refused on the best matches.)
        sims[chunk.id] = max(0.0, min(1.0, score))
        chunks[chunk.id] = chunk

    for rank, chunk in enumerate(keyword_hits):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        chunks.setdefault(chunk.id, chunk)
        sims.setdefault(chunk.id, 0.0)

    ranked = sorted(chunks.values(), key=lambda c: scores[c.id], reverse=True)
    return [
        RetrievedChunk(chunk=c, score=scores[c.id], similarity=sims[c.id])
        for c in ranked[:top_k]
    ]


def merge_unique(
    acc: list[RetrievedChunk], new: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Accumulate retrieved chunks across CM-84 multi-hop retrievals.

    Dedupes by ``chunk.id`` and, when the same chunk surfaces in more than one
    hop, keeps the higher RRF ``score`` **and** the higher ``similarity`` seen —
    a later, better-phrased query can rank a chunk higher, and confidence must be
    the best-across-hops cosine similarity (CM-47). The result is sorted by
    ``score`` descending so the most-relevant evidence leads the synthesis prompt.
    """
    by_id: dict[str, RetrievedChunk] = {}
    for rc in (*acc, *new):
        prev = by_id.get(rc.chunk.id)
        if prev is None:
            by_id[rc.chunk.id] = rc
        else:
            by_id[rc.chunk.id] = RetrievedChunk(
                chunk=rc.chunk,
                score=max(prev.score, rc.score),
                similarity=max(prev.similarity, rc.similarity),
            )
    return sorted(by_id.values(), key=lambda rc: rc.score, reverse=True)
