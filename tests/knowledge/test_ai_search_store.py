"""Tests for the Azure AI Search policy store (CM-100).

The `azure-search-documents` SDK is installed, so the store constructs for real
(no network at construction) and we replace its `SearchClient` with a mock to
assert query shape + result mapping offline. Coverage: vector `search_chunks`,
BM25 `keyword_search`, the tenant + shared-policy-partition filter, the
write/`upsert` mapping + key encoding, and the env-driven `get_search_store`
selector (which returns `None` → Cosmos fallback when unconfigured).
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock

import pytest
from agents.knowledge.ai_search_store import (
    DEFAULT_SEARCH_INDEX,
    AISearchStore,
    _encode_key,
    get_search_store,
)
from agents.knowledge.models import VectorChunk


def _make_store(policy_tenant_ids: list[str] | None = None) -> tuple[AISearchStore, MagicMock]:
    store = AISearchStore(
        endpoint="https://example.search.windows.net",
        api_key="fake-key",
        index_name="condo-policies",
        policy_tenant_ids=policy_tenant_ids if policy_tenant_ids is not None else [],
    )
    client = MagicMock()
    store._client = client  # type: ignore[attr-defined]
    return store, client


def _hit(doc_id: str, idx: int, *, score: float = 0.5, text: str = "policy text") -> dict[str, Any]:
    return {
        "@search.score": score,
        "id": f"t1:{doc_id}:{idx}",
        "tenant_id": "t1",
        "doc_id": doc_id,
        "doc_title": "Doc",
        "chunk_index": idx,
        "text": text,
        "content_hash": "h",
        "doc_version": 1,
        "source": "gdrive",
        "ts": "2026-06-10T00:00:00+00:00",
    }


# --- key encoding ------------------------------------------------------------


def test_encode_key_is_valid_and_reversible() -> None:
    key = _encode_key("t1:Clubhouse Booking Policy.pdf:3")
    # AI Search keys allow only letters/digits/_/-/= — base64url satisfies that.
    assert all(c.isalnum() or c in "-_=" for c in key)
    assert base64.urlsafe_b64decode(key).decode("utf-8") == "t1:Clubhouse Booking Policy.pdf:3"


# --- vector search -----------------------------------------------------------


def test_search_chunks_builds_vector_query_and_maps_hit() -> None:
    store, client = _make_store()
    client.search.return_value = iter([_hit("d1", 0, score=0.83)])

    out = store.search_chunks("t1", [0.1, 0.2, 0.3], top_k=3)

    assert len(out) == 1
    chunk, score = out[0]
    assert isinstance(chunk, VectorChunk)
    assert chunk.doc_id == "d1"
    assert chunk.id == "t1:d1:0"
    assert score == 0.83

    kwargs = client.search.call_args.kwargs
    assert kwargs["search_text"] is None
    assert len(kwargs["vector_queries"]) == 1
    assert kwargs["vector_queries"][0].fields == "embedding"
    assert kwargs["filter"] == "tenant_id eq 't1'"
    assert kwargs["top"] == 3


def test_search_chunks_clamps_score() -> None:
    store, client = _make_store()
    client.search.return_value = iter([_hit("d1", 0, score=1.4)])
    _, score = store.search_chunks("t1", [0.1], top_k=1)[0]
    assert score == 1.0


# --- keyword (BM25) search ---------------------------------------------------


def test_keyword_search_builds_bm25_query() -> None:
    store, client = _make_store()
    client.search.return_value = iter([_hit("d2", 0)])

    out = store.keyword_search("t1", ["owner", "noc"], top_k=2)

    assert [c.doc_id for c in out] == ["d2"]
    kwargs = client.search.call_args.kwargs
    assert kwargs["search_text"] == "owner noc"
    assert kwargs["search_fields"] == ["text"]
    assert kwargs["filter"] == "tenant_id eq 't1'"


def test_keyword_search_empty_terms_short_circuits() -> None:
    store, client = _make_store()
    assert store.keyword_search("t1", [], top_k=5) == []
    client.search.assert_not_called()


# --- tenant + shared-policy-partition filter ---------------------------------


def test_filter_unions_caller_and_policy_partitions() -> None:
    store, client = _make_store(policy_tenant_ids=["policies"])
    client.search.return_value = iter([])
    store.search_chunks("condo-tower-a", [0.1], top_k=3)
    assert (
        client.search.call_args.kwargs["filter"]
        == "tenant_id eq 'condo-tower-a' or tenant_id eq 'policies'"
    )


def test_filter_escapes_single_quotes() -> None:
    store, client = _make_store()
    client.search.return_value = iter([])
    store.search_chunks("o'brien", [0.1], top_k=1)
    assert client.search.call_args.kwargs["filter"] == "tenant_id eq 'o''brien'"


# --- write / upsert ----------------------------------------------------------


def test_upsert_maps_fields_and_encodes_key() -> None:
    store, client = _make_store()
    chunk = VectorChunk(
        id="t1:d1:0", tenantId="t1", doc_id="d1", doc_title="Doc", chunk_index=0,
        text="body", embedding=[0.1, 0.2], content_hash="h", doc_version=1,
        source="gdrive", ts="2026-06-10T00:00:00+00:00",
    )
    store.upsert_chunks([chunk])

    docs = client.merge_or_upload_documents.call_args.kwargs["documents"]
    assert len(docs) == 1
    doc = docs[0]
    assert doc["key"] == _encode_key("t1:d1:0")
    assert doc["id"] == "t1:d1:0"
    assert doc["tenant_id"] == "t1"
    assert doc["embedding"] == [0.1, 0.2]


def test_upsert_empty_is_noop() -> None:
    store, client = _make_store()
    store.upsert_chunks([])
    client.merge_or_upload_documents.assert_not_called()


# --- selector ----------------------------------------------------------------


def test_get_search_store_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_SEARCH_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_SEARCH_KEY", raising=False)
    assert get_search_store() is None


def test_get_search_store_placeholder_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://x.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_KEY", "REPLACE-ME")
    assert get_search_store() is None


def test_get_search_store_builds_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://x.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_KEY", "real-key")
    monkeypatch.delenv("AZURE_SEARCH_INDEX", raising=False)
    store = get_search_store()
    assert isinstance(store, AISearchStore)
    assert store._index_name == DEFAULT_SEARCH_INDEX  # type: ignore[attr-defined]
