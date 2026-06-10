"""Tests for ``agents.knowledge.retrieval`` (CM-33)."""

from __future__ import annotations

from agents.knowledge.models import RetrievedChunk, VectorChunk
from agents.knowledge.retrieval import (
    DEFAULT_FETCH_K,
    DEFAULT_TOP_K,
    merge_unique,
    retrieve,
    significant_terms,
)


def _vc(doc_id: str, idx: int = 0, text: str = "body") -> VectorChunk:
    return VectorChunk(
        id=f"t1:{doc_id}:{idx}",
        tenantId="t1",
        doc_id=doc_id,
        doc_title=f"Doc {doc_id}",
        chunk_index=idx,
        text=text,
        embedding=[0.1, 0.2],
        content_hash="h",
        doc_version=1,
        source="gdrive",
        ts="2026-05-29T00:00:00+00:00",
    )


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.5] for _ in texts]


class _FakeStore:
    def __init__(
        self,
        vector_hits: list[tuple[VectorChunk, float]],
        keyword_hits: list[VectorChunk],
    ) -> None:
        self._v = vector_hits
        self._k = keyword_hits

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        return self._v[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        return self._k[:top_k]


def test_significant_terms_drops_stopwords_and_short_tokens() -> None:
    terms = significant_terms("What are the gym hours on the weekends?")
    assert "gym" in terms
    assert "hours" in terms
    assert "weekends" in terms
    assert "the" not in terms  # stopword
    assert "on" not in terms  # too short + stopword


def test_fuse_dedupes_and_ranks_overlap_first() -> None:
    c1, c2, c3 = _vc("d1"), _vc("d2"), _vc("d3")
    store = _FakeStore(vector_hits=[(c1, 0.1), (c2, 0.5)], keyword_hits=[c2, c3])

    out = retrieve("anything", tenant_id="t1", store=store, embedder=_FakeEmbedder())

    ids = [rc.chunk.id for rc in out]
    assert ids.count(c2.id) == 1  # deduped across both lists
    assert set(ids) == {c1.id, c2.id, c3.id}
    # c2 appears in BOTH rank lists → highest fused score → ranked first.
    assert ids[0] == c2.id


def test_similarity_is_raw_cosine_score() -> None:
    # CM-47: VectorDistance returns the cosine *similarity* (1.0 = identical),
    # used directly — NOT 1 - score. A 0.82 score must surface as 0.82.
    c1 = _vc("d1")
    store = _FakeStore(vector_hits=[(c1, 0.82)], keyword_hits=[])
    out = retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder())
    assert abs(out[0].similarity - 0.82) < 1e-9


def test_similarity_clamped_to_unit_interval() -> None:
    # Float edge / out-of-range cosines clamp into [0, 1].
    hi, lo = _vc("hi"), _vc("lo")
    store = _FakeStore(vector_hits=[(hi, 1.5), (lo, -0.3)], keyword_hits=[])
    out = retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder())
    sims = {rc.chunk.doc_id: rc.similarity for rc in out}
    assert sims["hi"] == 1.0
    assert sims["lo"] == 0.0


def test_keyword_only_hit_has_zero_similarity() -> None:
    c1 = _vc("d1")
    store = _FakeStore(vector_hits=[], keyword_hits=[c1])
    out = retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder())
    assert out[0].similarity == 0.0


def test_top_k_caps_results() -> None:
    hits = [(_vc(f"d{i}"), 0.1 * i) for i in range(10)]
    store = _FakeStore(vector_hits=hits, keyword_hits=[])
    out = retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder(), top_k=3)
    assert len(out) == 3


def test_empty_corpus_returns_nothing() -> None:
    store = _FakeStore(vector_hits=[], keyword_hits=[])
    assert retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder()) == []


def test_default_topk_keeps_chunks_past_the_old_cut() -> None:
    # CM-99: with the default top_k (8), a chunk ranked 6th–8th — a rule chunk
    # sitting just behind a document's boilerplate — still reaches the LLM
    # context, where the old cut of 5 would have dropped it.
    assert DEFAULT_TOP_K == 8
    assert DEFAULT_FETCH_K == 20
    hits = [(_vc(f"d{i}"), 1.0 - 0.05 * i) for i in range(12)]  # 12 ranked candidates
    store = _FakeStore(vector_hits=hits, keyword_hits=[])
    out = retrieve("q", tenant_id="t1", store=store, embedder=_FakeEmbedder())
    ids = [rc.chunk.doc_id for rc in out]
    assert len(out) == DEFAULT_TOP_K  # 8, not 5
    assert "d7" in ids  # the 8th-ranked chunk survives (was cut at 5)
    assert "d8" not in ids  # still bounded by top_k


# --- CM-84: multi-hop chunk accumulation --------------------------------------


def _rc(doc_id: str, *, score: float, similarity: float) -> RetrievedChunk:
    return RetrievedChunk(chunk=_vc(doc_id), score=score, similarity=similarity)


def test_merge_unique_accumulates_distinct_chunks() -> None:
    hop1 = [_rc("d1", score=0.02, similarity=0.8)]
    hop2 = [_rc("d2", score=0.01, similarity=0.6)]
    merged = merge_unique(hop1, hop2)
    assert {rc.chunk.doc_id for rc in merged} == {"d1", "d2"}
    # Sorted by RRF score descending — best evidence leads.
    assert merged[0].chunk.doc_id == "d1"


def test_merge_unique_dedupes_keeping_best_score_and_similarity() -> None:
    # Same chunk id surfaces in two hops with different score/similarity.
    first = [_rc("d1", score=0.01, similarity=0.55)]
    second = [_rc("d1", score=0.03, similarity=0.82)]
    merged = merge_unique(first, second)
    assert len(merged) == 1
    assert merged[0].score == 0.03  # higher RRF score kept
    assert merged[0].similarity == 0.82  # best-across-hops cosine similarity (CM-47)


def test_merge_unique_empty_accumulator() -> None:
    new = [_rc("d1", score=0.02, similarity=0.7)]
    assert merge_unique([], new) == new
